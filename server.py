import asyncio
import json
import os
import queue as stdlib_queue
import re
import shutil
import tempfile
import threading
import unicodedata
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import (
    Column, DateTime, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session

import whisper
import yt_dlp
try:
    import zhconv
    _has_zhconv = True
except ImportError:
    _has_zhconv = False


def to_simplified(text: str, language: str) -> str:
    """Convert Traditional Chinese to Simplified if language is Chinese."""
    if _has_zhconv and (language.startswith('zh') or language in ('yue', 'chinese', 'cantonese')):
        return zhconv.convert(text, 'zh-hans')
    return text

# ---------------------------------------------------------------------------
# FFmpeg setup
# ---------------------------------------------------------------------------
_FFMPEG_FALLBACK = (
    r"C:\Users\BOBZHU01\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
)
FFMPEG_LOCATION: str | None = shutil.which("ffmpeg")
if FFMPEG_LOCATION:
    FFMPEG_LOCATION = os.path.dirname(FFMPEG_LOCATION)
elif os.path.isdir(_FFMPEG_FALLBACK):
    FFMPEG_LOCATION = _FFMPEG_FALLBACK

if FFMPEG_LOCATION and FFMPEG_LOCATION not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_LOCATION + os.pathsep + os.environ.get("PATH", "")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# On Fly.io set env var DB_PATH=/data/transcripts.db (persisted Volume).
# Locally falls back to the project directory.
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "transcripts.db"),
)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class TranscriptRecord(Base):
    __tablename__ = "transcripts"

    job_id = Column(String(24), primary_key=True)
    title = Column(String(200), nullable=False)
    url = Column(Text, nullable=False)
    language = Column(String(20), nullable=False)
    model = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    segments_json = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, nullable=False)


Base.metadata.create_all(engine)


def save_to_db(job_id: str, title: str, url: str, language: str,
               model: str, text: str, segments: list[dict]) -> None:
    with Session(engine) as session:
        record = TranscriptRecord(
            job_id=job_id,
            title=title,
            url=url,
            language=language,
            model=model,
            text=text,
            segments_json=json.dumps(segments, ensure_ascii=False),
            created_at=datetime.now(timezone.utc),
        )
        session.add(record)
        session.commit()


# ---------------------------------------------------------------------------
# App & in-memory job cache
# ---------------------------------------------------------------------------
app = FastAPI()

# In-memory cache so downloads within the same session are fast
jobs: dict[str, dict] = {}


class TranscribeRequest(BaseModel):
    url: str
    model: str = "base"
    language: str | None = None


class TranscribeResponse(BaseModel):
    job_id: str
    text: str
    language: str
    segments: list[dict]


# ---------------------------------------------------------------------------
# Cookie resolution (YouTube bot-detection bypass)
# ---------------------------------------------------------------------------
_COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
_COOKIES_BROWSER = os.environ.get("YOUTUBE_COOKIES_BROWSER")  # e.g. "chrome", "firefox"


def _apply_cookies(ydl_opts: dict) -> None:
    """Inject cookie configuration into yt-dlp options if available."""
    if os.path.isfile(_COOKIES_FILE):
        ydl_opts["cookiefile"] = _COOKIES_FILE
    elif _COOKIES_BROWSER:
        ydl_opts["cookiesfrombrowser"] = (_COOKIES_BROWSER,)


def download_audio(video_url: str, output_dir: str) -> str:
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
    }
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
    _apply_cookies(ydl_opts)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])
    audio_path = os.path.join(output_dir, "audio.mp3")
    if not os.path.exists(audio_path):
        raise FileNotFoundError("Audio download failed.")
    return audio_path


def sanitize_filename(title: str) -> str:
    """Strip characters that are invalid in filenames and limit length."""
    title = re.sub(r'[\\/:*?"<>|]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title[:80] or "transcript"


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _count_words(text: str) -> int:
    """Count words in a language-aware way (CJK chars count individually)."""
    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F'))
    if cjk > len(text) * 0.3:
        return cjk
    return len(text.split())


def merge_segments(
    raw_segments: list[dict],
    min_words: int = 40,
    max_words: int = 60,
) -> list[dict]:
    """
    Merge short Whisper segments into semantically coherent chunks.
    Flushes when word count reaches min_words AND the segment ends with
    sentence-ending punctuation, or unconditionally at max_words.
    """
    SENTENCE_END = re.compile(r'[.?!。？！…]+\s*$')

    merged: list[dict] = []
    buf: list[dict] = []
    buf_words = 0

    for seg in raw_segments:
        text = seg["text"].strip()
        buf.append(seg)
        buf_words += _count_words(text)

        at_boundary = bool(SENTENCE_END.search(text))
        if (buf_words >= min_words and at_boundary) or buf_words >= max_words:
            merged.append({
                "start": format_timestamp(buf[0]["start"]),
                "end": format_timestamp(buf[-1]["end"]),
                "text": " ".join(s["text"].strip() for s in buf),
            })
            buf = []
            buf_words = 0

    if buf:
        merged.append({
            "start": format_timestamp(buf[0]["start"]),
            "end": format_timestamp(buf[-1]["end"]),
            "text": " ".join(s["text"].strip() for s in buf),
        })

    return merged


@app.post("/api/transcribe", response_model=TranscribeResponse)
def transcribe(req: TranscribeRequest):
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = download_audio(req.url, tmp_dir)
            model = whisper.load_model(req.model)
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


@app.get("/api/history")
def get_history():
    with Session(engine) as session:
        records = (
            session.query(TranscriptRecord)
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


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str):
    with Session(engine) as session:
        record = session.get(TranscriptRecord, job_id)
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        session.delete(record)
        session.commit()
    jobs.pop(job_id, None)
    return {"ok": True}


@app.get("/api/download/{job_id}")
def download_transcript(job_id: str, timestamps: bool = True):
    # Try in-memory cache first, fall back to database
    job = jobs.get(job_id)
    if not job:
        with Session(engine) as session:
            record = session.get(TranscriptRecord, job_id)
            if not record:
                raise HTTPException(status_code=404, detail="Job not found")
            job = {
                "title": record.title,
                "text": record.text,
                "segments": json.loads(record.segments_json),
            }

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    try:
        if timestamps:
            for seg in job["segments"]:
                tmp.write(f"[{seg['start']} -> {seg['end']}]  {seg['text']}\n")
        else:
            tmp.write(job["text"] + "\n")
        tmp.close()
        title = job.get("title", "transcript")
        filename = f"{title}.txt"
        return FileResponse(
            tmp.name,
            media_type="text/plain",
            filename=filename,
        )
    except Exception:
        os.unlink(tmp.name)
        raise



@app.post("/api/transcribe/stream")
async def transcribe_stream(req: TranscribeRequest):
    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def worker():
        try:
            q.put({"type": "status", "message": "Starting download..."})

            with tempfile.TemporaryDirectory() as tmp_dir:
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
                }
                if FFMPEG_LOCATION:
                    ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION

                video_title = "transcript"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(req.url, download=False)
                    if info:
                        video_title = sanitize_filename(info.get("title", "transcript"))
                        q.put({"type": "status", "message": f"Video: {video_title}"})
                    ydl.download([req.url])

                audio_path = os.path.join(tmp_dir, "audio.mp3")
                if not os.path.exists(audio_path):
                    raise FileNotFoundError("Audio file not found after download. Is FFmpeg installed?")

                q.put({"type": "status", "message": f"Loading Whisper model '{req.model}'..."})
                model = whisper.load_model(req.model)

                q.put({"type": "status", "message": "Transcribing audio... (this may take several minutes)"})
                options = {}
                if req.language:
                    options["language"] = req.language
                result = model.transcribe(audio_path, **options)

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

            # Persist to database
            save_to_db(
                job_id=job_id,
                title=video_title,
                url=req.url,
                language=detected_lang,
                model=req.model,
                text=full_text,
                segments=segments,
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
# Serve React frontend (production build)
# Must be registered AFTER all /api/* routes
# ---------------------------------------------------------------------------
_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):  # noqa: ARG001
        """Catch-all: return index.html so React Router handles client-side nav."""
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))
