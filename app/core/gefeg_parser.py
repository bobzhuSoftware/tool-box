"""Parser for GEFEG.FX "Message Implementation Guideline" PDF exports.

Turns the PDF into a plain dict (no DB access):

- ``meta``     cover page data (title, model, version, variant, issue date, author, print date)
- ``nodes``    every element/attribute of the *Guideline* section, in document order, each with
               occurrence / type / use / notes and its ``mappings`` (Map Instructions)
- ``changes``  entries of the *New Changes Summary* section

The layout of GEFEG.FX exports is fixed, so the parser relies on the text layer's
coordinates and colours instead of OCR:

- guideline: left column (x < 285) holds the tree (indentation = x), right column the annotation;
  grey ``xs:sequence`` rows are structural groups and are skipped; attributes are the nodes that
  carry a ``Use`` row and no ``Occurence`` row;
- Map Instruction labels are blue (0x80), property labels grey (0x808080), change flags red.
"""
import re

import fitz  # PyMuPDF

MAX_PDF_BYTES = 30 * 1024 * 1024

_TREE_MAX_X = 285          # left (tree) / right (annotation) column split in the Guideline section
_BODY_TOP, _BODY_BOTTOM = 95, 700
_CHANGES_TOP = 125
_INDENT_TOLERANCE = 2.0    # node is a child of the nearest earlier node whose x is smaller by more
_BLUE, _GREY, _RED = 0x80, 0x808080, 0xFF0000

_PROP_LABELS = {"Occurence", "Type", "Use", "General Notes"}
_MAP_LABELS = {"Condition", "Instruction", "Additional Info"}
_CHANGE_LABELS = {
    "Tag": "tag",
    "Path": "path",
    "Instruction Number": "number",
    "Condition": "condition",
    "Instruction": "instruction",
    "Additional Information": "additional_info",
    "General Notes": "notes",
}


class GefegError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- text helpers
class _Row:
    """One visual text row (all spans share a baseline) within one column."""

    __slots__ = ("y", "x0", "x1", "spans")

    def __init__(self, y: float, spans: list[dict]):
        self.y = y
        self.spans = sorted(spans, key=lambda s: s["bbox"][0])
        self.x0 = self.spans[0]["bbox"][0]
        self.x1 = max(s["bbox"][2] for s in self.spans)

    @property
    def first(self) -> dict:
        return self.spans[0]

    def text(self, skip_red: bool = False, skip_first: bool = False) -> str:
        parts, prev = [], None
        for i, s in enumerate(self.spans):
            if (skip_first and i == 0) or (skip_red and s["color"] == _RED):
                continue
            if prev is not None and s["bbox"][0] - prev > 1:
                parts.append(" ")
            parts.append(s["text"].strip())
            prev = s["bbox"][2]
        return "".join(parts).strip()

    def red(self) -> str:
        return " ".join(s["text"].strip() for s in self.spans if s["color"] == _RED).strip()


def _page_spans(page) -> list[dict]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            out.extend(s for s in line["spans"] if s["text"].strip())
    return out


def _rows(spans: list[dict]) -> list[_Row]:
    """Group spans into rows by baseline (spans of one PDF line can be split)."""
    rows: list[list[dict]] = []
    for s in sorted(spans, key=lambda s: (s["bbox"][1], s["bbox"][0])):
        if rows and abs(rows[-1][0]["bbox"][1] - s["bbox"][1]) <= 1.5:
            rows[-1].append(s)
        else:
            rows.append([s])
    return [_Row(r[0]["bbox"][1], r) for r in rows]


class _Text:
    """Accumulates wrapped lines of one field value and re-joins them.

    PDF lines are either soft-wrapped (join) or real line breaks (keep ``\\n``).
    A line is considered soft-wrapped when the next word would not have fit on it.
    """

    def __init__(self, limit: float, keep_breaks: bool = False):
        self.limit = limit
        self.keep_breaks = keep_breaks  # notes / code tables: keep every visual line
        self.lines: list[tuple[str, float, float]] = []  # text, x0, x1

    def add(self, text: str, x0: float, x1: float) -> None:
        text = text.strip()
        if text:
            self.lines.append((text, x0, x1))

    def __bool__(self) -> bool:
        return bool(self.lines)

    def value(self) -> str:
        out = ""
        for i, (text, _x0, _x1) in enumerate(self.lines):
            if i == 0:
                out = text
                continue
            prev, px0, px1 = self.lines[i - 1]
            if re.search(r"(/|[A-Za-z0-9]\.|\d-)$", prev):
                out += text  # broken inside a path / code, e.g. "C507." + "2005"
                continue
            if self.keep_breaks or re.match(r"^[A-Z]{3}\+", text):  # next EDIFACT segment example
                out += "\n" + text
                continue
            word = text.split(" ", 1)[0]
            char_w = (px1 - px0) / max(len(prev), 1)
            if px1 + char_w * (len(word) + 1) > self.limit:
                out += " " + text
            else:
                out += "\n" + text
        return out


# --------------------------------------------------------------------------- cover page
def _parse_meta(doc) -> dict:
    rows = _rows(_page_spans(doc[0]))
    texts = [r.text() for r in rows]
    meta = {"title": "", "model": "", "version": "", "variant": "", "issue_date": "", "author": "", "print_date": ""}
    for i, t in enumerate(texts):
        if t == "Message Implementation Guideline" and i + 1 < len(texts):
            meta["title"] = texts[i + 1]
        m = re.match(r"^(Model|Version|Variant|Issue date|Author):\s*(.*)$", t)
        if m:
            meta[m.group(1).lower().replace(" ", "_")] = m.group(2).strip()
    # Print date only appears in page headers/footers
    for pi in range(1, min(len(doc), 4)):
        text = doc[pi].get_text("text")
        m = re.search(r"Print date:\s*(\d{2}\.\d{2}\.\d{4})", text)
        if m:
            meta["print_date"] = m.group(1)
            break
    return meta


# --------------------------------------------------------------------------- guideline
class _Node:
    def __init__(self, name: str, x: float, page: int, is_group: bool, is_attr: bool):
        self.name = name
        self.x = x
        self.page = page
        self.is_group = is_group
        self.is_attr = is_attr
        self.props: dict[str, _Text] = {}
        self.mappings: list[dict] = []
        self.cur_map: dict | None = None
        self.cur_field: _Text | None = None
        self.parent: "_Node | None" = None


def _parse_occurrence(text: str) -> tuple[int | None, str | None]:
    m = re.match(r"^(\d+)\s*\.\.\s*(\d+|unbounded)$", text.strip())
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def _parse_guideline(doc, first_page: int) -> list[dict]:
    spans_by_page = {pi: _page_spans(doc[pi]) for pi in range(first_page, len(doc))}
    limit = max(
        (s["bbox"][2] for spans in spans_by_page.values() for s in spans
         if s["bbox"][0] >= _TREE_MAX_X and _BODY_TOP < s["bbox"][1] < _BODY_BOTTOM),
        default=550,
    )

    all_nodes: list[_Node] = []
    stack: list[_Node] = []
    current: _Node | None = None

    for pi in range(first_page, len(doc)):
        body = [s for s in spans_by_page[pi] if _BODY_TOP < s["bbox"][1] < _BODY_BOTTOM]
        left = _rows([s for s in body if s["bbox"][0] < _TREE_MAX_X])
        right = _rows([s for s in body if s["bbox"][0] >= _TREE_MAX_X])
        # a name row sorts just before the annotation row that shares its baseline
        events = [(r.y - 2, 0, r) for r in left] + [(r.y, 1, r) for r in right]
        events.sort(key=lambda e: (e[0], e[1]))
        continued = True  # until the first node / annotation row: the page continues the previous page's node

        for _y, kind, row in events:
            text = row.text()
            if kind == 0:
                if text == "Element/Attribute":
                    continue
                continued = False
                is_group = row.first["color"] == _GREY and text.startswith("xs:")
                is_attr = not is_group and bool(row.first["flags"] & 2)  # attributes are italic
                node = _Node(text, row.x0, pi + 1, is_group, is_attr)
                if not is_group:
                    while stack and stack[-1].x > node.x - _INDENT_TOLERANCE:
                        stack.pop()
                    node.parent = stack[-1] if stack else None
                    if not is_attr:  # attributes never have children
                        stack.append(node)
                    all_nodes.append(node)
                current = node
                continue

            if current is None or text == "Annotation":
                continue
            _annotation_row(current, row, limit, continued)
            continued = False

    return _finalize(all_nodes)


def _annotation_row(node: _Node, row: _Row, limit: float, continued: bool = False) -> None:
    first = row.first
    label = first["text"].strip()
    color = first["color"]
    value_x0 = row.spans[1]["bbox"][0] if len(row.spans) > 1 else row.x0

    red = row.red()

    if color == _BLUE and label == "Map Instructions":
        # GEFEG repeats this heading on a continuation page; the open instruction carries on
        if not (continued and node.cur_map is not None):
            node.cur_map, node.cur_field = None, None
        return
    if color == _BLUE and label == "Instruction Nr":
        node.cur_map = {"nr": row.text(skip_red=True, skip_first=True), "flag": red, "_f": {}}
        node.mappings.append(node.cur_map)
        node.cur_field = None
        return
    if red and node.cur_map is not None and not row.text(skip_red=True):
        node.cur_map["flag"] = (node.cur_map["flag"] + " " + red).strip()  # wrapped flag
        return

    if color in (_BLUE, _GREY) and label in _MAP_LABELS | _PROP_LABELS:
        value = row.text(skip_red=True, skip_first=True)
        field = _Text(limit, keep_breaks=label == "General Notes")
        field.add(value, value_x0, row.x1)
        if label in _MAP_LABELS:
            if node.cur_map is None:  # mapping without an "Instruction Nr" row
                node.cur_map = {"nr": "", "flag": "", "_f": {}}
                node.mappings.append(node.cur_map)
            node.cur_map["_f"][label] = field
        else:
            node.props[label] = field
        node.cur_field = field
        return

    if node.cur_field is not None:
        node.cur_field.add(row.text(skip_red=True), row.x0, row.x1)


def _finalize(nodes: list[_Node]) -> list[dict]:
    index = {id(n): i for i, n in enumerate(nodes)}
    paths: dict[int, str] = {}
    depths: dict[int, int] = {}
    seen: dict[str, int] = {}
    out = []
    for i, n in enumerate(nodes):
        props = {k: v.value() for k, v in n.props.items()}
        min_occ, max_occ = _parse_occurrence(props.get("Occurence", ""))
        use = props.get("Use", "")
        seg = ("@" + n.name) if n.is_attr else n.name
        parent = n.parent
        path = (paths[index[id(parent)]] + "/" + seg) if parent else seg
        seen[path] = seen.get(path, 0) + 1
        if seen[path] > 1:  # progress is keyed by path, so it must be unique
            path += f"#{seen[path]}"
        paths[i] = path
        depths[i] = (depths[index[id(parent)]] + 1) if parent else 0
        out.append({
            "seq": i,
            "parent_seq": index[id(parent)] if parent else None,
            "depth": depths[i],
            "kind": "attribute" if n.is_attr else "element",
            "name": n.name,
            "path": paths[i],
            "min_occurs": min_occ,
            "max_occurs": max_occ,
            "type": props.get("Type", ""),
            "use": use,
            "notes": props.get("General Notes", ""),
            "page": n.page,
            "mappings": [
                {
                    "position": j,
                    "nr": m["nr"],
                    "flag": m["flag"],
                    "condition": m["_f"].get("Condition", _Text(0)).value(),
                    "instruction": m["_f"].get("Instruction", _Text(0)).value(),
                    "additional_info": m["_f"].get("Additional Info", _Text(0)).value(),
                }
                for j, m in enumerate(n.mappings)
            ],
        })
    return out


# --------------------------------------------------------------------------- changes summary
def _parse_changes(doc, last_page: int) -> list[dict]:
    spans_by_page = {pi: _page_spans(doc[pi]) for pi in range(1, last_page)}
    limit = max(
        (s["bbox"][2] for spans in spans_by_page.values() for s in spans
         if s["bbox"][0] > 150 and _CHANGES_TOP < s["bbox"][1] < _BODY_BOTTOM),
        default=550,
    )
    items: list[dict] = []
    cur: dict | None = None
    field: _Text | None = None
    field_name = ""

    def close_field() -> None:
        if cur is not None and field is not None and field_name:
            value = field.value()
            if field_name == "path":
                value = value.replace("\n", "").replace(" /", "/")
            cur[field_name] = value

    for pi in range(1, last_page):
        body = [s for s in spans_by_page[pi] if _CHANGES_TOP < s["bbox"][1] < _BODY_BOTTOM]
        for row in _rows(body):
            first = row.first
            text = row.text(skip_red=True)
            if first["bbox"][0] < 90 and re.fullmatch(r"\d{2,3}", first["text"].strip()):
                close_field()
                cur = {"no": first["text"].strip(), "flag": ""}
                items.append(cur)
                field, field_name = None, ""
                if len(row.spans) == 1:
                    continue
                row = _Row(row.y, row.spans[1:])
                first = row.first
                text = row.text(skip_red=True)
            if cur is None:
                continue
            label = first["text"].strip()
            if first["bbox"][0] < 150 and label in _CHANGE_LABELS:
                close_field()
                field_name = _CHANGE_LABELS[label]
                field = _Text(limit, keep_breaks=field_name == "notes")
                value = row.text(skip_red=True, skip_first=True)
                field.add(value, row.spans[1]["bbox"][0] if len(row.spans) > 1 else row.x0, row.x1)
                if row.red():
                    cur["flag"] = (cur["flag"] + " " + row.red()).strip()
                continue
            if row.red() and not text:
                cur["flag"] = (cur["flag"] + " " + row.red()).strip()
                continue
            if field is not None:
                field.add(text, row.x0, row.x1)
    close_field()
    out = []
    for it in items:
        out.append({
            "no": it["no"],
            "tag": it.get("tag", ""),
            "path": it.get("path", ""),
            "number": it.get("number", ""),
            "flag": it["flag"],
            "condition": it.get("condition", ""),
            "instruction": it.get("instruction", ""),
            "additional_info": it.get("additional_info", ""),
            "notes": it.get("notes", ""),
        })
    return out


# --------------------------------------------------------------------------- entry point
def parse_gefeg_pdf(data: bytes) -> dict:
    if not data.startswith(b"%PDF"):
        raise GefegError("Not a valid PDF file")
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise GefegError(f"Cannot open the PDF: {exc}")
    try:
        if len(doc) < 2 or "GEFEG" not in doc[0].get_text("text").upper() \
                or "Message Implementation Guideline" not in doc[0].get_text("text"):
            raise GefegError("This is not a Message Implementation Guideline PDF exported from GEFEG.FX")

        sections: dict[str, int] = {}
        for pi in range(1, len(doc)):
            head = doc[pi].get_text("text")[:200]
            for name in ("New Changes Summary", "Structure", "Guideline"):
                if re.search(rf"^{name}$", head, re.M) and name not in sections:
                    sections[name] = pi
        if "Guideline" not in sections:
            raise GefegError("The Guideline section was not found in the PDF")

        guideline_start = sections["Guideline"]
        changes_end = sections.get("Structure", guideline_start)
        nodes = _parse_guideline(doc, guideline_start)
        if not nodes:
            raise GefegError("No fields could be parsed from the Guideline section")
        changes = _parse_changes(doc, changes_end) if "New Changes Summary" in sections else []
        meta = _parse_meta(doc)
        meta["page_count"] = len(doc)
    finally:
        doc.close()

    # GEFEG writes "Date <X>" in the tree but "Date<X>" in change paths
    by_path = {re.sub(r"\s+", "", n["path"]): n["seq"] for n in nodes}
    for c in changes:
        c["node_seq"] = by_path.get(re.sub(r"\s+", "", c["path"]))
    return {"meta": meta, "nodes": nodes, "changes": changes}
