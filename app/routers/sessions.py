"""Copilot Session Reader — browse & read VS Code Copilot chat transcripts.

VS Code stores per-session chat transcripts as JSONL under
  %APPDATA%/Code/User/workspaceStorage/<hash>/GitHub.copilot-chat/transcripts/
These are hard to read raw; this module parses them into clean conversations.

Override the search root with env var VT_COPILOT_TRANSCRIPTS_ROOT (points at a
single `transcripts` folder OR at the `workspaceStorage` folder to scan all
workspaces). Defaults to the current user's VS Code storage on Windows.
"""
import json
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User

router = APIRouter()


def _copilot_transcript_roots() -> list[str]:
    """Return candidate `workspaceStorage` directories to scan for transcripts."""
    override = os.environ.get("VT_COPILOT_TRANSCRIPTS_ROOT")
    if override:
        return [override]
    roots: list[str] = []
    appdata = os.environ.get("APPDATA")  # Windows
    if appdata:
        roots.append(os.path.join(appdata, "Code", "User", "workspaceStorage"))
    # VS Code - Insiders and non-Windows fallbacks
    home = os.path.expanduser("~")
    roots.extend([
        os.path.join(home, ".config", "Code", "User", "workspaceStorage"),
        os.path.join(home, "Library", "Application Support", "Code", "User", "workspaceStorage"),
    ])
    return [r for r in roots if os.path.isdir(r)]


def _iter_transcript_files(custom_root: str | None = None):
    """Yield (session_id, file_path, workspace_hash) for every transcript JSONL.

    When ``custom_root`` is given, only that directory is scanned (it may be a
    `transcripts` folder, a `workspaceStorage` folder, or any folder that
    directly contains .jsonl files). Otherwise the default OS locations are used.
    """
    roots = [custom_root] if custom_root else _copilot_transcript_roots()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        # Case 1: root is a `workspaceStorage` folder — scan each workspace.
        # (Detected by the presence of GitHub.copilot-chat/transcripts subdirs.)
        found_workspace = False
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for ws in entries:
            tdir = os.path.join(root, ws, "GitHub.copilot-chat", "transcripts")
            if os.path.isdir(tdir):
                found_workspace = True
                for fn in os.listdir(tdir):
                    if fn.endswith(".jsonl"):
                        yield os.path.splitext(fn)[0], os.path.join(tdir, fn), ws
        if found_workspace:
            continue
        # Case 2: root directly contains .jsonl files (a `transcripts` folder or
        # any custom folder the user pointed at).
        for fn in entries:
            if fn.endswith(".jsonl"):
                yield os.path.splitext(fn)[0], os.path.join(root, fn), ""


def _build_transcript_index(custom_root: str | None = None) -> dict[str, dict]:
    """Map session_id -> {path, workspace} for all discoverable transcripts."""
    index: dict[str, dict] = {}
    for sid, path, ws in _iter_transcript_files(custom_root):
        index[sid] = {"path": path, "workspace": ws}
    return index


def _read_jsonl(path: str) -> list[dict]:
    """Read a JSONL file into a list of event dicts, skipping bad lines."""
    events: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _summarize_transcript(path: str) -> dict:
    """Return lightweight metadata for a transcript file (for the list view)."""
    return _summarize_events(_read_jsonl(path))


def _summarize_events(events: list[dict]) -> dict:
    """Return lightweight metadata from a list of transcript events."""
    start_meta: dict = {}
    first_user = ""
    user_count = 0
    assistant_count = 0
    tool_count = 0
    last_ts = ""
    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") or {}
        ts = ev.get("timestamp") or ""
        if ts:
            last_ts = ts
        if etype == "session.start":
            start_meta = data
        elif etype == "user.message":
            user_count += 1
            if not first_user:
                first_user = (data.get("content") or "").strip()
        elif etype == "assistant.message":
            assistant_count += 1
        elif etype == "tool.execution_start":
            tool_count += 1
    title = first_user.splitlines()[0][:80] if first_user else "(无用户消息)"
    return {
        "start_time": start_meta.get("startTime", ""),
        "last_time": last_ts,
        "producer": start_meta.get("producer", ""),
        "copilot_version": start_meta.get("copilotVersion", ""),
        "vscode_version": start_meta.get("vscodeVersion", ""),
        "title": title,
        "user_count": user_count,
        "assistant_count": assistant_count,
        "tool_count": tool_count,
    }


def _parse_transcript(path: str) -> list[dict]:
    """Parse a transcript JSONL file into an ordered list of conversation items."""
    return _parse_events(_read_jsonl(path))


def _parse_events(events: list[dict]) -> list[dict]:
    """Parse transcript events into an ordered list of conversation items.

    Item shapes:
      {"kind": "user", "content", "timestamp", "attachments"}
      {"kind": "assistant", "content", "reasoning", "timestamp",
       "tools": [{"name", "arguments", "success"}]}
    Tool success is merged in from tool.execution_complete by toolCallId.
    """
    # First pass: collect tool completion status by toolCallId.
    tool_status: dict[str, bool | None] = {}
    for ev in events:
        if ev.get("type") == "tool.execution_complete":
            data = ev.get("data") or {}
            tool_status[data.get("toolCallId")] = data.get("success")

    items: list[dict] = []
    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") or {}
        ts = ev.get("timestamp") or ""
        if etype == "user.message":
            items.append({
                "kind": "user",
                "content": data.get("content") or "",
                "attachments": data.get("attachments") or [],
                "timestamp": ts,
            })
        elif etype == "assistant.message":
            tools = []
            for tr in data.get("toolRequests") or []:
                tools.append({
                    "name": tr.get("name") or "",
                    "arguments": tr.get("arguments") or "",
                    "success": tool_status.get(tr.get("toolCallId")),
                })
            content = (data.get("content") or "").strip()
            reasoning = (data.get("reasoningText") or "").strip()
            # Skip fully empty assistant beats (no text, no reasoning, no tools).
            if not content and not reasoning and not tools:
                continue
            items.append({
                "kind": "assistant",
                "content": content,
                "reasoning": reasoning,
                "tools": tools,
                "timestamp": ts,
            })
    return items


_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class SessionFileRequest(BaseModel):
    path: str  # absolute path to a .jsonl transcript file


class SessionContentRequest(BaseModel):
    content: str            # raw JSONL text (for drag-and-drop uploads)
    filename: str = ""      # original filename, used to derive session id


@router.get("/api/sessions")
def list_sessions(root: str | None = None, user: User = Depends(require_user)):
    """List all discoverable Copilot chat transcripts with metadata.

    Optional ``root`` query param scans a custom folder instead of the default
    VS Code storage locations. It may point at a `transcripts` folder, a
    `workspaceStorage` folder, or any folder containing .jsonl files.
    """
    custom_root = (root or "").strip().strip('"').strip("'") or None
    if custom_root and not os.path.isdir(custom_root):
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {custom_root}")
    scanned_roots = [custom_root] if custom_root else _copilot_transcript_roots()
    sessions = []
    for sid, path, ws in _iter_transcript_files(custom_root):
        try:
            meta = _summarize_transcript(path)
            meta.update({
                "session_id": sid,
                "workspace": ws,
                "path": path,
                "size_bytes": os.path.getsize(path),
            })
            sessions.append(meta)
        except OSError:
            continue
    # Newest first (fall back to file mtime when start_time missing).
    sessions.sort(key=lambda s: s.get("start_time") or s.get("last_time") or "", reverse=True)
    return {"sessions": sessions, "count": len(sessions), "roots": scanned_roots}


@router.post("/api/sessions/load")
def load_session_file(req: SessionFileRequest, user: User = Depends(require_user)):
    """Parse and return a single transcript by its absolute file path."""
    path = (req.path or "").strip().strip('"').strip("'")
    if not path:
        raise HTTPException(status_code=400, detail="路径不能为空")
    if not path.lower().endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="请提供 .jsonl 文件路径")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    meta = _summarize_transcript(path)
    sid = os.path.splitext(os.path.basename(path))[0]
    meta.update({"session_id": sid, "workspace": "", "path": path})
    return {"meta": meta, "items": _parse_transcript(path)}


@router.post("/api/sessions/parse")
def parse_session_content(req: SessionContentRequest, user: User = Depends(require_user)):
    """Parse raw JSONL transcript text (from a drag-and-dropped file)."""
    text = req.content or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not events:
        raise HTTPException(status_code=400, detail="无法解析为 JSONL 会话文件")
    meta = _summarize_events(events)
    sid = os.path.splitext(os.path.basename(req.filename))[0] if req.filename else "dropped"
    meta.update({"session_id": sid, "workspace": "", "path": req.filename or ""})
    return {"meta": meta, "items": _parse_events(events)}


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str, root: str | None = None, user: User = Depends(require_user)):
    """Return the parsed conversation for a single transcript."""
    if not _VALID_SESSION_ID.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    custom_root = (root or "").strip().strip('"').strip("'") or None
    index = _build_transcript_index(custom_root)
    entry = index.get(session_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Session not found")
    path = entry["path"]
    meta = _summarize_transcript(path)
    meta.update({"session_id": session_id, "workspace": entry["workspace"], "path": path})
    return {"meta": meta, "items": _parse_transcript(path)}
