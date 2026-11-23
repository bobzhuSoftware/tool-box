"""Database engine, ORM models, and persistence helpers."""
import json
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, inspect as sa_inspect, text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import DB_PATH

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class TranscriptRecord(Base):
    __tablename__ = "transcripts"

    job_id = Column(String(24), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    url = Column(Text, nullable=False)
    language = Column(String(20), nullable=False)
    model = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    segments_json = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, nullable=False)


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    key = Column(String(80), nullable=False)
    value = Column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_setting"),
    )


class GefegGuideline(Base):
    """One imported GEFEG.FX Message Implementation Guideline PDF."""
    __tablename__ = "gefeg_guidelines"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(300), nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    title = Column(String(200), nullable=False, default="")
    model = Column(String(200), nullable=False, default="")
    version = Column(String(50), nullable=False, default="")
    variant = Column(String(100), nullable=False, default="")
    issue_date = Column(String(20), nullable=False, default="")
    print_date = Column(String(20), nullable=False, default="")
    author = Column(String(100), nullable=False, default="")
    page_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("user_id", "file_sha256", name="uq_gefeg_user_file"),
    )


class GefegNode(Base):
    """An element/attribute of the guideline tree (``seq`` = document order)."""
    __tablename__ = "gefeg_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    parent_seq = Column(Integer, nullable=True)
    depth = Column(Integer, nullable=False)
    kind = Column(String(10), nullable=False)  # element | attribute
    name = Column(String(200), nullable=False)
    path = Column(Text, nullable=False)
    min_occurs = Column(Integer, nullable=True)
    max_occurs = Column(String(20), nullable=True)  # "1" | "unbounded" | ...
    type = Column(String(200), nullable=False, default="")
    use = Column(String(20), nullable=False, default="")
    notes = Column(Text, nullable=False, default="")
    page = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("guideline_id", "seq", name="uq_gefeg_node_seq"),
    )


class GefegMapping(Base):
    """A "Map Instruction" of a node (condition → instruction)."""
    __tablename__ = "gefeg_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    node_seq = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)
    nr = Column(String(20), nullable=False, default="")
    flag = Column(Text, nullable=False, default="")  # red "!change ..." / "!new ..." marker
    condition = Column(Text, nullable=False, default="")
    instruction = Column(Text, nullable=False, default="")
    additional_info = Column(Text, nullable=False, default="")


class GefegChange(Base):
    """An entry of the guideline's "New Changes Summary"."""
    __tablename__ = "gefeg_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    no = Column(String(10), nullable=False)
    node_seq = Column(Integer, nullable=True)
    tag = Column(String(200), nullable=False, default="")
    path = Column(Text, nullable=False, default="")
    number = Column(String(20), nullable=False, default="")
    flag = Column(Text, nullable=False, default="")
    condition = Column(Text, nullable=False, default="")
    instruction = Column(Text, nullable=False, default="")
    additional_info = Column(Text, nullable=False, default="")
    notes = Column(Text, nullable=False, default="")


class GefegNodeProgress(Base):
    """Legacy per-node progress. Superseded by ``GefegMappingProgress``; rows are only read
    once to migrate them (see app.core.gefeg_progress.migrate_node_progress)."""
    __tablename__ = "gefeg_node_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    node_path = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="")  # "" | done | question
    note = Column(Text, nullable=False, default="")
    snapshot = Column(Text, nullable=False, default="[]")
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("guideline_id", "node_path", name="uq_gefeg_progress_path"),
    )


class GefegMappingProgress(Base):
    """Implementation progress of one Map Instruction, keyed by field path + instruction key
    (its number) so it can be carried over to a newer version of the same guideline.
    ``snapshot`` is the instruction's content (JSON) at the time it was marked; a later
    difference means "needs review"."""
    __tablename__ = "gefeg_mapping_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    node_path = Column(Text, nullable=False)
    mapping_key = Column(String(40), nullable=False)
    status = Column(String(20), nullable=False, default="")  # "" | done | question
    note = Column(Text, nullable=False, default="")
    snapshot = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("guideline_id", "node_path", "mapping_key", name="uq_gefeg_mapping_progress"),
    )


class GefegQuestion(Base):
    """A question about a node (optionally one Map Instruction of it) raised by the developer
    and answered by the BA. ``messages`` is an append-only JSON list
    ``[{id, by: dev|ba, text, images: [data URL], at}]`` so exports/imports merge by message id."""
    __tablename__ = "gefeg_questions"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    guideline_id = Column(String(36), ForeignKey("gefeg_guidelines.id"), nullable=False, index=True)
    node_path = Column(Text, nullable=False)
    mapping_nr = Column(String(20), nullable=False, default="")
    status = Column(String(20), nullable=False, default="open")  # open | answered | closed
    messages = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)

# Lightweight migration: add user_id column if upgrading from older schema
with engine.connect() as conn:
    cols = [c["name"] for c in sa_inspect(engine).get_columns("transcripts")]
    if "user_id" not in cols:
        conn.execute(sa_text("ALTER TABLE transcripts ADD COLUMN user_id VARCHAR(36)"))
        conn.commit()


def save_to_db(job_id: str, title: str, url: str, language: str,
               model: str, text: str, segments: list[dict],
               user_id: str | None = None) -> None:
    with Session(engine) as session:
        record = TranscriptRecord(
            job_id=job_id,
            user_id=user_id,
            title=title,
            url=url,
            language=language,
            model=model,
            text=text,
            segments_json=json.dumps(segments, ensure_ascii=False),
            created_at=datetime.now(timezone.utc),
        )
        session.add(record)
        session.commit()
