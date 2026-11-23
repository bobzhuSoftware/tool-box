"""GEFEG question threads: reply merging and the exported HTML data block (no database)."""
import json

import pytest

from app.core.gefeg_export import render_export_html
from app.core.gefeg_questions import EXPORT_FORMAT, QuestionError, extract_export, merge_messages, new_message

PNG = "data:image/png;base64,iVBORw0KGgo="


def _ba(mid, text="ok", at="2026-01-02T00:00:00+00:00", **kw):
    return {"id": mid, "by": "ba", "text": text, "images": [], "at": at, **kw}


def test_merge_is_idempotent_and_append_only():
    dev = new_message("dev", "Which qualifier?", [])
    merged, added = merge_messages([dev], [_ba("reply-0001", "Use 137")])
    assert added == 1 and [m["by"] for m in merged] == ["dev", "ba"]

    again, added = merge_messages(merged, [_ba("reply-0001", "changed text")])
    assert added == 0 and again == merged


def test_merge_skips_dev_messages_and_bad_images():
    incoming = [
        {**_ba("dev-00001"), "by": "dev"},
        _ba("bad-image-1", images=["javascript:alert(1)"]),
        _ba("good-img-1", text="", images=[PNG]),
        _ba("empty-0001", text=""),
    ]
    merged, added = merge_messages([], incoming)
    assert added == 1 and merged[0]["id"] == "good-img-1"


def test_new_message_needs_content():
    with pytest.raises(QuestionError):
        new_message("dev", "  ", [])


def test_export_block_survives_hostile_text_and_round_trips():
    payload = {
        "format": EXPORT_FORMAT, "version": 1, "export_id": "x", "exported_at": "t", "file_name": "a.html",
        "guideline": {"title": "T </script><b>", "filename": "f"},
        "nodes": [], "questions": [{"id": "q1", "messages": [_ba("m-12345678", "</script><img src=x onerror=alert(1)> & é")]}],
    }
    page = render_export_html(payload)
    block = page.split('id="gfg-data" type="application/json">')[1].split("</script>")[0]
    assert "<" not in block and ">" not in block
    assert extract_export(page.encode()) == json.loads(json.dumps(payload))


def test_extract_rejects_foreign_files():
    with pytest.raises(QuestionError):
        extract_export(b"<html><body>hello</body></html>")
    with pytest.raises(QuestionError):
        extract_export(b'{"format": "something-else"}')
