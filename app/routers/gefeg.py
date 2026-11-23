"""GEFEG.FX guideline viewer: upload a "Message Implementation Guideline" PDF, parse it
(see app.core.gefeg_parser) and store it in the DB so it can be browsed/extended later.

Implementation progress is tracked per Map Instruction (a node's status is derived from its
instructions) and keyed by field *path* + instruction key, so it can be carried over to a newer
version of the same model. A "done" instruction whose content differs from what it was when it
was marked is reported as ``needs_review``."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.db import (
    GefegChange, GefegGuideline, GefegMapping, GefegMappingProgress, GefegNode, GefegNodeProgress,
    GefegQuestion, User, engine,
)
from app.core.gefeg_export import render_export_html
from app.core.gefeg_parser import MAX_PDF_BYTES, GefegError, parse_gefeg_pdf
from app.core.gefeg_progress import mapping_keys, match_rows, migrate_node_progress, snapshot_of
from app.core.gefeg_questions import (
    EXPORT_FORMAT, MAX_TEXT, QuestionError, extract_export, merge_messages, new_message, now_iso,
)

router = APIRouter()

migrate_node_progress(engine)

MAX_IMPORT_BYTES = 60 * 1024 * 1024

_META_FIELDS = ("id", "filename", "title", "model", "version", "variant", "issue_date",
                "print_date", "author", "page_count")


# --------------------------------------------------------------------------- progress helpers
def _grouped_mappings(session: Session, guideline_id: str) -> dict[int, list[dict]]:
    """Map Instructions per node seq in document order, each with its stable ``key``."""
    grouped: dict[int, list[GefegMapping]] = {}
    for m in session.scalars(
        select(GefegMapping).where(GefegMapping.guideline_id == guideline_id)
        .order_by(GefegMapping.node_seq, GefegMapping.position)
    ):
        grouped.setdefault(m.node_seq, []).append(m)
    return {
        seq: [{
            "key": key, "nr": m.nr, "flag": m.flag, "condition": m.condition,
            "instruction": m.instruction, "additional_info": m.additional_info,
        } for key, m in zip(mapping_keys([m.nr for m in ms]), ms)]
        for seq, ms in grouped.items()
    }


class _Progress:
    """Progress state of one guideline: node paths, current instruction snapshots, saved rows."""

    def __init__(self, session: Session, guideline_id: str):
        self.paths: dict[int, str] = dict(session.execute(
            select(GefegNode.seq, GefegNode.path).where(GefegNode.guideline_id == guideline_id)
        ).all())
        self.mappings = _grouped_mappings(session, guideline_id)
        # current content per instruction: seq -> key -> snapshot
        self.snapshots: dict[int, dict[str, str]] = {
            seq: {m["key"]: snapshot_of(m["condition"], m["instruction"], m["additional_info"]) for m in ms}
            for seq, ms in self.mappings.items() if seq in self.paths
        }
        self.mapped_paths: dict[str, int] = {self.paths[s]: s for s in self.snapshots}
        self.rows: dict[tuple[str, str], GefegMappingProgress] = {
            (r.node_path, r.mapping_key): r for r in session.scalars(
                select(GefegMappingProgress).where(GefegMappingProgress.guideline_id == guideline_id))
        }

    def _row(self, seq: int, key: str) -> GefegMappingProgress | None:
        return self.rows.get((self.paths.get(seq, ""), key))

    def needs_review(self, seq: int, key: str) -> bool:
        row = self._row(seq, key)
        return bool(row and row.status == "done" and row.snapshot != self.snapshots[seq].get(key))

    def view(self, seq: int, key: str) -> dict | None:
        row = self._row(seq, key)
        if row is None:
            return None
        review = self.needs_review(seq, key)
        out = {
            "status": row.status, "note": row.note, "needs_review": review,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        if review:
            try:
                out["previous"] = json.loads(row.snapshot)
            except ValueError:
                out["previous"] = {}
        return out

    def counts(self) -> dict:
        done = question = review = total = 0
        for seq, snaps in self.snapshots.items():
            for key in snaps:
                total += 1
                row = self._row(seq, key)
                if not row or not row.status:
                    continue
                if row.status == "question":
                    question += 1
                elif self.needs_review(seq, key):
                    review += 1
                else:
                    done += 1
        return {"total": total, "done": done, "question": question, "review": review,
                "todo": total - done - question - review}


def _summary(session: Session, g: GefegGuideline) -> dict:
    nodes = session.scalar(select(func.count(GefegNode.id)).where(GefegNode.guideline_id == g.id))
    changes = session.scalar(select(func.count(GefegChange.id)).where(GefegChange.guideline_id == g.id))
    return {
        **{f: getattr(g, f) for f in _META_FIELDS},
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "node_count": nodes,
        "change_count": changes,
        "progress": _Progress(session, g.id).counts(),
    }


def _plan_inherit(src: _Progress, dst: _Progress) -> dict:
    """Which saved rows of ``src`` can be carried over to ``dst`` (matched by path and
    instruction key / content)."""
    saved = [r for r in src.rows.values() if r.status or r.note]
    dst_snaps = {dst.paths[s]: snaps for s, snaps in dst.snapshots.items()}
    pairs = match_rows(saved, dst_snaps)
    return {
        "pairs": pairs,
        "inheritable": len(pairs),
        "review": sum(1 for r, key in pairs if r.status == "done" and r.snapshot != dst_snaps[r.node_path][key]),
        "missing": len(saved) - len(pairs),
        "overlap": round(len(set(src.mapped_paths) & set(dst.mapped_paths)) / max(len(dst.mapped_paths), 1), 3),
    }


def _inherit_candidates(session: Session, user: User, g: GefegGuideline, same_model: bool = True) -> list[dict]:
    """Other guidelines that progress can be inherited from. By default only the same
    Model (= the same spec in another version/variant)."""
    if same_model and not g.model:
        return []
    dst = _Progress(session, g.id)
    query = select(GefegGuideline).where(GefegGuideline.user_id == user.id, GefegGuideline.id != g.id)
    if same_model:
        query = query.where(GefegGuideline.model == g.model)
    others = session.scalars(query.order_by(GefegGuideline.created_at.desc())).all()
    out = []
    for o in others:
        src = _Progress(session, o.id)
        plan = _plan_inherit(src, dst)
        out.append({
            "id": o.id, "model": o.model, "version": o.version, "variant": o.variant,
            "issue_date": o.issue_date, "progress": src.counts(), "inheritable": plan["inheritable"],
            "review": plan["review"], "missing": plan["missing"], "overlap": plan["overlap"],
        })
    return out


def _owned(session: Session, guideline_id: str, user: User) -> GefegGuideline:
    g = session.get(GefegGuideline, guideline_id)
    if g is None or g.user_id != user.id:
        raise HTTPException(status_code=404, detail="Guideline not found")
    return g


def _store(user: User, filename: str, sha: str, parsed: dict) -> str:
    meta = parsed["meta"]
    with Session(engine) as session:
        g = GefegGuideline(
            user_id=user.id, filename=filename, file_sha256=sha,
            title=meta["title"], model=meta["model"], version=meta["version"],
            variant=meta["variant"], issue_date=meta["issue_date"],
            print_date=meta["print_date"], author=meta["author"],
            page_count=meta["page_count"],
        )
        session.add(g)
        session.flush()
        for n in parsed["nodes"]:
            session.add(GefegNode(
                guideline_id=g.id, seq=n["seq"], parent_seq=n["parent_seq"], depth=n["depth"],
                kind=n["kind"], name=n["name"], path=n["path"], min_occurs=n["min_occurs"],
                max_occurs=n["max_occurs"], type=n["type"], use=n["use"], notes=n["notes"],
                page=n["page"],
            ))
            for m in n["mappings"]:
                session.add(GefegMapping(
                    guideline_id=g.id, node_seq=n["seq"], position=m["position"], nr=m["nr"],
                    flag=m["flag"], condition=m["condition"], instruction=m["instruction"],
                    additional_info=m["additional_info"],
                ))
        for c in parsed["changes"]:
            session.add(GefegChange(
                guideline_id=g.id, no=c["no"], node_seq=c["node_seq"], tag=c["tag"], path=c["path"],
                number=c["number"], flag=c["flag"], condition=c["condition"],
                instruction=c["instruction"], additional_info=c["additional_info"], notes=c["notes"],
            ))
        session.commit()
        return g.id


@router.post("/api/gefeg/upload")
async def gefeg_upload(file: UploadFile = File(...), user: User = Depends(require_user)):
    """Parse and save a GEFEG.FX PDF. Re-uploading the identical file returns the
    existing record (``duplicate: true``) instead of creating another one."""
    data = await file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF file is too large (max 30 MB)")
    sha = hashlib.sha256(data).hexdigest()

    def work() -> tuple[str, bool]:
        with Session(engine) as session:
            existing = session.scalar(
                select(GefegGuideline).where(
                    GefegGuideline.user_id == user.id, GefegGuideline.file_sha256 == sha
                )
            )
            if existing:
                return existing.id, True
        parsed = parse_gefeg_pdf(data)
        return _store(user, file.filename or "guideline.pdf", sha, parsed), False

    try:
        guideline_id, duplicate = await run_in_threadpool(work)
    except GefegError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    with Session(engine) as session:
        g = session.get(GefegGuideline, guideline_id)
        candidates = [] if duplicate else _inherit_candidates(session, user, g)
        return {**_summary(session, g), "duplicate": duplicate, "inherit_candidates": candidates}


@router.get("/api/gefeg")
def gefeg_list(user: User = Depends(require_user)):
    with Session(engine) as session:
        rows = session.scalars(
            select(GefegGuideline)
            .where(GefegGuideline.user_id == user.id)
            .order_by(GefegGuideline.created_at.desc())
        ).all()
        return {"guidelines": [_summary(session, g) for g in rows]}


def _tree_nodes(session: Session, g: GefegGuideline) -> list[dict]:
    """Nodes in document order, each with its Map Instructions."""
    mappings = _grouped_mappings(session, g.id)
    nodes = session.scalars(
        select(GefegNode).where(GefegNode.guideline_id == g.id).order_by(GefegNode.seq)
    ).all()
    return [{
        "seq": n.seq, "parent_seq": n.parent_seq, "depth": n.depth, "kind": n.kind,
        "name": n.name, "path": n.path, "min_occurs": n.min_occurs,
        "max_occurs": n.max_occurs, "type": n.type, "use": n.use, "notes": n.notes,
        "page": n.page, "mappings": mappings.get(n.seq, []),
    } for n in nodes]


@router.get("/api/gefeg/{guideline_id}")
def gefeg_detail(guideline_id: str, user: User = Depends(require_user)):
    """Full guideline: meta, node tree (document order, with mappings), change summary and questions."""
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        nodes = _tree_nodes(session, g)
        changes = session.scalars(
            select(GefegChange).where(GefegChange.guideline_id == g.id).order_by(GefegChange.id)
        ).all()
        progress = _Progress(session, g.id)
        return {
            "guideline": _summary(session, g),
            "nodes": [{
                **n,
                "mappings": [{**m, "progress": progress.view(n["seq"], m["key"])} for m in n["mappings"]],
            } for n in nodes],
            "changes": [{
                "no": c.no, "node_seq": c.node_seq, "tag": c.tag, "path": c.path,
                "number": c.number, "flag": c.flag, "condition": c.condition,
                "instruction": c.instruction, "additional_info": c.additional_info,
                "notes": c.notes,
            } for c in changes],
            "questions": _question_views(session, g.id, progress.paths),
        }


# --------------------------------------------------------------------------- questions
def _question_view(q: GefegQuestion, seq_by_path: dict[str, int]) -> dict:
    return {
        "id": q.id, "node_seq": seq_by_path.get(q.node_path), "node_path": q.node_path,
        "mapping_nr": q.mapping_nr, "status": q.status, "messages": json.loads(q.messages),
        "created_at": q.created_at.isoformat() + "Z" if q.created_at else None,
        "updated_at": q.updated_at.isoformat() + "Z" if q.updated_at else None,
    }


def _seq_by_path(paths: dict[int, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for seq, path in paths.items():
        out.setdefault(path, seq)
    return out


def _question_views(session: Session, guideline_id: str, paths: dict[int, str]) -> list[dict]:
    seq_by_path = _seq_by_path(paths)
    rows = session.scalars(
        select(GefegQuestion).where(GefegQuestion.guideline_id == guideline_id)
        .order_by(GefegQuestion.created_at)
    ).all()
    return [v for v in (_question_view(q, seq_by_path) for q in rows) if v["node_seq"] is not None]


def _node_paths(session: Session, guideline_id: str) -> dict[int, str]:
    return dict(session.execute(
        select(GefegNode.seq, GefegNode.path).where(GefegNode.guideline_id == guideline_id)
    ).all())


def _owned_question(session: Session, g: GefegGuideline, question_id: str) -> GefegQuestion:
    q = session.get(GefegQuestion, question_id)
    if q is None or q.guideline_id != g.id:
        raise HTTPException(status_code=404, detail="Question not found")
    return q


class QuestionCreate(BaseModel):
    seq: int
    mapping_nr: str = Field(default="", max_length=20)
    text: str = Field(default="", max_length=MAX_TEXT)
    images: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    text: str = Field(default="", max_length=MAX_TEXT)
    images: list[str] = Field(default_factory=list)


class QuestionPatch(BaseModel):
    status: Literal["open", "closed"]


def _build_message(text: str, images: list[str]) -> dict:
    try:
        return new_message("dev", text, images)
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/gefeg/{guideline_id}/questions")
def gefeg_question_create(guideline_id: str, req: QuestionCreate, user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        paths = _node_paths(session, g.id)
        if req.seq not in paths:
            raise HTTPException(status_code=404, detail="Field not found")
        message = _build_message(req.text, req.images)
        q = GefegQuestion(
            guideline_id=g.id, node_path=paths[req.seq], mapping_nr=req.mapping_nr.strip(),
            messages=json.dumps([message], ensure_ascii=False),
        )
        session.add(q)
        session.commit()
        return _question_view(q, _seq_by_path(paths))


@router.post("/api/gefeg/{guideline_id}/questions/{question_id}/messages")
def gefeg_question_message(guideline_id: str, question_id: str, req: MessageCreate,
                           user: User = Depends(require_user)):
    """Developer follow-up; the question waits for the BA again."""
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        q = _owned_question(session, g, question_id)
        q.messages = json.dumps(
            [*json.loads(q.messages), _build_message(req.text, req.images)], ensure_ascii=False)
        q.status = "open"
        q.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _question_view(q, _seq_by_path(_node_paths(session, g.id)))


def _editable_message(q: GefegQuestion, message_id: str) -> tuple[list[dict], int]:
    """Developer messages of an open/answered question; closed questions stay as a record."""
    if q.status == "closed":
        raise HTTPException(status_code=409, detail="Reopen the question before changing its messages")
    messages = json.loads(q.messages)
    for i, m in enumerate(messages):
        if m["id"] == message_id:
            if m.get("by") != "dev":
                raise HTTPException(status_code=403, detail="Only your own messages can be changed")
            return messages, i
    raise HTTPException(status_code=404, detail="Message not found")


@router.patch("/api/gefeg/{guideline_id}/questions/{question_id}/messages/{message_id}")
def gefeg_message_edit(guideline_id: str, question_id: str, message_id: str, req: MessageCreate,
                       user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        q = _owned_question(session, g, question_id)
        messages, i = _editable_message(q, message_id)
        new = _build_message(req.text, req.images)
        messages[i] = {**messages[i], "text": new["text"], "images": new["images"], "edited_at": new["at"]}
        q.messages = json.dumps(messages, ensure_ascii=False)
        q.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _question_view(q, _seq_by_path(_node_paths(session, g.id)))


@router.delete("/api/gefeg/{guideline_id}/questions/{question_id}/messages/{message_id}")
def gefeg_message_delete(guideline_id: str, question_id: str, message_id: str,
                         user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        q = _owned_question(session, g, question_id)
        messages, i = _editable_message(q, message_id)
        if len(messages) == 1:
            raise HTTPException(status_code=400, detail="This is the only message; delete the whole question instead")
        del messages[i]
        q.messages = json.dumps(messages, ensure_ascii=False)
        q.status = "answered" if messages[-1].get("by") == "ba" else "open"
        q.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _question_view(q, _seq_by_path(_node_paths(session, g.id)))


@router.patch("/api/gefeg/{guideline_id}/questions/{question_id}")
def gefeg_question_update(guideline_id: str, question_id: str, req: QuestionPatch,
                          user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        q = _owned_question(session, g, question_id)
        q.status = req.status
        q.updated_at = datetime.now(timezone.utc)
        session.commit()
        return _question_view(q, _seq_by_path(_node_paths(session, g.id)))


@router.delete("/api/gefeg/{guideline_id}/questions/{question_id}")
def gefeg_question_delete(guideline_id: str, question_id: str, user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        q = _owned_question(session, g, question_id)
        if q.status == "closed":
            raise HTTPException(status_code=409, detail="Reopen the question before deleting it")
        session.delete(q)
        session.commit()
        return {"ok": True}


@router.get("/api/gefeg/{guideline_id}/export")
def gefeg_export(guideline_id: str, user: User = Depends(require_user)):
    """Self-contained HTML with the whole guideline structure and the questions that are not
    closed yet. The BA replies in it and saves it; the file is imported back with ``/import``."""
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        nodes = _tree_nodes(session, g)
        paths = {n["seq"]: n["path"] for n in nodes}
        questions = [q for q in _question_views(session, g.id, paths) if q["status"] != "closed"]
        if not questions:
            raise HTTPException(status_code=400, detail="There are no open questions to export")
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{g.model or g.title or 'guideline'}_{g.version}").strip("_")
        filename = f"{base}_questions.html"
        payload = {
            "format": EXPORT_FORMAT, "version": 1, "export_id": uuid.uuid4().hex,
            "exported_at": now_iso(), "file_name": filename,
            "guideline": {**{f: getattr(g, f) for f in _META_FIELDS if f != "id"}, "sha256": g.file_sha256},
            "nodes": nodes,
            "questions": [{k: q[k] for k in ("id", "node_seq", "node_path", "mapping_nr", "status", "messages")}
                          for q in questions],
        }
        return {"filename": filename, "html": render_export_html(payload), "question_count": len(questions)}


@router.post("/api/gefeg/{guideline_id}/import")
async def gefeg_import(guideline_id: str, file: UploadFile = File(...), user: User = Depends(require_user)):
    """Merge the BA's replies from a saved export file into the questions (append-only,
    matched by question id and message id, so importing twice changes nothing)."""
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="File is too large")
    try:
        data = extract_export(content)
    except QuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        if (data.get("guideline") or {}).get("sha256") != g.file_sha256:
            raise HTTPException(status_code=400, detail="This file was exported from a different guideline")
        incoming = data.get("questions")
        if not isinstance(incoming, list):
            raise HTTPException(status_code=400, detail="The data inside the file is damaged")
        by_id = {q.id: q for q in session.scalars(
            select(GefegQuestion).where(GefegQuestion.guideline_id == g.id))}
        new_messages = updated = unmatched = 0
        for item in incoming:
            q = by_id.get(item.get("id")) if isinstance(item, dict) else None
            if q is None:
                unmatched += 1
                continue
            merged, added = merge_messages(json.loads(q.messages), item.get("messages"))
            if added:
                q.messages = json.dumps(merged, ensure_ascii=False)
                q.status = "answered"
                q.updated_at = datetime.now(timezone.utc)
                new_messages += added
                updated += 1
        session.commit()
        return {
            "new_messages": new_messages, "updated_questions": updated, "unmatched": unmatched,
            "questions": _question_views(session, g.id, _node_paths(session, g.id)),
        }


class ProgressTarget(BaseModel):
    seq: int
    key: str | None = None  # None = every Map Instruction of the node


class ProgressUpdate(BaseModel):
    targets: list[ProgressTarget] = Field(min_length=1, max_length=5000)
    status: Literal["", "done", "question"] | None = None  # None = leave unchanged, "" = clear
    note: str | None = Field(default=None, max_length=4000)


@router.put("/api/gefeg/{guideline_id}/progress")
def gefeg_progress_update(guideline_id: str, req: ProgressUpdate, user: User = Depends(require_user)):
    """Set status and/or note of single Map Instructions, or of all instructions of a node
    (bulk). Unknown nodes/instructions are ignored; at least one valid one is required."""
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        progress = _Progress(session, g.id)
        picked: dict[int, list[str]] = {}
        for t in req.targets:
            snaps = progress.snapshots.get(t.seq)
            if not snaps:
                continue
            for key in ([t.key] if t.key is not None else list(snaps)):
                if key in snaps and key not in picked.setdefault(t.seq, []):
                    picked[t.seq].append(key)
        picked = {s: ks for s, ks in picked.items() if ks}
        if not picked:
            raise HTTPException(status_code=400, detail="Only Map Instructions can be marked")
        now = datetime.now(timezone.utc)
        for seq, keys in picked.items():
            path = progress.paths[seq]
            for key in keys:
                row = progress.rows.get((path, key))
                if row is None:
                    row = GefegMappingProgress(
                        guideline_id=g.id, node_path=path, mapping_key=key, snapshot=progress.snapshots[seq][key])
                    session.add(row)
                    progress.rows[(path, key)] = row
                if req.status is not None:
                    row.status = req.status
                    if req.status:  # (re-)confirming also acknowledges any content change
                        row.snapshot = progress.snapshots[seq][key]
                if req.note is not None:
                    row.note = req.note.strip()
                row.updated_at = now
                if not row.status and not row.note:
                    session.delete(row)
                    del progress.rows[(path, key)]
        session.commit()
        return {
            "mappings": {s: {k: progress.view(s, k) for k in ks} for s, ks in picked.items()},
            "progress": progress.counts(),
        }


class InheritRequest(BaseModel):
    from_id: str
    overwrite: bool = False


@router.get("/api/gefeg/{guideline_id}/progress/candidates")
def gefeg_inherit_candidates(guideline_id: str, all: bool = False, user: User = Depends(require_user)):
    """Guidelines this one can inherit progress from (``all=true``: any Model)."""
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        return {"candidates": _inherit_candidates(session, user, g, same_model=not all)}


@router.post("/api/gefeg/{guideline_id}/progress/inherit")
def gefeg_progress_inherit(guideline_id: str, req: InheritRequest, user: User = Depends(require_user)):
    """Copy status/notes from another guideline of the same user, matched by node path and
    instruction. Instructions already marked here are kept unless ``overwrite`` is set."""
    with Session(engine) as session:
        dst_g = _owned(session, guideline_id, user)
        src_g = _owned(session, req.from_id, user)
        if src_g.id == dst_g.id:
            raise HTTPException(status_code=400, detail="Cannot inherit progress from itself")
        src, dst = _Progress(session, src_g.id), _Progress(session, dst_g.id)
        plan = _plan_inherit(src, dst)
        copied = skipped = 0
        for r, key in plan["pairs"]:
            existing = dst.rows.get((r.node_path, key))
            if existing and (existing.status or existing.note) and not req.overwrite:
                skipped += 1
                continue
            if existing is None:
                existing = GefegMappingProgress(guideline_id=dst_g.id, node_path=r.node_path, mapping_key=key)
                session.add(existing)
            existing.status, existing.note = r.status, r.note
            existing.snapshot, existing.updated_at = r.snapshot, r.updated_at  # old snapshot → review if changed
            copied += 1
        session.commit()
        result = _Progress(session, dst_g.id)
        return {
            "copied": copied, "skipped": skipped, "missing": plan["missing"],
            "review": result.counts()["review"], "progress": result.counts(),
        }


@router.delete("/api/gefeg/{guideline_id}")
def gefeg_delete(guideline_id: str, user: User = Depends(require_user)):
    with Session(engine) as session:
        g = _owned(session, guideline_id, user)
        for model in (GefegMapping, GefegChange, GefegMappingProgress, GefegNodeProgress, GefegQuestion, GefegNode):
            session.execute(delete(model).where(model.guideline_id == g.id))
        session.delete(g)
        session.commit()
    return {"ok": True}
