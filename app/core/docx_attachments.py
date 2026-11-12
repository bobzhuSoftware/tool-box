"""Extract embedded (OLE) attachments from a Word .docx/.docm package.

A .docx is a zip; embedded objects live under ``word/embeddings/`` and are tied
to the document by ``<o:OLEObject>`` elements in ``word/document.xml``:

* ``Package`` objects: OLE container whose ``\\x01Ole10Native`` stream holds
  ``label, source path, temp path, raw bytes`` of the original file.
* Acrobat objects: OLE container whose ``CONTENTS`` stream is the raw PDF.
* Office objects: stored as a plain .xlsx/.docx/.pptx part (or an OLE .xls/.doc/.ppt).

The original file name of Acrobat/Office objects is not stored in the object, so
it is best-effort recovered from the text drawn in the preview image (EMF).
"""
import io
import re
import struct
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import olefile

_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R_ID_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_OLE_OBJECT_RE = re.compile(r"<o:OLEObject\b[^>]*>", re.S)
_ATTR_RE = re.compile(r'([\w:]+)="([^"]*)"')
_IMAGEDATA_RE = re.compile(r"<v:imagedata\b[^>]*>", re.S)
_OBJECT_RE = re.compile(r"<w:object\b.*?</w:object>", re.S)

# Compound-file stream that identifies a whole legacy Office file.
_LEGACY_OFFICE = {"Workbook": ".xls", "Book": ".xls", "WordDocument": ".doc", "PowerPoint Document": ".ppt"}
_MAX_DOCX_BYTES = 300 * 1024 * 1024
_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class DocxAttachmentError(ValueError):
    """The input is not a readable docx package."""


@dataclass
class Attachment:
    index: int
    name: str
    data: bytes
    prog_id: str
    kind: str  # package | pdf | office | ole | unknown
    source_path: str = ""


def safe_filename(name: str, fallback: str) -> str:
    """Make ``name`` a valid single-component Windows file name."""
    name = _INVALID_NAME_CHARS.sub("_", name).strip(" .")
    name = re.sub(r"\s+", " ", name)
    if not name:
        return fallback
    stem, dot, ext = name.rpartition(".")
    if stem and ext and stem.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    elif not stem and name.upper() in _RESERVED:
        name = "_" + name
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + dot + ext) if dot and len(ext) < 10 else name[:150]
    return name


def _decode_ansi(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("mbcs")  # Windows ANSI code page
    except (LookupError, UnicodeDecodeError):
        return raw.decode("cp1252", errors="replace")


def parse_ole10native(raw: bytes) -> tuple[str, str, bytes]:
    """Return ``(file_name, source_path, data)`` from an ``\\x01Ole10Native`` stream."""

    def cstring(pos: int) -> tuple[str, int]:
        end = raw.index(b"\0", pos)
        return _decode_ansi(raw[pos:end]), end + 1

    pos = 6  # 4-byte total size + 2-byte flags
    name, pos = cstring(pos)
    source, pos = cstring(pos)
    pos += 4  # reserved + type
    (temp_len,) = struct.unpack_from("<I", raw, pos)
    pos += 4 + temp_len
    (data_len,) = struct.unpack_from("<I", raw, pos)
    pos += 4
    data = raw[pos : pos + data_len]
    if len(data) != data_len:
        raise ValueError("truncated Ole10Native data")
    return name, source, data


def emf_text(data: bytes) -> str:
    """Concatenate the UTF-16 text runs drawn by an EMF (the object's caption)."""
    parts: list[str] = []
    pos = 0
    while pos + 8 <= len(data):
        rec_type, rec_size = struct.unpack_from("<II", data, pos)
        if rec_size < 8 or pos + rec_size > len(data):
            break
        if rec_type == 84 and rec_size >= 52:  # EMR_EXTTEXTOUTW
            n_chars, off_string = struct.unpack_from("<II", data, pos + 44)
            start = pos + off_string
            chunk = data[start : start + n_chars * 2]
            if len(chunk) == n_chars * 2:
                parts.append(chunk.decode("utf-16-le", errors="replace"))
        pos += rec_size
    return "".join(parts).strip()


def _caption_name(caption: str) -> str:
    # Acrobat appends a "; 1" page suffix to the caption.
    return re.sub(r";\s*\d+$", "", caption.strip()).strip()


def _with_ext(caption: str, ext: str) -> str:
    name = _caption_name(caption) or "attachment"
    return name if name.lower().endswith(ext) else name + ext


def _load_rels(zf: zipfile.ZipFile) -> dict[str, str]:
    try:
        root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    return {rel.get("Id"): rel.get("Target", "") for rel in root.iter(f"{_REL_NS}Relationship")}


def _resolve(target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else f"word/{target}"


def _read_member(zf: zipfile.ZipFile, path: str) -> bytes | None:
    try:
        info = zf.getinfo(path)
    except KeyError:
        return None
    if info.file_size > _MAX_DOCX_BYTES:
        return None
    return zf.read(path)


def _from_ole(data: bytes, prog_id: str, caption: str) -> tuple[str, bytes, str, str] | None:
    """Return ``(name, payload, kind, source_path)`` for an OLE container, or None."""
    if not olefile.isOleFile(data):
        return None
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if ole.exists("\x01Ole10Native"):
            name, source, payload = parse_ole10native(ole.openstream("\x01Ole10Native").read())
            return name, payload, "package", source
        if ole.exists("CONTENTS"):
            payload = ole.openstream("CONTENTS").read()
            if payload.startswith(b"%PDF"):
                return _with_ext(caption, ".pdf"), payload, "pdf", ""
        for stream, ext in _LEGACY_OFFICE.items():
            if ole.exists(stream):
                return _with_ext(caption, ext), data, "office", ""
    finally:
        ole.close()
    return None


def _picture_caption(zf: zipfile.ZipFile, rels: dict[str, str], block: str) -> str:
    match = _IMAGEDATA_RE.search(block)
    if not match:
        return ""
    rid = dict(_ATTR_RE.findall(match.group(0))).get("r:id")
    target = rels.get(rid or "")
    if not target or not target.lower().endswith(".emf"):
        return ""
    emf = _read_member(zf, _resolve(target))
    return emf_text(emf) if emf else ""


def extract_attachments(docx_bytes: bytes) -> list[Attachment]:
    """Return every embedded object of a .docx in document order."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        document = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as e:
        raise DocxAttachmentError("不是有效的 .docx 文件（需要 .docx / .docm，不支持旧版 .doc）。") from e

    rels = _load_rels(zf)
    results: list[Attachment] = []
    seen_targets: set[str] = set()

    for block_match in _OBJECT_RE.finditer(document):
        block = block_match.group(0)
        ole_tag = _OLE_OBJECT_RE.search(block)
        if not ole_tag:
            continue
        attrs = dict(_ATTR_RE.findall(ole_tag.group(0)))
        target = rels.get(attrs.get("r:id", ""))
        if not target or attrs.get("Type") == "Link":
            continue
        part = _resolve(target)
        payload_raw = _read_member(zf, part)
        if payload_raw is None:
            continue
        seen_targets.add(part)
        prog_id = attrs.get("ProgID", "")
        caption = _picture_caption(zf, rels, block)
        base = part.rsplit("/", 1)[-1]

        extracted = _from_ole(payload_raw, prog_id, caption) if base.lower().endswith(".bin") else None
        if extracted:
            name, payload, kind, source = extracted
        else:
            # Plain Office part (xlsx/docx/pptx) or an unrecognised container.
            ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
            cap = _caption_name(caption)
            if ext == ".bin":
                name, kind = cap or base, "unknown"
            else:
                name, kind = (cap if cap.lower().endswith(ext) else base), "office"
            payload, source = payload_raw, ""
        results.append(Attachment(0, name, payload, prog_id, kind, source))

    # Embeddings present in the package but not referenced from the body.
    for info in zf.infolist():
        if info.filename.startswith("word/embeddings/") and info.filename not in seen_targets and not info.is_dir():
            payload_raw = _read_member(zf, info.filename)
            if payload_raw is None:
                continue
            base = info.filename.rsplit("/", 1)[-1]
            extracted = _from_ole(payload_raw, "", "") if base.lower().endswith(".bin") else None
            if extracted:
                name, payload, kind, source = extracted
            else:
                name, payload, kind, source = base, payload_raw, "unknown", ""
            results.append(Attachment(0, name, payload, "", kind, source))

    used: set[str] = set()
    for i, att in enumerate(results, start=1):
        att.index = i
        name = safe_filename(att.name, f"attachment_{i}")
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        candidate, n = name, 2
        while candidate.lower() in used:
            candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
            n += 1
        used.add(candidate.lower())
        att.name = candidate
    return results
