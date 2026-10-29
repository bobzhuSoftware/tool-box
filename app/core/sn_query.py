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
    """{kind, table, query, title, host, app_path}; table is '' when the link isn't a list link."""
    p = urlparse(url)
    out = {"kind": "", "table": "", "query": "", "title": "", "host": p.netloc, "app_path": ""}
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
    ("!=", "≠ {}"), (">=", "≥ {}"), ("<=", "≤ {}"), ("=", "= {}"), (">", "> {}"), ("<", "< {}"),
)


def _term_parts(term: str) -> tuple[str, str]:
    """(field, readable condition without the field) for one term; field '' if the operator is unknown."""
    m = _FIELD_RE.match(term)
    field = m.group(0) if m else ""
    rest = term[len(field):]
    for op, fmt in _OP_WORDS:
        if field and rest.startswith(op):
            return field, fmt.format(rest[len(op):])
    return "", term


def _group_label(unit: str, custom: dict[str, str] | None) -> str:
    terms = [t[2:] if i else t for i, t in enumerate(unit.split("^"))]
    if any((custom or {}).get(t) or t in KNOWN_LABELS for t in terms):
        parts = [(custom or {}).get(t) or KNOWN_LABELS.get(t) or " ".join(_term_parts(t)).strip() for t in terms]
        return " 或 ".join(parts)
    parts = [_term_parts(t) for t in terms]
    fields = {f for f, _ in parts}
    if len(fields) == 1 and "" not in fields:
        return f"{parts[0][0]}：" + " 或 ".join(text for _, text in parts)
    return " 或 ".join(f"{f} {text}".strip() for f, text in parts)


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


def label_for(fragment: str, custom: dict[str, str] | None = None) -> str:
    if (custom or {}).get(fragment) or fragment in KNOWN_LABELS:
        return (custom or {}).get(fragment) or KNOWN_LABELS[fragment]
    return _group_label(fragment, custom) if "^" in fragment else fragment


def build_url(kind: str, host: str, table: str, query: str, title: str = "", app_path: str = "") -> str:
    """Absolute link opening <table> filtered by <query>, as 'classic' or 'simplelist'."""
    if kind == "simplelist":
        return (
            f"https://{host}{app_path or DEFAULT_APP_PATH}/simplelist/{table}"
            f"/params/list-title/{quote(title or table, safe='')}/query/{quote(query, safe='!()')}"
        )
    inner = f"{table}_list.do?sysparm_query={quote(query, safe='')}&sysparm_first_row=1&sysparm_view="
    return f"https://{host}/now/nav/ui/classic/params/target/{quote(inner, safe='')}"
