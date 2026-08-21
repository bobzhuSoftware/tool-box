"""Desktop-app support endpoint: reports whether any long-running job is active.

Used by the native-window launcher (``desktop_app.py``) to avoid shutting the
backend down (and killing an in-flight job) when the user closes the window.
"""
from fastapi import APIRouter

router = APIRouter()


def _is_busy() -> bool:
    """True if a transcription, recording, PDF/Teams export, or model download is running."""
    # Recordings: a live subprocess means still recording.
    try:
        from app.routers.audio import audio_recordings
        for rec in audio_recordings.values():
            proc = rec.get("proc")
            if proc is not None and proc.poll() is None:
                return True
    except Exception:
        pass

    try:
        from app.routers.screen import screen_recordings
        for rec in screen_recordings.values():
            proc = rec.get("proc")
            if proc is not None and proc.poll() is None:
                return True
    except Exception:
        pass

    # Queued jobs expose a status field.
    for module, name in (
        ("app.routers.pdf", "_pdf_jobs"),
        ("app.routers.teams_transcript", "_teams_jobs"),
        ("app.routers.transcribe", "_transcript_jobs"),
    ):
        try:
            mod = __import__(module, fromlist=[name])
            for job in getattr(mod, name, {}).values():
                if job.get("status") == "running":
                    return True
        except Exception:
            pass

    try:
        from app.routers.transcribe import _model_download_status
        for status in _model_download_status.values():
            if status == "downloading":
                return True
    except Exception:
        pass

    return False


@router.get("/api/app/status")
def app_status():
    return {"busy": _is_busy()}
