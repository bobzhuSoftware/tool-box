"""Per-user key/value settings persistence."""
from sqlalchemy.orm import Session

from app.core.db import UserSetting, engine


def _get_user_setting(user_id: str, key: str) -> str | None:
    with Session(engine) as s:
        row = s.query(UserSetting).filter_by(user_id=user_id, key=key).first()
        return row.value if row else None


def _set_user_setting(user_id: str, key: str, value: str) -> None:
    with Session(engine) as s:
        row = s.query(UserSetting).filter_by(user_id=user_id, key=key).first()
        if row:
            row.value = value
        else:
            s.add(UserSetting(user_id=user_id, key=key, value=value))
        s.commit()


def _delete_user_setting(user_id: str, key: str) -> None:
    with Session(engine) as s:
        s.query(UserSetting).filter_by(user_id=user_id, key=key).delete()
        s.commit()
