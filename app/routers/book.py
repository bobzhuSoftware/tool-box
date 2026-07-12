"""Book format conversion (PDF ↔ EPUB) via Calibre, plus per-user Calibre config."""
import asyncio
import json
import os
import queue as stdlib_queue
import shutil
import tempfile
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User
from app.core.settings import _delete_user_setting, _get_user_setting, _set_user_setting
from app.core.text_utils import sanitize_filename

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

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


@router.post("/api/book/convert")
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
    calibre_path = _get_user_setting(user.id, "calibre_path") or ""

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(_REPO_ROOT, "book_converter_worker.py")
        stderr_lines: list[str] = []

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if calibre_path:
            _env["VT_CALIBRE_PATH"] = calibre_path

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, tmp_input.name, tmp_output.name, direction],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=_env,
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


@router.get("/api/book/download/{job_id}")
def book_download(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
    _cleanup_old_books()
    result = _get_book_job(job_id, user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversion job not found or expired")

    file_path, filename = result
    ext = os.path.splitext(filename)[1].lower()
    media_type = "application/epub+zip" if ext == ".epub" else "application/pdf"
    return FileResponse(file_path, media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# Calibre detection / per-user path (EPUB → PDF engine)
# ---------------------------------------------------------------------------
CALIBRE_DOWNLOAD_URL = "https://calibre-ebook.com/download"


class CalibrePathRequest(BaseModel):
    path: str


@router.get("/api/book/calibre-status")
def calibre_status(user: User = Depends(require_user)):
    """Report whether a Calibre engine is reachable on this machine for the
    current user, plus the saved custom path and cloud-API fallback state."""
    import book_converter_worker as _bcw

    custom = _get_user_setting(user.id, "calibre_path") or ""
    resolved = _bcw._find_ebook_convert(custom or None)
    cloud_available = bool(_bcw.CLOUDCONVERT_API_KEY or _bcw.ZAMZAR_API_KEY)
    return {
        "installed": bool(resolved),
        "path": resolved or "",
        "custom_path": custom,
        "cloud_available": cloud_available,
        "download_url": CALIBRE_DOWNLOAD_URL,
    }


@router.put("/api/book/calibre-path")
def set_calibre_path(req: CalibrePathRequest, user: User = Depends(require_user)):
    """Persist a custom Calibre location (the ebook-convert executable or its
    install directory) and report whether it resolves."""
    import book_converter_worker as _bcw

    path = req.path.strip().strip('"')
    if not path:
        _delete_user_setting(user.id, "calibre_path")
        resolved = _bcw._find_ebook_convert(None)
        return {"ok": True, "custom_path": "", "installed": bool(resolved), "path": resolved or ""}

    resolved = _bcw._resolve_calibre_candidate(path)
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="该路径下未找到 ebook-convert，请填写 Calibre 安装目录或 ebook-convert 可执行文件的完整路径。",
        )
    _set_user_setting(user.id, "calibre_path", path)
    return {"ok": True, "custom_path": path, "installed": True, "path": resolved}


@router.delete("/api/book/calibre-path")
def clear_calibre_path(user: User = Depends(require_user)):
    """Clear the saved custom Calibre path and fall back to auto-detection."""
    import book_converter_worker as _bcw

    _delete_user_setting(user.id, "calibre_path")
    resolved = _bcw._find_ebook_convert(None)
    return {"ok": True, "custom_path": "", "installed": bool(resolved), "path": resolved or ""}
