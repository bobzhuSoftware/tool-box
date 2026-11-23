"""Per-Map-Instruction progress of GEFEG guidelines: keys, version matching, legacy migration."""
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.db import (  # noqa: E402
    Base, GefegGuideline, GefegMapping, GefegMappingProgress, GefegNode, GefegNodeProgress,
)
from app.core.gefeg_progress import (  # noqa: E402
    mapping_keys, match_rows, migrate_node_progress, snapshot_of,
)


def row(path, key, snap):
    return SimpleNamespace(node_path=path, mapping_key=key, snapshot=snap)


def test_mapping_keys_use_number_then_position_and_stay_unique():
    assert mapping_keys(["1", "", "3"]) == ["1", "2", "3"]
    assert mapping_keys(["1", "1"]) == ["1", "1~2"]


def test_snapshot_ignores_whitespace():
    assert snapshot_of("a  b", "x\ny", "") == snapshot_of("a b", "x y", "")


def test_match_prefers_same_key_then_same_content_then_changed_content():
    a, b, c = snapshot_of("", "A", ""), snapshot_of("", "B", ""), snapshot_of("", "C", "")
    dst = {"p": {"1": snapshot_of("", "NEW", ""), "2": a, "3": c}}
    pairs = match_rows([row("p", "1", a), row("p", "2", b), row("p", "3", c)], dst)
    # "A" was renumbered 1 -> 2; "B" no longer exists and its key 2 is taken; "C" is unchanged
    assert [(r.mapping_key, key) for r, key in pairs] == [("1", "2"), ("3", "3")]


def test_match_falls_back_to_same_key_with_changed_content():
    old = snapshot_of("", "old", "")
    pairs = match_rows([row("p", "1", old), row("gone", "1", old)], {"p": {"1": snapshot_of("", "new", "")}})
    assert [(r.node_path, key) for r, key in pairs] == [("p", "1")]


def test_migration_splits_node_progress_per_instruction_and_keeps_review():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    old = [
        {"nr": "1", "condition": "c1", "instruction": "i1", "additional_info": ""},
        {"nr": "2", "condition": "c2", "instruction": "i2 (old)", "additional_info": ""},
    ]
    with Session(engine) as s:
        s.add(GefegGuideline(id="g", user_id="u", filename="f", file_sha256="x"))
        s.add(GefegNode(guideline_id="g", seq=1, depth=0, kind="element", name="N", path="a/N"))
        s.add_all([
            GefegMapping(guideline_id="g", node_seq=1, position=0, nr="1", condition="c1", instruction="i1"),
            GefegMapping(guideline_id="g", node_seq=1, position=1, nr="2", condition="c2", instruction="i2"),
        ])
        s.add(GefegNodeProgress(guideline_id="g", node_path="a/N", status="done", note="hello",
                                snapshot=json.dumps(old)))
        s.commit()

    migrate_node_progress(engine)
    migrate_node_progress(engine)  # idempotent

    with Session(engine) as s:
        rows = {r.mapping_key: r for r in s.scalars(select(GefegMappingProgress))}
        assert s.scalar(select(GefegNodeProgress.id)) is None
    assert {k: (r.status, r.note) for k, r in rows.items()} == {"1": ("done", "hello"), "2": ("done", "")}
    assert rows["1"].snapshot == snapshot_of("c1", "i1", "")  # unchanged since marked
    assert rows["2"].snapshot == snapshot_of("c2", "i2 (old)", "")  # differs from now -> needs review
