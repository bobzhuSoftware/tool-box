"""ServiceNow count tables: saved table layouts + a worker run that reads list counts (Edge automation)."""
import asyncio
import json
import os
import queue as stdlib_queue
import tempfile
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _get_user_setting, _set_user_setting

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_SETTING_KEY = "servicenow_tables"
_MAX_URLS = 200

# One worker at a time: all runs share a single Edge automation profile.
_run_lock = threading.Lock()

_DEFAULT_URL = (
    "https://dsv.service-now.com/x/dsva2/enterprise-integrations/simplelist/x_dsva2_enterpri_0_edi_case"
    "/params/list-title/Cases%20Received%20in%20past%207%20days%20Blue/query/"
    "origin_of_request!%3Dschenker_origin%5Estate!%3D1000%5Estate!%3D1300"
    "%5Edivision%3D420b49d01b3ae5103620b6c5464bcb89"
    "%5Esys_created_onBETWEENjavascript%3Ags.beginningOfLast7Days()%40javascript%3Ags.endOfToday()"
    "%5Eassignment_group!%3D2dafd3ec2b6d7690db00f454c891bf54"
)


class SnTable(BaseModel):
    id: str
    title: str = ""
    rows: list[str] = []
    cols: list[str] = []
    # "<row>,<col>" -> ServiceNow list URL
    cells: dict[str, str] = {}


class SnConfig(BaseModel):
    tables: list[SnTable] = []


def _default_config() -> SnConfig:
    return SnConfig(
        tables=[
            SnTable(
                id="default",
                title="All",
                rows=["Blue", "Red"],
                cols=["Opened", "Unassigned Requested", "Unassigned Pre-Sales"],
                cells={"0,0": _DEFAULT_URL},
            )
        ]
    )


def _is_servicenow_url(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "service-now.com" or host.endswith(".service-now.com"))


@router.get("/api/servicenow/config")
def get_config(user: User = Depends(require_user)) -> SnConfig:
    raw = _get_user_setting(user.id, _SETTING_KEY)
    if not raw:
        return _default_config()
    try:
        return SnConfig(**json.loads(raw))
    except (TypeError, ValueError):
        return _default_config()


@router.post("/api/servicenow/config")
def save_config(cfg: SnConfig, user: User = Depends(require_user)) -> SnConfig:
    _set_user_setting(user.id, _SETTING_KEY, cfg.model_dump_json())
    return cfg


class SnRunRequest(BaseModel):
    urls: list[str]


@router.post("/api/servicenow/run/stream")
async def run_stream(req: SnRunRequest, user: User = Depends(require_user)):
    import subprocess as _sp
    import sys as _sys

    urls = list(dict.fromkeys(u.strip() for u in req.urls if u.strip()))
    if not urls:
        raise HTTPException(status_code=400, detail="没有可读取的链接")
    if len(urls) > _MAX_URLS:
        raise HTTPException(status_code=400, detail=f"链接过多（最多 {_MAX_URLS} 个）")
    bad = [u for u in urls if not _is_servicenow_url(u)]
    if bad:
        raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有读取任务在运行，请稍后再试")

    edge_profile = _get_user_setting(user.id, "edge_profile") or ""
    q: stdlib_queue.Queue = stdlib_queue.Queue()

    def run_worker():
        url_file = None
        try:
            fd, url_file = tempfile.mkstemp(prefix="vt_sn_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(urls, f)

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            if edge_profile:
                env["VT_EDGE_PROFILE"] = edge_profile
            proc = _sp.Popen(
                [_sys.executable, os.path.join(_REPO_ROOT, "servicenow_worker.py"), "count", "--file", url_file],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", env=env,
            )
            stderr_lines: list = []
            threading.Thread(
                target=lambda: stderr_lines.extend(proc.stderr), daemon=True
            ).start()

            finished = False
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("RESULT:"):
                    try:
                        q.put({"type": "result", "data": json.loads(line[7:])})
                    except ValueError:
                        pass
                elif line.startswith("DONE:"):
                    finished = True
                    q.put({"type": "done"})
                elif line.startswith("ERROR:"):
                    finished = True
                    q.put({"type": "error", "message": line[6:]})
            proc.wait()
            if not finished:
                tail = "".join(stderr_lines).strip()[-400:]
                q.put({"type": "error", "message": tail or "读取进程异常退出"})
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": str(e)})
        finally:
            if url_file:
                try:
                    os.unlink(url_file)
                except OSError:
                    pass
            _run_lock.release()

    threading.Thread(target=run_worker, daemon=True).start()

    async def generate():
        while True:
            try:
                event = q.get_nowait()
            except stdlib_queue.Empty:
                await asyncio.sleep(0.15)
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
