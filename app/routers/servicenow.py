"""ServiceNow count tables: saved table layouts, login identities, and worker runs (Edge automation)."""
import asyncio
import json
import os
import queue as stdlib_queue
import re
import shutil
import tempfile
import threading
import uuid
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import sn_query
from app.core.auth import require_user
from app.core.db import User, engine
from app.core.settings import _get_user_setting, _set_user_setting

router = APIRouter()

# Repo root (two levels up from app/routers/), where the *_worker.py scripts live.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

_SETTING_KEY = "servicenow_tables"
_ACCOUNTS_KEY = "servicenow_accounts"
_LINKS_KEY = "servicenow_links"
_CONDITION_NAMES_KEY = "servicenow_condition_names"
_MAX_CONDITION_NAMES = 500
_CONDITION_LIBRARY_KEY = "servicenow_condition_library"
_CONDITION_TABLES_KEY = "servicenow_condition_tables"
_VALUE_NAMES_KEY = "servicenow_value_names"
_MAX_NAME_ENTRIES = 5000
_MAX_NAME_TABLES = 30
_MAX_NAME_FIELDS = 60
_MAX_NAME_VALUES = 200
_MAX_LIBRARY_CONDITIONS = 1000
_MAX_IMPORT_URLS = 20
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
_MAX_BUILD_CONDITIONS = 60
_MAX_LINKS = 200
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,19}$")
_MAX_URLS = 200
_DEFAULT_ACCOUNT_ID = "default"
_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")

# One worker at a time: login/refresh/logout all touch the same Edge profiles.
_run_lock = threading.Lock()

_DEFAULT_URL = (
    "https://dsv.service-now.com/x/dsva2/enterprise-integrations/simplelist/x_dsva2_enterpri_0_edi_case"
    "/params/list-title/Cases%20Received%20in%20past%207%20days%20Blue/query/"
    "origin_of_request!%3Dschenker_origin%5Estate!%3D1000%5Estate!%3D1300"
    "%5Edivision%3D420b49d01b3ae5103620b6c5464bcb89"
    "%5Esys_created_onBETWEENjavascript%3Ags.beginningOfLast7Days()%40javascript%3Ags.endOfToday()"
    "%5Eassignment_group!%3D2dafd3ec2b6d7690db00f454c891bf54"
)


# ---------------------------------------------------------------------------
# Table layouts
# ---------------------------------------------------------------------------
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


def _is_servicenow_url(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "service-now.com" or host.endswith(".service-now.com"))


@router.get("/api/servicenow/config")
def get_config(user: User = Depends(require_user)) -> SnConfig:
    raw = _get_user_setting(user.id, _SETTING_KEY)
    if not raw:
        return _default_config()
    try:
        return SnConfig(**json.loads(raw))
    except (TypeError, ValueError):
        return _default_config()


@router.post("/api/servicenow/config")
def save_config(cfg: SnConfig, user: User = Depends(require_user)) -> SnConfig:
    _set_user_setting(user.id, _SETTING_KEY, cfg.model_dump_json())
    return cfg


# ---------------------------------------------------------------------------
# Saved links: a per-user bookmark list of pre-filtered ServiceNow URLs
# ---------------------------------------------------------------------------
class SnLink(BaseModel):
    id: str
    name: str = ""
    url: str
    note: str = ""
    # Short name used as [alias] in cell formulas.
    alias: str = ""


class SnLinks(BaseModel):
    links: list[SnLink] = []


@router.get("/api/servicenow/links")
def get_links(user: User = Depends(require_user)) -> SnLinks:
    raw = _get_user_setting(user.id, _LINKS_KEY)
    if not raw:
        return SnLinks()
    try:
        return SnLinks(**json.loads(raw))
    except (TypeError, ValueError):
        return SnLinks()


@router.post("/api/servicenow/links")
def save_links(body: SnLinks, user: User = Depends(require_user)) -> SnLinks:
    if len(body.links) > _MAX_LINKS:
        raise HTTPException(status_code=400, detail=f"最多保存 {_MAX_LINKS} 条链接")
    cleaned: list[SnLink] = []
    seen_aliases: set[str] = set()
    for link in body.links:
        url = link.url.strip()
        if len(url) > 4000 or not _is_servicenow_url(url):
            raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")
        alias = link.alias.strip()
        if alias:
            if not _ALIAS_RE.match(alias):
                raise HTTPException(
                    status_code=400, detail="缩写只能包含字母、数字、下划线，不能以数字开头，最长 20 个字符"
                )
            if alias.lower() in seen_aliases:
                raise HTTPException(status_code=400, detail=f"缩写「{alias}」重复")
            seen_aliases.add(alias.lower())
        cleaned.append(
            SnLink(
                id=link.id[:36],
                name=link.name.strip()[:100],
                url=url,
                note=link.note.strip()[:500],
                alias=alias,
            )
        )
    result = SnLinks(links=cleaned)
    # Collect the conditions of the links being replaced first, so dropping a link never loses its conditions.
    _sync_library(user.id, get_links(user).links)
    _set_user_setting(user.id, _LINKS_KEY, result.model_dump_json())
    _sync_library(user.id, cleaned)
    return result


# ---------------------------------------------------------------------------
# Condition composer: split saved links into condition fragments and recombine them
# ---------------------------------------------------------------------------
def _condition_names(user_id: str) -> dict[str, str]:
    raw = _get_user_setting(user_id, _CONDITION_NAMES_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _load_value_names(user_id: str) -> dict[str, dict]:
    """table -> {"fields": {field: label}, "values": {field: {value: name}}}: names read from ServiceNow,
    only used to show conditions in a readable way (the conditions themselves are never changed)."""
    raw = _get_user_setting(user_id, _VALUE_NAMES_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return {t: d for t, d in data.items() if isinstance(t, str) and isinstance(d, dict)} if isinstance(data, dict) else {}


def _store_value_names(user_id: str, result: dict) -> None:
    """Merges one worker `names` result ({table, fields, values}) into the saved names."""
    table = result.get("table")
    if not isinstance(table, str) or not _TABLE_RE.match(table):
        return
    cache = _load_value_names(user_id)
    if table not in cache and len(cache) >= _MAX_NAME_TABLES:
        return
    entry = cache.setdefault(table, {"fields": {}, "values": {}})
    entry.setdefault("fields", {})
    entry.setdefault("values", {})
    for field, label in (result.get("fields") or {}).items():
        if isinstance(field, str) and isinstance(label, str):
            entry["fields"][field[:100]] = label[:200]
    total = sum(len(v) for v in entry["values"].values())
    for field, names in (result.get("values") or {}).items():
        if not isinstance(field, str) or not isinstance(names, dict):
            continue
        bucket = entry["values"].setdefault(field[:100], {})
        for value, name in names.items():
            if not isinstance(value, str) or not isinstance(name, str):
                continue
            if value not in bucket:
                if total >= _MAX_NAME_ENTRIES:
                    break
                total += 1
            # A refresh that finds no name keeps the name read earlier.
            if name or not bucket.get(value):
                bucket[value[:100]] = name[:200]
    _set_user_setting(user_id, _VALUE_NAMES_KEY, json.dumps(cache, ensure_ascii=False))


class SnCondition(BaseModel):
    raw: str
    # raw with ids replaced by their names; for display only.
    raw_display: str = ""
    label: str
    field: str
    op: str
    value: str
    custom: bool = False


class SnLinkConditions(BaseModel):
    id: str
    name: str
    kind: str = ""
    table: str = ""
    title: str = ""
    conditions: list[SnCondition] = []
    # Why the link can't be split (e.g. it uses ^NQ); empty when it can.
    unsupported: str = ""


class SnConditionsResponse(BaseModel):
    links: list[SnLinkConditions] = []


@router.get("/api/servicenow/links/conditions")
def get_link_conditions(user: User = Depends(require_user)) -> SnConditionsResponse:
    names = _condition_names(user.id)
    display = _load_value_names(user.id)
    out: list[SnLinkConditions] = []
    for link in get_links(user).links:
        info = sn_query.parse_list_url(link.url)
        item = SnLinkConditions(id=link.id, name=link.name, kind=info["kind"], table=info["table"], title=info["title"])
        if info["kind"] == "savedlist":
            item.unsupported = "已保存列表链接的条件在服务端，请用条件库的“从链接导入”读取"
        elif not info["table"]:
            item.unsupported = "无法解析这条链接（需要 simplelist 或经典列表链接）"
        else:
            fragments, reason = sn_query.split_query(info["query"])
            item.unsupported = reason
            item.conditions = [
                SnCondition(
                    raw=f,
                    raw_display=sn_query.readable_raw(f, display.get(info["table"])),
                    label=sn_query.label_for(f, names, display.get(info["table"])),
                    custom=f in names,
                    **sn_query.describe(f),
                )
                for f in fragments
            ]
        out.append(item)
    return SnConditionsResponse(links=out)


class SnConditionName(BaseModel):
    raw: str
    name: str = ""


def _set_condition_name(user_id: str, raw: str, name: str) -> dict[str, str]:
    names = _condition_names(user_id)
    name = name.strip()[:100]
    if name:
        if raw not in names and len(names) >= _MAX_CONDITION_NAMES:
            raise HTTPException(status_code=400, detail=f"最多保存 {_MAX_CONDITION_NAMES} 个条件名称")
        names[raw] = name
    else:
        names.pop(raw, None)
    _set_user_setting(user_id, _CONDITION_NAMES_KEY, json.dumps(names, ensure_ascii=False))
    return names


@router.post("/api/servicenow/links/condition-names")
def save_condition_name(body: SnConditionName, user: User = Depends(require_user)) -> dict[str, str]:
    raw = body.raw.strip()
    if not raw or len(raw) > 1000 or not sn_query.is_valid_unit(raw):
        raise HTTPException(status_code=400, detail="无效的条件")
    return _set_condition_name(user.id, raw, body.name)


# ---------------------------------------------------------------------------
# Condition library: every condition the user has used, kept independently of the saved links
# ---------------------------------------------------------------------------
class SnLibraryEntry(BaseModel):
    table: str
    raw: str
    note: str = ""
    # link: collected from a saved link; import: imported from a link that isn't saved; manual: typed in.
    # Only "link" conditions are removed by the cleanup of unused conditions.
    source: Literal["link", "import", "manual"] = "link"


def _load_library(user_id: str) -> list[SnLibraryEntry]:
    raw = _get_user_setting(user_id, _CONDITION_LIBRARY_KEY)
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    out: list[SnLibraryEntry] = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(SnLibraryEntry(**item))
        except (TypeError, ValueError):
            continue
    return out


def _save_library(user_id: str, library: list[SnLibraryEntry]) -> None:
    _set_user_setting(user_id, _CONDITION_LIBRARY_KEY, json.dumps([e.model_dump() for e in library], ensure_ascii=False))


def _load_origins(user_id: str) -> dict[str, dict[str, str]]:
    """table -> {host, app_path}: where to point a link built for that table."""
    raw = _get_user_setting(user_id, _CONDITION_TABLES_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    out: dict[str, dict[str, str]] = {}
    for table, o in data.items() if isinstance(data, dict) else []:
        if isinstance(table, str) and isinstance(o, dict):
            out[table] = {"host": str(o.get("host", "")), "app_path": str(o.get("app_path", ""))}
    return out


def _save_origins(user_id: str, origins: dict[str, dict[str, str]]) -> None:
    _set_user_setting(user_id, _CONDITION_TABLES_KEY, json.dumps(origins, ensure_ascii=False))


def _remember_origin(origins: dict[str, dict[str, str]], info: dict) -> bool:
    """Records the host / app path of a parsed link for its table; the first one seen wins. True if changed."""
    if not info["table"] or not info["host"]:
        return False
    cur = origins.setdefault(info["table"], {"host": "", "app_path": ""})
    before = dict(cur)
    if not cur["host"]:
        cur["host"] = info["host"]
    if not cur["app_path"] and info["app_path"] and cur["host"] == info["host"]:
        cur["app_path"] = info["app_path"]
    return cur != before


def _link_units(link: SnLink) -> tuple[str, list[str]]:
    """(table, condition units) of a saved link; no units when the link can't be split."""
    info = sn_query.parse_list_url(link.url)
    if not info["table"]:
        return "", []
    fragments, _ = sn_query.split_query(info["query"])
    return info["table"], [f for f in fragments if len(f) <= 1000]


def _sync_library(user_id: str, links: list[SnLink]) -> list[SnLibraryEntry]:
    """Adds the conditions (and table origins) of the saved links that the library doesn't have yet.
    Never removes any."""
    library = _load_library(user_id)
    origins = _load_origins(user_id)
    known = {(e.table, e.raw) for e in library}
    added = False
    origins_changed = False
    for link in links:
        origins_changed |= _remember_origin(origins, sn_query.parse_list_url(link.url))
        table, units = _link_units(link)
        for unit in units:
            if (table, unit) in known or len(library) >= _MAX_LIBRARY_CONDITIONS:
                continue
            library.append(SnLibraryEntry(table=table, raw=unit))
            known.add((table, unit))
            added = True
    if added:
        _save_library(user_id, library)
    if origins_changed:
        _save_origins(user_id, origins)
    return library


class SnUsedBy(BaseModel):
    id: str
    name: str


class SnLibraryItem(BaseModel):
    table: str
    raw: str
    raw_display: str = ""
    label: str
    custom: bool = False
    note: str = ""
    source: str = "link"
    field: str = ""
    op: str = ""
    value: str = ""
    used_by: list[SnUsedBy] = []


class SnLibraryResponse(BaseModel):
    conditions: list[SnLibraryItem] = []
    # Tables a link can be built for (their host is known).
    tables: list[str] = []
    # Conditions that hold ids or codes whose readable names haven't been read from ServiceNow yet.
    names_missing: int = 0


def _usage(links: list[SnLink]) -> dict[tuple[str, str], list[SnUsedBy]]:
    used: dict[tuple[str, str], list[SnUsedBy]] = {}
    for link in links:
        table, units = _link_units(link)
        for unit in units:
            used.setdefault((table, unit), []).append(SnUsedBy(id=link.id, name=link.name))
    return used


def _library_response(user_id: str, links: list[SnLink]) -> SnLibraryResponse:
    library = _sync_library(user_id, links)
    names = _condition_names(user_id)
    display = _load_value_names(user_id)
    used = _usage(links)
    items = [
        SnLibraryItem(
            table=e.table,
            raw=e.raw,
            raw_display=sn_query.readable_raw(e.raw, display.get(e.table)),
            label=sn_query.label_for(e.raw, names, display.get(e.table)),
            custom=e.raw in names,
            note=e.note,
            source=e.source,
            used_by=used.get((e.table, e.raw), []),
            **sn_query.describe(e.raw),
        )
        for e in library
    ]
    missing = sum(1 for e in library if _unit_missing_names(e.table, [e.raw], display))
    return SnLibraryResponse(conditions=items, tables=list(_load_origins(user_id)), names_missing=missing)


def _unit_missing_names(table: str, units: list[str], display: dict[str, dict]) -> dict[str, list[str]]:
    """Fields (and values) of the conditions whose readable names haven't been read yet."""
    return sn_query.missing_targets(sn_query.name_targets(units), display.get(table))


@router.get("/api/servicenow/conditions")
def get_condition_library(user: User = Depends(require_user)) -> SnLibraryResponse:
    return _library_response(user.id, get_links(user).links)


class SnLibrarySave(BaseModel):
    table: str
    raw: str
    # None leaves the current value unchanged; "" clears it.
    name: str | None = None
    note: str | None = None


@router.post("/api/servicenow/conditions")
def save_library_condition(body: SnLibrarySave, user: User = Depends(require_user)) -> SnLibraryResponse:
    """Adds a condition to the library, or updates the name / note of one that is already there."""
    table = body.table.strip()
    raw = body.raw.strip()
    if not _TABLE_RE.match(table):
        raise HTTPException(status_code=400, detail="无效的表名")
    if not raw or len(raw) > 1000 or not sn_query.is_valid_unit(raw):
        raise HTTPException(status_code=400, detail="无效的条件，格式如 state!=1000 或 field=value")
    links = get_links(user).links
    library = _sync_library(user.id, links)
    entry = next((e for e in library if e.table == table and e.raw == raw), None)
    if entry is None:
        if table not in _load_origins(user.id):
            raise HTTPException(status_code=400, detail="这张表还没有记录，请先保存或导入一条这张表的链接")
        if len(library) >= _MAX_LIBRARY_CONDITIONS:
            raise HTTPException(status_code=400, detail=f"最多保存 {_MAX_LIBRARY_CONDITIONS} 个条件")
        entry = SnLibraryEntry(table=table, raw=raw, source="manual")
        library.append(entry)
    if body.note is not None:
        entry.note = body.note.strip()[:500]
    if body.name is not None:
        _set_condition_name(user.id, raw, body.name)
    _save_library(user.id, library)
    return _library_response(user.id, links)


class SnLibraryKey(BaseModel):
    table: str
    raw: str


@router.post("/api/servicenow/conditions/delete")
def delete_library_condition(body: SnLibraryKey, user: User = Depends(require_user)) -> SnLibraryResponse:
    """Removes a condition that no saved link uses (a used one would be collected again)."""
    links = get_links(user).links
    library = _sync_library(user.id, links)
    used = _usage(links)
    key = (body.table, body.raw)
    if used.get(key):
        raise HTTPException(status_code=400, detail="还有链接在使用这个条件，请先修改或删除那些链接")
    remaining = [e for e in library if (e.table, e.raw) != key]
    if len(remaining) == len(library):
        raise HTTPException(status_code=404, detail="找不到这个条件")
    _save_library(user.id, remaining)
    if not any(e.raw == body.raw for e in remaining) and body.raw in _condition_names(user.id):
        _set_condition_name(user.id, body.raw, "")
    return _library_response(user.id, links)


@router.post("/api/servicenow/conditions/cleanup")
def cleanup_library(user: User = Depends(require_user)) -> SnLibraryResponse:
    """Removes the conditions collected from links that no saved link uses any more.
    Imported and manually added conditions are never removed here."""
    links = get_links(user).links
    library = _sync_library(user.id, links)
    used = _usage(links)
    remaining = [e for e in library if e.source != "link" or used.get((e.table, e.raw))]
    if len(remaining) != len(library):
        _save_library(user.id, remaining)
    return _library_response(user.id, links)


# ---------------------------------------------------------------------------
# Import: take the conditions out of links without saving the links
# ---------------------------------------------------------------------------
class SnImportRequest(BaseModel):
    urls: list[str]


class SnImportCondition(BaseModel):
    raw: str
    raw_display: str = ""
    label: str
    exists: bool = False


class SnImportItem(BaseModel):
    url: str
    ok: bool = False
    error: str = ""
    needs_resolve: bool = False
    # Ids or codes in the conditions whose readable names haven't been read yet.
    names_missing: bool = False
    kind: str = ""
    table: str = ""
    title: str = ""
    conditions: list[SnImportCondition] = []


class SnImportPreview(BaseModel):
    items: list[SnImportItem] = []


def _clean_import_urls(urls: list[str]) -> list[str]:
    cleaned = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    if not cleaned:
        raise HTTPException(status_code=400, detail="请粘贴至少一条链接")
    if len(cleaned) > _MAX_IMPORT_URLS:
        raise HTTPException(status_code=400, detail=f"一次最多导入 {_MAX_IMPORT_URLS} 条链接")
    return cleaned


def _inspect_import_url(
    url: str, existing: set[tuple[str, str]], names: dict[str, str], display: dict[str, dict] | None = None
) -> tuple[SnImportItem, dict]:
    item = SnImportItem(url=url)
    if len(url) > 4000 or not _is_servicenow_url(url):
        item.error = "只支持 https://*.service-now.com 的链接"
        return item, {}
    info = sn_query.parse_list_url(url)
    if info["kind"] == "savedlist":
        # The filter of a saved list lives on the server; /conditions/import/resolve/stream reads it.
        item.needs_resolve = True
        item.error = "已保存列表链接，需要先解析"
        return item, {}
    if not info["table"]:
        item.error = "无法解析这条链接（需要 simplelist、经典列表或已保存列表链接）"
        return item, {}
    fragments, reason = sn_query.split_query(info["query"])
    units = [f for f in fragments if len(f) <= 1000]
    if reason or not units:
        item.error = reason or "这条链接里没有可导入的条件"
        return item, {}
    item.ok = True
    item.kind = info["kind"]
    item.table = info["table"]
    item.title = info["title"]
    display = display or {}
    item.conditions = [
        SnImportCondition(
            raw=u,
            raw_display=sn_query.readable_raw(u, display.get(info["table"])),
            label=sn_query.label_for(u, names, display.get(info["table"])),
            exists=(info["table"], u) in existing,
        )
        for u in units
    ]
    item.names_missing = bool(_unit_missing_names(info["table"], units, display))
    return item, info


@router.post("/api/servicenow/conditions/import/preview")
def preview_import(body: SnImportRequest, user: User = Depends(require_user)) -> SnImportPreview:
    """Shows the conditions found in the pasted links, and which of them the library already has."""
    existing = {(e.table, e.raw) for e in _sync_library(user.id, get_links(user).links)}
    names = _condition_names(user.id)
    display = _load_value_names(user.id)
    return SnImportPreview(
        items=[_inspect_import_url(u, existing, names, display)[0] for u in _clean_import_urls(body.urls)]
    )


class SnResolveRequest(BaseModel):
    urls: list[str]


@router.post("/api/servicenow/conditions/import/resolve/stream")
async def resolve_import_links(body: SnResolveRequest, user: User = Depends(require_user)):
    """Reads the filter behind saved-list links with the signed-in Edge identity (the one selected in the
    count tables). Each RESULT carries a `resolved_url`, an equivalent simplelist link that the normal
    preview / import endpoints understand. The readable names of the ids in the filters are read in the
    same browser session and saved."""
    urls = _clean_import_urls(body.urls)
    if any(len(u) > 4000 or not _is_servicenow_url(u) for u in urls):
        raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")
    if any(sn_query.parse_list_url(u)["kind"] != "savedlist" for u in urls):
        raise HTTPException(status_code=400, detail="只能解析已保存列表链接")
    state = _load_accounts(user.id)
    acc = _require_account(state, state.selected)
    return _stream_worker(user, acc.id, ["resolve"], urls=urls, on_result=lambda data: _on_worker_result(user.id, data))


def _on_worker_result(user_id: str, data: dict) -> None:
    if data.get("kind") == "names":
        _store_value_names(user_id, data)


class SnNamesRequest(BaseModel):
    # Links whose conditions to read names for; empty means the whole condition library.
    urls: list[str] = []
    # Read every id again (e.g. after a rename in ServiceNow), not only the ones without a name yet.
    refresh: bool = False


@router.post("/api/servicenow/conditions/names/stream")
async def read_condition_names(body: SnNamesRequest, user: User = Depends(require_user)):
    """Reads the readable names (field labels, people, choice labels) of the ids and codes in conditions
    with the signed-in Edge identity, and saves them. They are only used to display conditions."""
    display = _load_value_names(user.id)
    by_table: dict[str, list[str]] = {}
    if body.urls:
        for url in _clean_import_urls(body.urls):
            if len(url) > 4000 or not _is_servicenow_url(url):
                raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")
            info = sn_query.parse_list_url(url)
            if info["table"]:
                units = [f for f in sn_query.split_query(info["query"])[0] if len(f) <= 1000]
                by_table.setdefault(info["table"], []).extend(units)
    else:
        for e in _sync_library(user.id, get_links(user).links):
            by_table.setdefault(e.table, []).append(e.raw)
    specs = []
    for table, units in list(by_table.items())[:_MAX_NAME_TABLES]:
        targets = sn_query.name_targets(units)
        if not body.refresh:
            targets = sn_query.missing_targets(targets, display.get(table))
        targets = {f: v[:_MAX_NAME_VALUES] for f, v in list(targets.items())[:_MAX_NAME_FIELDS]}
        if targets and _TABLE_RE.match(table):
            specs.append({"table": table, "targets": targets})
    if not specs:
        raise HTTPException(status_code=400, detail="没有需要读取名称的条件")
    state = _load_accounts(user.id)
    acc = _require_account(state, state.selected)
    return _stream_worker(user, acc.id, ["names"], urls=specs, on_result=lambda data: _on_worker_result(user.id, data))


class SnImportSelection(BaseModel):
    url: str
    conditions: list[str]


class SnImportConfirm(BaseModel):
    items: list[SnImportSelection]


class SnImportResult(BaseModel):
    library: SnLibraryResponse
    added: int = 0
    existing: int = 0


@router.post("/api/servicenow/conditions/import")
def import_conditions(body: SnImportConfirm, user: User = Depends(require_user)) -> SnImportResult:
    """Adds the chosen conditions of the pasted links to the library. The links themselves are not saved,
    so the table, host and conditions are re-read from each link rather than taken from the client."""
    if len(body.items) > _MAX_IMPORT_URLS:
        raise HTTPException(status_code=400, detail=f"一次最多导入 {_MAX_IMPORT_URLS} 条链接")
    links = get_links(user).links
    library = _sync_library(user.id, links)
    origins = _load_origins(user.id)
    by_key = {(e.table, e.raw): e for e in library}
    names = _condition_names(user.id)
    added = existing = 0
    for sel in body.items:
        item, info = _inspect_import_url(sel.url.strip(), set(by_key), names)
        if not item.ok:
            raise HTTPException(status_code=400, detail=item.error)
        found = {c.raw for c in item.conditions}
        for raw in dict.fromkeys(sel.conditions):
            if raw not in found:
                raise HTTPException(status_code=400, detail="条件不属于这条链接")
            entry = by_key.get((item.table, raw))
            if entry is not None:
                existing += 1
                # Importing it on purpose keeps it out of the cleanup of unused conditions.
                if entry.source == "link":
                    entry.source = "import"
                continue
            if len(library) >= _MAX_LIBRARY_CONDITIONS:
                raise HTTPException(status_code=400, detail=f"最多保存 {_MAX_LIBRARY_CONDITIONS} 个条件")
            entry = SnLibraryEntry(
                table=item.table, raw=raw, source="import", note=f"来自链接：{item.title or item.table}"[:500]
            )
            library.append(entry)
            by_key[(item.table, raw)] = entry
            added += 1
        _remember_origin(origins, info)
    _save_library(user.id, library)
    _save_origins(user.id, origins)
    return SnImportResult(library=_library_response(user.id, links), added=added, existing=existing)


class SnBuildRequest(BaseModel):
    # Either a saved link to take the table and host from, or a table known to the condition library.
    base_id: str = ""
    table: str = ""
    conditions: list[str]
    format: Literal["classic", "simplelist"] = "classic"
    title: str = ""


class SnBuildResponse(BaseModel):
    url: str
    query: str


@router.post("/api/servicenow/links/build")
def build_link(body: SnBuildRequest, user: User = Depends(require_user)) -> SnBuildResponse:
    """Build a link from the base link's (or table's) host and table plus the chosen condition fragments."""
    links = get_links(user).links
    if body.base_id:
        base = next((l for l in links if l.id == body.base_id), None)
        if base is None:
            raise HTTPException(status_code=404, detail="找不到基础链接")
        info = sn_query.parse_list_url(base.url)
        default_title = base.name
    else:
        _sync_library(user.id, links)
        origin = _load_origins(user.id).get(body.table) if body.table else None
        if origin is None:
            raise HTTPException(status_code=404, detail="找不到这张表的链接信息")
        info = {"table": body.table, "host": origin["host"], "app_path": origin["app_path"]}
        default_title = body.table
    if not info["table"]:
        raise HTTPException(status_code=400, detail="基础链接无法解析")
    fragments = [c.strip() for c in body.conditions if c.strip()]
    if not fragments:
        raise HTTPException(status_code=400, detail="请至少选择一个条件")
    if len(fragments) > _MAX_BUILD_CONDITIONS:
        raise HTTPException(status_code=400, detail=f"最多组合 {_MAX_BUILD_CONDITIONS} 个条件")
    for f in fragments:
        if len(f) > 1000 or not sn_query.is_valid_unit(f):
            raise HTTPException(status_code=400, detail="条件格式无效")
    fragments = list(dict.fromkeys(fragments))

    app_path = info["app_path"]
    if body.format == "simplelist" and not app_path:
        # A classic base has no app path; borrow it from a simplelist link, or a recorded table, on the same host.
        for l in links:
            other = sn_query.parse_list_url(l.url)
            if other["kind"] == "simplelist" and other["host"] == info["host"]:
                app_path = other["app_path"]
                break
        else:
            for o in _load_origins(user.id).values():
                if o["host"] == info["host"] and o["app_path"]:
                    app_path = o["app_path"]
                    break
    query = "^".join(fragments)
    url = sn_query.build_url(
        body.format, info["host"], info["table"], query, body.title.strip()[:100] or default_title, app_path
    )
    if len(url) > 4000 or not _is_servicenow_url(url):
        raise HTTPException(status_code=400, detail="生成的链接无效或过长")
    return SnBuildResponse(url=url, query=query)


# ---------------------------------------------------------------------------
# Login identities: each has its own Edge profile dir + cookie file on disk
# ---------------------------------------------------------------------------
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
    raw = _get_user_setting(user_id, _ACCOUNTS_KEY)
    state = AccountsState()
    if raw:
        try:
            state = AccountsState(**json.loads(raw))
        except (TypeError, ValueError):
            state = AccountsState()
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


# ---------------------------------------------------------------------------
# Worker runs (SSE)
# ---------------------------------------------------------------------------
def _remember_identity(user_id: str, acc_id: str, ident: dict) -> None:
    state = _load_accounts(user_id)
    for a in state.accounts:
        if a.id == acc_id:
            a.user_name = str(ident.get("user_name", ""))
            a.display_name = str(ident.get("display_name", ""))
    _save_accounts(user_id, state)


def _kill_tree(pid: int) -> None:
    import subprocess as _sp

    if os.name == "nt":
        _sp.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def _stream_worker(
    user: User, acc_id: str, worker_args: list[str], urls: list | None = None, on_result=None
):
    """Spawn servicenow_worker.py for one identity and stream its output as SSE (caller holds no lock).
    `urls` is the JSON input handed to the worker with --file; `on_result(data)` sees every RESULT on the server."""
    import subprocess as _sp
    import sys as _sys

    acc_dir = _account_dir(user.id, acc_id)
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务在运行，请稍后再试")

    edge_profile = _get_user_setting(user.id, "edge_profile") or ""
    q: stdlib_queue.Queue = stdlib_queue.Queue()
    live: dict = {"proc": None}

    def run_worker():
        url_file = None
        got_final = False
        final: dict = {"type": "error", "message": "读取进程异常退出"}
        try:
            if acc_id == _DEFAULT_ACCOUNT_ID:
                _migrate_legacy_session(user.id)
            os.makedirs(acc_dir, exist_ok=True)
            args = list(worker_args)
            if urls is not None:
                fd, url_file = tempfile.mkstemp(prefix="vt_sn_", suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(urls, f)
                args += ["--file", url_file]

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["VT_EDGE_AUTOMATION_DIR"] = os.path.join(acc_dir, "edge")
            env["VT_SN_SESSION_FILE"] = os.path.join(acc_dir, "session.json")
            if edge_profile:
                env["VT_EDGE_PROFILE"] = edge_profile  # only selects which profile seeds the config files
            # Own process group: a Ctrl+C aimed at uvicorn (reload/shutdown) must not reach the worker.
            proc = _sp.Popen(
                [_sys.executable, os.path.join(_REPO_ROOT, "servicenow_worker.py"), *args],
                stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding="utf-8", env=env,
                creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            live["proc"] = proc
            stderr_lines: list = []
            threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True).start()

            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line.startswith("STATUS:"):
                    q.put({"type": "status", "message": line[7:]})
                elif line.startswith("STAGE:"):
                    q.put({"type": "stage", "stage": line[6:]})
                elif line.startswith("RESULT:"):
                    try:
                        data = json.loads(line[7:])
                    except ValueError:
                        continue
                    if on_result:
                        try:
                            on_result(data)
                        except Exception:  # noqa: BLE001
                            pass
                    q.put({"type": "result", "data": data})
                elif line.startswith("IDENTITY:"):
                    try:
                        ident = json.loads(line[9:])
                    except ValueError:
                        continue
                    _remember_identity(user.id, acc_id, ident)
                    q.put({"type": "identity", "data": ident})
                elif line.startswith("DONE:"):
                    final, got_final = {"type": "done"}, True
                elif line.startswith("ERROR:"):
                    final, got_final = {"type": "error", "message": line[6:]}, True
            proc.wait()
            tail = "".join(stderr_lines).strip()[-400:]
            if not got_final and tail:
                final = {"type": "error", "message": tail}
        except Exception as e:  # noqa: BLE001
            final = {"type": "error", "message": str(e)}
        finally:
            if url_file:
                try:
                    os.unlink(url_file)
                except OSError:
                    pass
            _run_lock.release()
            # Sent after the lock is free so the client can start the next action right away.
            q.put(final)

    threading.Thread(target=run_worker, daemon=True).start()

    async def generate():
        try:
            while True:
                try:
                    event = q.get_nowait()
                except stdlib_queue.Empty:
                    await asyncio.sleep(0.15)
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
        finally:
            # Client gone or server shutting down: don't leave the worker (and the run lock) hanging.
            proc = live["proc"]
            if proc is not None and proc.poll() is None:
                _kill_tree(proc.pid)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class SnRunRequest(BaseModel):
    urls: list[str]
    account: str = ""


@router.post("/api/servicenow/run/stream")
async def run_stream(req: SnRunRequest, user: User = Depends(require_user)):
    urls = list(dict.fromkeys(u.strip() for u in req.urls if u.strip()))
    if not urls:
        raise HTTPException(status_code=400, detail="没有可读取的链接")
    if len(urls) > _MAX_URLS:
        raise HTTPException(status_code=400, detail=f"链接过多（最多 {_MAX_URLS} 个）")
    if any(not _is_servicenow_url(u) for u in urls):
        raise HTTPException(status_code=400, detail="只支持 https://*.service-now.com 的链接")

    state = _load_accounts(user.id)
    acc = _require_account(state, req.account or state.selected)
    return _stream_worker(user, acc.id, ["count"], urls=urls)


@router.post("/api/servicenow/accounts/{acc_id}/login/stream")
async def login_stream(acc_id: str, user: User = Depends(require_user)):
    acc = _require_account(_load_accounts(user.id), acc_id)
    return _stream_worker(user, acc.id, ["login"])
