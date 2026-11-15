"""ServiceNow host migration: move saved URLs from the old instance (dsv.service-now.com) to the new
ServiceCentral portal (servicecentral.dsv.com).

The new portal serves the same app with the same table names, fields and condition values, so only the
host changes and path and query stay as they are (see docs/servicenow-servicecentral). Rewritten:
count-table cells, saved links and the table origins of the condition library. Cells that reference a
link by id ("ref:<id>") or by alias inside a formula keep working; when two links become the same URL
the duplicate is dropped and the references to it are pointed at the link that stays.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_user
from app.core.db import User
from app.core.settings import _set_user_setting

from .common import _LINKS_KEY, _SETTING_KEY
from .links import SnLink, SnLinks, _save_origins, _sync_library
from .migrate import _State, _load_state, _rewrite_cell
from .tables import SnConfig

router = APIRouter()

OLD_HOST = "dsv.service-now.com"
NEW_HOST = "servicecentral.dsv.com"
_OLD_PREFIX_RE = re.compile(rf"^https://{re.escape(OLD_HOST)}(?=[/?#]|$)", re.IGNORECASE)


class SnRehostResult(BaseModel):
    applied: bool = False
    old_host: str = OLD_HOST
    new_host: str = NEW_HOST
    # Links whose URL moved to the new host.
    links_changed: int = 0
    # Links dropped because they became the same URL as another link.
    links_merged: int = 0
    cells_changed: int = 0
    # Cells whose "ref:" or formula alias was pointed at the link that stayed after a merge.
    refs_remapped: int = 0
    origins_changed: int = 0


def _rehost_url(url: str) -> str:
    """`url` on the new host when it is on the old one; anything else is returned unchanged."""
    stripped = url.strip()
    return _OLD_PREFIX_RE.sub(f"https://{NEW_HOST}", stripped, count=1) if _OLD_PREFIX_RE.match(stripped) else url


def _rehost_links(links: list[SnLink]):
    """(links, old id -> kept id, old alias (lower) -> kept alias, rewritten count, merged count)."""
    by_url: dict[str, SnLink] = {}
    out: list[SnLink] = []
    id_map: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    rewritten = merged = 0
    for link in links:
        url = _rehost_url(link.url)
        rewritten += url != link.url
        first = by_url.get(url)
        if first is None:
            link = link.model_copy(update={"url": url})
            by_url[url] = link
            out.append(link)
            continue
        merged += 1
        id_map[link.id] = first.id
        if link.alias:
            if not first.alias:
                first.alias = link.alias
            alias_map[link.alias.lower()] = first.alias
    return out, id_map, alias_map, rewritten, merged


def _rehost_state(cur: _State) -> tuple[_State, SnRehostResult]:
    result = SnRehostResult()
    links, id_map, alias_map, result.links_changed, result.links_merged = _rehost_links(cur.links)

    tables = []
    for table in cur.tables:
        cells = {}
        for key, value in table.cells.items():
            new = _rehost_url(value)
            if new != value:
                result.cells_changed += 1
            else:
                new = _rewrite_cell(value, id_map, alias_map)
                if new.strip() != value.strip():
                    result.refs_remapped += 1
                else:
                    new = value
            cells[key] = new
        tables.append(table.model_copy(update={"cells": cells}))

    origins = {}
    for name, origin in cur.origins.items():
        if origin.get("host", "").lower() == OLD_HOST:
            origin = {**origin, "host": NEW_HOST}
            result.origins_changed += 1
        origins[name] = origin

    new_state = _State(
        tables=tables, links=links, library=cur.library, names=cur.names, origins=origins,
        value_names=cur.value_names,
    )
    return new_state, result


@router.post("/api/servicenow/migrate/rehost/preview")
def preview_rehost(user: User = Depends(require_user)) -> SnRehostResult:
    return _rehost_state(_load_state(user))[1]


@router.post("/api/servicenow/migrate/rehost")
def apply_rehost(user: User = Depends(require_user)) -> SnRehostResult:
    cur = _load_state(user)
    new, result = _rehost_state(cur)
    if not (result.links_changed or result.cells_changed or result.origins_changed):
        raise HTTPException(status_code=400, detail=f"没有需要迁移的内容（没有指向 {OLD_HOST} 的链接）")
    # Keep the conditions of links that are about to be merged away, as save_links does.
    _sync_library(user.id, cur.links)
    _set_user_setting(user.id, _LINKS_KEY, SnLinks(links=new.links).model_dump_json())
    _set_user_setting(user.id, _SETTING_KEY, SnConfig(tables=new.tables).model_dump_json())
    _save_origins(user.id, new.origins)
    _sync_library(user.id, new.links)
    result.applied = True
    return result
