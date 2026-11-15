"""Merge rules of the ServiceNow migrate import (pure logic, no database)."""
from app.routers.servicenow.links import SnLibraryEntry, SnLink
from app.routers.servicenow.migrate import SnBundle, SnBundleConditions, _State, _merge
from app.routers.servicenow.tables import SnTable

URL_A = "https://dsv.service-now.com/x/app/simplelist/x_case/params/query/state%3D1"
URL_B = "https://dsv.service-now.com/x/app/simplelist/x_case/params/query/state%3D2"
URL_C = "https://dsv.service-now.com/x/app/simplelist/x_case/params/query/state%3D3"


def _state(**kw) -> _State:
    base = dict(tables=[], links=[], library=[], names={}, origins={}, value_names={})
    base.update(kw)
    return _State(**base)


def _bundle(tables=None, links=None, conditions=None) -> SnBundle:
    return SnBundle(format="servicenow-migrate", version=1, tables=tables, links=links, conditions=conditions)


ALL = {"tables", "links", "conditions"}


def test_refs_and_formulas_follow_remapped_links():
    local = _state(links=[SnLink(id="L1", url=URL_A, alias=""), SnLink(id="L2", url=URL_C, alias="PR")],
        tables=[SnTable(id="x", title="T")],
    )
    bundle = _bundle(
        links=[
            {"id": "old1", "url": URL_A, "alias": "PB"},  # same URL as a local link: reused, takes the alias
            {"id": "old2", "url": URL_B, "alias": "PR"},  # alias already taken locally: gets a suffix
        ],
        tables=[{"id": "o", "title": "T", "rows": ["r"], "cols": ["c", "d"],
                 "cells": {"0,0": "ref:old2", "0,1": "=[PB]+[PR]", "5,5": URL_A}}],
    )
    new, result, _ = _merge(local, bundle, "merge", ALL)

    assert [link.alias for link in new.links] == ["PB", "PR", "PR2"]
    assert result.reused_links == 1 and result.added.links == 1
    imported = new.tables[1]
    assert imported.title == "T（导入）" and result.renamed_tables == ["T → T（导入）"]
    assert imported.cells["0,0"] == f"ref:{new.links[2].id}"
    assert imported.cells["0,1"] == "=[PB]+[PR2]"
    assert result.dropped_cells == 1 and result.dangling_refs == 0


def test_replace_discards_local_and_flags_unresolved_refs():
    local = _state(links=[SnLink(id="L1", url=URL_A)], tables=[SnTable(id="x", title="Old")])
    bundle = _bundle(
        links=[{"id": "o1", "url": "https://evil.example.com/list", "alias": ""}],
        tables=[{"id": "o", "title": "New", "rows": ["r"], "cols": ["c"], "cells": {"0,0": "ref:o1"}}],
    )
    new, result, sections = _merge(local, bundle, "replace", ALL)

    assert sections == {"tables"}  # the only link was rejected, so the links section is empty
    assert [t.title for t in new.tables] == ["New"]
    assert new.links == local.links
    assert result.dangling_refs == 1


def test_conditions_merge_keeps_local_names_and_skips_invalid():
    local = _state(
        library=[SnLibraryEntry(table="x_case", raw="state=1")],
        names={"state=1": "Mine"},
        origins={"x_case": {"host": "dsv.service-now.com", "app_path": ""}},
    )
    cond = SnBundleConditions(
        library=[{"table": "x_case", "raw": "state=1"}, {"table": "x_case", "raw": "state=2"},
                 {"table": "bad table", "raw": "state=3"}],
        names={"state=1": "Theirs", "state=2": "Two"},
        origins={"x_case": {"host": "other.service-now.com", "app_path": ""},
                 "x_new": {"host": "dsv.service-now.com", "app_path": "/x"},
                 "x_evil": {"host": "evil.example.com", "app_path": ""}},
    )
    new, result, _ = _merge(local, _bundle(conditions=cond), "merge", {"conditions"})

    assert [e.raw for e in new.library] == ["state=1", "state=2"]
    assert (result.added.conditions, result.skipped.conditions) == (1, 1)
    assert new.names == {"state=1": "Mine", "state=2": "Two"}
    assert set(new.origins) == {"x_case", "x_new"} and new.origins["x_case"]["host"] == "dsv.service-now.com"


def test_servicenow_url_hosts():
    from app.routers.servicenow.common import _is_servicenow_url
    ok = [
        "https://dsv.service-now.com/x/a",
        "https://servicecentral.dsv.com/x/dsva2/enterprise-integrations/list/params/list-id/81b0/tiny-id/lqki",
    ]
    bad = [
        "http://servicecentral.dsv.com/x",
        "https://servicecentral.dsv.com.evil.com/x",
        "https://evil-servicecentral.dsv.com/x",
        "https://dsv.com/x",
    ]
    assert all(_is_servicenow_url(u) for u in ok)
    assert not any(_is_servicenow_url(u) for u in bad)


def test_rehost_moves_urls_and_merges_duplicate_links():
    from app.routers.servicenow.rehost import _rehost_state
    new_a = URL_A.replace("dsv.service-now.com", "servicecentral.dsv.com")
    other = "https://other.service-now.com/x/app/simplelist/x_case/params/query/state%3D9"
    state = _state(
        links=[
            SnLink(id="L1", url=URL_A, alias=""),
            SnLink(id="L2", url=new_a, alias="PR"),  # same URL as L1 once moved; L1 stays and takes the alias
            SnLink(id="L3", url=other, alias="OT"),
        ],
        tables=[SnTable(id="t", title="T", rows=["r"], cols=["a", "b", "c", "d"],
                        cells={"0,0": URL_A, "0,1": "ref:L2", "0,2": "=[PR]+[OT]", "0,3": other})],
        origins={"x_case": {"host": "dsv.service-now.com", "app_path": "/x"}},
    )
    new, r = _rehost_state(state)
    assert [(l.id, l.url, l.alias) for l in new.links] == [("L1", new_a, "PR"), ("L3", other, "OT")]
    assert new.tables[0].cells == {"0,0": new_a, "0,1": "ref:L1", "0,2": "=[PR]+[OT]", "0,3": other}
    assert new.origins["x_case"]["host"] == "servicecentral.dsv.com"
    assert (r.links_changed, r.links_merged, r.cells_changed, r.refs_remapped, r.origins_changed) == (1, 1, 1, 1, 1)
