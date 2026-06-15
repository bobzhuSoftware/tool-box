import asyncio
import http.cookiejar
import io
import json
import os
import queue as stdlib_queue
import re
import shutil
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse, unquote, parse_qs, urlencode, urlunparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
import bcrypt as _bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session

import whisper
import yt_dlp
try:
    import zhconv
    _has_zhconv = True
except ImportError:
    _has_zhconv = False

# ---------------------------------------------------------------------------
# Whisper model cache — avoids reloading the same model on every request
# ---------------------------------------------------------------------------
_whisper_models: dict[str, whisper.Whisper] = {}
_whisper_lock = threading.Lock()


def _get_whisper_model(model_name: str) -> whisper.Whisper:
    """Return a cached Whisper model, loading it on first use."""
    if model_name not in _whisper_models:
        with _whisper_lock:
            if model_name not in _whisper_models:  # double-check after acquiring lock
                _whisper_models[model_name] = whisper.load_model(model_name)
    return _whisper_models[model_name]


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
# Locally falls back to <repo>/data/transcripts.db. The `data/` folder at the
# repo root is a junction pointing at OneDrive ProjectData (so the DB is
# backed up via OneDrive and never tracked in git).
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "data", "transcripts.db"),
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class TranscriptRecord(Base):
    __tablename__ = "transcripts"

    job_id = Column(String(24), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    url = Column(Text, nullable=False)
    language = Column(String(20), nullable=False)
    model = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    segments_json = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, nullable=False)


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    key = Column(String(80), nullable=False)
    value = Column(Text, nullable=False)
    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("user_id", "key", name="uq_user_setting"),
    )


Base.metadata.create_all(engine)

# Lightweight migration: add user_id column if upgrading from older schema
with engine.connect() as conn:
    from sqlalchemy import inspect as sa_inspect, text
    cols = [c["name"] for c in sa_inspect(engine).get_columns("transcripts")]
    if "user_id" not in cols:
        conn.execute(text("ALTER TABLE transcripts ADD COLUMN user_id VARCHAR(36)"))
        conn.commit()


def save_to_db(job_id: str, title: str, url: str, language: str,
               model: str, text: str, segments: list[dict],
               user_id: str | None = None) -> None:
    with Session(engine) as session:
        record = TranscriptRecord(
            job_id=job_id,
            user_id=user_id,
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
# Auth / JWT configuration
# ---------------------------------------------------------------------------
# Generate a real secret in production: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-random-secret-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24h

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User | None:
    """Return the authenticated User or None (for optional auth)."""
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    with Session(engine) as session:
        return session.get(User, user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Raise 401 if no valid user."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# App & in-memory job cache
# ---------------------------------------------------------------------------
app = FastAPI()

# In-memory cache so downloads within the same session are fast
jobs: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Disk-based PDF job cache (survives hot-reloads in dev mode)
# ---------------------------------------------------------------------------
_PDF_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_pdf_cache")
os.makedirs(_PDF_CACHE_DIR, exist_ok=True)


def _pdf_file_path(job_id: str) -> str:
    return os.path.join(_PDF_CACHE_DIR, f"{job_id}.pdf")


def _pdf_meta_path(job_id: str) -> str:
    return os.path.join(_PDF_CACHE_DIR, f"{job_id}.json")


def _save_pdf_job(job_id: str, src_path: str, user_id: str, url: str, title: str = "") -> None:
    shutil.move(src_path, _pdf_file_path(job_id))
    with open(_pdf_meta_path(job_id), "w", encoding="utf-8") as f:
        json.dump({"user_id": user_id, "url": url, "title": title}, f)


def _get_pdf_job(job_id: str, user_id: str) -> str | None:
    """Return PDF path if job exists and belongs to user, else None."""
    meta = _pdf_meta_path(job_id)
    pdf = _pdf_file_path(job_id)
    if not os.path.isfile(meta) or not os.path.isfile(pdf):
        return None
    with open(meta, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("user_id") != user_id:
        return None
    return pdf


def _cleanup_old_pdfs(max_age_seconds: int = 3600) -> None:
    """Delete PDF cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_PDF_CACHE_DIR):
        fpath = os.path.join(_PDF_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


class RegisterRequest(BaseModel):
    username: str
    password: str


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


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
@app.post("/api/register")
def register(req: RegisterRequest):
    username = req.username.strip()
    if not username or len(username) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    with Session(engine) as session:
        existing = session.query(User).filter(User.username == username).first()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        user = User(
            id=uuid.uuid4().hex,
            username=username,
            password_hash=_hash_password(req.password),
            created_at=datetime.now(timezone.utc),
        )
        session.add(user)
        session.commit()
        token = _create_access_token({"sub": user.id})
    return {"access_token": token, "token_type": "bearer", "username": username}


@app.post("/api/login")
def login(req: RegisterRequest):
    with Session(engine) as session:
        user = session.query(User).filter(User.username == req.username.strip()).first()
    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _create_access_token({"sub": user.id})
    return {"access_token": token, "token_type": "bearer", "username": user.username}


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/me")
def get_me(user: User = Depends(require_user)):
    return {"username": user.username}


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


@app.get("/api/whisper/models")
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


@app.post("/api/whisper/models/{model_name}/download")
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



# ---------------------------------------------------------------------------
# Cookie resolution (YouTube bot-detection bypass)
# ---------------------------------------------------------------------------
_COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
_COOKIES_BROWSER = os.environ.get("YOUTUBE_COOKIES_BROWSER")  # e.g. "chrome", "firefox"


def _apply_cookies(ydl_opts: dict) -> None:
    """Inject cookie configuration into yt-dlp options if available.

    Instead of passing a file path (which yt-dlp will try to *write back*,
    causing Permission Denied on Windows), we load the cookies into a
    MozillaCookieJar with its save method disabled and pass the jar object.
    This way yt-dlp can read cookies but never attempts a file write.
    """
    if os.path.isfile(_COOKIES_FILE):
        try:
            jar = http.cookiejar.MozillaCookieJar()
            jar.load(_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
            # Disable save so yt-dlp can never trigger a write
            jar.save = lambda *a, **kw: None
            ydl_opts["cookiejar"] = jar
        except OSError:
            pass  # Skip cookies if we can't read the file
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


def _ts_to_seconds(ts: str) -> int:
    """Convert 'HH:MM:SS' timestamp string back to total seconds."""
    parts = ts.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _count_words(text: str) -> int:
    """Count words in a language-aware way (CJK chars count individually)."""
    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in ('W', 'F'))
    if cjk > len(text) * 0.3:
        return cjk
    return len(text.split())


# ---------------------------------------------------------------------------
# Subtitle / Caption extraction helpers
# ---------------------------------------------------------------------------

class CaptionsNotFoundError(Exception):
    """Raised when no captions/subtitles are available for a video."""


def _parse_vtt(content: str) -> list[dict]:
    """
    Parse a WebVTT subtitle file into Whisper-compatible segment dicts.

    Returns: [{"start": float, "end": float, "text": str}, ...]

    Handles:
    - YouTube timing tags like <c>, <00:00:00.000>
    - HTML tags
    - Consecutive duplicate cue blocks (YouTube auto-caption overlap)
    """
    # Strip YouTube timing/colour tags and HTML
    content = re.sub(r'<[^>]+>', '', content)

    cue_re = re.compile(
        r'(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})[^\n]*\n'
        r'((?:(?!\d{1,2}:\d{2}:\d{2}).*\n?)*)',
        re.MULTILINE,
    )

    def ts_to_sec(ts: str) -> float:
        ts = ts.replace(',', '.')
        parts = ts.split(':')
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s

    segments: list[dict] = []
    prev_text: str = ''
    for m in cue_re.finditer(content):
        start_s = ts_to_sec(m.group(1))
        end_s   = ts_to_sec(m.group(2))
        text    = m.group(3).strip()
        if not text or text == prev_text:
            continue
        # Skip WebVTT NOTE blocks and header lines
        if text.upper().startswith('NOTE') or text.upper().startswith('WEBVTT'):
            continue
        segments.append({"start": start_s, "end": end_s, "text": text})
        prev_text = text

    return segments


def _extract_captions(
    url: str,
    language_pref: str | None,
    tmp_dir: str,
    q: stdlib_queue.Queue,
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

    info_opts: dict = {"quiet": True, "skip_download": True}
    _apply_cookies(info_opts)
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
    }
    _apply_cookies(dl_opts)
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
def transcribe(req: TranscribeRequest, user: User = Depends(require_user)):
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = download_audio(req.url, tmp_dir)
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


@app.get("/api/history")
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


@app.delete("/api/history/{job_id}")
def delete_history(job_id: str, user: User = Depends(require_user)):
    with Session(engine) as session:
        record = session.get(TranscriptRecord, job_id)
        if not record or record.user_id != user.id:
            raise HTTPException(status_code=404, detail="Record not found")
        session.delete(record)
        session.commit()
    jobs.pop(job_id, None)
    return {"ok": True}


@app.get("/api/download/{job_id}")
def download_transcript(job_id: str, timestamps: bool = True, chunk_minutes: int = 0, token: str | None = None, user: User | None = Depends(get_current_user)):
    # Support token as query param for browser window.open() downloads
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
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



@app.post("/api/transcribe/stream")
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
                    }
                    if FFMPEG_LOCATION:
                        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION
                    _apply_cookies(ydl_opts)

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
                        req.url, req.language, tmp_dir, q
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
                            req.url, req.language, tmp_dir, q
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


@app.post("/api/transcribe/upload")
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
# Web → PDF endpoint
# ---------------------------------------------------------------------------
_URL_PATTERN = re.compile(r'^https?://', re.IGNORECASE)


class PdfRequest(BaseModel):
    url: str
    is_x: bool = False  # True = use Firefox profile path for X/Twitter articles


@app.post("/api/pdf/stream")
async def pdf_stream(req: PdfRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(os.path.dirname(__file__), "pdf_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp.name, "1" if req.is_x else "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Drain stderr in a background thread to prevent the subprocess
            # from blocking when its stderr pipe buffer fills up (e.g. Firefox
            # printing lots of debug lines during headless startup on Windows).
            stderr_lines: list = []
            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_lines.append(ln)
            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            captured_title = ""
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("TITLE:"):
                    captured_title = line[6:]
                elif line == "DONE":
                    q.put({"type": "_done_marker", "path": tmp.name, "title": captured_title})
            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0:
                stderr_out = "".join(stderr_lines)
                # Ignore benign asyncio ProactorEventLoop cleanup noise on Windows.
                real_errors = "\n".join(
                    ln for ln in stderr_out.splitlines()
                    if "Exception ignored in" not in ln
                    and "proactor_events" not in ln
                    and "windows_utils" not in ln
                    and "I/O operation on closed pipe" not in ln
                ).strip()
                if real_errors:
                    q.put({"type": "error", "message": f"PDF generation failed: {real_errors[-400:]}"})
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
                pdf_path = event["path"]
                article_title = event.get("title", "")
                try:
                    _save_pdf_job(job_id, pdf_path, user.id, url, article_title)
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save PDF: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id})}\n\n"
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


@app.get("/api/pdf/download/{job_id}")
def download_pdf(
    job_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user),
):
    # Support token as query param (for window.open downloads)
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    _cleanup_old_pdfs()
    pdf_file = _get_pdf_job(job_id, user.id)
    if not pdf_file:
        raise HTTPException(status_code=404, detail="PDF not found or expired")

    # Read metadata for filename
    try:
        with open(_pdf_meta_path(job_id), encoding="utf-8") as f:
            meta = json.load(f)
        raw_title = meta.get("title", "").strip()
        if raw_title:
            # Sanitize title for use as a filename
            safe_name = unicodedata.normalize("NFC", raw_title)
            safe_name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", safe_name)
            safe_name = re.sub(r" +", " ", safe_name).strip()
            safe_name = safe_name[:120]  # cap length
            filename = f"{safe_name}.pdf"
        else:
            parsed = urlparse(meta.get("url", ""))
            safe_host = re.sub(r"[^a-zA-Z0-9._-]", "_", parsed.hostname or "page")
            filename = f"{safe_host}.pdf"
    except (OSError, ValueError):
        filename = "download.pdf"

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# PDF2: Readability-based clean article extraction
# ---------------------------------------------------------------------------
class Pdf2Request(BaseModel):
    url: str


@app.post("/api/pdf2/stream")
async def pdf2_stream(req: Pdf2Request, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(os.path.dirname(__file__), "pdf2_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            # Drain stderr in a background thread to prevent the subprocess
            # from blocking when its stderr pipe buffer fills up.
            stderr_lines: list = []
            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_lines.append(ln)
            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            captured_title = ""
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("TITLE:"):
                    captured_title = line[6:]
                elif line == "DONE":
                    q.put({"type": "_done_marker", "path": tmp.name, "title": captured_title})
            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0:
                stderr_out = "".join(stderr_lines)
                # Ignore benign asyncio ProactorEventLoop cleanup noise on Windows.
                real_errors = "\n".join(
                    ln for ln in stderr_out.splitlines()
                    if "Exception ignored in" not in ln
                    and "proactor_events" not in ln
                    and "windows_utils" not in ln
                    and "I/O operation on closed pipe" not in ln
                ).strip()
                if real_errors:
                    q.put({"type": "error", "message": f"PDF generation failed: {real_errors[-400:]}"})
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
                pdf_path = event["path"]
                article_title = event.get("title", "")
                try:
                    _save_pdf_job(job_id, pdf_path, user.id, url, article_title)
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save PDF: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id})}\n\n"
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


# ---------------------------------------------------------------------------
# DSV ServiceNow URL normalizer
# ---------------------------------------------------------------------------
# DSV ServiceNow wraps every page inside a frameset URL like
#   https://dsv.service-now.com/now/nav/ui/classic/params/target/<encoded-target>
# The bare page (kb_view.do?sys_kb_id=...) renders much cleaner. Strip the
# wrapper and keep only sys_kb_id; the user opens the result in their own
# signed-in Edge and prints to PDF from there.
_DSV_FRAME_WRAPPER_RE = re.compile(
    r"^https?://dsv\.service-now\.com/now/nav/ui/classic/params/target/(.+)$",
    re.IGNORECASE,
)


def _normalize_dsv_url(url: str) -> str:
    m = _DSV_FRAME_WRAPPER_RE.match(url.strip())
    if not m:
        return url.strip()
    inner = unquote(m.group(1))
    if "?" in inner:
        path, query = inner.split("?", 1)
    else:
        path, query = inner, ""
    params = parse_qs(query, keep_blank_values=False)
    kb_id = params.get("sys_kb_id", [None])[0]
    new_query = urlencode({"sys_kb_id": kb_id}) if kb_id else ""
    return urlunparse(("https", "dsv.service-now.com",
                       "/" + path.lstrip("/"), "", new_query, ""))


class DsvNormalizeRequest(BaseModel):
    url: str


@app.post("/api/dsv-pdf/normalize")
def dsv_pdf_normalize(req: DsvNormalizeRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")
    normalized = _normalize_dsv_url(url)
    return {"normalized_url": normalized, "changed": normalized != url}


# ---------------------------------------------------------------------------
# Book format conversion (PDF ↔ EPUB)
# ---------------------------------------------------------------------------
_BOOK_CACHE_DIR = os.path.join(tempfile.gettempdir(), "vt_book_cache")
os.makedirs(_BOOK_CACHE_DIR, exist_ok=True)


def _book_file_path(job_id: str, ext: str) -> str:
    return os.path.join(_BOOK_CACHE_DIR, f"{job_id}{ext}")


def _book_meta_path(job_id: str) -> str:
    return os.path.join(_BOOK_CACHE_DIR, f"{job_id}.json")


def _save_book_job(job_id: str, src_path: str, user_id: str, filename: str) -> None:
    ext = os.path.splitext(filename)[1].lower()
    shutil.move(src_path, _book_file_path(job_id, ext))
    with open(_book_meta_path(job_id), "w", encoding="utf-8") as f:
        json.dump({"user_id": user_id, "filename": filename, "ext": ext}, f)


def _cleanup_old_books(max_age_seconds: int = 3600) -> None:
    """Delete Book cache files older than max_age_seconds."""
    now = datetime.now().timestamp()
    for fname in os.listdir(_BOOK_CACHE_DIR):
        fpath = os.path.join(_BOOK_CACHE_DIR, fname)
        try:
            if now - os.path.getmtime(fpath) > max_age_seconds:
                os.unlink(fpath)
        except OSError:
            pass


def _get_book_job(job_id: str, user_id: str) -> tuple[str, str] | None:
    """Return (file_path, download_filename) if job belongs to user, else None."""
    meta_path = _book_meta_path(job_id)
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("user_id") != user_id:
        return None
    ext = data.get("ext", "")
    file_path = _book_file_path(job_id, ext)
    if not os.path.isfile(file_path):
        return None
    return file_path, data["filename"]


@app.post("/api/book/convert")
async def book_convert(
    file: UploadFile = File(...),
    direction: str = Form(...),
    user: User = Depends(require_user),
):
    """Accept an uploaded book file and stream conversion progress (SSE)."""
    if direction not in ("epub2pdf", "pdf2epub"):
        raise HTTPException(status_code=400, detail="direction must be 'epub2pdf' or 'pdf2epub'")

    original_name = file.filename or "upload"
    src_ext = os.path.splitext(original_name)[1].lower()
    expected_src = ".epub" if direction == "epub2pdf" else ".pdf"
    if src_ext != expected_src:
        raise HTTPException(
            status_code=400,
            detail=f"Expected a {expected_src.upper()} file for this conversion direction",
        )

    out_ext = ".pdf" if direction == "epub2pdf" else ".epub"
    out_filename = sanitize_filename(os.path.splitext(original_name)[0]) + out_ext

    # Save uploaded file to temp location
    tmp_input = tempfile.NamedTemporaryFile(delete=False, suffix=src_ext)
    try:
        content = await file.read()
        tmp_input.write(content)
        tmp_input.close()
    except Exception as exc:
        tmp_input.close()
        os.unlink(tmp_input.name)
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}")

    tmp_output = tempfile.NamedTemporaryFile(delete=False, suffix=out_ext)
    tmp_output.close()

    q: stdlib_queue.Queue = stdlib_queue.Queue()
    user_id = user.id

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(os.path.dirname(__file__), "book_converter_worker.py")
        stderr_lines: list[str] = []

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, tmp_input.name, tmp_output.name, direction],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )

            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_lines.append(ln)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            for raw_line in proc.stdout:
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("ERROR:"):
                    q.put({"type": "error", "message": line[6:]})
                elif line == "DONE":
                    q.put({"type": "_done_marker"})

            proc.wait()
            stderr_thread.join(timeout=5)

            if proc.returncode != 0 and not any(
                e["type"] in ("error", "_done_marker") for e in list(q.queue)
            ):
                err = "".join(stderr_lines[-30:]).strip() or "Conversion failed (non-zero exit)"
                q.put({"type": "error", "message": err[-600:]})

        except Exception as exc:
            q.put({"type": "error", "message": str(exc)})
        finally:
            try:
                os.unlink(tmp_input.name)
            except OSError:
                pass

    threading.Thread(target=run_worker, daemon=True).start()

    async def generate():
        job_id = uuid.uuid4().hex[:12]
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue

            if event["type"] == "_done_marker":
                if not os.path.isfile(tmp_output.name) or os.path.getsize(tmp_output.name) == 0:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Conversion produced no output'})}\n\n"
                    break
                try:
                    _save_book_job(job_id, tmp_output.name, user_id, out_filename)
                except OSError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Failed to save result: {exc}'})}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, 'filename': out_filename})}\n\n"
                break
            else:
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "error":
                    try:
                        os.unlink(tmp_output.name)
                    except OSError:
                        pass
                    break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/book/download/{job_id}")
def book_download(
    job_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user),
):
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    _cleanup_old_books()
    result = _get_book_job(job_id, user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversion job not found or expired")

    file_path, filename = result
    ext = os.path.splitext(filename)[1].lower()
    media_type = "application/epub+zip" if ext == ".epub" else "application/pdf"
    return FileResponse(file_path, media_type=media_type, filename=filename)


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


@app.post("/api/teams-transcript/stream")
async def teams_transcript_stream(req: TeamsTranscriptRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url) and "sharepoint.com" not in url:
        raise HTTPException(status_code=400, detail="Please provide a SharePoint/Teams recording URL")

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(os.path.dirname(__file__), "teams_transcript_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.close()

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
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


@app.get("/api/teams-transcript/download/{job_id}")
def download_vtt(
    job_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user),
):
    # Support token as query param (for window.open downloads)
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    _cleanup_old_vtt()
    vtt_file = _vtt_file_path(job_id)
    meta_file = _vtt_meta_path(job_id)
    if not os.path.isfile(vtt_file) or not os.path.isfile(meta_file):
        raise HTTPException(status_code=404, detail="Transcript not found or expired")

    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Transcript not found or expired")

    name = re.sub(r'[\\/:*?"<>|]', "_", meta.get("name", "transcript"))
    filename = f"{name}.txt"

    return FileResponse(vtt_file, media_type="text/plain", filename=filename)


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


@app.post("/api/wechat/contacts/stream")
async def wechat_contacts_stream(req: WechatContactsRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        worker = os.path.join(os.path.dirname(__file__), "wechat_worker.py")
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


@app.post("/api/wechat/export/stream")
async def wechat_export_stream(req: WechatExportRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        worker = os.path.join(os.path.dirname(__file__), "wechat_worker.py")
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


@app.get("/api/wechat/download/{job_id}")
def download_wechat(
    job_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user),
):
    # Support token as query param (for window.open downloads)
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

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


def _get_user_setting(user_id: str, key: str) -> str | None:
    with Session(engine) as s:
        row = s.query(UserSetting).filter_by(user_id=user_id, key=key).first()
        return row.value if row else None


def _set_user_setting(user_id: str, key: str, value: str) -> None:
    with Session(engine) as s:
        row = s.query(UserSetting).filter_by(user_id=user_id, key=key).first()
        if row:
            row.value = value
        else:
            s.add(UserSetting(user_id=user_id, key=key, value=value))
        s.commit()


def _delete_user_setting(user_id: str, key: str) -> None:
    with Session(engine) as s:
        s.query(UserSetting).filter_by(user_id=user_id, key=key).delete()
        s.commit()


@app.get("/api/discord/token")
def get_discord_token(user: User = Depends(require_user)):
    value = _get_user_setting(user.id, "discord_token")
    return {"token": value or ""}


@app.put("/api/discord/token")
def save_discord_token(req: DiscordTokenRequest, user: User = Depends(require_user)):
    _set_user_setting(user.id, "discord_token", req.token.strip())
    return {"ok": True}


@app.delete("/api/discord/token")
def clear_discord_token(user: User = Depends(require_user)):
    _delete_user_setting(user.id, "discord_token")
    return {"ok": True}


@app.post("/api/discord/stream")
async def discord_stream(req: DiscordExportRequest, user: User = Depends(require_user)):
    channel_url = req.channel_url.strip()
    if not channel_url:
        raise HTTPException(status_code=400, detail="Channel URL is required")

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess as _sp
        import sys as _sys

        worker = os.path.join(os.path.dirname(__file__), "discord_worker.py")
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


@app.get("/api/discord/download/{job_id}")
def download_discord(
    job_id: str,
    token: str | None = None,
    user: User | None = Depends(get_current_user),
):
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as s:
                    user = s.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
