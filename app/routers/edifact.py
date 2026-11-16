"""UN/EDIFACT structure viewer: fetch a message definition and render it as a
standalone, ad-free HTML document (see app.core.edifact_dir)."""
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.edifact_dir import (
    CACHE_DIR,
    EdifactError,
    list_messages,
    list_versions,
    load_message,
    normalize_message,
    normalize_version,
)
from app.core.edifact_html import render_html

router = APIRouter()


def _fail(exc: EdifactError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/api/edifact/versions")
async def edifact_versions(refresh: bool = False, user: User = Depends(require_user)):
    try:
        return {"versions": await run_in_threadpool(list_versions, refresh)}
    except EdifactError as exc:
        raise _fail(exc)


@router.get("/api/edifact/messages")
async def edifact_messages(
    version: str, refresh: bool = False, user: User = Depends(require_user)
):
    try:
        version = normalize_version(version)
        return {"version": version, "messages": await run_in_threadpool(list_messages, version, refresh)}
    except EdifactError as exc:
        raise _fail(exc)


class EdifactRequest(BaseModel):
    version: str
    message: str
    include_codes: bool = True
    refresh: bool = False


@router.post("/api/edifact/html")
async def edifact_html(req: EdifactRequest, user: User = Depends(require_user)):
    """Return the standalone HTML for one message; a copy is also saved to
    ``data/edifact/output/`` so it can be opened later without the app."""

    def build() -> tuple[str, str]:
        version = normalize_version(req.version)
        message = normalize_message(req.message)
        data = load_message(version, message, req.include_codes, req.refresh)
        page = render_html(data)
        out_dir = os.path.join(CACHE_DIR, "output")
        os.makedirs(out_dir, exist_ok=True)
        filename = f"{message}_{version}.html"
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write(page)
        return page, filename

    try:
        page, filename = await run_in_threadpool(build)
    except EdifactError as exc:
        raise _fail(exc)
    return Response(
        content=page,
        media_type="text/html; charset=utf-8",
        headers={"X-Filename": filename, "Access-Control-Expose-Headers": "X-Filename"},
    )
