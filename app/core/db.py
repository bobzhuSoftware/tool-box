"""Database engine, ORM models, and persistence helpers."""
import json
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, String, Text, UniqueConstraint,
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
