"""Video transcription — Whisper models, caption extraction, jobs, cookies."""
import asyncio
import io
import json
import os
import queue as stdlib_queue
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import whisper
import yt_dlp

from app.core.auth import require_user, user_from_token_or_header
from app.core.cookies import (
    _COOKIES_BROWSER, _COOKIES_FILE, _SUPPORTED_COOKIE_BROWSERS,
    _apply_cookies, _decrypt_secret, _encrypt_secret, _jar_from_text,
    _summarize_cookies,
)
from app.core.db import TranscriptRecord, User, engine, save_to_db
from app.core.ffmpeg import FFMPEG_LOCATION
from app.core.settings import _delete_user_setting, _get_user_setting, _set_user_setting
from app.core.text_utils import (
    _parse_vtt, _ts_to_seconds, merge_segments, sanitize_filename, to_simplified,
)
from app.core.whisper import _get_whisper_model

router = APIRouter()

# In-memory cache so downloads within the same session are fast
jobs: dict[str, dict] = {}


class TranscribeRequest(BaseModel):
    url: str
    model: str = "base"
    language: str | None = None
    mode: str = "auto"  # "auto" | "captions" | "whisper"


class TranscribeResponse(BaseModel):
    job_id: str
    text: str
    language: str
    segments: list[dict]


class TranscribeEnqueueRequest(BaseModel):
    url: str
    model: str = "base"
    language: str | None = None
    mode: str = "auto"  # "auto" | "captions" | "whisper"


# ---------------------------------------------------------------------------
# Whisper model management
# ---------------------------------------------------------------------------
_WHISPER_MODEL_SIZES = {
    "tiny": 72, "base": 139, "small": 461,
    "medium": 1457, "large": 2944,
}  # accurate download sizes in MB (from Content-Length headers)

_model_download_status: dict[str, str] = {}  # model_name -> "downloading" | "done" | "error:..."


def _whisper_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "whisper")


def _get_installed_models() -> list[dict]:
    """Return list of models with their install status."""
    cache_dir = _whisper_cache_dir()
    results = []
    for name in ["tiny", "base", "small", "medium", "large"]:
        # Use actual filename from whisper's URL (e.g. large -> large-v3.pt)
        url = whisper._MODELS.get(name, "")
        expected_file = os.path.basename(url) if url else f"{name}.pt"
        file_path = os.path.join(cache_dir, expected_file)
        installed = False
        file_size_mb = 0
        if os.path.isfile(file_path):
            file_size_mb = os.path.getsize(file_path) / 1024 / 1024
            expected_mb = _WHISPER_MODEL_SIZES.get(name, 0)
            # Consider installed if file is at least 85% of expected size
            installed = file_size_mb >= expected_mb * 0.85
        status = _model_download_status.get(name, "")
        results.append({
            "name": name,
            "installed": installed,
            "size_mb": round(file_size_mb),
            "expected_mb": _WHISPER_MODEL_SIZES.get(name, 0),
            "downloading": status == "downloading",
        })
    return results


@router.get("/api/whisper/models")
def list_whisper_models():
    """Return available Whisper models and their install status."""
    return _get_installed_models()


def _download_model_with_progress(model_name: str, progress_queue: stdlib_queue.Queue):
    """Download a whisper model file with progress reporting via queue."""
    import urllib.request
    import hashlib

    url = whisper._MODELS[model_name]
    root = _whisper_cache_dir()
    os.makedirs(root, exist_ok=True)

    expected_sha256 = url.split("/")[-2]
    download_target = os.path.join(root, os.path.basename(url))

    # Check if already valid
    if os.path.isfile(download_target):
        with open(download_target, "rb") as f:
            model_bytes = f.read()
        if hashlib.sha256(model_bytes).hexdigest() == expected_sha256:
            progress_queue.put({"type": "done", "message": "Model already installed"})
            return

    progress_queue.put({"type": "status", "message": f"Connecting to download server..."})

    with urllib.request.urlopen(url) as source, open(download_target, "wb") as output:
        total = int(source.info().get("Content-Length", 0))
        downloaded = 0
        last_report = 0

        while True:
            buffer = source.read(65536)  # 64KB chunks for better progress
            if not buffer:
                break
            output.write(buffer)
            downloaded += len(buffer)

            # Report progress every 1%
            if total > 0:
                pct = int(downloaded * 100 / total)
                if pct > last_report:
                    last_report = pct
                    speed_mb = downloaded / 1024 / 1024
                    total_mb = total / 1024 / 1024
                    progress_queue.put({
                        "type": "progress",
                        "message": f"Downloading: {pct}% ({speed_mb:.0f}MB / {total_mb:.0f}MB)",
                        "percent": pct,
                        "downloaded_mb": round(speed_mb),
                        "total_mb": round(total_mb),
                    })

    # Verify checksum
    progress_queue.put({"type": "status", "message": "Verifying file integrity..."})
    with open(download_target, "rb") as f:
        model_bytes = f.read()
    if hashlib.sha256(model_bytes).hexdigest() != expected_sha256:
        os.remove(download_target)
        progress_queue.put({"type": "error", "message": "Download corrupted, please retry"})
        return

    progress_queue.put({"type": "status", "message": "Loading model into memory..."})
    # Load model to verify it works
    whisper.load_model(model_name)
    progress_queue.put({"type": "done", "message": f"Model '{model_name}' installed successfully!"})


@router.post("/api/whisper/models/{model_name}/download")
def download_whisper_model_stream(model_name: str):
    """Stream model download progress via SSE."""
    valid = ["tiny", "base", "small", "medium", "large"]
    if model_name not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid model. Choose from: {valid}")
    if _model_download_status.get(model_name) == "downloading":
        raise HTTPException(status_code=409, detail="Already downloading this model")

    progress_queue: stdlib_queue.Queue = stdlib_queue.Queue()

    def _worker():
        try:
            _model_download_status[model_name] = "downloading"
            _download_model_with_progress(model_name, progress_queue)
            _model_download_status[model_name] = "done"
        except Exception as e:
            _model_download_status[model_name] = f"error:{e}"
            progress_queue.put({"type": "error", "message": str(e)})

    threading.Thread(target=_worker, daemon=True).start()

    def _event_stream():
        while True:
            try:
                msg = progress_queue.get(timeout=60)
            except stdlib_queue.Empty:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Still downloading...'})}\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("type") in ("done", "error"):
                break

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


def download_audio(video_url: str, output_dir: str, user_id: str | None = None) -> str:
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": output_template,
        "quiet": True,
        "nocheckcertificate": True,
    }
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
    _apply_cookies(ydl_opts, user_id)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])
    audio_path = os.path.join(output_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise FileNotFoundError("Audio download failed.")
    return audio_path


# ---------------------------------------------------------------------------
# Subtitle / Caption extraction helpers
# ---------------------------------------------------------------------------

class CaptionsNotFoundError(Exception):
    """Raised when no captions/subtitles are available for a video."""


def _extract_captions(
    url: str,
    language_pref: str | None,
    tmp_dir: str,
    q: stdlib_queue.Queue,
    user_id: str | None = None,
) -> tuple[list[dict], str, str]:
    """
    Try to extract existing subtitles/captions from a video URL using yt-dlp.

    Returns: (raw_segments, detected_lang, video_title)
    Raises:  CaptionsNotFoundError if no captions are available.

    Strategy (two lightweight calls, cookie write-back suppressed on both):
      1. extract_info(download=False) — metadata only, no subtitle downloads.
         Determine which language to fetch.
      2. extract_info(download=True) — download only the ONE chosen language.
         Using ["all"] is avoided because it fires dozens of HTTP requests
         and triggers YouTube's HTTP 429 rate-limiting.

    Bilibili support:
      B站 AI-generated subtitles appear in the 'subtitles' dict (not
      'automatic_captions') under keys such as 'zh-CN' or 'ai-zh'.
      When no language preference is given, we default to Chinese for
      Bilibili URLs so the AI captions are selected automatically.
      Bilibili may require valid login cookies (cookies.txt) to expose
      subtitle metadata for some videos.
    """
    import glob as _glob

    # For Bilibili URLs, default to Chinese when the caller didn't specify.
    _is_bilibili = 'bilibili.com' in url or 'b23.tv' in url
    effective_lang_pref = language_pref or ('zh-CN' if _is_bilibili else None)

    # ------------------------------------------------------------------
    # Step 1: metadata only — discover available subtitle languages.
    # ------------------------------------------------------------------
    q.put({"type": "status", "message": "Checking for available captions..."})

    info_opts: dict = {"quiet": True, "skip_download": True, "nocheckcertificate": True}
    _apply_cookies(info_opts, user_id)
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_title     = sanitize_filename((info or {}).get("title", "transcript"))
    subtitles: dict     = (info or {}).get("subtitles", {}) or {}
    auto_captions: dict = (info or {}).get("automatic_captions", {}) or {}

    # Filter out non-subtitle tracks that yt-dlp exposes in the subtitles dict
    # but cannot be downloaded as VTT (e.g. live_chat replay).
    _NON_SUBTITLE_TRACKS = {'live_chat', 'live_chat_replay'}
    manual_langs = set(subtitles.keys()) - _NON_SUBTITLE_TRACKS
    auto_langs   = set(auto_captions.keys()) - _NON_SUBTITLE_TRACKS

    if not manual_langs and not auto_langs:
        hint = " (try adding Bilibili cookies to cookies.txt)" if _is_bilibili else ""
        raise CaptionsNotFoundError(f"No subtitles or automatic captions found for this video{hint}.")

    # Choose the best language (manual preferred over auto).
    # For Bilibili, 'ai-zh' is the AI-subtitle variant of 'zh'.
    def _pick(pool: set[str]) -> str | None:
        if effective_lang_pref and effective_lang_pref in pool:
            return effective_lang_pref
        if effective_lang_pref:
            base = effective_lang_pref.split('-')[0]
            for k in sorted(pool):
                # Match zh-CN, zh-Hans, ai-zh, ai-zh-CN, etc.
                if k.startswith(base) or k == f'ai-{base}' or k.startswith(f'ai-{base}'):
                    return k
        if 'en' in pool:
            return 'en'
        return next(iter(sorted(pool)), None)

    chosen_lang: str
    is_auto: bool
    picked = _pick(manual_langs)
    if picked:
        chosen_lang, is_auto = picked, False
    else:
        picked = _pick(auto_langs)
        if picked:
            chosen_lang, is_auto = picked, True
        else:
            raise CaptionsNotFoundError("No suitable subtitle language found.")

    # Bilibili AI subtitle keys look like 'ai-zh' — label them clearly.
    if chosen_lang.startswith('ai-'):
        source_label = "AI subtitles"
    elif is_auto:
        source_label = "automatic captions"
    else:
        source_label = "manual subtitles"
    q.put({"type": "status", "message": f"Found {source_label} in '{chosen_lang}' — downloading..."})

    # ------------------------------------------------------------------
    # Step 2: download ONLY the chosen language — avoids 429 rate-limiting.
    # ------------------------------------------------------------------
    dl_opts: dict = {
        "quiet": True,
        "skip_download": True,
        "writesubtitles": not is_auto,
        "writeautomaticsub": is_auto,
        "subtitleslangs": [chosen_lang],
        "subtitlesformat": "vtt",
        "outtmpl": os.path.join(tmp_dir, "sub.%(ext)s"),
        "nocheckcertificate": True,
    }
    _apply_cookies(dl_opts, user_id)
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])

    vtt_files = _glob.glob(os.path.join(tmp_dir, "*.vtt"))
    if not vtt_files:
        raise CaptionsNotFoundError("Subtitle file was not downloaded (unexpected yt-dlp behaviour).")

    vtt_content = open(vtt_files[0], encoding="utf-8", errors="replace").read()
    raw_segments = _parse_vtt(vtt_content)

    if not raw_segments:
        raise CaptionsNotFoundError("Subtitle file was empty or could not be parsed.")

    return raw_segments, chosen_lang, video_title


@router.post("/api/transcribe", response_model=TranscribeResponse)
def transcribe(req: TranscribeRequest, user: User = Depends(require_user)):
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = download_audio(req.url, tmp_dir, user.id)
            model = _get_whisper_model(req.model)
            options = {}
            if req.language:
                options["language"] = req.language
            result = model.transcribe(audio_path, **options)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    job_id = uuid.uuid4().hex[:12]
    detected_lang = result.get("language", "unknown")
    segments = merge_segments(result["segments"])
    full_text = to_simplified(result["text"].strip(), detected_lang)
    for seg in segments:
        seg["text"] = to_simplified(seg["text"], detected_lang)

    jobs[job_id] = {
        "text": full_text,
        "language": detected_lang,
        "segments": segments,
    }

    return TranscribeResponse(
        job_id=job_id,
        text=full_text,
        language=detected_lang,
        segments=segments,
    )


@router.get("/api/history")
def get_history(user: User = Depends(require_user)):
    with Session(engine) as session:
        records = (
            session.query(TranscriptRecord)
            .filter(TranscriptRecord.user_id == user.id)
            .order_by(TranscriptRecord.created_at.desc())
            .limit(50)
            .all()
        )
        return [
            {
                "job_id": r.job_id,
                "title": r.title,
                "url": r.url,
                "language": r.language,
                "model": r.model,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ]


@router.delete("/api/history/{job_id}")
def delete_history(job_id: str, user: User = Depends(require_user)):
    with Session(engine) as session:
        record = session.get(TranscriptRecord, job_id)
        if not record or record.user_id != user.id:
            raise HTTPException(status_code=404, detail="Record not found")
        session.delete(record)
        session.commit()
    jobs.pop(job_id, None)
    return {"ok": True}


@router.get("/api/download/{job_id}")
def download_transcript(job_id: str, timestamps: bool = True, chunk_minutes: int = 0, user: User = Depends(user_from_token_or_header)):
    # Try in-memory cache first, fall back to database
    job = jobs.get(job_id)
    if not job:
        with Session(engine) as session:
            record = session.get(TranscriptRecord, job_id)
            if not record or record.user_id != user.id:
                raise HTTPException(status_code=404, detail="Job not found")
            job = {
                "title": record.title,
                "text": record.text,
                "segments": json.loads(record.segments_json),
            }

    title = sanitize_filename(job.get("title", "transcript")) or "transcript"
    segments = job["segments"]

    def render_chunk(chunk_segs: list[dict]) -> str:
        if timestamps:
            return "".join(
                f"[{s['start']} -> {s['end']}]  {s['text']}\n" for s in chunk_segs
            )
        return " ".join(s["text"] for s in chunk_segs) + "\n"

    # ------------------------------------------------------------------ #
    # Chunked download: split segments into N-minute blocks → ZIP file    #
    # ------------------------------------------------------------------ #
    if chunk_minutes > 0:
        chunk_secs = chunk_minutes * 60
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        chunk_start_sec: float = 0.0

        for seg in segments:
            # Start a new chunk when the segment's start time has crossed
            # another chunk_secs boundary relative to the first segment.
            seg_start = _ts_to_seconds(seg["start"])
            if not current_chunk:
                chunk_start_sec = seg_start

            if current_chunk and (seg_start - chunk_start_sec) >= chunk_secs:
                chunks.append(current_chunk)
                current_chunk = []
                chunk_start_sec = seg_start

            current_chunk.append(seg)

        if current_chunk:
            chunks.append(current_chunk)

        zip_buffer = io.BytesIO()
        total = len(chunks)
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, chunk_segs in enumerate(chunks, 1):
                part_name = f"{title}_part{i:02d}_of{total:02d}.txt"
                zf.writestr(part_name, render_chunk(chunk_segs))
        zip_filename = f"{title}_split{chunk_minutes}min.zip"
        ascii_zip = zip_filename.encode("ascii", "ignore").decode("ascii") or "transcript.zip"
        encoded_zip = quote(zip_filename, safe="")
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{ascii_zip}"; filename*=UTF-8\'\'{encoded_zip}'},
        )

    # ------------------------------------------------------------------ #
    # Single-file download (original behaviour)                           #
    # ------------------------------------------------------------------ #
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(render_chunk(segments))
        tmp.close()
        filename = f"{title}.txt"
        ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "transcript.txt"
        encoded_name = quote(filename, safe="")
        return FileResponse(
            tmp.name,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'},
        )
    except Exception:
        os.unlink(tmp.name)
        raise


@router.post("/api/transcribe/stream")
async def transcribe_stream(req: TranscribeRequest, user: User = Depends(require_user)):
    user_id = user.id
    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def worker():
        try:
            mode = req.mode  # "auto" | "captions" | "whisper"

            with tempfile.TemporaryDirectory() as tmp_dir:

                # ----------------------------------------------------------
                # Helper: run Whisper pipeline
                # ----------------------------------------------------------
                def run_whisper(title_hint: str = "transcript") -> tuple[list[dict], str, str]:
                    output_template = os.path.join(tmp_dir, "audio.%(ext)s")

                    def progress_hook(d):
                        if d["status"] == "downloading":
                            percent = d.get("_percent_str", "?%").strip()
                            speed = d.get("_speed_str", "").strip()
                            eta = d.get("_eta_str", "").strip()
                            msg = f"Downloading audio: {percent}"
                            if speed and speed not in ("", "N/A"):
                                msg += f" at {speed}"
                            if eta and eta not in ("", "N/A"):
                                msg += f" — ETA {eta}"
                            q.put({"type": "progress", "message": msg})
                        elif d["status"] == "finished":
                            q.put({"type": "status", "message": "Download complete, converting to MP3..."})
                        elif d["status"] == "error":
                            q.put({"type": "error", "message": "Download error occurred"})

                    q.put({"type": "status", "message": "Starting audio download..."})
                    ydl_opts = {
                        "format": "bestaudio/best",
                        "postprocessors": [
                            {
                                "key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": "192",
                            }
                        ],
                        "outtmpl": output_template,
                        "quiet": True,
                        "progress_hooks": [progress_hook],
                        "nocheckcertificate": True,
                    }
                    if FFMPEG_LOCATION:
                        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
                    _apply_cookies(ydl_opts, user_id)

                    video_title_w = title_hint
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(req.url, download=False)
                        if info:
                            video_title_w = sanitize_filename(info.get("title", "transcript"))
                            q.put({"type": "status", "message": f"Video: {video_title_w}"})
                        ydl.download([req.url])

                    audio_path = os.path.join(tmp_dir, "audio.mp3")
                    if not os.path.exists(audio_path):
                        raise FileNotFoundError("Audio file not found after download. Is FFmpeg installed?")

                    q.put({"type": "status", "message": f"Loading Whisper model '{req.model}'..."})
                    whisper_model = _get_whisper_model(req.model)

                    q.put({"type": "status", "message": "Transcribing audio... (this may take several minutes)"})
                    options: dict = {}
                    if req.language:
                        options["language"] = req.language
                    result = whisper_model.transcribe(audio_path, **options)

                    segs = merge_segments(result["segments"])
                    lang = result.get("language", "unknown")
                    return segs, lang, video_title_w

                # ----------------------------------------------------------
                # Route by mode
                # ----------------------------------------------------------
                source: str  # "captions" or "whisper"
                segments: list[dict]
                detected_lang: str
                video_title: str

                if mode == "captions":
                    # Captions only — fail loudly if none found
                    raw_segs, detected_lang, video_title = _extract_captions(
                        req.url, req.language, tmp_dir, q, user_id
                    )
                    segments = merge_segments(raw_segs)
                    source = "captions"

                elif mode == "whisper":
                    # Whisper only — existing behaviour
                    segments, detected_lang, video_title = run_whisper()
                    source = "whisper"

                else:
                    # mode == "auto" — try captions first, fall back to Whisper
                    try:
                        raw_segs, detected_lang, video_title = _extract_captions(
                            req.url, req.language, tmp_dir, q, user_id
                        )
                        segments = merge_segments(raw_segs)
                        source = "captions"
                    except CaptionsNotFoundError as exc:
                        q.put({"type": "status", "message": f"No captions found ({exc}). Falling back to Whisper AI transcription..."})
                        segments, detected_lang, video_title = run_whisper()
                        source = "whisper"

                # ----------------------------------------------------------
                # Post-process and save
                # ----------------------------------------------------------
                full_text_parts: list[str] = []
                for seg in segments:
                    seg["text"] = to_simplified(seg["text"], detected_lang)
                    full_text_parts.append(seg["text"])
                full_text = "\n".join(full_text_parts)

            job_id = uuid.uuid4().hex[:12]
            db_model = "captions" if source == "captions" else req.model

            jobs[job_id] = {
                "text": full_text,
                "language": detected_lang,
                "segments": segments,
                "title": video_title,
            }

            # Persist to database
            save_to_db(
                job_id=job_id,
                title=video_title,
                url=req.url,
                language=detected_lang,
                model=db_model,
                text=full_text,
                segments=segments,
                user_id=user_id,
            )

            q.put({
                "type": "done",
                "job_id": job_id,
                "text": full_text,
                "language": detected_lang,
                "segments": segments,
                "title": video_title,
                "source": source,
            })

        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def generate():
        while True:
            try:
                event = q.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            except stdlib_queue.Empty:
                await asyncio.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/transcribe/upload")
async def transcribe_upload(
    file: UploadFile = File(...),
    model: str = Form("base"),
    language: str = Form(""),
    user: User = Depends(require_user),
):
    user_id = user.id
    q: stdlib_queue.Queue = stdlib_queue.Queue()

    # Save uploaded file to a temp location
    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    tmp_upload = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        tmp_upload.write(content)
        tmp_upload.close()
    except Exception as e:
        tmp_upload.close()
        os.unlink(tmp_upload.name)
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {e}")

    upload_path = tmp_upload.name
    video_title = sanitize_filename(os.path.splitext(file.filename or "upload")[0])

    def worker():
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                q.put({"type": "status", "message": f"Uploaded: {file.filename}"})

                # Convert to mp3 via ffmpeg
                audio_path = os.path.join(tmp_dir, "audio.mp3")
                ffmpeg_bin = shutil.which("ffmpeg") or (os.path.join(FFMPEG_LOCATION, "ffmpeg") if FFMPEG_LOCATION else "ffmpeg")
                import subprocess
                q.put({"type": "status", "message": "Converting to audio..."})
                proc = subprocess.run(
                    [ffmpeg_bin, "-i", upload_path, "-vn", "-acodec", "libmp3lame",
                     "-q:a", "2", "-y", audio_path],
                    capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"FFmpeg conversion failed: {proc.stderr[-500:] if proc.stderr else 'unknown error'}")

                q.put({"type": "status", "message": f"Loading Whisper model '{model}'..."})
                whisper_model = _get_whisper_model(model)

                q.put({"type": "status", "message": "Transcribing audio... (this may take several minutes)"})
                options = {}
                if language.strip():
                    options["language"] = language.strip()
                result = whisper_model.transcribe(audio_path, **options)

            job_id = uuid.uuid4().hex[:12]
            segments = merge_segments(result["segments"])
            detected_lang = result.get("language", "unknown")
            full_text = to_simplified(result["text"].strip(), detected_lang)
            for seg in segments:
                seg["text"] = to_simplified(seg["text"], detected_lang)

            jobs[job_id] = {
                "text": full_text,
                "language": detected_lang,
                "segments": segments,
                "title": video_title,
            }

            save_to_db(
                job_id=job_id,
                title=video_title,
                url=f"[upload] {file.filename}",
                language=detected_lang,
                model=model,
                text=full_text,
                segments=segments,
                user_id=user_id,
            )

            q.put({
                "type": "done",
                "job_id": job_id,
                "text": full_text,
                "language": detected_lang,
                "segments": segments,
                "title": video_title,
            })

        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            try:
                os.unlink(upload_path)
            except OSError:
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    async def generate():
        while True:
            try:
                event = q.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            except stdlib_queue.Empty:
                await asyncio.sleep(0.1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Video Transcript — in-memory job registry (queue-based workflow)
# ---------------------------------------------------------------------------
_transcript_jobs: dict[str, dict] = {}


class _QueueAdapter:
    """Lets _extract_captions() write into _log() via a queue-like interface."""
    def __init__(self, log_fn):
        self._log = log_fn

    def put(self, item: dict) -> None:
        self._log(item.get("type", "status"), item.get("message", ""))


def _transcript_run_url_job(
    job_id: str, url: str, model_name: str, language: str | None,
    mode: str, user_id: str,
) -> None:
    """Background thread: URL-based transcription → updates _transcript_jobs."""
    job = _transcript_jobs.get(job_id)
    if job is None:
        return

    def _log(msg_type: str, message: str) -> None:
        job["progress"].append({"type": msg_type, "message": message})
        if len(job["progress"]) > 200:
            job["progress"] = job["progress"][-200:]

    q = _QueueAdapter(_log)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:

            def run_whisper(title_hint: str = "transcript") -> tuple:
                output_template = os.path.join(tmp_dir, "audio.%(ext)s")

                def progress_hook(d):
                    if d["status"] == "downloading":
                        percent = d.get("_percent_str", "?%").strip()
                        speed = d.get("_speed_str", "").strip()
                        eta = d.get("_eta_str", "").strip()
                        msg = f"Downloading audio: {percent}"
                        if speed and speed not in ("", "N/A"):
                            msg += f" at {speed}"
                        if eta and eta not in ("", "N/A"):
                            msg += f" — ETA {eta}"
                        _log("progress", msg)
                    elif d["status"] == "finished":
                        _log("status", "Download complete, converting to MP3...")
                    elif d["status"] == "error":
                        _log("error", "Download error occurred")

                _log("status", "Starting audio download...")
                ydl_opts = {
                    "format": "bestaudio/best",
                    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
                    "outtmpl": output_template,
                    "quiet": True,
                    "progress_hooks": [progress_hook],
                    "nocheckcertificate": True,
                }
                if FFMPEG_LOCATION:
                    ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
                _apply_cookies(ydl_opts, user_id)

                video_title_w = title_hint
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        video_title_w = sanitize_filename(info.get("title", "transcript"))
                        _log("status", f"Video: {video_title_w}")
                    if job.get("_cancel"):
                        raise InterruptedError("cancelled")
                    ydl.download([url])

                audio_path = os.path.join(tmp_dir, "audio.mp3")
                if not os.path.exists(audio_path):
                    raise FileNotFoundError("Audio file not found after download. Is FFmpeg installed?")

                if job.get("_cancel"):
                    raise InterruptedError("cancelled")

                _log("status", f"Loading Whisper model '{model_name}'...")
                whisper_model = _get_whisper_model(model_name)
                _log("status", "Transcribing audio... (this may take several minutes)")
                options: dict = {}
                if language:
                    options["language"] = language
                result = whisper_model.transcribe(audio_path, **options)

                segs = merge_segments(result["segments"])
                lang = result.get("language", "unknown")
                return segs, lang, video_title_w

            source: str
            segments: list
            detected_lang: str
            video_title: str

            if mode == "captions":
                raw_segs, detected_lang, video_title = _extract_captions(url, language, tmp_dir, q, user_id)
                if job.get("_cancel"):
                    raise InterruptedError("cancelled")
                segments = merge_segments(raw_segs)
                source = "captions"
            elif mode == "whisper":
                segments, detected_lang, video_title = run_whisper()
                source = "whisper"
            else:  # auto
                try:
                    raw_segs, detected_lang, video_title = _extract_captions(url, language, tmp_dir, q, user_id)
                    if job.get("_cancel"):
                        raise InterruptedError("cancelled")
                    segments = merge_segments(raw_segs)
                    source = "captions"
                except CaptionsNotFoundError as exc:
                    _log("status", f"No captions found ({exc}). Falling back to Whisper AI transcription...")
                    segments, detected_lang, video_title = run_whisper()
                    source = "whisper"

            full_text_parts: list[str] = []
            for seg in segments:
                seg["text"] = to_simplified(seg["text"], detected_lang)
                full_text_parts.append(seg["text"])
            full_text = "\n".join(full_text_parts)

        if job.get("_cancel"):
            raise InterruptedError("cancelled")

        db_model = "captions" if source == "captions" else model_name
        jobs[job_id] = {"text": full_text, "language": detected_lang, "segments": segments, "title": video_title}
        save_to_db(job_id=job_id, title=video_title, url=url, language=detected_lang,
                   model=db_model, text=full_text, segments=segments, user_id=user_id)
        job["result"] = {"job_id": job_id, "title": video_title, "language": detected_lang,
                         "source": source, "text": full_text, "segments": segments}
        job["status"] = "done"
        _log("done", f"✓ 字幕已就绪：{video_title}")

    except InterruptedError:
        job["status"] = "error"
        job["error_message"] = "已取消"
        _log("error", "已取消")
    except Exception as exc:
        job["status"] = "error"
        job["error_message"] = str(exc)
        _log("error", str(exc))


def _transcript_run_upload_job(
    job_id: str, upload_path: str, filename: str,
    model_name: str, language: str, user_id: str,
) -> None:
    """Background thread: upload-based transcription → updates _transcript_jobs."""
    job = _transcript_jobs.get(job_id)
    if job is None:
        return

    def _log(msg_type: str, message: str) -> None:
        job["progress"].append({"type": msg_type, "message": message})
        if len(job["progress"]) > 200:
            job["progress"] = job["progress"][-200:]

    video_title = sanitize_filename(os.path.splitext(filename)[0])

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            _log("status", f"Uploaded: {filename}")
            audio_path = os.path.join(tmp_dir, "audio.mp3")
            ffmpeg_bin = shutil.which("ffmpeg") or (
                os.path.join(FFMPEG_LOCATION, "ffmpeg") if FFMPEG_LOCATION else "ffmpeg"
            )
            import subprocess as _sp
            _log("status", "Converting to audio...")
            proc = _sp.run(
                [ffmpeg_bin, "-i", upload_path, "-vn", "-acodec", "libmp3lame",
                 "-q:a", "2", "-y", audio_path],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg conversion failed: {proc.stderr[-500:] if proc.stderr else 'unknown error'}"
                )

            if job.get("_cancel"):
                raise InterruptedError("cancelled")

            _log("status", f"Loading Whisper model '{model_name}'...")
            whisper_model = _get_whisper_model(model_name)
            _log("status", "Transcribing audio... (this may take several minutes)")
            options: dict = {}
            if language.strip():
                options["language"] = language.strip()
            result = whisper_model.transcribe(audio_path, **options)

        if job.get("_cancel"):
            raise InterruptedError("cancelled")

        segments = merge_segments(result["segments"])
        detected_lang = result.get("language", "unknown")
        full_text = to_simplified(result["text"].strip(), detected_lang)
        for seg in segments:
            seg["text"] = to_simplified(seg["text"], detected_lang)

        jobs[job_id] = {"text": full_text, "language": detected_lang, "segments": segments, "title": video_title}
        save_to_db(job_id=job_id, title=video_title, url=f"[upload] {filename}",
                   language=detected_lang, model=model_name, text=full_text,
                   segments=segments, user_id=user_id)
        job["result"] = {"job_id": job_id, "title": video_title, "language": detected_lang,
                         "source": "whisper", "text": full_text, "segments": segments}
        job["status"] = "done"
        _log("done", f"✓ 字幕已就绪：{video_title}")

    except InterruptedError:
        job["status"] = "error"
        job["error_message"] = "已取消"
        _log("error", "已取消")
    except Exception as exc:
        job["status"] = "error"
        job["error_message"] = str(exc)
        _log("error", str(exc))
    finally:
        try:
            os.unlink(upload_path)
        except OSError:
            pass


@router.post("/api/transcribe/enqueue")
def transcribe_enqueue(req: TranscribeEnqueueRequest, user: User = Depends(require_user)):
    """Submit a URL-based transcription job; returns job_id immediately."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    job_id = uuid.uuid4().hex[:12]
    _transcript_jobs[job_id] = {
        "job_id": job_id, "user_id": user.id,
        "label": url, "input_type": "url",
        "status": "running", "progress": [],
        "result": None, "error_message": None,
        "created_at": datetime.now(timezone.utc).timestamp(),
        "_cancel": False,
    }
    threading.Thread(
        target=_transcript_run_url_job,
        args=(job_id, url, req.model, req.language, req.mode, user.id),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@router.post("/api/transcribe/enqueue-upload")
async def transcribe_enqueue_upload(
    file: UploadFile = File(...),
    model: str = Form("base"),
    language: str = Form(""),
    user: User = Depends(require_user),
):
    """Accept an uploaded file and submit transcription job; returns job_id immediately."""
    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    tmp_upload = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        tmp_upload.write(content)
        tmp_upload.close()
    except Exception as exc:
        tmp_upload.close()
        os.unlink(tmp_upload.name)
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}")

    filename = file.filename or "upload"
    job_id = uuid.uuid4().hex[:12]
    _transcript_jobs[job_id] = {
        "job_id": job_id, "user_id": user.id,
        "label": filename, "input_type": "upload",
        "status": "running", "progress": [],
        "result": None, "error_message": None,
        "created_at": datetime.now(timezone.utc).timestamp(),
        "_cancel": False,
    }
    threading.Thread(
        target=_transcript_run_upload_job,
        args=(job_id, tmp_upload.name, filename, model, language, user.id),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@router.get("/api/transcribe/jobs")
def transcribe_jobs_list(user: User = Depends(require_user)):
    """Return a slim list of all transcript jobs for the current user."""
    user_jobs = [j for j in _transcript_jobs.values() if j["user_id"] == user.id]
    user_jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": [{
        "job_id": j["job_id"],
        "label": j["label"],
        "input_type": j["input_type"],
        "status": j["status"],
        "last_message": j["progress"][-1]["message"] if j["progress"] else "",
        # Exclude heavy fields (text/segments) from the list endpoint
        "result": {k: v for k, v in j["result"].items() if k not in ("text", "segments")}
                   if j["result"] else None,
        "error_message": j["error_message"],
        "created_at": j["created_at"],
    } for j in user_jobs]}


@router.get("/api/transcribe/status/{job_id}")
def transcribe_status(job_id: str, user: User = Depends(require_user)):
    """Return full state (including progress log + result with text/segments)."""
    job = _transcript_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {k: v for k, v in job.items() if not k.startswith("_")}


@router.delete("/api/transcribe/jobs/{job_id}")
def transcribe_delete_job(job_id: str, user: User = Depends(require_user)):
    """Cancel and remove a transcript job."""
    job = _transcript_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    job["_cancel"] = True
    del _transcript_jobs[job_id]
    return {"ok": True}


# ---------------------------------------------------------------------------
# Video transcription cookies (per-user YouTube/Bilibili login)
# ---------------------------------------------------------------------------
class YtCookiesRequest(BaseModel):
    text: str | None = None      # Netscape cookies.txt content
    browser: str | None = None   # e.g. "chrome" or "edge:Default"


def _yt_cookies_status(user_id: str) -> dict:
    """Non-sensitive status for the cookies UI (never returns raw cookies)."""
    text = _decrypt_secret(_get_user_setting(user_id, "yt_cookies"))
    browser = _get_user_setting(user_id, "yt_cookies_browser") or ""
    has_global = os.path.isfile(_COOKIES_FILE) or bool(_COOKIES_BROWSER)
    summary = _summarize_cookies(text) if text else {"cookie_count": 0, "domains": []}
    return {
        "has_cookies": bool(text),
        "browser": browser,
        "cookie_count": summary["cookie_count"],
        "domains": summary["domains"],
        "supported_browsers": list(_SUPPORTED_COOKIE_BROWSERS),
        "global_fallback": has_global,
    }


@router.get("/api/transcribe/cookies")
def get_yt_cookies(user: User = Depends(require_user)):
    return _yt_cookies_status(user.id)


@router.put("/api/transcribe/cookies")
def set_yt_cookies(req: YtCookiesRequest, user: User = Depends(require_user)):
    text = (req.text or "").strip()
    browser = (req.browser or "").strip()

    if text:
        if _jar_from_text(text) is None:
            raise HTTPException(
                status_code=400,
                detail="无法识别为 Netscape 格式的 cookies.txt，请确认从浏览器扩展正确导出。",
            )
        _set_user_setting(user.id, "yt_cookies", _encrypt_secret(text))
        _delete_user_setting(user.id, "yt_cookies_browser")
    elif browser:
        name = browser.partition(":")[0].lower()
        if name not in _SUPPORTED_COOKIE_BROWSERS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的浏览器：{name}。可选：{', '.join(_SUPPORTED_COOKIE_BROWSERS)}",
            )
        _set_user_setting(user.id, "yt_cookies_browser", browser)
        _delete_user_setting(user.id, "yt_cookies")
    else:
        raise HTTPException(status_code=400, detail="请提供 cookies 文本或选择浏览器。")

    return {"ok": True, **_yt_cookies_status(user.id)}


@router.delete("/api/transcribe/cookies")
def clear_yt_cookies(user: User = Depends(require_user)):
    _delete_user_setting(user.id, "yt_cookies")
    _delete_user_setting(user.id, "yt_cookies_browser")
    return {"ok": True, **_yt_cookies_status(user.id)}
