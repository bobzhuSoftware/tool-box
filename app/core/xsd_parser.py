"""XSD structure parser: turn a set of .xsd files (or a zip of them) into a
JSON-friendly model that the standalone HTML viewer (app.core.xsd_html) renders.

All uploaded schemas are loaded together and cross-referenced by
``(targetNamespace, name)``, so ``xs:import`` / ``xs:include`` chains resolve
without caring about ``schemaLocation``. Nothing is written to disk.

Model (all keys are ``"<namespace>|<local name>"``; built-ins are ``"xs:<name>"``;
anonymous inline types get ``"#<n>"``):

- ``types``    complex/simple type definitions
- ``elements`` global elements
- ``groups``   named model groups (``xs:group``)
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

XS = "http://www.w3.org/2001/XMLSchema"

MAX_FILES = 500
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

_PARTICLES = ("element", "sequence", "choice", "all", "group", "any")
_FACETS = (
    "length", "minLength", "maxLength", "pattern", "minInclusive", "maxInclusive",
    "minExclusive", "maxExclusive", "totalDigits", "fractionDigits", "whiteSpace",
)
_DOCTYPE_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


class XsdError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _local(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _is_xs(el: ET.Element) -> bool:
    return isinstance(el.tag, str) and el.tag.startswith("{" + XS + "}")


class _Doc:
    def __init__(self, name: str, root: ET.Element, prefixes: dict[str, str]):
        self.name = name
        self.root = root
        self.prefixes = prefixes
        self.ns = root.get("targetNamespace", "")


def collect_sources(uploads: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Expand zips and keep only ``.xsd`` files, enforcing size limits."""
    sources: list[tuple[str, bytes]] = []
    total = 0

    def add(name: str, data: bytes) -> None:
        nonlocal total
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise XsdError("文件过大（单个 XSD ≤ 10 MB，总计 ≤ 50 MB）", 413)
        if len(sources) >= MAX_FILES:
            raise XsdError(f"XSD 文件过多（最多 {MAX_FILES} 个）", 413)
        sources.append((name, data))

    for name, data in uploads:
        if data[:2] == b"PK" or name.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise XsdError(f"{name} 不是有效的 ZIP 文件")
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".xsd"):
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise XsdError(f"{info.filename} 过大（> 10 MB）", 413)
                add(info.filename, zf.read(info))
        elif name.lower().endswith(".xsd"):
            add(name, data)
    if not sources:
        raise XsdError("没有找到 .xsd 文件（请上传 .xsd 或包含 .xsd 的 .zip）")
    return sources


def _parse_doc(name: str, data: bytes) -> _Doc:
    if _DOCTYPE_RE.search(data):
        raise XsdError(f"{name}: 不支持包含 DTD / 实体声明的文件")
    prefixes: dict[str, str] = {}
    try:
        it = ET.iterparse(io.BytesIO(data), events=("start-ns",))
        for _, (prefix, uri) in it:
            prefixes[prefix] = uri
        root = it.root
    except ET.ParseError as exc:
        raise XsdError(f"{name}: XML 解析失败（{exc}）")
    if not (_is_xs(root) and _local(root) == "schema"):
        raise XsdError(f"{name}: 不是 XSD（根元素不是 xs:schema）")
    return _Doc(name, root, prefixes)


def _key(ns: str, local: str) -> str:
    return f"{ns}|{local}"


class _Builder:
    def __init__(self, docs: list[_Doc]):
        self.sym: dict[str, dict[str, tuple[ET.Element, _Doc]]] = {
            k: {} for k in ("element", "complexType", "simpleType", "group", "attributeGroup", "attribute")
        }
        self.by_local: dict[str, dict[str, list[str]]] = {k: {} for k in self.sym}
        self.types: dict[str, dict] = {}
        self.elements: dict[str, dict] = {}
        self.groups: dict[str, dict] = {}
        self.warnings: list[str] = []
        self._anon = 0
        self._missing: set[str] = set()
        for doc in docs:
            for child in doc.root:
                tag = _local(child)
                name = child.get("name")
                if not _is_xs(child) or not name:
                    continue
                kind = "simpleType" if tag == "simpleType" else tag
                if kind in self.sym:
                    key = _key(doc.ns, name)
                    self.sym[kind].setdefault(key, (child, doc))
                    self.by_local[kind].setdefault(name, []).append(key)

    # ---- name resolution -------------------------------------------------
    def _resolve(self, qname: str, doc: _Doc, kind: str) -> str:
        prefix, _, local = qname.rpartition(":")
        ns = doc.prefixes.get(prefix) if prefix else doc.prefixes.get("")
        ns = ns or ""
        if kind == "type" and ns == XS:
            return "xs:" + local
        kinds = ("complexType", "simpleType") if kind == "type" else (kind,)
        key = _key(ns, local)
        for k in kinds:
            if key in self.sym[k]:
                return key
        # Tolerate a missing/odd prefix: accept a unique match by local name.
        for k in kinds:
            cands = self.by_local[k].get(local, [])
            if len(cands) == 1:
                return cands[0]
        if key not in self._missing and len(self._missing) < 50:
            self._missing.add(key)
            self.warnings.append(f"未找到定义：{local}（命名空间 {ns or '无'}，在 {doc.name} 中被引用）")
        return key

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _doc(el: ET.Element) -> str:
        texts = []
        for ann in el:
            if _is_xs(ann) and _local(ann) == "annotation":
                for d in ann:
                    if _is_xs(d) and _local(d) == "documentation":
                        t = " ".join("".join(d.itertext()).split())
                        if t:
                            texts.append(t)
        return " ".join(texts)

    @staticmethod
    def _occurs(el: ET.Element) -> tuple[int, int]:
        def num(v: str, default: int) -> int:
            try:
                return int(v)
            except ValueError:
                return default

        mn = num(el.get("minOccurs", "1"), 1)
        mx_raw = el.get("maxOccurs", "1")
        mx = -1 if mx_raw == "unbounded" else num(mx_raw, 1)
        return mn, mx

    def _new_anon(self, el: ET.Element, doc: _Doc) -> str:
        self._anon += 1
        key = f"#{self._anon}"
        self.types[key] = self._build_type(el, doc, None)
        return key

    # ---- build everything ------------------------------------------------
    def build(self) -> None:
        for key, (el, doc) in self.sym["simpleType"].items():
            self.types[key] = self._build_type(el, doc, el.get("name"))
        for key, (el, doc) in self.sym["complexType"].items():
            self.types[key] = self._build_type(el, doc, el.get("name"))
        for key, (el, doc) in self.sym["group"].items():
            for c in el:
                if _is_xs(c) and _local(c) in ("sequence", "choice", "all"):
                    self.groups[key] = self._particle(c, doc)
                    break
        for key, (el, doc) in self.sym["element"].items():
            node = self._particle(el, doc)
            node.pop("min", None)
            node.pop("max", None)
            self.elements[key] = node

    def _build_type(self, el: ET.Element, doc: _Doc, name: str | None) -> dict:
        if _local(el) == "simpleType":
            return self._simple(el, doc, name)
        return self._complex(el, doc, name)

    def _complex(self, el: ET.Element, doc: _Doc, name: str | None) -> dict:
        t: dict = {"kind": "complex", "name": name, "doc": self._doc(el)}
        if el.get("abstract") == "true":
            t["abstract"] = 1
        if el.get("mixed") == "true":
            t["mixed"] = 1
        for c in el:
            if not _is_xs(c):
                continue
            tag = _local(c)
            if tag in ("simpleContent", "complexContent"):
                for d in c:
                    if not (_is_xs(d) and _local(d) in ("extension", "restriction")):
                        continue
                    base = self._resolve(d.get("base", "xs:anyType"), doc, "type")
                    derive = _local(d)
                    if tag == "simpleContent":
                        t["text"] = base
                        if derive == "restriction":
                            t["facets"] = self._facets(d, doc)
                    elif derive == "extension":
                        t["base"] = base
                    self._content(d, doc, t)
        self._content(el, doc, t)
        return t

    def _content(self, parent: ET.Element, doc: _Doc, t: dict) -> None:
        for c in parent:
            if not _is_xs(c):
                continue
            tag = _local(c)
            if tag in ("sequence", "choice", "all", "group"):
                t["items"] = self._particle(c, doc)
            elif tag == "attribute":
                t.setdefault("attrs", []).append(self._attr(c, doc))
            elif tag == "attributeGroup":
                t.setdefault("attrs", []).extend(self._attr_group(c, doc, set()))
            elif tag == "anyAttribute":
                t.setdefault("attrs", []).append({"n": "（任意属性）", "u": "optional", "any": 1})

    def _attr_group(self, el: ET.Element, doc: _Doc, seen: set[str]) -> list[dict]:
        ref = el.get("ref")
        if not ref:
            return []
        key = self._resolve(ref, doc, "attributeGroup")
        if key in seen or key not in self.sym["attributeGroup"]:
            return []
        seen.add(key)
        gel, gdoc = self.sym["attributeGroup"][key]
        out: list[dict] = []
        for c in gel:
            if not _is_xs(c):
                continue
            if _local(c) == "attribute":
                out.append(self._attr(c, gdoc))
            elif _local(c) == "attributeGroup":
                out.extend(self._attr_group(c, gdoc, seen))
        return out

    def _attr(self, el: ET.Element, doc: _Doc) -> dict:
        src, sdoc = el, doc
        ref = el.get("ref")
        if ref:
            key = self._resolve(ref, doc, "attribute")
            if key in self.sym["attribute"]:
                src, sdoc = self.sym["attribute"][key]
        a: dict = {"n": src.get("name") or (ref.rpartition(":")[2] if ref else "?")}
        a["u"] = el.get("use", "optional")
        typ = src.get("type")
        if typ:
            a["t"] = self._resolve(typ, sdoc, "type")
        else:
            inline = next((c for c in src if _is_xs(c) and _local(c) == "simpleType"), None)
            a["t"] = self._new_anon(inline, sdoc) if inline is not None else "xs:anySimpleType"
        for k in ("default", "fixed"):
            if el.get(k) or src.get(k):
                a[k] = el.get(k) or src.get(k)
        d = self._doc(el) or self._doc(src)
        if d:
            a["doc"] = d
        return a

    def _particle(self, el: ET.Element, doc: _Doc) -> dict:
        tag = _local(el)
        mn, mx = self._occurs(el)
        if tag == "element":
            n: dict = {"k": "el", "min": mn, "max": mx}
            ref = el.get("ref")
            if ref:
                n["ref"] = self._resolve(ref, doc, "element")
            else:
                n["name"] = el.get("name", "?")
                typ = el.get("type")
                if typ:
                    n["type"] = self._resolve(typ, doc, "type")
                else:
                    inline = next((c for c in el if _is_xs(c) and _local(c) in ("complexType", "simpleType")), None)
                    n["type"] = self._new_anon(inline, doc) if inline is not None else "xs:anyType"
                if el.get("nillable") == "true":
                    n["nil"] = 1
                for k in ("default", "fixed"):
                    if el.get(k):
                        n[k] = el.get(k)
                if el.get("abstract") == "true":
                    n["abstract"] = 1
            d = self._doc(el)
            if d:
                n["doc"] = d
            return n
        if tag in ("sequence", "choice", "all"):
            return {
                "k": tag, "min": mn, "max": mx,
                "items": [self._particle(c, doc) for c in el if _is_xs(c) and _local(c) in _PARTICLES],
            }
        if tag == "group":
            ref = el.get("ref")
            return {"k": "group", "ref": self._resolve(ref, doc, "group") if ref else "", "min": mn, "max": mx}
        return {"k": "any", "min": mn, "max": mx, "ns": el.get("namespace", "##any")}

    def _simple(self, el: ET.Element, doc: _Doc, name: str | None) -> dict:
        t: dict = {"kind": "simple", "name": name, "doc": self._doc(el)}
        for c in el:
            if not _is_xs(c):
                continue
            tag = _local(c)
            if tag == "restriction":
                base = c.get("base")
                if base:
                    t["base"] = self._resolve(base, doc, "type")
                else:
                    inline = next((x for x in c if _is_xs(x) and _local(x) == "simpleType"), None)
                    if inline is not None:
                        t["base"] = self._new_anon(inline, doc)
                t["facets"] = self._facets(c, doc)
            elif tag == "list":
                item = c.get("itemType")
                t["list"] = self._resolve(item, doc, "type") if item else "xs:anySimpleType"
            elif tag == "union":
                members = (c.get("memberTypes") or "").split()
                t["union"] = [self._resolve(m, doc, "type") for m in members]
        return t

    def _facets(self, el: ET.Element, doc: _Doc) -> dict:
        facets: dict = {}
        enums: list[list[str]] = []
        for c in el:
            if not _is_xs(c):
                continue
            tag = _local(c)
            if tag == "enumeration":
                enums.append([c.get("value", ""), self._doc(c)])
            elif tag in _FACETS:
                v = c.get("value", "")
                facets[tag] = f"{facets[tag]} | {v}" if tag in facets else v
        if enums:
            facets["enum"] = enums
        return facets

    # ---- roots -----------------------------------------------------------
    def roots(self) -> tuple[list[dict], str]:
        used_types: set[str] = set()
        used_elements: set[str] = set()

        def scan(node: dict) -> None:
            if node.get("k") == "el":
                if node.get("ref"):
                    used_elements.add(node["ref"])
                if node.get("type"):
                    used_types.add(node["type"])
            for sub in node.get("items", []) if isinstance(node.get("items"), list) else []:
                scan(sub)

        for t in self.types.values():
            if t.get("base"):
                used_types.add(t["base"])
            if isinstance(t.get("items"), dict):
                scan(t["items"])
        for g in self.groups.values():
            scan(g)
        for e in self.elements.values():
            if e.get("type"):
                used_types.add(e["type"])

        roots = [
            {"key": k, "name": k.rpartition("|")[2], "kind": "element", "ns": k.partition("|")[0]}
            for k in sorted(self.elements, key=lambda x: x.rpartition("|")[2].lower())
        ]
        complex_keys = [k for k, t in self.types.items() if t["kind"] == "complex" and t.get("name")]
        for k in sorted(complex_keys, key=lambda x: x.rpartition("|")[2].lower()):
            roots.append({"key": k, "name": k.rpartition("|")[2], "kind": "type", "ns": k.partition("|")[0]})

        default = ""
        free_elements = [k for k in self.elements if k not in used_elements]
        if free_elements:
            default = free_elements[0]
        elif self.elements:
            default = next(iter(self.elements))
        else:
            free_types = [k for k in complex_keys if k not in used_types]
            default = (free_types or complex_keys or [""])[0]
        return roots, default


def parse_xsd(uploads: list[tuple[str, bytes]]) -> dict:
    """Parse uploaded files (``.xsd`` and/or ``.zip``) into the viewer model."""
    sources = collect_sources(uploads)
    docs: list[_Doc] = []
    warnings: list[str] = []
    for name, data in sources:
        try:
            docs.append(_parse_doc(name, data))
        except XsdError as exc:
            warnings.append(str(exc))
    if not docs:
        raise XsdError("；".join(warnings) or "没有可解析的 XSD")
    builder = _Builder(docs)
    builder.build()
    roots, default = builder.roots()
    if not roots:
        raise XsdError("XSD 中没有找到全局元素或复杂类型")
    return {
        "types": builder.types,
        "elements": builder.elements,
        "groups": builder.groups,
        "roots": roots,
        "default": default,
        "files": [d.name for d in docs],
        "warnings": warnings + builder.warnings,
    }
