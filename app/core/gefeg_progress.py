"""Pure helpers for per-Map-Instruction implementation progress of a GEFEG guideline:
instruction keys, content snapshots, matching between two versions, and the one-time
migration of the legacy per-node progress rows."""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def snapshot_of(condition: str, instruction: str, additional_info: str) -> str:
    """Content of one instruction as compared for "needs review"; the red change flag and the
    instruction number are left out on purpose."""
    return json.dumps({
        "condition": collapse(condition), "instruction": collapse(instruction),
        "additional_info": collapse(additional_info),
    }, ensure_ascii=False, sort_keys=True)


def mapping_keys(nrs: list[str]) -> list[str]:
    """Stable key of each instruction of one node: its number, else its 1-based position.
    A duplicated number gets the position appended so keys stay unique."""
    seen: set[str] = set()
    out = []
    for i, nr in enumerate(nrs, 1):
        key = nr or str(i)
        if key in seen:
            key = f"{key}~{i}"
        seen.add(key)
        out.append(key)
    return out


def match_rows(src_rows, dst: dict[str, dict[str, str]]) -> list[tuple[object, str]]:
    """Map saved rows (``node_path``, ``mapping_key``, ``snapshot``) of an old version onto the
    instructions of a new one (``dst``: path -> key -> snapshot). Same key with identical content
    wins, then identical content under another key (instructions were renumbered), then the same
    key with changed content (it will need review)."""
    taken: set[tuple[str, str]] = set()
    matched: dict[int, str] = {}
    rest = []
    for i, r in enumerate(src_rows):
        if dst.get(r.node_path, {}).get(r.mapping_key) == r.snapshot:
            matched[i] = r.mapping_key
            taken.add((r.node_path, r.mapping_key))
        else:
            rest.append(i)
    later = []
    for i in rest:
        r = src_rows[i]
        key = next((k for k, s in dst.get(r.node_path, {}).items()
                    if s == r.snapshot and (r.node_path, k) not in taken), None)
        if key is None:
            later.append(i)
        else:
            matched[i] = key
            taken.add((r.node_path, key))
    for i in later:
        r = src_rows[i]
        if r.mapping_key in dst.get(r.node_path, {}) and (r.node_path, r.mapping_key) not in taken:
            matched[i] = r.mapping_key
            taken.add((r.node_path, r.mapping_key))
    return [(src_rows[i], matched[i]) for i in sorted(matched)]


def migrate_node_progress(engine) -> None:
    """Turn legacy per-node rows into per-instruction rows (idempotent: the legacy rows are
    deleted once converted). done/question apply to every instruction of the node, the note goes
    to the first one, and each instruction keeps the content it had when the node was marked, so
    "needs review" survives the migration."""
    from app.core.db import GefegMapping, GefegMappingProgress, GefegNode, GefegNodeProgress

    with Session(engine) as session:
        legacy = session.scalars(select(GefegNodeProgress)).all()
        if not legacy:
            return
        for row in legacy:
            seq = session.scalar(select(GefegNode.seq).where(
                GefegNode.guideline_id == row.guideline_id, GefegNode.path == row.node_path))
            mappings = [] if seq is None else session.scalars(
                select(GefegMapping).where(
                    GefegMapping.guideline_id == row.guideline_id, GefegMapping.node_seq == seq,
                ).order_by(GefegMapping.position)).all()
            try:
                old = json.loads(row.snapshot)
            except ValueError:
                old = []
            if not isinstance(old, list):
                old = []
            keys = mapping_keys([m.nr for m in mappings])
            for i, (m, key) in enumerate(zip(mappings, keys)):
                status = row.status or ""
                note = (row.note or "") if i == 0 else ""
                if not status and not note:
                    continue
                prev = next((o for o in old if m.nr and o.get("nr") == m.nr), None)
                if prev is None and not m.nr and i < len(old):
                    prev = old[i]
                if prev is not None:
                    snap = snapshot_of(prev.get("condition", ""), prev.get("instruction", ""),
                                       prev.get("additional_info", ""))
                elif status == "done" and old:
                    snap = "{}"  # added after it was marked: always needs review
                else:
                    snap = snapshot_of(m.condition, m.instruction, m.additional_info)
                session.add(GefegMappingProgress(
                    guideline_id=row.guideline_id, node_path=row.node_path, mapping_key=key,
                    status=status, note=note, snapshot=snap,
                    updated_at=row.updated_at or datetime.now(timezone.utc),
                ))
        session.execute(delete(GefegNodeProgress))
        session.commit()
