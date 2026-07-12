"""Discord channel export — token settings + channel scrape (subprocess → SSE → cache)."""
import asyncio
import json
import os
import queue as stdlib_queue
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
from app.core.settings import _delete_user_setting, _get_user_setting, _set_user_setting

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Discord Chat Export
# ---------------------------------------------------------------------------
_DISCORD_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_discord_cache")
os.makedirs(_DISCORD_CACHE_DIR, exist_ok=True)


def _discord_file_path(job_id: str) -> str:
    return os.path.join(_DISCORD_CACHE_DIR, f"{job_id}.html")


def _discord_meta_path(job_id: str) -> str:
    return os.path.join(_DISCORD_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_discord(max_age_seconds: int = 3600) -> None:
    now = datetime.now().timestamp()
    for fname in os.listdir(_DISCORD_CACHE_DIR):
        fpath = os.path.join(_DISCORD_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class DiscordExportRequest(BaseModel):
    token: str
    channel_url: str
    limit: int | None = None
    start_date: str | None = None
    end_date: str | None = None


class DiscordTokenRequest(BaseModel):
    token: str


@router.get("/api/discord/token")
def get_discord_token(user: User = Depends(require_user)):
    value = _get_user_setting(user.id, "discord_token")
    return {"token": value or ""}


@router.put("/api/discord/token")
def save_discord_token(req: DiscordTokenRequest, user: User = Depends(require_user)):
    _set_user_setting(user.id, "discord_token", req.token.strip())
    return {"ok": True}


@router.delete("/api/discord/token")
def clear_discord_token(user: User = Depends(require_user)):
    _delete_user_setting(user.id, "discord_token")
    return {"ok": True}


@router.post("/api/discord/stream")
async def discord_stream(req: DiscordExportRequest, user: User = Depends(require_user)):
    channel_url = req.channel_url.strip()
    if not channel_url:
        raise HTTPException(status_code=400, detail="Channel URL is required")

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess as _sp
        import sys as _sys

        worker = os.path.join(_REPO_ROOT, "discord_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, dir=_DISCORD_CACHE_DIR)
        tmp.close()

        try:
            cmd = [_sys.executable, worker, req.token, channel_url, tmp.name]
            # Positional args: limit start_date end_date (empty string = omitted)
            cmd.append(str(req.limit) if req.limit else "")
            cmd.append(req.start_date.strip() if req.start_date else "")
            cmd.append(req.end_date.strip() if req.end_date else "")

            proc = _sp.Popen(
                cmd,
                stdout=_sp.PIPE,
                stderr=_sp.PIPE,
                text=True,
                encoding="utf-8",
            )

            stderr_lines: list = []
            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_lines.append(ln)
            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("DONE:"):
                    try:
                        data = json.loads(line[5:])
                        if isinstance(data, str):
                            data = json.loads(data)
                        q.put({"type": "_done_marker", "path": tmp.name, "data": data})
                    except (json.JSONDecodeError, ValueError):
                        q.put({"type": "_done_marker", "path": tmp.name, "data": {}})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})

            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0 and not any(
                e["type"] in ("_done_marker", "error") for e in list(q.queue)
            ):
                stderr_out = "".join(stderr_lines).strip()
                q.put({"type": "error", "message": stderr_out[-400:] if stderr_out else "Worker process failed"})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()

    async def generate():
        job_id = uuid.uuid4().hex[:12]
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "_done_marker":
                html_path = event["path"]
                data = event.get("data", {})
                # Move to cache with proper job_id
                final_path = _discord_file_path(job_id)
                try:
                    if html_path != final_path:
                        shutil.move(html_path, final_path)
                except OSError:
                    pass
                # Save metadata
                meta = {
                    "user_id": user.id,
                    "channel_url": channel_url,
                    "channel_name": data.get("channel_name", ""),
                    "guild_name": data.get("guild_name", ""),
                    "message_count": data.get("message_count", 0),
                    "filename": data.get("filename", "discord_export.html"),
                }
                with open(_discord_meta_path(job_id), "w", encoding="utf-8") as f:
                    json.dump(meta, f)
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'message_count': meta['message_count']})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "error":
                    break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/discord/download/{job_id}")
def download_discord(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_discord()
    meta_path = _discord_meta_path(job_id)
    html_path = _discord_file_path(job_id)
    if not os.path.isfile(meta_path) or not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="Export not found or expired")

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Export not found or expired")

    filename = meta.get("filename", "discord_export.html")
    return FileResponse(html_path, media_type="text/html; charset=utf-8", filename=filename)
