"""Application / system audio recording (system loopback + optional mic mix)."""
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
# Application / system audio recording (方案A 系统回环 + 方案B 进程回环)
# ---------------------------------------------------------------------------
_AUDIO_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_audio_cache")
os.makedirs(_AUDIO_CACHE_DIR, exist_ok=True)

# In-memory map of active recordings: job_id -> dict(proc, wav_path, queue, ...)
audio_recordings: dict[str, dict] = {}


def _audio_file_path(job_id: str, fmt: str = "wav") -> str:
    ext = "mp3" if fmt == "mp3" else "wav"
    return os.path.join(_AUDIO_CACHE_DIR, f"{job_id}.{ext}")


def _audio_meta_path(job_id: str) -> str:
    return os.path.join(_AUDIO_CACHE_DIR, f"{job_id}.json")


def _cleanup_old_audio(max_age_seconds: int = 3600) -> None:
    """Delete audio cache files older than max_age_seconds, but always keep each
    user's single most recent recording so the module can offer it for download
    at any time (even long after it finished)."""
    now = datetime.now().timestamp()
    # Find the newest recording per user (by meta-file mtime) and protect it.
    newest: dict[str, tuple[float, str]] = {}  # user_id -> (mtime, job_id)
    for fname in os.listdir(_AUDIO_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        jid = fname[:-5]
        try:
            with open(os.path.join(_AUDIO_CACHE_DIR, fname), encoding="utf-8") as f:
                meta = json.load(f)
            mt = os.path.getmtime(os.path.join(_AUDIO_CACHE_DIR, fname))
        except (OSError, ValueError):
            continue
        uid = meta.get("user_id")
        if uid is None:
            continue
        if uid not in newest or mt > newest[uid][0]:
            newest[uid] = (mt, jid)
    keep = {jid for _, jid in newest.values()}

    for fname in os.listdir(_AUDIO_CACHE_DIR):
        if fname.rsplit(".", 1)[0] in keep:
            continue
        fpath = os.path.join(_AUDIO_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class AudioStartRequest(BaseModel):
    format: str = "wav"           # "wav" | "mp3"
    mic: bool = False             # also capture the default microphone and mix it in


# Hard upper bound for a single recording. The worker auto-stops and finalizes
# the file when this is reached, so a recording that the user forgot about can
# never run forever / fill the disk.
_AUDIO_MAX_SECONDS = 2 * 60 * 60  # 2 hours


@router.get("/api/audio/active")
def audio_active(user: User = Depends(require_user)):
    """List the current user's in-progress recordings so the UI can recover its
    state after a page refresh or tool switch (the recording itself runs in a
    backend subprocess and is unaffected by the browser)."""
    now = datetime.now().timestamp()
    out = []
    for jid, rec in list(audio_recordings.items()):
        if rec.get("user_id") != user.id:
            continue
        proc = rec.get("proc")
        try:
            running = proc.poll() is None
        except Exception:
            running = False
        started = rec.get("started_at", now)
        out.append({
            "job_id": jid,
            "started_at": started,
            "elapsed": int(max(0, now - started)),
            "format": rec.get("format", "wav"),
            "mic": bool(rec.get("mic_path")),
            "running": running,
        })
    return out


@router.post("/api/audio/start")
def audio_start(req: AudioStartRequest, user: User = Depends(require_user)):
    """Start a system-loopback recording subprocess; returns a job_id once capture has begun."""
    import subprocess as _sp

    fmt = "mp3" if req.format == "mp3" else "wav"

    worker = os.path.join(_REPO_ROOT, "audio_record_worker.py")
    wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_tmp.close()

    mic_path: str | None = None
    if req.mic:
        mic_tmp = tempfile.NamedTemporaryFile(suffix=".mic.wav", delete=False)
        mic_tmp.close()
        mic_path = mic_tmp.name

    cmd = [sys.executable, worker, "record", "--output", wav_tmp.name,
           "--max-seconds", str(_AUDIO_MAX_SECONDS)]
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

    # Wait until the worker confirms recording has started (or fails).
    started = False
    err_msg: str | None = None
    deadline = datetime.now().timestamp() + 12
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
        try:
            os.unlink(wav_tmp.name)
        except OSError:
            pass
        if mic_path:
            try:
                os.unlink(mic_path)
            except OSError:
                pass
        detail = err_msg or ("".join(stderr_lines))[-300:] or "录制启动失败"
        raise HTTPException(status_code=500, detail=detail.strip())

    job_id = uuid.uuid4().hex[:12]
    audio_recordings[job_id] = {
        "proc": proc,
        "wav_path": wav_tmp.name,
        "mic_path": mic_path,
        "queue": q,
        "stderr": stderr_lines,
        "user_id": user.id,
        "format": fmt,
        "started_at": datetime.now().timestamp(),
    }
    return {"job_id": job_id, "format": fmt, "mic": bool(mic_path)}


@router.post("/api/audio/stop/{job_id}")
def audio_stop(job_id: str, user: User = Depends(require_user)):
    """Stop an active recording, finalize the file, and cache it for download."""
    rec = audio_recordings.get(job_id)
    if not rec or rec["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="录制任务不存在或已结束")

    proc = rec["proc"]
    q: stdlib_queue.Queue = rec["queue"]

    # Signal the worker to stop and finalize the WAV.
    try:
        proc.stdin.write("STOP\n")
        proc.stdin.flush()
    except Exception:
        pass

    result: dict | None = None
    err_msg: str | None = None
    deadline = datetime.now().timestamp() + 30
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

    audio_recordings.pop(job_id, None)
    wav_path = rec["wav_path"]
    mic_path = rec.get("mic_path")

    def _cleanup_mic():
        if mic_path:
            try:
                os.unlink(mic_path)
            except OSError:
                pass

    if result is None:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        _cleanup_mic()
        raise HTTPException(status_code=500, detail=err_msg or "录制结束失败")

    fmt = rec["format"]
    final_path = _audio_file_path(job_id, fmt)
    has_mic = bool(
        result.get("mic") and mic_path and os.path.isfile(mic_path)
        and os.path.getsize(mic_path) > 1024
    )
    try:
        import subprocess as _sp
        ff = shutil.which("ffmpeg") or "ffmpeg"
        if has_mic:
            # Mix the application/system track with the microphone track.
            # normalize=0 keeps both sources at full volume (ffmpeg resamples
            # automatically if the two tracks differ in rate/channels).
            filt = "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0"
            cmd = [ff, "-y", "-i", wav_path, "-i", mic_path,
                   "-filter_complex", filt, "-ac", "2"]
            if fmt == "mp3":
                cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
            cmd += [final_path]
            cp = _sp.run(cmd, capture_output=True, text=True)
            for p in (wav_path, mic_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            if cp.returncode != 0 or not os.path.isfile(final_path):
                raise HTTPException(status_code=500, detail="音频混合失败（ffmpeg）")
        elif fmt == "mp3":
            cp = _sp.run(
                [ff, "-y", "-i", wav_path, "-c:a", "libmp3lame", "-b:a", "192k", final_path],
                capture_output=True, text=True,
            )
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            _cleanup_mic()
            if cp.returncode != 0 or not os.path.isfile(final_path):
                raise HTTPException(status_code=500, detail="MP3 转码失败（ffmpeg）")
        else:
            shutil.move(wav_path, final_path)
            _cleanup_mic()
    except HTTPException:
        raise
    except Exception as exc:
        _cleanup_mic()
        raise HTTPException(status_code=500, detail=f"保存音频失败: {exc}")

    with open(_audio_meta_path(job_id), "w", encoding="utf-8") as f:
        json.dump({
            "user_id": user.id,
            "format": fmt,
            "seconds": result.get("seconds", 0),
            "mic": has_mic,
            "peak": int(result.get("peak", 0) or 0),
            "bytes": os.path.getsize(final_path),
            "created_at": datetime.now().timestamp(),
        }, f)

    peak = int(result.get("peak", 0) or 0)
    return {
        "job_id": job_id,
        "format": fmt,
        "seconds": result.get("seconds", 0),
        "mic": has_mic,
        "peak": peak,
        "silent": peak < 64,  # ~-54 dBFS; essentially no audio captured
        "bytes": os.path.getsize(final_path),
    }


@router.get("/api/audio/last")
def audio_last(user: User = Depends(require_user)):
    """Return the user's most recent saved recording so the module can always
    offer it for download — even if they stopped from the global banner and
    navigated away without downloading at that moment."""
    newest = None  # (mtime, job_id, meta, audio_file)
    for fname in os.listdir(_AUDIO_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        jid = fname[:-5]
        try:
            with open(os.path.join(_AUDIO_CACHE_DIR, fname), encoding="utf-8") as f:
                meta = json.load(f)
            mt = os.path.getmtime(os.path.join(_AUDIO_CACHE_DIR, fname))
        except (OSError, ValueError):
            continue
        if meta.get("user_id") != user.id:
            continue
        audio_file = _audio_file_path(jid, meta.get("format", "wav"))
        if not os.path.isfile(audio_file):
            continue
        if newest is None or mt > newest[0]:
            newest = (mt, jid, meta, audio_file)
    if newest is None:
        return {}
    _, jid, meta, audio_file = newest
    peak = int(meta.get("peak", 0) or 0)
    return {
        "job_id": jid,
        "format": meta.get("format", "wav"),
        "seconds": meta.get("seconds", 0),
        "mic": bool(meta.get("mic")),
        "peak": peak,
        "silent": ("peak" in meta) and peak < 64,
        "bytes": meta.get("bytes") or os.path.getsize(audio_file),
        "recovered": True,
    }


@router.get("/api/audio/download/{job_id}")
def download_audio(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_audio()
    meta_file = _audio_meta_path(job_id)
    if not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="录音不存在或已过期")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="录音不存在或已过期")

    fmt = meta.get("format", "wav")
    audio_file = _audio_file_path(job_id, fmt)
    if not os.path.isfile(audio_file):
        raise HTTPException(status_code=404, detail="录音不存在或已过期")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if fmt == "mp3":
        return FileResponse(audio_file, media_type="audio/mpeg", filename=f"录音_{stamp}.mp3")
    return FileResponse(audio_file, media_type="audio/wav", filename=f"录音_{stamp}.wav")
