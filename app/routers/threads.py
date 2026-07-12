"""Threads video download (yt-dlp worker → SSE progress → cache → zip download)."""
import asyncio
import io
import json
import os
import queue as stdlib_queue
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Threads Video Download
# ---------------------------------------------------------------------------
_THREADS_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_threads_cache")
os.makedirs(_THREADS_CACHE_DIR, exist_ok=True)


def _threads_job_dir(job_id: str) -> str:
    return os.path.join(_THREADS_CACHE_DIR, job_id)


def _threads_meta_path(job_id: str) -> str:
    return os.path.join(_THREADS_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_threads(max_age_seconds: int = 3600) -> None:
    """Delete Threads cache job dirs / meta files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_THREADS_CACHE_DIR):
        fpath = os.path.join(_THREADS_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                if os.path.isdir(fpath):
                    shutil.rmtree(fpath, ignore_errors=True)
                else:
                    os.unlink(fpath)
        except OSError:
            pass


class ThreadsDownloadRequest(BaseModel):
    url: str


@router.post("/api/threads/stream")
async def threads_stream(req: ThreadsDownloadRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    job_id = uuid.uuid4().hex[:12]
    job_dir = _threads_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)

    def run_worker():
        worker = os.path.join(_REPO_ROOT, "threads_worker.py")
        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = _sp.Popen(
                [_sys.executable, worker, url, job_dir],
                stdout=_sp.PIPE, stderr=_sp.PIPE,
                text=True, encoding="utf-8", env=_env,
            )

            stderr_lines: list = []

            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_lines.append(ln)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("PROGRESS:"):
                    q.put({"type": "progress", "message": line[9:]})
                elif line.startswith("DONE:"):
                    try:
                        data = json.loads(line[5:])
                    except (json.JSONDecodeError, ValueError):
                        data = {}
                    q.put({"type": "_done_marker", "data": data})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})

            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0 and not any(
                e["type"] in ("_done_marker", "error") for e in list(q.queue)
            ):
                stderr_out = "".join(stderr_lines).strip()
                q.put({"type": "error", "message": (stderr_out[-400:] if stderr_out else "Worker process failed")})
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": str(e)})

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()

    async def generate():
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "_done_marker":
                data = event.get("data", {})
                meta = {
                    "user_id": user.id,
                    "url": url,
                    "title": data.get("title", "threads_video"),
                    "uploader": data.get("uploader", ""),
                    "files": data.get("files", []),
                    "count": data.get("count", 0),
                }
                try:
                    with open(_threads_meta_path(job_id), "w", encoding="utf-8") as f:
                        json.dump(meta, f, ensure_ascii=False)
                except OSError as exc:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save metadata: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'title': meta['title'], 'uploader': meta['uploader'], 'count': meta['count']}, ensure_ascii=False)}\n\n"
                break
            elif event["type"] == "error":
                shutil.rmtree(job_dir, ignore_errors=True)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                break
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/threads/download/{job_id}")
def download_threads(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_threads()
    meta_path = _threads_meta_path(job_id)
    job_dir = _threads_job_dir(job_id)
    if not os.path.isfile(meta_path) or not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail="Download not found or expired")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Download not found or expired")

    files = [f for f in meta.get("files", []) if os.path.isfile(os.path.join(job_dir, f))]
    if not files:
        raise HTTPException(status_code=404, detail="No video files found")

    safe_title = re.sub(r'[\\/:*?"<>|]', "_", meta.get("title", "threads_video")).strip()
    safe_title = (safe_title[:80] or "threads_video")

    if len(files) == 1:
        single = os.path.join(job_dir, files[0])
        ext = os.path.splitext(files[0])[1] or ".mp4"
        download_name = f"{safe_title}{ext}"
        ascii_name = download_name.encode("ascii", "ignore").decode("ascii") or f"threads_video{ext}"
        encoded = quote(download_name, safe="")
        return FileResponse(
            single,
            media_type="video/mp4",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_name}"; '
                    f"filename*=UTF-8''{encoded}"
                )
            },
        )

    # Multiple videos → zip on-the-fly
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in files:
            zf.write(os.path.join(job_dir, fname), fname)
    zip_filename = f"{safe_title}.zip"
    ascii_zip = zip_filename.encode("ascii", "ignore").decode("ascii") or "threads_videos.zip"
    encoded_zip = quote(zip_filename, safe="")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_zip}"; '
                f"filename*=UTF-8''{encoded_zip}"
            )
        },
    )
