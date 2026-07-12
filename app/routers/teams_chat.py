"""Teams chat history export (Edge automation) + selector config & diagnose endpoints."""
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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User
from app.core.settings import _get_user_setting

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Teams Chat History Export (web scraping via signed-in Edge profile)
# ---------------------------------------------------------------------------
_TEAMS_CHAT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_teams_chat_cache")
os.makedirs(_TEAMS_CHAT_CACHE_DIR, exist_ok=True)


def _teams_chat_file_path(job_id: str, fmt: str = "html") -> str:
    ext = "html" if fmt == "html" else "txt"
    return os.path.join(_TEAMS_CHAT_CACHE_DIR, f"{job_id}.{ext}")


def _teams_chat_meta_path(job_id: str) -> str:
    return os.path.join(_TEAMS_CHAT_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_teams_chat(max_age_seconds: int = 3600) -> None:
    """Delete Teams chat cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_TEAMS_CHAT_CACHE_DIR):
        fpath = os.path.join(_TEAMS_CHAT_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class TeamsChatExportRequest(BaseModel):
    chat_id: str
    chat_name: str = ""
    start_date: str = ""
    end_date: str = ""
    format: str = "html"


@router.post("/api/teams-chat/list/stream")
async def teams_chat_list_stream(user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "teams_chat_worker.py")
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if edge_profile:
            _env["VT_EDGE_PROFILE"] = edge_profile
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, "list"],
                stdout=_sp.PIPE, stderr=_sp.PIPE,
                text=True, encoding="utf-8", env=_env,
            )
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("DONE:"):
                    try:
                        data = json.loads(line[5:])
                    except Exception:
                        data = {}
                    q.put({"type": "done", "data": data})
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
        error_seen = False
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "done":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                break
            elif event["type"] == "error":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                error_seen = True
                break
            elif event["type"] == "_worker_exit":
                if not error_seen and event["code"] != 0:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Worker process failed unexpectedly.'})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/teams-chat/export/stream")
async def teams_chat_export_stream(req: TeamsChatExportRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "teams_chat_worker.py")
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
                [_sys.executable, worker, "export",
                 req.chat_id, req.chat_name or req.chat_id, tmp.name,
                 req.start_date or "", req.end_date or "", fmt],
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
                chat_name = req.chat_name or req.chat_id
                with open(_teams_chat_meta_path(job_id), "w", encoding="utf-8") as f:
                    json.dump({"user_id": user.id, "chat": chat_name, "count": meta.get("count", 0), "format": fmt}, f)
                try:
                    shutil.move(out_path, _teams_chat_file_path(job_id, fmt))
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save file: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'chat': chat_name, 'count': meta.get('count', 0)}, ensure_ascii=False)}\n\n"
                break
            elif event["type"] == "error":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                error_seen = True
                break
            elif event["type"] == "_worker_exit":
                if not error_seen and event["code"] != 0:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Worker process failed unexpectedly.'})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/teams-chat/download/{job_id}")
def download_teams_chat(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_teams_chat()
    meta_file = _teams_chat_meta_path(job_id)
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Export not found or expired")

    fmt = meta.get("format", "html")
    export_file = _teams_chat_file_path(job_id, fmt)
    if not os.path.isfile(export_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    chat_name = re.sub(r'[\\/:*?"<>|]', "_", meta.get("chat", "chat"))
    if fmt == "html":
        filename = f"Teams聊天记录_{chat_name}.html"
        return FileResponse(export_file, media_type="text/html; charset=utf-8", filename=filename)
    else:
        filename = f"Teams聊天记录_{chat_name}.txt"
        return FileResponse(export_file, media_type="text/plain; charset=utf-8", filename=filename)


# ---------------------------------------------------------------------------
# Teams Chat selector config & diagnose endpoints
# ---------------------------------------------------------------------------
_TEAMS_CHAT_SELECTOR_FILE = os.path.join(_REPO_ROOT, "teams_chat_selectors.json")


@router.get("/api/teams-chat/selectors")
def get_teams_chat_selectors(user: User = Depends(require_user)):
    """Return the current teams_chat_selectors.json content."""
    if os.path.isfile(_TEAMS_CHAT_SELECTOR_FILE):
        try:
            with open(_TEAMS_CHAT_SELECTOR_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


@router.put("/api/teams-chat/selectors")
async def save_teams_chat_selectors(request: Request, user: User = Depends(require_user)):
    """Overwrite teams_chat_selectors.json with the request body."""
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    try:
        with open(_TEAMS_CHAT_SELECTOR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write config: {exc}")
    return {"ok": True}


@router.post("/api/teams-chat/diagnose/stream")
async def teams_chat_diagnose_stream(user: User = Depends(require_user)):
    """Run 'python teams_chat_worker.py diagnose' and stream STATUS lines via SSE."""
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "teams_chat_worker.py")
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if edge_profile:
            _env["VT_EDGE_PROFILE"] = edge_profile
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, "diagnose"],
                stdout=_sp.PIPE, stderr=_sp.PIPE,
                text=True, encoding="utf-8", env=_env,
            )
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("DONE:"):
                    try:
                        data = json.loads(line[5:])
                    except Exception:
                        data = {}
                    q.put({"type": "done", "data": data})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})
            proc.wait()
            if proc.returncode != 0:
                stderr_out = (proc.stderr.read() or "")[-600:]
                q.put({"type": "_worker_exit", "code": proc.returncode, "stderr": stderr_out})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    threading.Thread(target=run_worker, daemon=True).start()

    async def generate():
        error_seen = False
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue
            if event["type"] == "done":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                break
            elif event["type"] == "error":
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                error_seen = True
                break
            elif event["type"] == "_worker_exit":
                if not error_seen and event["code"] != 0:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Diagnose process failed.'}, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
