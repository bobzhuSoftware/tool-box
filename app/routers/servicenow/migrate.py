"""ServiceNow migrate: export the count tables, saved links and condition library as one JSON file,
and import such a file (merge or replace) on another machine.

Cells may reference saved links by id ("ref:<id>") or by alias inside formulas ("=[PB]+[PR]"), so the
import gives every imported link a local id and rewrites those references in the imported tables.
Login identities and refreshed counts are machine-local and are never exported.
"""
import copy
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from app.core import sn_query
from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _set_user_setting

from .common import (
    _ALIAS_RE, _CONDITION_NAMES_KEY, _LINKS_KEY, _MAX_CONDITION_NAMES, _MAX_LIBRARY_CONDITIONS, _MAX_LINKS,
    _MAX_NAME_ENTRIES, _MAX_NAME_TABLES, _SETTING_KEY, _TABLE_RE, _VALUE_NAMES_KEY, _is_servicenow_url,
)
from .links import (
    SnLibraryEntry, SnLink, SnLinks, _condition_names, _load_origins, _load_value_names, _save_library,
    _save_origins, _sync_library, get_links,
)
from .tables import SnConfig, SnTable, get_config

router = APIRouter()

_FORMAT = "servicenow-migrate"
_VERSION = 1
_REF_PREFIX = "ref:"
_FORMULA_ALIAS_RE = re.compile(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]")
_CELL_KEY_RE = re.compile(r"^(\d+),(\d+)$")
_SUM_KEY_RE = re.compile(r"^[rc]\d+$")
_SECTIONS = ("tables", "links", "conditions")


class SnBundleConditions(BaseModel):
    library: list[dict] = []
    names: dict = {}
    origins: dict = {}
    value_names: dict = {}


class SnBundle(BaseModel):
    format: str = ""
    version: int = 0
    exported_at: str = ""
    # None: the section is not in the file.
    tables: list[dict] | None = None
    links: list[dict] | None = None
    conditions: SnBundleConditions | None = None


class SnMigrateCounts(BaseModel):
    tables: int = 0
    links: int = 0
    conditions: int = 0


class SnMigrateResult(BaseModel):
    applied: bool = False
    # Sections that are in the file and hold at least one item.
    present: list[str] = []
    available: SnMigrateCounts = SnMigrateCounts()
    current: SnMigrateCounts = SnMigrateCounts()
    added: SnMigrateCounts = SnMigrateCounts()
    skipped: SnMigrateCounts = SnMigrateCounts()
    reused_links: int = 0
    renamed_tables: list[str] = []
    changed_aliases: list[str] = []
    dropped_cells: int = 0
    dangling_refs: int = 0
    exported_at: str = ""


@dataclass
class _State:
    tables: list[SnTable]
    links: list[SnLink]
    library: list[SnLibraryEntry]
    names: dict[str, str]
    origins: dict[str, dict[str, str]]
    value_names: dict[str, dict]


def _load_state(user: User) -> _State:
    links = get_links(user).links
    return _State(
        tables=get_config(user).tables,
        links=links,
        library=_sync_library(user.id, links),
        names=_condition_names(user.id),
        origins=_load_origins(user.id),
        value_names=_load_value_names(user.id),
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.get("/api/servicenow/migrate/export")
def export_bundle(
    tables: bool = True, links: bool = True, conditions: bool = True, user: User = Depends(require_user)
) -> dict:
    state = _load_state(user)
    bundle: dict = {
        "format": _FORMAT,
        "version": _VERSION,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if tables:
        bundle["tables"] = [t.model_dump() for t in state.tables]
    if links:
        bundle["links"] = [link.model_dump() for link in state.links]
    if conditions:
        bundle["conditions"] = {
            "library": [e.model_dump() for e in state.library],
            "names": state.names,
            "origins": state.origins,
            "value_names": state.value_names,
        }
    return bundle


# ---------------------------------------------------------------------------
# Merge (pure: no I/O)
# ---------------------------------------------------------------------------
def _parse_all(items: list[dict], model) -> tuple[list, int]:
    """(valid items, number of invalid ones)."""
    out, bad = [], 0
    for item in items:
        try:
            out.append(model(**item))
        except (TypeError, ValueError):
            bad += 1
    return out, bad


def _unique_alias(wanted: str, taken: set[str]) -> str:
    """`wanted` when free, otherwise with a numeric suffix; '' when it isn't a valid alias."""
    if not wanted or not _ALIAS_RE.match(wanted):
        return ""
    alias, n = wanted, 1
    while alias.lower() in taken:
        n += 1
        suffix = str(n)
        alias = wanted[: 20 - len(suffix)] + suffix
    return alias


def _merge_links(base: list[SnLink], imported: list[SnLink]):
    """(links, old id -> local id, old alias (lower) -> local alias, added, reused, skipped)."""
    links = [link.model_copy() for link in base]
    by_url = {link.url: link for link in links}
    taken = {link.alias.lower() for link in links if link.alias}
    id_map: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    changed: list[str] = []
    added = reused = skipped = 0
    for imp in imported:
        url = imp.url.strip()
        wanted = imp.alias.strip()
        if len(url) > 4000 or not _is_servicenow_url(url):
            skipped += 1
            continue
        link = by_url.get(url)
        if link:
            reused += 1
            if not link.alias and wanted:
                link.alias = _unique_alias(wanted, taken)
                if link.alias:
                    taken.add(link.alias.lower())
        else:
            if len(links) >= _MAX_LINKS:
                skipped += 1
                continue
            alias = _unique_alias(wanted, taken)
            link = SnLink(
                id=uuid.uuid4().hex[:12], name=imp.name.strip()[:100], url=url, note=imp.note.strip()[:500], alias=alias
            )
            links.append(link)
            by_url[url] = link
            if alias:
                taken.add(alias.lower())
            added += 1
        if imp.id:
            id_map[imp.id] = link.id
        if wanted:
            alias_map[wanted.lower()] = link.alias
            if link.alias and link.alias.lower() != wanted.lower():
                changed.append(f"{wanted} → {link.alias}")
    return links, id_map, alias_map, changed, added, reused, skipped


def _rewrite_cell(value: str, id_map: dict[str, str], alias_map: dict[str, str]) -> str:
    v = value.strip()
    if v.startswith(_REF_PREFIX):
        old = v[len(_REF_PREFIX):]
        return _REF_PREFIX + id_map.get(old, old)
    if v.startswith("="):
        def sub(m: re.Match) -> str:
            new = alias_map.get(m.group(1).lower())
            return f"[{new}]" if new and new.lower() != m.group(1).lower() else m.group(0)

        return _FORMULA_ALIAS_RE.sub(sub, v)
    return value


def _is_dangling(value: str, link_ids: set[str], aliases: set[str]) -> bool:
    v = value.strip()
    if v.startswith(_REF_PREFIX):
        return v[len(_REF_PREFIX):] not in link_ids
    if v.startswith("="):
        return any(a.lower() not in aliases for a in _FORMULA_ALIAS_RE.findall(v))
    return False


def _merge_tables(base: list[SnTable], imported: list[SnTable], id_map, alias_map, links: list[SnLink]):
    """(tables, renamed titles, dropped cells / totals, cells whose link reference doesn't resolve)."""
    tables = list(base)
    titles = {t.title for t in tables}
    link_ids = {link.id for link in links}
    aliases = {link.alias.lower() for link in links if link.alias}
    renamed: list[str] = []
    dropped = dangling = 0
    for imp in imported:
        title = imp.title
        if title in titles:
            new, n = f"{title}（导入）", 1
            while new in titles:
                n += 1
                new = f"{title}（导入 {n}）"
            renamed.append(f"{title} → {new}")
            title = new
        titles.add(title)
        cells: dict[str, str] = {}
        for key, value in imp.cells.items():
            m = _CELL_KEY_RE.match(key)
            if not m or int(m.group(1)) >= len(imp.rows) or int(m.group(2)) >= len(imp.cols):
                dropped += 1
                continue
            if not value.strip():
                continue
            value = _rewrite_cell(value, id_map, alias_map)
            if _is_dangling(value, link_ids, aliases):
                dangling += 1
            cells[key] = value
        sums: dict[str, list[int]] = {}
        for key, members in imp.sums.items():
            if _SUM_KEY_RE.match(key) and all(isinstance(i, int) and i >= 0 for i in members):
                sums[key] = members
            else:
                dropped += 1
        tables.append(
            SnTable(id=f"t{uuid.uuid4().hex[:8]}", title=title, rows=imp.rows, cols=imp.cols, cells=cells, sums=sums)
        )
    return tables, renamed, dropped, dangling


def _merge_library(base: list[SnLibraryEntry], imported: list[dict]):
    """(library, added, skipped)."""
    library = list(base)
    known = {(e.table, e.raw) for e in library}
    added = skipped = 0
    for item in imported:
        try:
            e = SnLibraryEntry(**item)
        except (TypeError, ValueError):
            skipped += 1
            continue
        e.table, e.raw, e.note = e.table.strip(), e.raw.strip(), e.note.strip()[:500]
        if not _TABLE_RE.match(e.table) or len(e.raw) > 1000 or not sn_query.is_valid_unit(e.raw):
            skipped += 1
        elif (e.table, e.raw) in known:
            continue
        elif len(library) >= _MAX_LIBRARY_CONDITIONS:
            skipped += 1
        else:
            library.append(e)
            known.add((e.table, e.raw))
            added += 1
    return library, added, skipped


def _merge_names(base: dict[str, str], imported: dict) -> dict[str, str]:
    names = dict(base)
    for raw, name in imported.items():
        if not isinstance(raw, str) or not isinstance(name, str) or raw in names:
            continue
        name = name.strip()[:100]
        if name and len(raw) <= 1000 and sn_query.is_valid_unit(raw) and len(names) < _MAX_CONDITION_NAMES:
            names[raw] = name
    return names


def _merge_origins(base: dict[str, dict[str, str]], imported: dict) -> dict[str, dict[str, str]]:
    origins = {t: dict(o) for t, o in base.items()}
    for table, o in imported.items():
        if not isinstance(table, str) or not _TABLE_RE.match(table) or not isinstance(o, dict) or table in origins:
            continue
        host, app_path = o.get("host"), o.get("app_path", "")
        if (
            isinstance(host, str) and isinstance(app_path, str) and len(app_path) <= 500
            and _is_servicenow_url(f"https://{host}")
        ):
            origins[table] = {"host": host, "app_path": app_path}
    return origins


def _merge_value_names(base: dict[str, dict], imported: dict) -> dict[str, dict]:
    out = copy.deepcopy(base)
    total = sum(len(v) for e in out.values() for v in (e.get("values") or {}).values())
    for table, entry in imported.items():
        if not isinstance(table, str) or not _TABLE_RE.match(table) or not isinstance(entry, dict):
            continue
        if table not in out and len(out) >= _MAX_NAME_TABLES:
            continue
        dest = out.setdefault(table, {"fields": {}, "values": {}})
        fields = dest.setdefault("fields", {})
        values = dest.setdefault("values", {})
        for f, label in (entry.get("fields") or {}).items():
            if isinstance(f, str) and isinstance(label, str) and f not in fields:
                fields[f[:100]] = label[:200]
        for f, names in (entry.get("values") or {}).items():
            if not isinstance(f, str) or not isinstance(names, dict):
                continue
            bucket = values.setdefault(f[:100], {})
            for value, name in names.items():
                if isinstance(value, str) and isinstance(name, str) and value not in bucket and total < _MAX_NAME_ENTRIES:
                    bucket[value[:100]] = name[:200]
                    total += 1
    return out


def _merge(cur: _State, bundle: SnBundle, mode: str, wanted: set[str]) -> tuple[_State, SnMigrateResult, set[str]]:
    replace = mode == "replace"
    new = _State(**{k: getattr(cur, k) for k in ("tables", "links", "library", "names", "origins", "value_names")})
    result = SnMigrateResult(
        present=[], current=SnMigrateCounts(tables=len(cur.tables), links=len(cur.links), conditions=len(cur.library)),
        exported_at=bundle.exported_at,
    )

    imp_links, bad_links = _parse_all(bundle.links or [], SnLink)
    imp_tables, bad_tables = _parse_all(bundle.tables or [], SnTable)
    cond = bundle.conditions
    result.available = SnMigrateCounts(
        tables=len(imp_tables), links=len(imp_links), conditions=len(cond.library) if cond else 0
    )
    result.present = [
        s for s in _SECTIONS
        if getattr(result.available, s) > 0 and (s != "conditions" or cond is not None)
    ]
    sections = wanted & set(result.present)
    result.skipped.tables, result.skipped.links = bad_tables, bad_links

    id_map: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    if "links" in sections:
        links, id_map, alias_map, changed, added, reused, skipped = _merge_links([] if replace else cur.links, imp_links)
        if links or not replace:
            new.links = links
        else:
            # Every imported link was rejected: replacing would only wipe the local ones.
            sections = sections - {"links"}
        result.added.links, result.reused_links, result.skipped.links = added, reused, skipped + bad_links
        result.changed_aliases = changed
    if "tables" in sections:
        tables, renamed, dropped, dangling = _merge_tables(
            [] if replace else cur.tables, imp_tables, id_map, alias_map, new.links
        )
        new.tables = tables
        result.added.tables = len(imp_tables)
        result.renamed_tables, result.dropped_cells, result.dangling_refs = renamed, dropped, dangling
    if "conditions" in sections and cond is not None:
        new.library, result.added.conditions, result.skipped.conditions = _merge_library(
            [] if replace else cur.library, cond.library
        )
        new.names = _merge_names({} if replace else cur.names, cond.names)
        new.origins = _merge_origins({} if replace else cur.origins, cond.origins)
        new.value_names = _merge_value_names({} if replace else cur.value_names, cond.value_names)
    return new, result, sections


# ---------------------------------------------------------------------------
# Preview / import
# ---------------------------------------------------------------------------
class SnMigrateRequest(BaseModel):
    bundle: dict
    mode: Literal["merge", "replace"] = "merge"
    sections: list[Literal["tables", "links", "conditions"]] = list(_SECTIONS)


def _parse_bundle(raw: dict) -> SnBundle:
    try:
        bundle = SnBundle(**raw)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="不是有效的迁移文件") from None
    if bundle.format != _FORMAT:
        raise HTTPException(status_code=400, detail="不是 ServiceNow 迁移文件（格式不匹配）")
    if bundle.version < 1 or bundle.version > _VERSION:
        raise HTTPException(status_code=400, detail=f"文件版本（v{bundle.version}）不受支持，请先升级本应用再导入")
    return bundle


def _plan(user: User, body: SnMigrateRequest) -> tuple[_State, _State, SnMigrateResult, set[str]]:
    bundle = _parse_bundle(body.bundle)
    cur = _load_state(user)
    new, result, sections = _merge(cur, bundle, body.mode, set(body.sections))
    return cur, new, result, sections


@router.post("/api/servicenow/migrate/preview")
def preview_import(body: SnMigrateRequest, user: User = Depends(require_user)) -> SnMigrateResult:
    return _plan(user, body)[2]


@router.post("/api/servicenow/migrate/import")
def import_bundle(body: SnMigrateRequest, user: User = Depends(require_user)) -> SnMigrateResult:
    cur, new, result, sections = _plan(user, body)
    if not sections:
        raise HTTPException(status_code=400, detail="没有可导入的内容")
    if "links" in sections:
        # Keep the conditions of links that are about to be dropped, as save_links does.
        _sync_library(user.id, cur.links)
        _set_user_setting(user.id, _LINKS_KEY, SnLinks(links=new.links).model_dump_json())
    if "tables" in sections:
        _set_user_setting(user.id, _SETTING_KEY, SnConfig(tables=new.tables).model_dump_json())
    if "conditions" in sections:
        _save_library(user.id, new.library)
        _set_user_setting(user.id, _CONDITION_NAMES_KEY, json.dumps(new.names, ensure_ascii=False))
        _save_origins(user.id, new.origins)
        _set_user_setting(user.id, _VALUE_NAMES_KEY, json.dumps(new.value_names, ensure_ascii=False))
    if "links" in sections:
        _sync_library(user.id, new.links)
    result.applied = True
    return result
