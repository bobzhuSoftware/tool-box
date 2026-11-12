"""Word attachment extractor — list and download the embedded objects of a .docx."""
import io
import os
import threading
import uuid
import zipfile
from collections import OrderedDict
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.docx_attachments import (
    Attachment,
    DocxAttachmentError,
    extract_attachments,
    safe_filename,
)

router = APIRouter()

_MAX_DOCX_BYTES = 300 * 1024 * 1024
_ALLOWED_EXTS = {".docx", ".docm"}
_CACHE_LIMIT = 5

# id -> (owner user id, source docx path or "", docx file name, attachments)
_cache: "OrderedDict[str, tuple[str, str, str, list[Attachment]]]" = OrderedDict()
_cache_lock = threading.Lock()


class SaveRequest(BaseModel):
    folder: str = ""
    indexes: list[int] = []  # empty = all


def _get_entry(doc_id: str, user: User):
    with _cache_lock:
        entry = _cache.get(doc_id)
    if not entry or entry[0] != user.id:
        raise HTTPException(status_code=404, detail="记录已失效，请重新解析文档。")
    return entry


def _attachment_response(data: bytes, name: str, media_type: str) -> Response:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'
        },
    )


@router.post("/api/docx-attachments")
async def list_docx_attachments(
    file: UploadFile | None = File(None),
    path: str = Form(""),
    user: User = Depends(require_user),
):
    """Parse a .docx (uploaded, or read from a local path) and list its attachments."""
    source_path = ""
    path = path.strip().strip('"').strip("'")
    if file is not None and file.filename:
        filename = os.path.basename(file.filename)
        data = await file.read()
    elif path:
        if not os.path.isfile(path):
            raise HTTPException(status_code=400, detail=f"文件不存在: {path}")
        source_path = os.path.abspath(path)
        filename = os.path.basename(source_path)
        if os.path.getsize(source_path) > _MAX_DOCX_BYTES:
            raise HTTPException(status_code=413, detail="文件过大。")
        try:
            with open(source_path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法读取文件: {e}")
    else:
        raise HTTPException(status_code=400, detail="请上传文件或填写文件路径。")

    if os.path.splitext(filename)[1].lower() not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 .docx / .docm 文件。")
    if len(data) > _MAX_DOCX_BYTES:
        raise HTTPException(status_code=413, detail="文件过大。")

    try:
        attachments = extract_attachments(data)
    except DocxAttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析失败: {e}")

    doc_id = uuid.uuid4().hex[:12]
    with _cache_lock:
        _cache[doc_id] = (user.id, source_path, filename, attachments)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)

    return {
        "id": doc_id,
        "filename": filename,
        "source_path": source_path,
        "attachments": [
            {
                "index": a.index,
                "name": a.name,
                "size": len(a.data),
                "kind": a.kind,
                "prog_id": a.prog_id,
                "source_path": a.source_path,
            }
            for a in attachments
        ],
    }


@router.get("/api/docx-attachments/{doc_id}/zip")
def download_all_attachments(doc_id: str, user: User = Depends(require_user)):
    _, _, filename, attachments = _get_entry(doc_id, user)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for a in attachments:
            zf.writestr(a.name, a.data)
    stem = os.path.splitext(filename)[0]
    return _attachment_response(buf.getvalue(), safe_filename(stem, "attachments") + "_attachments.zip", "application/zip")


@router.get("/api/docx-attachments/{doc_id}/{index}")
def download_attachment(doc_id: str, index: int, user: User = Depends(require_user)):
    _, _, _, attachments = _get_entry(doc_id, user)
    att = next((a for a in attachments if a.index == index), None)
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在。")
    return _attachment_response(att.data, att.name, "application/octet-stream")


@router.post("/api/docx-attachments/{doc_id}/save")
def save_attachments(doc_id: str, req: SaveRequest, user: User = Depends(require_user)):
    """Write attachments to a local folder (defaults to ``<docx name>_attachments`` beside the docx)."""
    _, source_path, filename, attachments = _get_entry(doc_id, user)
    folder = req.folder.strip().strip('"').strip("'")
    if not folder:
        if not source_path:
            raise HTTPException(status_code=400, detail="请填写保存文件夹。")
        stem = os.path.splitext(filename)[0]
        folder = os.path.join(os.path.dirname(source_path), safe_filename(stem, "docx") + "_attachments")
    folder = os.path.abspath(folder)
    wanted = set(req.indexes)
    selected = [a for a in attachments if not wanted or a.index in wanted]
    try:
        os.makedirs(folder, exist_ok=True)
        saved = []
        for a in selected:
            # a.name is already a sanitized single path component.
            target = os.path.join(folder, a.name)
            with open(target, "wb") as f:
                f.write(a.data)
            saved.append(a.name)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"保存失败: {e}")
    return {"folder": folder, "saved": saved}
