"""Per-user UI preferences (e.g. the default transcript split duration)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _get_user_setting, _set_user_setting

router = APIRouter()

DEFAULT_SPLIT_MINUTES = 30
MIN_SPLIT_MINUTES = 30
MAX_SPLIT_MINUTES = 600
_SPLIT_KEY = "split_chunk_minutes"


class Preferences(BaseModel):
    split_chunk_minutes: int = Field(DEFAULT_SPLIT_MINUTES, ge=MIN_SPLIT_MINUTES, le=MAX_SPLIT_MINUTES)


class PreferencesUpdate(BaseModel):
    split_chunk_minutes: int | None = Field(None, ge=MIN_SPLIT_MINUTES, le=MAX_SPLIT_MINUTES)


@router.get("/api/preferences")
def get_preferences(user: User = Depends(require_user)) -> Preferences:
    raw = _get_user_setting(user.id, _SPLIT_KEY)
    try:
        return Preferences(split_chunk_minutes=int(raw)) if raw else Preferences()
    except (TypeError, ValueError):
        return Preferences()


@router.put("/api/preferences")
def update_preferences(body: PreferencesUpdate, user: User = Depends(require_user)) -> Preferences:
    if body.split_chunk_minutes is not None:
        _set_user_setting(user.id, _SPLIT_KEY, str(body.split_chunk_minutes))
    return get_preferences(user)
