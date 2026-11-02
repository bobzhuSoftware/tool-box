"""ServiceNow worker runs (SSE): spawn servicenow_worker.py for one identity and stream its output."""
import asyncio
import json
import os
import queue as stdlib_queue
import tempfile
import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _get_user_setting

from .accounts import (
    _account_dir, _load_accounts, _migrate_legacy_session, _require_account, _save_accounts,
)
from .common import _DEFAULT_ACCOUNT_ID, _MAX_URLS, _REPO_ROOT, _is_servicenow_url, _run_lock

router = APIRouter()


def _remember_identity(user_id: str, acc_id: str, ident: dict) -> None:
    state = _load_accounts(user_id)
    for a in state.accounts:
        if a.id == acc_id:
            a.user_name = str(ident.get("user_name", ""))
            a.display_name = str(ident.get("display_name", ""))
    _save_accounts(user_id, state)


def _kill_tree(pid: int) -> None:
    import subprocess as _sp

    if os.name == "nt":
        _sp.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _stream_worker(
    user: User, acc_id: str, worker_args: list[str], urls: list | None = None, on_result=None
):
    """Spawn servicenow_worker.py for one identity and stream its output as SSE (caller holds no lock).
    `urls` is the JSON input handed to the worker with --file; `on_result(data)` sees every RESULT on the server."""
    import subprocess as _sp
    import sys as _sys

    acc_dir = _account_dir(user.id, acc_id)
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务在运行，请稍后再试")

    edge_profile = _get_user_setting(user.id, "edge_profile") or ""
    q: stdlib_queue.Queue = stdlib_queue.Queue()
    live: dict = {"proc": None}

    def run_worker():
        url_file = None
        got_final = False
        final: dict = {"type": "error", "message": "读取进程异常退出"}
        try:
            if acc_id == _DEFAULT_ACCOUNT_ID:
                _migrate_legacy_session(user.id)
            os.makedirs(acc_dir, exist_ok=True)
            args = list(worker_args)
            if urls is not None:
                fd, url_file = tempfile.mkstemp(prefix="vt_sn_", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(urls, f)
                args += ["--file", url_file]

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["VT_EDGE_AUTOMATION_DIR"] = os.path.join(acc_dir, "edge")
            env["VT_SN_SESSION_FILE"] = os.path.join(acc_dir, "session.json")
            if edge_profile:
                env["VT_EDGE_PROFILE"] = edge_profile  # only selects which profile seeds the config files
            # Own process group: a Ctrl+C aimed at uvicorn (reload/shutdown) must not reach the worker.
            proc = _sp.Popen(
                [_sys.executable, os.path.join(_REPO_ROOT, "servicenow_worker.py"), *args],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", env=env,
                creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            live["proc"] = proc
            stderr_lines: list = []
            threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True).start()

            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("STAGE:"):
                    q.put({"type": "stage", "stage": line[6:]})
                elif line.startswith("RESULT:"):
                    try:
                        data = json.loads(line[7:])
                    except ValueError:
                        continue
                    if on_result:
                        try:
                            on_result(data)
                        except Exception:  # noqa: BLE001
                            pass
                    q.put({"type": "result", "data": data})
                elif line.startswith("IDENTITY:"):
                    try:
                        ident = json.loads(line[9:])
                    except ValueError:
                        continue
                    _remember_identity(user.id, acc_id, ident)
                    q.put({"type": "identity", "data": ident})
                elif line.startswith("DONE:"):
                    final, got_final = {"type": "done"}, True
                elif line.startswith("ERROR:"):
                    final, got_final = {"type": "error", "message": line[6:]}, True
            proc.wait()
            tail = "".join(stderr_lines).strip()[-400:]
            if not got_final and tail:
                final = {"type": "error", "message": tail}
        except Exception as e:  # noqa: BLE001
            final = {"type": "error", "message": str(e)}
        finally:
            if url_file:
                try:
                    os.unlink(url_file)
                except OSError:
                    pass
            _run_lock.release()
            # Sent after the lock is free so the client can start the next action right away.
            q.put(final)

    threading.Thread(target=run_worker, daemon=True).start()

    async def generate():
        try:
            while True:
                try:
                    event = q.get_nowait()
                except stdlib_queue.Empty:
                    await asyncio.sleep(0.15)
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            # Client gone or server shutting down: don't leave the worker (and the run lock) hanging.
            proc = live["proc"]
            if proc is not None and proc.poll() is None:
                _kill_tree(proc.pid)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class SnRunRequest(BaseModel):
    urls: list[str]
    account: str = ""


@router.post("/api/servicenow/run/stream")
async def run_stream(req: SnRunRequest, user: User = Depends(require_user)):
    urls = list(dict.fromkeys(u.strip() for u in req.urls if u.strip()))
    if not urls:
        raise HTTPException(status_code=400, detail="没有可读取的链接")
    if len(urls) > _MAX_URLS:
        raise HTTPException(status_code=400, detail=f"链接过多（最多 {_MAX_URLS} 个）")
    if any(not _is_servicenow_url(u) for u in urls):
        raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")

    state = _load_accounts(user.id)
    acc = _require_account(state, req.account or state.selected)
    return _stream_worker(user, acc.id, ["count"], urls=urls)


@router.post("/api/servicenow/accounts/{acc_id}/login/stream")
async def login_stream(acc_id: str, user: User = Depends(require_user)):
    acc = _require_account(_load_accounts(user.id), acc_id)
    return _stream_worker(user, acc.id, ["login"])
