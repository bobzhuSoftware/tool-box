"""Split ServiceNow list URLs into a table and condition fragments, and build them back as a
classic or a Next Experience (simplelist) link. Pure functions, no I/O.

A query is conditions joined by "^" (AND). "^OR" binds to the condition right before it
(A^B^C^ORD means A AND B AND (C OR D)), so a condition plus its "^OR..." followers is kept together
as one unit such as "C^ORD"; units are checked, unchecked and AND-ed as a whole. Multi-set queries
(^NQ, ^EQ) are not supported; sort/group directives (^ORDERBY..., ^GROUPBY...) don't change
a record count, so they are dropped.
"""
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

_FIELD_RE = re.compile(r"^[a-z_][a-z0-9_.]*")
_COMPARISONS = ("!=", ">=", "<=", "=", ">", "<")
DEFAULT_APP_PATH = "/x/dsva2/enterprise-integrations"

# Friendly names for conditions seen in this deployment's saved links.
KNOWN_LABELS = {
    "business_analystISEMPTY": "Business analyst 为空",
    "state!=1000": "State ≠ Cancelled",
    "state!=1300": "State ≠ Closed Complete",
    "sub_phase=1": "Sub phase = Pre-Sales",
    "sub_phase=2": "Sub phase = Requests",
    "origin_of_request=schenker_origin": "Origin of Request = Schenker",
    "origin_of_request!=schenker_origin": "Origin of Request ≠ Schenker",
    "division=420b49d01b3ae5103620b6c5464bcb89": "Division = Air & Sea IT",
    "assignment_group!=2dafd3ec2b6d7690db00f454c891bf54": "Assignment group ≠ IT.EDI.SC.Integration",
    "sys_tags.e965fb502b627adcbe57f2c0ce91bf48EXCLUDINGe965fb502b627adcbe57f2c0ce91bf48": "Tags excluding STC",
    "short_descriptionNOT LIKEAPI": "Short description 不含 API",
    "descriptionNOT LIKEnGXD": "Description 不含 nGXD",
    "sys_created_onBETWEENjavascript:gs.beginningOfLast7Days()@javascript:gs.endOfToday()": "Created 在过去 7 天内",
}


def _classic_target(url: str) -> str:
    """'<table>_list.do[?params]' from a classic list link (wrapped, nav_to or direct), or ''."""
    p = urlparse(url)
    m = re.search(r"/target/(.+)$", unquote(p.path))
    if m:
        return m.group(1)
    if p.path.endswith("/nav_to.do"):
        return parse_qs(p.query).get("uri", [""])[0]
    if re.search(r"[^/]+_list\.do$", p.path):
        return p.path.rsplit("/", 1)[1] + (f"?{p.query}" if p.query else "")
    return ""


def parse_list_url(url: str) -> dict:
    """{kind, table, query, title, host, app_path}; table is '' when the link isn't a list link.

    A saved-list link (kind 'savedlist', .../list/params/list-id/<id>/tiny-id/<tiny>) keeps its filter
    on the server, so table and query stay '' until it is resolved in a signed-in browser."""
    p = urlparse(url)
    out = {"kind": "", "table": "", "query": "", "title": "", "host": p.netloc, "app_path": ""}
    s = re.search(r"/list/params/list-id/[^/]+/tiny-id/[^/?#]+", p.path)
    if s:
        out.update(kind="savedlist", app_path=p.path[: s.start()])
        return out
    m = re.search(r"/simplelist/([^/]+)", p.path)
    if m:
        q = re.search(r"/query/(.+)$", p.path)
        t = re.search(r"/list-title/([^/]+)", p.path)
        out.update(
            kind="simplelist",
            table=m.group(1),
            query=unquote(q.group(1)) if q else "",
            title=unquote(t.group(1)) if t else "",
            app_path=p.path[: m.start()],
        )
        return out
    tm = re.match(r"/?([A-Za-z0-9_]+)_list\.do(?:\?(.*))?$", _classic_target(url))
    if tm:
        query = parse_qs(tm.group(2) or "").get("sysparm_query", [""])[0]
        out.update(kind="classic", table=tm.group(1), query=query)
    return out


def _is_or_part(part: str) -> bool:
    return part.startswith("OR") and not part.startswith("ORDERBY")


def is_unsupported_fragment(part: str) -> str:
    """Reason a single '^'-separated part can't be recombined, or ''. A '^OR' part is fine
    after another condition (see split_query) and is not flagged here."""
    if part == "EQ" or part.startswith("NQ"):
        return "含 ^NQ（多组查询），暂不支持拆分"
    return ""


def is_valid_unit(unit: str) -> bool:
    """A single condition, optionally followed by '^OR<condition>' terms (an OR group)."""
    terms = unit.split("^")
    head = terms[0]
    if not head or _is_or_part(head) or head.startswith(("ORDERBY", "GROUPBY")) or is_unsupported_fragment(head):
        return False
    return all(_is_or_part(t) and len(t) > 2 and not is_unsupported_fragment(t[2:]) for t in terms[1:])


def split_query(query: str) -> tuple[list[str], str]:
    """(unique units in order, unsupported reason or ''). An OR group is one unit, e.g. 'a=1^ORb=2'."""
    groups: list[list[str]] = []
    for part in query.split("^"):
        if not part:
            continue
        if _is_or_part(part):
            if not groups:
                return [], "^OR 前面没有条件，无法拆分"
            if len(part) == 2:
                continue
            reason = is_unsupported_fragment(part[2:])
            if reason:
                return [], reason
            groups[-1].append(part)
            continue
        reason = is_unsupported_fragment(part)
        if reason:
            return [], reason
        if part.startswith(("ORDERBY", "GROUPBY")):
            continue
        groups.append([part])
    return list(dict.fromkeys("^".join(g) for g in groups)), ""


_OP_WORDS = (
    ("NOT LIKE", "不含 {}"), ("STARTSWITH", "以 {} 开头"), ("ENDSWITH", "以 {} 结尾"), ("LIKE", "包含 {}"),
    ("NOT IN", "不在 {}"), ("IN", "在 {}"),
    ("!=", "≠ {}"), (">=", "≥ {}"), ("<=", "≤ {}"), ("=", "= {}"), (">", "> {}"), ("<", "< {}"),
)


# Readable text for the date helpers that saved lists use, e.g. javascript:gs.beginningOfLastWeek().
_GS_RE = re.compile(r"javascript:gs\.(\w+)\(([^()]*)\)")
_GS_WORDS = {
    f"{edge}{span}": f"{when}{word}"
    for edge, word in (("beginningOf", "开始"), ("endOf", "结束"))
    for span, when in (
        ("Today", "今天"), ("Yesterday", "昨天"), ("Tomorrow", "明天"),
        ("ThisWeek", "本周"), ("LastWeek", "上周"), ("NextWeek", "下周"),
        ("ThisMonth", "本月"), ("LastMonth", "上月"), ("NextMonth", "下月"),
        ("ThisQuarter", "本季度"), ("LastQuarter", "上季度"), ("NextQuarter", "下季度"),
        ("ThisYear", "今年"), ("LastYear", "去年"), ("NextYear", "明年"),
        ("Last7Days", "过去 7 天"), ("Last30Days", "过去 30 天"),
    )
}
_GS_AGO = {
    "daysAgo": "{} 天前", "daysAgoStart": "{} 天前开始", "daysAgoEnd": "{} 天前结束",
    "hoursAgo": "{} 小时前", "minutesAgo": "{} 分钟前", "monthsAgo": "{} 个月前",
}


def _gs_text(m: re.Match) -> str:
    name, arg = m.group(1), m.group(2).strip().strip("'\"")
    if name in _GS_WORDS:
        return _GS_WORDS[name]
    if name in _GS_AGO and arg.isdigit():
        return _GS_AGO[name].format(arg)
    return m.group(0)


def _value_text(op: str, field: str, value: str, display: dict | None) -> tuple[str, bool]:
    """(readable value, whether anything was translated). Names come from the cached lookups in `display`."""
    names = ((display or {}).get("values") or {}).get(field) or {}
    many = op in ("IN", "NOT IN")
    out = []
    changed = False
    for v in value.split(",") if many else [value]:
        shown = names.get(v) or _GS_RE.sub(_gs_text, v)
        changed |= shown != v
        out.append(shown)
    return ("、" if many else "").join(out), changed


def _term_info(term: str, display: dict | None = None) -> tuple[str, str, bool]:
    """(field shown, readable condition without the field, changed) for one term; field '' if the operator
    is unknown. `changed` is True when a field label, a value name or a date helper was translated."""
    m = _FIELD_RE.match(term)
    field = m.group(0) if m else ""
    rest = term[len(field):]
    for op, fmt in _OP_WORDS:
        if not field or not rest.startswith(op) or (op == "IN" and rest.startswith("INSTANCEOF")):
            continue
        text, changed = _value_text(op, field, rest[len(op):], display)
        label = ((display or {}).get("fields") or {}).get(field)
        return label or field, fmt.format(text), bool(changed or label)
    return "", term, False


def _term_parts(term: str, display: dict | None = None) -> tuple[str, str]:
    field, text, _ = _term_info(term, display)
    return field, text


def _group_label(unit: str, custom: dict[str, str] | None, display: dict | None = None) -> str:
    terms = [t[2:] if i else t for i, t in enumerate(unit.split("^"))]
    if any((custom or {}).get(t) or t in KNOWN_LABELS for t in terms):
        parts = [(custom or {}).get(t) or KNOWN_LABELS.get(t) or " ".join(_term_parts(t, display)).strip() for t in terms]
        return " 或 ".join(parts)
    infos = [_term_info(t, display) for t in terms]
    fields = {f for f, _, _ in infos}
    if len(fields) == 1 and "" not in fields:
        texts = [text for _, text, _ in infos]
        if any(c for _, _, c in infos) and all(t.startswith("= ") for t in texts):
            texts = [t[2:] for t in texts]
        return f"{infos[0][0]}：" + " 或 ".join(texts)
    return " 或 ".join(f"{f} {text}".strip() for f, text, _ in infos)


def describe(fragment: str) -> dict:
    """field / op / value of a unit. op is '=', '!=', ... for plain comparisons, else ''.
    An OR group has no single field, so it never counts as conflicting with another condition."""
    if "^" in fragment:
        return {"field": "", "op": "", "value": fragment}
    if fragment.startswith("sys_tags."):
        return {"field": "sys_tags", "op": "", "value": fragment}
    m = _FIELD_RE.match(fragment)
    field = m.group(0) if m else ""
    rest = fragment[len(field):]
    op = next((c for c in _COMPARISONS if rest.startswith(c)), "")
    return {"field": field, "op": op, "value": rest[len(op):] if op else rest}


def label_for(fragment: str, custom: dict[str, str] | None = None, display: dict | None = None) -> str:
    """Display text of a condition. `display` ({"fields": {field: label}, "values": {field: {value: name}}})
    is the cached lookup for the condition's table; the raw condition is never changed."""
    if (custom or {}).get(fragment) or fragment in KNOWN_LABELS:
        return (custom or {}).get(fragment) or KNOWN_LABELS[fragment]
    if "^" in fragment:
        return _group_label(fragment, custom, display)
    field, text, changed = _term_info(fragment, display)
    return f"{field} {text}" if changed else fragment


_TARGET_RE = re.compile(r"^([a-z_][a-z0-9_]*)(NOT IN|IN|!=|=)(.+)$")
_RAW_TERM_RE = re.compile(r"^(OR)?([a-z_][a-z0-9_]*)(NOT IN|IN|!=|=)(.+)$")
_TARGET_VALUE_RE = re.compile(r"^[\w\- .]{1,100}$")


def readable_raw(fragment: str, display: dict | None = None) -> str:
    """The condition as written, with ids and codes replaced by their cached names (display only: the
    result is not a valid condition). Anything without a name is left as it is."""
    values = (display or {}).get("values") or {}
    out = []
    for term in fragment.split("^"):
        m = _RAW_TERM_RE.match(term)
        if m:
            names = values.get(m.group(2)) or {}
            items = m.group(4).split(",") if m.group(3) in ("IN", "NOT IN") else [m.group(4)]
            if any(names.get(v) for v in items):
                term = f"{m.group(1) or ''}{m.group(2)}{m.group(3)}" + ",".join(names.get(v) or v for v in items)
        out.append(term)
    return "^".join(out)


def name_targets(units: list[str]) -> dict[str, list[str]]:
    """field -> values compared with =, != or IN in the conditions: the ones that may be an id or a code
    with a readable name in ServiceNow. Dot-walked fields and date helpers are left out."""
    out: dict[str, list[str]] = {}
    for unit in units:
        for i, term in enumerate(unit.split("^")):
            m = _TARGET_RE.match(term[2:] if i else term)
            if not m or m.group(3).startswith("javascript:"):
                continue
            bucket = out.setdefault(m.group(1), [])
            for v in m.group(3).split(",") if m.group(2) in ("IN", "NOT IN") else [m.group(3)]:
                if _TARGET_VALUE_RE.match(v) and v not in bucket:
                    bucket.append(v)
    return {f: v for f, v in out.items() if v}


def missing_targets(targets: dict[str, list[str]], display: dict | None) -> dict[str, list[str]]:
    """The part of `targets` the cached lookup in `display` hasn't covered yet. A value that was looked up
    but has no name is stored with an empty name, so it isn't asked for again."""
    fields = (display or {}).get("fields") or {}
    values = (display or {}).get("values") or {}
    out: dict[str, list[str]] = {}
    for field, vals in targets.items():
        if field not in fields:
            out[field] = list(vals)
        elif field in values:
            miss = [v for v in vals if v not in values[field]]
            if miss:
                out[field] = miss
    return out


def build_url(kind: str, host: str, table: str, query: str, title: str = "", app_path: str = "") -> str:
    """Absolute link opening <table> filtered by <query>, as 'classic' or 'simplelist'."""
    if kind == "simplelist":
        return (
            f"https://{host}{app_path or DEFAULT_APP_PATH}/simplelist/{table}"
            f"/params/list-title/{quote(title or table, safe='')}/query/{quote(query, safe='!()')}"
        )
    inner = f"{table}_list.do?sysparm_query={quote(query, safe='')}&sysparm_first_row=1&sysparm_view="
    return f"https://{host}/now/nav/ui/classic/params/target/{quote(inner, safe='')}"
