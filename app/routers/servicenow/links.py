"""ServiceNow saved links, condition composer, condition library, import and link builder."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import sn_query
from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _set_user_setting

from .accounts import _load_accounts, _require_account
from .common import (
    _ALIAS_RE, _CONDITION_LIBRARY_KEY, _CONDITION_NAMES_KEY, _CONDITION_TABLES_KEY, _LINKS_KEY,
    _MAX_BUILD_CONDITIONS, _MAX_CONDITION_NAMES, _MAX_IMPORT_URLS, _MAX_LIBRARY_CONDITIONS, _MAX_LINKS,
    _MAX_NAME_ENTRIES, _MAX_NAME_FIELDS, _MAX_NAME_TABLES, _MAX_NAME_VALUES, _TABLE_RE, _VALUE_NAMES_KEY,
    _is_servicenow_url, _load_json_setting, _load_model_setting,
)
from .worker import _stream_worker

router = APIRouter()


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
    return _load_model_setting(user.id, _LINKS_KEY, SnLinks, SnLinks)


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
    data = _load_json_setting(user_id, _CONDITION_NAMES_KEY, dict)
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _load_value_names(user_id: str) -> dict[str, dict]:
    """table -> {"fields": {field: label}, "values": {field: {value: name}}}: names read from ServiceNow,
    only used to show conditions in a readable way (the conditions themselves are never changed)."""
    data = _load_json_setting(user_id, _VALUE_NAMES_KEY, dict)
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
    data = _load_json_setting(user_id, _CONDITION_LIBRARY_KEY, list)
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
    data = _load_json_setting(user_id, _CONDITION_TABLES_KEY, dict)
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


# ---------------------------------------------------------------------------
# Link builder: recombine condition fragments into a classic or simplelist URL
# ---------------------------------------------------------------------------
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
