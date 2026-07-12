"""Window screen recording (Windows Graphics Capture video + full-channel audio)."""
import json
import os
import queue as stdlib_queue
import shutil
import sys
import tempfile
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Window screen recording (Windows Graphics Capture video + 全声道 audio)
# ---------------------------------------------------------------------------
_SCREEN_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_screen_cache")
os.makedirs(_SCREEN_CACHE_DIR, exist_ok=True)

# In-memory map of active screen recordings: job_id -> dict(proc, mp4_path, ...)
screen_recordings: dict[str, dict] = {}


def _screen_file_path(job_id: str) -> str:
    return os.path.join(_SCREEN_CACHE_DIR, f"{job_id}.mp4")


def _screen_meta_path(job_id: str) -> str:
    return os.path.join(_SCREEN_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_screen(max_age_seconds: int = 3600) -> None:
    """Delete screen cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_SCREEN_CACHE_DIR):
        fpath = os.path.join(_SCREEN_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


@router.get("/api/screen/windows")
def screen_windows(user: User = Depends(require_user)):  # noqa: ARG001
    """Enumerate visible top-level windows that can be recorded."""
    import subprocess as _sp

    worker = os.path.join(_REPO_ROOT, "screen_record_worker.py")
    _env = os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    try:
        cp = _sp.run(
            [sys.executable, worker, "list-windows"],
            capture_output=True, text=True, encoding="utf-8",
            env=_env, timeout=20,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"枚举窗口失败: {exc}")

    windows: list[dict] = []
    for line in (cp.stdout or "").splitlines():
        if line.startswith("DONE:"):
            try:
                windows = json.loads(line[5:]).get("windows", [])
            except Exception:
                windows = []
            break
        if line.startswith("ERROR:"):
            raise HTTPException(status_code=500, detail=line[6:])
    return {"windows": windows}


class ScreenStartRequest(BaseModel):
    hwnd: int                     # target window handle (from /api/screen/windows)
    mic: bool = False             # also capture the default microphone and mix it in
    fps: int = 25                 # capture frame rate (1-60)


@router.post("/api/screen/start")
def screen_start(req: ScreenStartRequest, user: User = Depends(require_user)):
    """Start a window screen-recording subprocess; returns a job_id once capture has begun."""
    import subprocess as _sp

    fps = max(1, min(60, int(req.fps or 25)))

    worker = os.path.join(_REPO_ROOT, "screen_record_worker.py")
    mp4_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    mp4_tmp.close()

    mic_path: str | None = None
    if req.mic:
        mic_tmp = tempfile.NamedTemporaryFile(suffix=".mic.wav", delete=False)
        mic_tmp.close()
        mic_path = mic_tmp.name

    cmd = [sys.executable, worker, "record",
           "--hwnd", str(req.hwnd), "--output", mp4_tmp.name, "--fps", str(fps)]
    if mic_path:
        cmd += ["--mic-output", mic_path]

    _env = os.environ.copy()
    _env["PYTHONIOENCODING"] = "utf-8"
    proc = _sp.Popen(
        cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE,
        text=True, encoding="utf-8", env=_env,
    )

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    stderr_lines: list[str] = []

    def _read_stdout():
        for raw_line in proc.stdout:
            q.put(raw_line.strip())
        q.put(None)

    def _read_stderr():
        for raw_line in proc.stderr:
            stderr_lines.append(raw_line)

    threading.Thread(target=_read_stdout, daemon=True).start()
    threading.Thread(target=_read_stderr, daemon=True).start()

    # Wait until the worker confirms recording has started (or fails). The
    # worker has to import windows-capture (+OpenCV) and spin up WGC, so allow
    # a longer deadline than the audio-only path.
    started = False
    err_msg: str | None = None
    deadline = datetime.now().timestamp() + 25
    while True:
        remaining = deadline - datetime.now().timestamp()
        if remaining <= 0:
            break
        try:
            line = q.get(timeout=remaining)
        except stdlib_queue.Empty:
            break
        if line is None:
            break
        if line.startswith("STATUS:RECORDING"):
            started = True
            break
        if line.startswith("ERROR:"):
            err_msg = line[6:]
            break

    if not started:
        try:
            proc.kill()
        except Exception:
            pass
        for p in (mp4_tmp.name, mic_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        detail = err_msg or ("".join(stderr_lines))[-300:] or "录屏启动失败"
        raise HTTPException(status_code=500, detail=detail.strip())

    job_id = uuid.uuid4().hex[:12]
    screen_recordings[job_id] = {
        "proc": proc,
        "mp4_path": mp4_tmp.name,
        "mic_path": mic_path,
        "queue": q,
        "stderr": stderr_lines,
        "user_id": user.id,
        "started_at": datetime.now().timestamp(),
    }
    return {"job_id": job_id, "mic": bool(mic_path), "fps": fps}


@router.post("/api/screen/stop/{job_id}")
def screen_stop(job_id: str, user: User = Depends(require_user)):
    """Stop an active screen recording, finalize the MP4, and cache it for download."""
    rec = screen_recordings.get(job_id)
    if not rec or rec["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="录屏任务不存在或已结束")

    proc = rec["proc"]
    q: stdlib_queue.Queue = rec["queue"]

    # Signal the worker to stop and mux the final MP4.
    try:
        proc.stdin.write("STOP\n")
        proc.stdin.flush()
    except Exception:
        pass

    result: dict | None = None
    err_msg: str | None = None
    # Muxing video+audio can take a while for long recordings.
    deadline = datetime.now().timestamp() + 120
    while True:
        remaining = deadline - datetime.now().timestamp()
        if remaining <= 0:
            break
        try:
            line = q.get(timeout=remaining)
        except stdlib_queue.Empty:
            break
        if line is None:
            break
        if line.startswith("DONE:"):
            try:
                result = json.loads(line[5:])
            except Exception:
                result = {}
            break
        if line.startswith("ERROR:"):
            err_msg = line[6:]
            break

    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    screen_recordings.pop(job_id, None)
    mp4_path = rec["mp4_path"]
    mic_path = rec.get("mic_path")

    if mic_path:
        try:
            os.unlink(mic_path)
        except OSError:
            pass

    if result is None or not os.path.isfile(mp4_path):
        try:
            os.unlink(mp4_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=err_msg or "录屏结束失败")

    final_path = _screen_file_path(job_id)
    try:
        shutil.move(mp4_path, final_path)
    except Exception as exc:
        try:
            os.unlink(mp4_path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"保存视频失败: {exc}")

    with open(_screen_meta_path(job_id), "w", encoding="utf-8") as f:
        json.dump({
            "user_id": user.id,
            "seconds": result.get("seconds", 0),
            "mic": bool(result.get("mic")),
            "width": result.get("width"),
            "height": result.get("height"),
            "fps": result.get("fps"),
        }, f)

    return {
        "job_id": job_id,
        "seconds": result.get("seconds", 0),
        "mic": bool(result.get("mic")),
        "width": result.get("width"),
        "height": result.get("height"),
        "fps": result.get("fps"),
        "bytes": os.path.getsize(final_path),
    }


@router.get("/api/screen/download/{job_id}")
def download_screen(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_screen()
    meta_file = _screen_meta_path(job_id)
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="录屏不存在或已过期")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="录屏不存在或已过期")

    video_file = _screen_file_path(job_id)
    if not os.path.isfile(video_file):
        raise HTTPException(status_code=404, detail="录屏不存在或已过期")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(video_file, media_type="video/mp4", filename=f"录屏_{stamp}.mp4")
