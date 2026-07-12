"""Web → PDF generation (background jobs + SSE) and DSV ServiceNow URL normalizer."""
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
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user, user_from_token_or_header
from app.core.db import User
from app.core.settings import _get_user_setting
from app.core.validation import _URL_PATTERN

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

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


# ---------------------------------------------------------------------------
# Web → PDF endpoint
# ---------------------------------------------------------------------------
class PdfRequest(BaseModel):
    url: str
    is_x: bool = False  # True = use Firefox profile path for X/Twitter articles


# ---------------------------------------------------------------------------
# PDF queue — background jobs (in-memory, same pattern as Teams transcript)
# ---------------------------------------------------------------------------
_pdf_jobs: dict[str, dict] = {}


def _pdf_run_job(job_id: str, url: str, user_id: str, is_x: bool, firefox_profile: str) -> None:
    """Background thread: runs pdf_worker.py subprocess and updates _pdf_jobs."""
    import subprocess
    import sys as _sys

    job = _pdf_jobs.get(job_id)
    if job is None:
        return

    def _log(msg_type: str, message: str) -> None:
        job["progress"].append({"type": msg_type, "message": message})
        if len(job["progress"]) > 200:
            job["progress"] = job["progress"][-200:]

    tmp_path: str | None = None
    try:
        _log("status", "开始运行…")
        worker = os.path.join(_REPO_ROOT, "pdf_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        tmp_path = tmp.name

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"
        if firefox_profile:
            _env["VT_FIREFOX_PROFILE"] = firefox_profile

        stderr_lines: list = []

        def _drain_stderr(proc):
            for ln in proc.stderr:
                stderr_lines.append(ln)

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp_path, "1" if is_x else "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=_env,
            )
            job["_proc"] = proc
            stderr_thread = threading.Thread(target=_drain_stderr, args=(proc,), daemon=True)
            stderr_thread.start()

            captured_title = ""
            done_seen = False
            for raw_line in proc.stdout:
                if job.get("_cancel"):
                    proc.kill()
                    break
                line = raw_line.strip()
                if line.startswith("STATUS:"):
                    _log("status", line[7:])
                elif line.startswith("TITLE:"):
                    captured_title = line[6:]
                elif line == "DONE":
                    try:
                        _save_pdf_job(job_id, tmp_path, user_id, url, captured_title)
                        tmp_path = None  # moved; skip cleanup
                    except OSError as exc:
                        job["status"] = "error"
                        job["error_message"] = f"Failed to save PDF: {exc}"
                        _log("error", job["error_message"])
                        done_seen = True
                        break
                    job["result"] = {"title": captured_title}
                    job["status"] = "done"
                    _log("done", f"✓ PDF 已就绪：{captured_title or url}")
                    done_seen = True
            proc.wait()
            stderr_thread.join(timeout=5)
            if proc.returncode != 0 and not done_seen:
                real_errors = "\n".join(
                    ln for ln in stderr_lines
                    if "Exception ignored in" not in ln
                    and "proactor_events" not in ln
                    and "windows_utils" not in ln
                    and "I/O operation on closed pipe" not in ln
                ).strip()
                msg = real_errors[-400:] if real_errors else "Worker process failed unexpectedly."
                job["status"] = "error"
                job["error_message"] = msg
                _log("error", msg)
        except Exception as exc:
            job["status"] = "error"
            job["error_message"] = str(exc)
            _log("error", str(exc))
    finally:
        job.pop("_proc", None)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@router.post("/api/pdf/enqueue")
def pdf_enqueue(req: PdfRequest, user: User = Depends(require_user)):
    """Submit a PDF generation job; returns job_id immediately."""
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")
    firefox_profile = _get_user_setting(user.id, "firefox_profile") if req.is_x else None
    job_id = uuid.uuid4().hex[:12]
    _pdf_jobs[job_id] = {
        "job_id": job_id,
        "user_id": user.id,
        "url": url,
        "is_x": req.is_x,
        "status": "running",
        "progress": [],
        "result": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc).timestamp(),
        "_cancel": False,
        "_proc": None,
    }
    threading.Thread(
        target=_pdf_run_job,
        args=(job_id, url, user.id, req.is_x, firefox_profile or ""),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@router.get("/api/pdf/jobs")
def pdf_jobs_list(user: User = Depends(require_user)):
    """Return a slim list of all PDF jobs for the current user."""
    user_jobs = [j for j in _pdf_jobs.values() if j["user_id"] == user.id]
    user_jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": [{
        "job_id": j["job_id"],
        "url": j["url"],
        "is_x": j["is_x"],
        "status": j["status"],
        "last_message": j["progress"][-1]["message"] if j["progress"] else "",
        "result": j["result"],
        "error_message": j["error_message"],
        "created_at": j["created_at"],
    } for j in user_jobs]}


@router.get("/api/pdf/status/{job_id}")
def pdf_status(job_id: str, user: User = Depends(require_user)):
    """Return full state (including progress log) of a single PDF job."""
    job = _pdf_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {k: v for k, v in job.items() if not k.startswith("_")}


@router.delete("/api/pdf/jobs/{job_id}")
def pdf_delete_job(job_id: str, user: User = Depends(require_user)):
    """Cancel and remove a PDF job."""
    job = _pdf_jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    job["_cancel"] = True
    proc = job.get("_proc")
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
    del _pdf_jobs[job_id]
    return {"ok": True}


@router.post("/api/pdf/stream")
async def pdf_stream(req: PdfRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")

    # For X/Twitter mode, resolve the user's chosen Firefox profile up-front
    # (require_user / DB access must happen outside the worker thread closure).
    firefox_profile = _get_user_setting(user.id, "firefox_profile") if req.is_x else None

    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        import subprocess
        import sys as _sys

        worker = os.path.join(_REPO_ROOT, "pdf_worker.py")
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()

        _env = os.environ.copy()
        if firefox_profile:
            _env["VT_FIREFOX_PROFILE"] = firefox_profile

        try:
            proc = subprocess.Popen(
                [_sys.executable, worker, url, tmp.name, "1" if req.is_x else "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=_env,
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


@router.get("/api/pdf/download/{job_id}")
def download_pdf(
    job_id: str,
    user: User = Depends(user_from_token_or_header),
):
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


@router.post("/api/dsv-pdf/normalize")
def dsv_pdf_normalize(req: DsvNormalizeRequest, user: User = Depends(require_user)):
    url = req.url.strip()
    if not _URL_PATTERN.match(url):
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported")
    normalized = _normalize_dsv_url(url)
    return {"normalized_url": normalized, "changed": normalized != url}
