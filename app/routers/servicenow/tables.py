"""ServiceNow count tables: the saved grid layout (config)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _set_user_setting

from .common import _DEFAULT_URL, _SETTING_KEY, _load_model_setting

router = APIRouter()


class SnTable(BaseModel):
    id: str
    title: str = ""
    rows: list[str] = []
    cols: list[str] = []
    # "<row>,<col>" -> ServiceNow list URL
    cells: dict[str, str] = {}
    # "c<idx>" / "r<idx>" -> indices of the columns / rows that total column / row adds up
    sums: dict[str, list[int]] = {}


class SnConfig(BaseModel):
    tables: list[SnTable] = []


def _default_config() -> SnConfig:
    return SnConfig(
        tables=[
            SnTable(
                id="default",
                title="All",
                rows=["Blue", "Red"],
                cols=["Opened", "Unassigned Requested", "Unassigned Pre-Sales"],
                cells={"0,0": _DEFAULT_URL},
            )
        ]
    )


@router.get("/api/servicenow/config")
def get_config(user: User = Depends(require_user)) -> SnConfig:
    return _load_model_setting(user.id, _SETTING_KEY, SnConfig, _default_config)


@router.post("/api/servicenow/config")
def save_config(cfg: SnConfig, user: User = Depends(require_user)) -> SnConfig:
    _set_user_setting(user.id, _SETTING_KEY, cfg.model_dump_json())
    return cfg
