"""Pure logic of GEFEG question threads: message validation, export-file parsing and merging
of the BA's replies (no database access)."""
import json
import re
import uuid
from datetime import datetime, timezone

MAX_TEXT = 8000
MAX_IMAGES = 6
MAX_IMAGE_CHARS = 3_000_000  # per image, as a base64 data URL
EXPORT_FORMAT = "gefeg-questions"

_DATA_URL = re.compile(r"^data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")
_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_DATA_SCRIPT = re.compile(
    r'<script[^>]*\bid="gfg-data"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)


class QuestionError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_images(images) -> list[str]:
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise QuestionError(f"At most {MAX_IMAGES} images per message")
    for img in images:
        if not isinstance(img, str) or len(img) > MAX_IMAGE_CHARS or not _DATA_URL.match(img):
            raise QuestionError("Images must be PNG/JPEG/WEBP/GIF data URLs smaller than ~2 MB")
    return images


def new_message(by: str, text: str, images: list[str]) -> dict:
    text = (text or "").strip()
    images = clean_images(images or [])
    if not text and not images:
        raise QuestionError("A message needs some text or an image")
    if len(text) > MAX_TEXT:
        raise QuestionError("Message is too long")
    return {"id": uuid.uuid4().hex, "by": by, "text": text, "images": images, "at": now_iso()}


def _clean_incoming(raw) -> dict:
    """Validate one message coming from an uploaded file. Only BA messages are accepted."""
    if not isinstance(raw, dict) or raw.get("by") != "ba":
        raise QuestionError("Unexpected message")
    mid = raw.get("id")
    if not isinstance(mid, str) or not _ID.match(mid):
        raise QuestionError("Invalid message id")
    text = raw.get("text", "")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise QuestionError("Invalid message text")
    at = raw.get("at")
    return {
        "id": mid, "by": "ba", "text": text.strip(),
        "images": clean_images(raw.get("images") or []),
        "at": at if isinstance(at, str) and len(at) <= 40 else now_iso(),
    }


def merge_messages(existing: list[dict], incoming) -> tuple[list[dict], int]:
    """Append the incoming BA messages that are not known yet (matched by id).
    Invalid or non-BA messages are skipped. Returns (merged list, number added)."""
    merged = list(existing)
    known = {m["id"] for m in merged}
    added = 0
    for raw in incoming if isinstance(incoming, list) else []:
        try:
            msg = _clean_incoming(raw)
        except QuestionError:
            continue
        if msg["id"] in known or not (msg["text"] or msg["images"]):
            continue
        known.add(msg["id"])
        merged.append(msg)
        added += 1
    return merged, added


def extract_export(content: bytes) -> dict:
    """Read the data block of an exported HTML file (or a bare JSON file)."""
    text = content.decode("utf-8", errors="replace").lstrip("\ufeff")
    if not text.lstrip().startswith("{"):
        match = _DATA_SCRIPT.search(text)
        if not match:
            raise QuestionError("This file does not look like an exported GEFEG questions file")
        text = match.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        raise QuestionError("The data inside the file is damaged")
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise QuestionError("This file does not look like an exported GEFEG questions file")
    return data
