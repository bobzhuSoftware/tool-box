"""Subtitle processing — upload an existing subtitle file (VTT/SRT/TXT) and
reuse the transcript download pipeline to export it as plain / timestamped /
split ``.txt`` files.

The parsed result is stored in the shared ``jobs`` cache (and the database) so
the existing ``GET /api/download/{job_id}`` endpoint handles every export
format (``timestamps=true|false``, ``chunk_minutes=N``) with no extra code.
"""
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.auth import require_user
from app.core.db import User, save_to_db
from app.core.text_utils import build_subtitle_segments, sanitize_filename
from app.routers.transcribe import jobs

router = APIRouter()

# Extensions we accept for upload. Unknown extensions are treated as plain text.
_SUPPORTED_SUBTITLE_EXTS = {".vtt", ".srt", ".txt"}


@router.post("/api/subtitle/upload")
async def upload_subtitle(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
):
    """Parse an uploaded subtitle file into transcript segments.

    Returns ``{job_id, title, segments}``. Download the result via
    ``GET /api/download/{job_id}`` (supports ``timestamps`` and
    ``chunk_minutes`` query params, identical to normal transcripts).
    """
    filename = file.filename or "subtitle"
    ext = os.path.splitext(filename)[1].lower()

    try:
        raw = (await file.read()).decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {e}")

    if not raw.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    segments, full_text = build_subtitle_segments(raw, ext)
    if not segments:
        raise HTTPException(
            status_code=400,
            detail="Could not parse any subtitle content from the file.",
        )

    job_id = uuid.uuid4().hex[:12]
    title = sanitize_filename(os.path.splitext(filename)[0]) or "subtitle"

    jobs[job_id] = {
        "text": full_text,
        "language": "unknown",
        "segments": segments,
        "title": title,
    }

    save_to_db(
        job_id=job_id,
        title=title,
        url=f"[subtitle] {filename}",
        language="unknown",
        model="subtitle",
        text=full_text,
        segments=segments,
        user_id=user.id,
    )

    return {
        "job_id": job_id,
        "title": title,
        "segments": segments,
    }
