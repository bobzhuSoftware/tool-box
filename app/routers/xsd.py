"""XSD structure viewer: upload .xsd files or a .zip of them and render the
schema as a standalone HTML tree (see app.core.xsd_parser). Nothing is stored."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.core.auth import require_user
from app.core.db import User
from app.core.xsd_html import render_html
from app.core.xsd_parser import MAX_TOTAL_BYTES, XsdError, parse_xsd

router = APIRouter()


@router.post("/api/xsd/html")
async def xsd_html(files: list[UploadFile] = File(...), user: User = Depends(require_user)):
    """Return the standalone HTML for the uploaded schema set. Warnings
    (e.g. unresolved type references) are sent URL-encoded in ``X-Warnings``."""
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read(MAX_TOTAL_BYTES + 1)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="上传文件过大（总计 ≤ 50 MB）")
        uploads.append((f.filename or "schema.xsd", data))

    def build() -> tuple[str, str, list[str]]:
        model = parse_xsd(uploads)
        root_name = next((r["name"] for r in model["roots"] if r["key"] == model["default"]), "schema")
        return render_html(model, f"{root_name} · XSD 结构"), root_name, model["warnings"]

    try:
        page, root_name, warnings = await run_in_threadpool(build)
    except XsdError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    return Response(
        content=page,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Filename": quote(f"{root_name}_structure.html"),
            "X-Warnings": quote("\n".join(warnings[:20])),
            "Access-Control-Expose-Headers": "X-Filename, X-Warnings",
        },
    )
