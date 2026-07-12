"""Microsoft 365 Copilot chat export (Edge automation → SSE → cache)."""
import asyncio
import json
import os
import queue as stdlib_queue
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User
from app.core.settings import _get_user_setting

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Microsoft 365 Copilot Chat Export (web scraping via signed-in Edge profile)
# ---------------------------------------------------------------------------
_COPILOT_CHAT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_copilot_chat_cache")
os.makedirs(_COPILOT_CHAT_CACHE_DIR, exist_ok=True)


def _copilot_chat_file_path(job_id: str, fmt: str = "html") -> str:
    ext = "html" if fmt == "html" else "txt"
    return os.path.join(_COPILOT_CHAT_CACHE_DIR, f"{job_id}.{ext}")


def _copilot_chat_meta_path(job_id: str) -> str:
    return os.path.join(_COPILOT_CHAT_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_copilot_chat(max_age_seconds: int = 3600) -> None:
    """Delete Copilot chat cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_COPILOT_CHAT_CACHE_DIR):
        fpath = os.path.join(_COPILOT_CHAT_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class CopilotChatExportRequest(BaseModel):
    url: str
    format: str = "html"


@router.post("/api/copilot-chat/export/stream")
async def copilot_chat_export_stream(req: CopilotChatExportRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "copilot_chat_worker.py")
        fmt = req.format if req.format in ("html", "txt") else "html"
        suffix = ".html" if fmt == "html" else ".txt"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if edge_profile:
            _env["VT_EDGE_PROFILE"] = edge_profile
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, "export", req.url, tmp.name, fmt],
                stdout=_sp.PIPE, stderr=_sp.PIPE,
                text=True, encoding="utf-8", env=_env,
            )
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("DONE:"):
                    try:
                        meta = json.loads(line[5:])
                    except Exception:
                        meta = {}
                    q.put({"type": "_done_marker", "path": tmp.name, "meta": meta})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})
            proc.wait()
            if proc.returncode != 0:
                stderr_out = (proc.stderr.read() or "")[-600:]
                q.put({"type": "_worker_exit", "code": proc.returncode, "stderr": stderr_out})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()

    async def generate():
        job_id = uuid.uuid4().hex[:12]
        error_seen = False
        fmt = req.format if req.format in ("html", "txt") else "html"
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "_done_marker":
                out_path = event["path"]
                meta = event.get("meta", {})
                title = meta.get("title", "") or "Copilot 对话"
                with open(_copilot_chat_meta_path(job_id), "w", encoding="utf-8") as f:
                    json.dump({"user_id": user.id, "title": title, "count": meta.get("count", 0), "format": fmt}, f)
                try:
                    shutil.move(out_path, _copilot_chat_file_path(job_id, fmt))
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save file: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'title': title, 'count': meta.get('count', 0)}, ensure_ascii=False)}\n\n"
                break
            elif event["type"] == "error":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                error_seen = True
                break
            elif event["type"] == "_worker_exit":
                if not error_seen and event["code"] != 0:
                    detail = (event.get("stderr") or "").strip()
                    msg = f"Worker process failed unexpectedly.{(' — ' + detail[-400:]) if detail else ''}"
                    yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/copilot-chat/download/{job_id}")
def download_copilot_chat(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_copilot_chat()
    meta_file = _copilot_chat_meta_path(job_id)
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Export not found or expired")

    fmt = meta.get("format", "html")
    export_file = _copilot_chat_file_path(job_id, fmt)
    if not os.path.isfile(export_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    title = re.sub(r'[\\/:*?"<>|]', "_", meta.get("title", "Copilot对话"))[:80] or "Copilot对话"
    if fmt == "html":
        filename = f"Copilot对话_{title}.html"
        return FileResponse(export_file, media_type="text/html; charset=utf-8", filename=filename)
    else:
        filename = f"Copilot对话_{title}.txt"
        return FileResponse(export_file, media_type="text/plain; charset=utf-8", filename=filename)
