"""Teams / SharePoint recording → VTT transcript (Edge automation, queue + SSE)."""
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
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User, save_to_db
from app.core.settings import _get_user_setting
from app.core.text_utils import _parse_vtt, _split_vtt_into_chunks, format_timestamp
from app.core.validation import _URL_PATTERN

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Teams Transcript endpoint
# ---------------------------------------------------------------------------
_VTT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_vtt_cache")
os.makedirs(_VTT_CACHE_DIR, exist_ok=True)


def _vtt_file_path(job_id: str) -> str:
    return os.path.join(_VTT_CACHE_DIR, f"{job_id}.txt")


def _vtt_meta_path(job_id: str) -> str:
    return os.path.join(_VTT_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_vtt(max_age_seconds: int = 3600) -> None:
    """Delete VTT cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_VTT_CACHE_DIR):
        fpath = os.path.join(_VTT_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class TeamsTranscriptRequest(BaseModel):
    url: str


# ---------------------------------------------------------------------------
# Teams Transcript — in-memory job registry (queue-based workflow)
# ---------------------------------------------------------------------------
# Maps job_id -> job dict for the lifetime of the server process.
# { job_id, user_id, url, status: "running"|"done"|"error",
#   progress: [{type, message}], result: {name, lang}|None,
#   error_message: str|None, created_at: float }
_teams_jobs: dict[str, dict] = {}
_teams_edge_semaphore = threading.Semaphore(1)  # one Edge instance at a time


def _teams_run_job(job_id: str, url: str, user_id: str, edge_profile: str) -> None:
    """Background thread: runs the Edge worker subprocess and updates _teams_jobs."""
    import subprocess
    import sys as _sys
    import time as _time

    job = _teams_jobs.get(job_id)
    if job is None:
        return  # job was deleted before thread started

    def _log(msg_type: str, message: str) -> None:
        job["progress"].append({"type": msg_type, "message": message})
        if len(job["progress"]) > 200:
            job["progress"] = job["progress"][-200:]

    # Block until no other Edge automation job is running,
    # but poll for cancellation every 0.5 s so the user can stop a queued job.
    if not _teams_edge_semaphore.acquire(blocking=False):
        _log("status", "排队等待中（Edge 自动化同时只允许一个任务，请稍候）…")
        while not _teams_edge_semaphore.acquire(blocking=False):
            if job.get("_cancel"):
                job["status"] = "cancelled"
                return
            _time.sleep(0.5)

    if job.get("_cancel"):
        _teams_edge_semaphore.release()
        job["status"] = "cancelled"
        return

    tmp_path: str | None = None
    try:
        _log("status", "开始运行…")
        worker = os.path.join(_REPO_ROOT, "teams_transcript_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()
        tmp_path = tmp.name

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if edge_profile:
            _env["VT_EDGE_PROFILE"] = edge_profile

        error_seen = False
        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=_env,
            )
            job["_proc"] = proc  # expose for cancellation
            for raw_line in proc.stdout:
                if job.get("_cancel"):
                    proc.kill()
                    break
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    _log("status", line[7:])
                elif line.startswith("DONE:"):
                    try:
                        meta = json.loads(line[5:])
                    except Exception:
                        meta = {"name": "transcript", "lang": ""}
                    try:
                        with open(_vtt_meta_path(job_id), "w", encoding="utf-8") as f:
                            json.dump({
                                "user_id": user_id,
                                "name": meta.get("name", "transcript"),
                                "lang": meta.get("lang", ""),
                                "url": url,
                            }, f)
                        shutil.move(tmp_path, _vtt_file_path(job_id))
                        tmp_path = None  # moved; skip cleanup in finally
                    except OSError as exc:
                        job["status"] = "error"
                        job["error_message"] = f"Failed to save VTT: {exc}"
                        error_seen = True
                        break
                    try:
                        with open(_vtt_file_path(job_id), encoding="utf-8") as f:
                            vtt_content = f.read()
                        raw_segs = _parse_vtt(vtt_content)
                        db_segments = [
                            {
                                "start": format_timestamp(s["start"]),
                                "end": format_timestamp(s["end"]),
                                "text": s["text"],
                            }
                            for s in raw_segs
                        ]
                        full_text = " ".join(s["text"] for s in raw_segs)
                        save_to_db(
                            job_id=job_id,
                            title=meta.get("name", "transcript"),
                            url=url,
                            language=meta.get("lang", "") or "unknown",
                            model="teams",
                            text=full_text,
                            segments=db_segments,
                            user_id=user_id,
                        )
                    except Exception:  # noqa: BLE001
                        pass  # history save is best-effort
                    job["result"] = {
                        "name": meta.get("name", "transcript"),
                        "lang": meta.get("lang", ""),
                    }
                    job["status"] = "done"
                    _log("done", f"✓ 字幕已就绪：{meta.get('name', 'transcript')}")
                    error_seen = True
                elif line.startswith("ERROR:"):
                    job["status"] = "error"
                    job["error_message"] = line[6:]
                    _log("error", line[6:])
                    error_seen = True
            proc.wait()
            if proc.returncode != 0 and not error_seen:
                job["status"] = "error"
                job["error_message"] = "Worker process failed unexpectedly."
                _log("error", "Worker process failed unexpectedly.")
        except Exception as exc:
            job["status"] = "error"
            job["error_message"] = str(exc)
            _log("error", str(exc))
    finally:
        _teams_edge_semaphore.release()
        job.pop("_proc", None)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.post("/api/teams-transcript/enqueue")
def teams_transcript_enqueue(req: TeamsTranscriptRequest, user: User = Depends(require_user)):
    """Submit a Teams transcript job; returns job_id immediately."""
    url = req.url.strip()
    if not _URL_PATTERN.match(url) and "sharepoint.com" not in url:
        raise HTTPException(status_code=400, detail="Please provide a SharePoint/Teams recording URL")
    job_id = uuid.uuid4().hex[:12]
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""
    _teams_jobs[job_id] = {
        "job_id": job_id,
        "user_id": user.id,
        "url": url,
        "status": "running",
        "progress": [],
        "result": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc).timestamp(),
        "_cancel": False,   # set True to request cancellation
        "_proc": None,      # subprocess.Popen reference while running
    }
    threading.Thread(
        target=_teams_run_job,
        args=(job_id, url, user.id, edge_profile),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@router.get("/api/teams-transcript/jobs")
def teams_transcript_jobs_list(user: User = Depends(require_user)):
    """Return a slim list of all Teams transcript jobs for the current user."""
    user_jobs = [j for j in _teams_jobs.values() if j["user_id"] == user.id]
    user_jobs.sort(key=lambda j: j["created_at"], reverse=True)
    slim = []
    for j in user_jobs:
        last_msg = j["progress"][-1]["message"] if j["progress"] else ""
        slim.append({
            "job_id": j["job_id"],
            "url": j["url"],
            "status": j["status"],
            "last_message": last_msg,
            "result": j["result"],
            "error_message": j["error_message"],
            "created_at": j["created_at"],
        })
    return {"jobs": slim}


@router.get("/api/teams-transcript/status/{job_id}")
def teams_transcript_status(job_id: str, user: User = Depends(require_user)):
    """Return the full state (including progress log) of a single job."""
    job = _teams_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    # Exclude internal runtime fields (e.g. _proc, _cancel) — not JSON-serializable
    return {k: v for k, v in job.items() if not k.startswith("_")}


@router.delete("/api/teams-transcript/jobs/{job_id}")
def teams_transcript_delete_job(job_id: str, user: User = Depends(require_user)):
    """Stop and remove a job (running or finished)."""
    job = _teams_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    # Signal the background thread to cancel
    job["_cancel"] = True
    proc = job.get("_proc")
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
    del _teams_jobs[job_id]
    return {"ok": True}


@router.post("/api/teams-transcript/stream")
async def teams_transcript_stream(req: TeamsTranscriptRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url) and "sharepoint.com" not in url:
        raise HTTPException(status_code=400, detail="Please provide a SharePoint/Teams recording URL")

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    edge_profile = _get_user_setting(user.id, "edge_profile") or ""

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(_REPO_ROOT, "teams_transcript_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if edge_profile:
            _env["VT_EDGE_PROFILE"] = edge_profile
        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=_env,
            )
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("DONE:"):
                    try:
                        meta = json.loads(line[5:])
                    except Exception:
                        meta = {"name": "transcript", "lang": ""}
                    q.put({"type": "_done_marker", "path": tmp.name, "meta": meta})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})
            proc.wait()
            if proc.returncode != 0:
                stderr_out = proc.stderr.read()[-600:]
                # Only emit if not already handled by ERROR: line
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
                vtt_path = event["path"]
                meta = event.get("meta", {})
                # Save meta
                with open(_vtt_meta_path(job_id), "w", encoding="utf-8") as f:
                    json.dump({"user_id": user.id, "name": meta.get("name", "transcript"), "lang": meta.get("lang", ""), "url": url}, f)
                # Move vtt file
                try:
                    shutil.move(vtt_path, _vtt_file_path(job_id))
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save VTT: {exc}'})}\n\n"
                    break

                # Persist to history DB so this transcript appears in the
                # Video Transcript "Recent" list and survives a server restart.
                try:
                    with open(_vtt_file_path(job_id), encoding="utf-8") as f:
                        vtt_content = f.read()
                    raw_segs = _parse_vtt(vtt_content)
                    db_segments = [
                        {
                            "start": format_timestamp(s["start"]),
                            "end": format_timestamp(s["end"]),
                            "text": s["text"],
                        }
                        for s in raw_segs
                    ]
                    full_text = " ".join(s["text"] for s in raw_segs)
                    save_to_db(
                        job_id=job_id,
                        title=meta.get("name", "transcript"),
                        url=url,
                        language=meta.get("lang", "") or "unknown",
                        model="teams",
                        text=full_text,
                        segments=db_segments,
                        user_id=user.id,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Saving to history is best-effort; the VTT cache file
                    # is still usable for the immediate download.
                    yield f"data: {json.dumps({'type': 'status', 'message': f'Note: could not save to history ({exc})'})}\n\n"

                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'name': meta.get('name', 'transcript'), 'lang': meta.get('lang', '')})}\n\n"
                break
            elif event["type"] == "error":
                yield f"data: {json.dumps(event)}\n\n"
                error_seen = True
                break
            elif event["type"] == "_worker_exit":
                if not error_seen and event["code"] != 0:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Worker process failed unexpectedly.'})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/teams-transcript/download/{job_id}")
def download_vtt(
    job_id: str,
    chunk_minutes: int = 0,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_vtt()
    vtt_file = _vtt_file_path(job_id)
    meta_file = _vtt_meta_path(job_id)
    if not os.path.isfile(vtt_file) or not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Transcript not found or expired")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Transcript not found or expired")

    name = re.sub(r'[\\/:*?"<>|]', "_", meta.get("name", "transcript")) or "transcript"

    if chunk_minutes > 0:
        with open(vtt_file, encoding="utf-8") as f:
            vtt_text = f.read()
        parts = _split_vtt_into_chunks(vtt_text, chunk_minutes * 60)
        total = len(parts)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for idx, body in enumerate(parts, 1):
                part_name = f"{name}_part{idx:02d}_of{total:02d}.txt"
                zf.writestr(part_name, body)
        zip_filename = f"{name}_split{chunk_minutes}min.zip"
        ascii_zip = zip_filename.encode("ascii", "ignore").decode("ascii") or "transcript.zip"
        encoded_zip = quote(zip_filename, safe="")
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_zip}"; '
                    f"filename*=UTF-8''{encoded_zip}"
                )
            },
        )

    filename = f"{name}.txt"
    return FileResponse(vtt_file, media_type="text/plain", filename=filename)
