"""WeChat chat export — list contacts + export history (subprocess → SSE → cache)."""
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

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# WeChat Chat Export
# ---------------------------------------------------------------------------
_WECHAT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_wechat_cache")
os.makedirs(_WECHAT_CACHE_DIR, exist_ok=True)


def _wechat_file_path(job_id: str, fmt: str = "txt") -> str:
    ext = "html" if fmt == "html" else "txt"
    return os.path.join(_WECHAT_CACHE_DIR, f"{job_id}.{ext}")


def _wechat_meta_path(job_id: str) -> str:
    return os.path.join(_WECHAT_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_wechat(max_age_seconds: int = 3600) -> None:
    """Delete WeChat cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_WECHAT_CACHE_DIR):
        fpath = os.path.join(_WECHAT_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class WechatContactsRequest(BaseModel):
    data_dir: str = "auto"


class WechatExportRequest(BaseModel):
    data_dir: str = "auto"
    contact_id: str
    contact_name: str = ""
    start_date: str = ""
    end_date: str = ""
    format: str = "txt"


@router.post("/api/wechat/contacts/stream")
async def wechat_contacts_stream(req: WechatContactsRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "wechat_worker.py")
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, "contacts", req.data_dir],
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


@router.post("/api/wechat/export/stream")
async def wechat_export_stream(req: WechatExportRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "wechat_worker.py")
        suffix = ".html" if req.format == "html" else ".txt"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, "export", req.data_dir, req.contact_id, tmp.name,
                 req.start_date or "", req.end_date or "", req.format or "txt"],
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
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "_done_marker":
                txt_path = event["path"]
                meta = event.get("meta", {})
                contact_name = req.contact_name or req.contact_id
                fmt = req.format or "txt"
                # Save metadata
                with open(_wechat_meta_path(job_id), "w", encoding="utf-8") as f:
                    json.dump({"user_id": user.id, "contact": contact_name, "count": meta.get("count", 0), "format": fmt}, f)
                # Move output file
                try:
                    shutil.move(txt_path, _wechat_file_path(job_id, fmt))
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save file: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'contact': contact_name, 'count': meta.get('count', 0)}, ensure_ascii=False)}\n\n"
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


@router.get("/api/wechat/download/{job_id}")
def download_wechat(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_wechat()
    txt_file = _wechat_file_path(job_id)
    meta_file = _wechat_meta_path(job_id)
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Export not found or expired")

    fmt = meta.get("format", "txt")
    export_file = _wechat_file_path(job_id, fmt)
    if not os.path.isfile(export_file):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    contact_name = re.sub(r'[\\/:*?"<>|]', "_", meta.get("contact", "chat"))
    if fmt == "html":
        filename = f"微信聊天记录_{contact_name}.html"
        return FileResponse(export_file, media_type="text/html; charset=utf-8", filename=filename)
    else:
        filename = f"微信聊天记录_{contact_name}.txt"
        return FileResponse(export_file, media_type="text/plain; charset=utf-8", filename=filename)
