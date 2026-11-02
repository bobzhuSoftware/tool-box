"""ServiceNow login identities: each has its own Edge profile dir + cookie file on disk."""
import os
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.db import User, engine
from app.core.settings import _set_user_setting

from .common import (
    _ACCOUNT_ID_RE, _ACCOUNTS_KEY, _DEFAULT_ACCOUNT_ID, _USER_ID_RE,
    _load_model_setting, _run_lock,
)

router = APIRouter()


class Account(BaseModel):
    id: str
    name: str
    user_name: str = ""
    display_name: str = ""


class AccountsState(BaseModel):
    accounts: list[Account] = []
    selected: str = _DEFAULT_ACCOUNT_ID


class AccountName(BaseModel):
    name: str


class AccountSelect(BaseModel):
    id: str


def _load_accounts(user_id: str) -> AccountsState:
    state = _load_model_setting(user_id, _ACCOUNTS_KEY, AccountsState, AccountsState)
    if not state.accounts:
        state.accounts = [Account(id=_DEFAULT_ACCOUNT_ID, name="默认")]
    if state.selected not in {a.id for a in state.accounts}:
        state.selected = state.accounts[0].id
    return state


def _save_accounts(user_id: str, state: AccountsState) -> None:
    _set_user_setting(user_id, _ACCOUNTS_KEY, state.model_dump_json())


def _data_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "VideoTranscript", "servicenow")


def _account_dir(user_id: str, acc_id: str) -> str:
    # Both parts become path segments, so only accept strictly safe values.
    if not _USER_ID_RE.match(user_id) or not _ACCOUNT_ID_RE.match(acc_id):
        raise HTTPException(status_code=400, detail="无效的身份标识")
    return os.path.join(_data_root(), user_id, acc_id)


def _session_path(user_id: str, acc_id: str) -> str:
    return os.path.join(_account_dir(user_id, acc_id), "session.json")


def _legacy_session_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "VideoTranscript", "servicenow-session.json")


def _is_first_user(user_id: str) -> bool:
    with Session(engine) as s:
        first = s.query(User).order_by(User.created_at, User.id).first()
        return bool(first and first.id == user_id)


def _migrate_legacy_session(user_id: str) -> None:
    """Hand the pre-identity cookie file to the default identity of the first (owner) user only."""
    legacy = _legacy_session_path()
    if not os.path.isfile(legacy) or not _is_first_user(user_id):
        return
    target = _session_path(user_id, _DEFAULT_ACCOUNT_ID)
    if os.path.exists(target):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(legacy, target)


def _account_payload(user_id: str, state: AccountsState) -> dict:
    return {
        "selected": state.selected,
        "accounts": [
            {**a.model_dump(), "logged_in": os.path.isfile(_session_path(user_id, a.id))}
            for a in state.accounts
        ],
    }


def _clean_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 40:
        raise HTTPException(status_code=400, detail="名称长度需要在 1 到 40 个字符之间")
    return name


def _require_account(state: AccountsState, acc_id: str) -> Account:
    for a in state.accounts:
        if a.id == acc_id:
            return a
    raise HTTPException(status_code=404, detail="身份不存在")


def _require_idle() -> None:
    if _run_lock.locked():
        raise HTTPException(status_code=409, detail="已有任务在运行，请稍后再试")


@router.get("/api/servicenow/accounts")
def list_accounts(user: User = Depends(require_user)):
    _migrate_legacy_session(user.id)
    return _account_payload(user.id, _load_accounts(user.id))


@router.post("/api/servicenow/accounts")
def create_account(req: AccountName, user: User = Depends(require_user)):
    state = _load_accounts(user.id)
    acc = Account(id=uuid.uuid4().hex[:8], name=_clean_name(req.name))
    state.accounts.append(acc)
    state.selected = acc.id
    _save_accounts(user.id, state)
    return _account_payload(user.id, state)


@router.post("/api/servicenow/accounts/select")
def select_account(req: AccountSelect, user: User = Depends(require_user)):
    state = _load_accounts(user.id)
    _require_account(state, req.id)
    state.selected = req.id
    _save_accounts(user.id, state)
    return _account_payload(user.id, state)


@router.put("/api/servicenow/accounts/{acc_id}")
def rename_account(acc_id: str, req: AccountName, user: User = Depends(require_user)):
    state = _load_accounts(user.id)
    _require_account(state, acc_id).name = _clean_name(req.name)
    _save_accounts(user.id, state)
    return _account_payload(user.id, state)


@router.delete("/api/servicenow/accounts/{acc_id}")
def delete_account(acc_id: str, user: User = Depends(require_user)):
    state = _load_accounts(user.id)
    _require_account(state, acc_id)
    if len(state.accounts) <= 1:
        raise HTTPException(status_code=400, detail="至少需要保留一个身份")
    _require_idle()
    shutil.rmtree(_account_dir(user.id, acc_id), ignore_errors=True)
    state.accounts = [a for a in state.accounts if a.id != acc_id]
    if state.selected == acc_id:
        state.selected = state.accounts[0].id
    _save_accounts(user.id, state)
    return _account_payload(user.id, state)


@router.post("/api/servicenow/accounts/{acc_id}/logout")
def clear_login(acc_id: str, user: User = Depends(require_user)):
    state = _load_accounts(user.id)
    acc = _require_account(state, acc_id)
    _require_idle()
    shutil.rmtree(_account_dir(user.id, acc_id), ignore_errors=True)
    acc.user_name = ""
    acc.display_name = ""
    _save_accounts(user.id, state)
    return _account_payload(user.id, state)
