"""UN/EDIFACT directory data: download, cache and parse message structures.

Source: the open-source ``php-edifact/edifact-mapping`` repository, which
republishes the UNECE UNTDID directories as XML (one folder per version, e.g.
``D01B``). The UNECE site itself sits behind a Cloudflare challenge and cannot
be fetched programmatically, so it is not used.

Files are downloaded once into ``data/edifact/<VERSION>/`` and reused offline.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from app.core.config import REPO_ROOT

CACHE_DIR = os.path.join(REPO_ROOT, "data", "edifact")

_REPO = "php-edifact/edifact-mapping"
_BRANCH = "master"
_API = f"https://api.github.com/repos/{_REPO}/contents/src/Mapping"

# File sources, tried in order each attempt. raw.githubusercontent.com is
# frequently unreachable/slow from some networks, so it is the last resort.
_FILE_SOURCES = [
    (f"https://cdn.jsdelivr.net/gh/{_REPO}@{_BRANCH}/src/Mapping/{{path}}", {}),
    (f"{_API}/{{path}}", {"Accept": "application/vnd.github.raw"}),
    (f"https://raw.githubusercontent.com/{_REPO}/{_BRANCH}/src/Mapping/{{path}}", {}),
]

_TIMEOUT = 25
_RETRIES = 3
_VERSION_RE = re.compile(r"^D\d{2}[A-Z]$")
_MESSAGE_RE = re.compile(r"^[A-Za-z0-9]{6}$")


class EdifactError(Exception):
    """Raised for invalid input or when the directory data cannot be obtained.

    ``status`` is the HTTP status the API should answer with.
    """

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def normalize_version(version: str) -> str:
    version = (version or "").strip().upper()
    if not _VERSION_RE.match(version):
        raise EdifactError(f"无效的版本号: {version!r}（应类似 D01B）", 400)
    return version


def normalize_message(message: str) -> str:
    message = (message or "").strip().upper()
    if not _MESSAGE_RE.match(message):
        raise EdifactError(f"无效的消息类型: {message!r}（应为 6 位字母，如 IFTMIN）", 400)
    return message


def _http_get(sources: list[tuple[str, dict]], what: str) -> bytes:
    """GET trying each (url, headers) source per attempt; retries with backoff."""
    last: Exception | None = None
    for attempt in range(_RETRIES):
        not_found = 0
        for url, headers in sources:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "tool-box-edifact", **headers})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    not_found += 1
                last = exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = exc
        if not_found == len(sources):
            raise EdifactError(f"数据源中不存在: {what}", 404)
        time.sleep(1.5 * (attempt + 1))
    raise EdifactError(f"下载失败（已重试 {_RETRIES} 轮）: {what}\n{last}")


def _file_sources(rel_path: str) -> list[tuple[str, dict]]:
    return [(tpl.format(path=rel_path), headers) for tpl, headers in _FILE_SOURCES]


def _cached(path: str, sources: list[tuple[str, dict]], what: str, refresh: bool = False) -> bytes:
    if not refresh and os.path.isfile(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            return f.read()
    data = _http_get(sources, what)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return data


def _list_dir_names(api_url: str, cache_name: str, refresh: bool, want_dirs: bool) -> list[str]:
    path = os.path.join(CACHE_DIR, cache_name)
    data = _cached(path, [(api_url, {})], "目录列表", refresh)
    try:
        items = json.loads(data)
    except ValueError as exc:
        raise EdifactError("无法解析 GitHub 目录列表") from exc
    wanted = "dir" if want_dirs else "file"
    return sorted(i["name"] for i in items if i.get("type") == wanted)


def list_versions(refresh: bool = False) -> list[str]:
    """Available directory versions, newest first (e.g. D25A … D93A)."""
    names = _list_dir_names(_API, "versions.json", refresh, want_dirs=True)
    names = [n for n in names if _VERSION_RE.match(n)]
    return sorted(names, key=_version_sort_key, reverse=True)


def _version_sort_key(v: str):
    # D96A < D00A < D25A chronologically: map the two-digit year onto 1990–2089.
    m = re.match(r"^([A-Za-z])(\d{2})([A-Za-z])$", v)
    if not m:
        return (9999, v)
    year = int(m.group(2))
    year += 1900 if year >= 90 else 2000
    return (year, m.group(3).upper(), v)


def list_messages(version: str, refresh: bool = False) -> list[str]:
    """Message codes (upper case) available in a version."""
    version = normalize_version(version)
    names = _list_dir_names(
        f"{_API}/{version}/messages", f"{version}/messages.json", refresh, want_dirs=False
    )
    return sorted(n[:-4].upper() for n in names if n.lower().endswith(".xml"))


def _parse(data: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise EdifactError(f"{what} 解析失败: {exc}") from exc


def _load_segments(version: str, refresh: bool) -> dict[str, dict]:
    path = os.path.join(CACHE_DIR, version, "segments.xml")
    root = _parse(
        _cached(path, _file_sources(f"{version}/segments.xml"), "segments.xml", refresh),
        "segments.xml",
    )

    def element(el: ET.Element) -> dict:
        return {
            "kind": "element",
            "id": el.get("id", ""),
            "name": el.get("name", ""),
            "status": "M" if el.get("required") == "true" else (el.get("usage") or "C"),
            "type": el.get("type", ""),
            "maxlength": el.get("maxlength", ""),
            "desc": el.get("desc", ""),
        }

    segments: dict[str, dict] = {}
    for seg in root.findall("segment"):
        items = []
        for child in seg:
            if child.tag == "data_element":
                items.append(element(child))
            elif child.tag == "composite_data_element":
                items.append({
                    "kind": "composite",
                    "id": child.get("id", ""),
                    "name": child.get("name", ""),
                    "status": "M" if child.get("required") == "true" else "C",
                    "desc": child.get("desc", ""),
                    "elements": [element(e) for e in child.findall("data_element")],
                })
        segments[seg.get("id", "")] = {
            "id": seg.get("id", ""),
            "name": seg.get("name", ""),
            "desc": seg.get("desc", ""),
            "items": items,
        }
    return segments


def _se(id_, name, status, type_, maxlength, desc=""):
    return {"kind": "element", "id": id_, "name": name, "status": status,
            "type": type_, "maxlength": maxlength, "desc": desc}


def _sc(id_, name, status, desc, elements):
    return {"kind": "composite", "id": id_, "name": name, "status": status,
            "desc": desc, "elements": elements}


# UNH/UNT are service segments: the UNTDID segment directory does not list them.
_SERVICE_SEGMENTS = {
    "UNH": {
        "id": "UNH",
        "name": "messageHeader",
        "desc": "To head, identify and specify a message.",
        "items": [
            _se("0062", "messageReferenceNumber", "M", "an", "14",
                "Unique message reference assigned by the sender."),
            _sc("S009", "messageIdentifier", "M", "Identification of the type, version etc. of the message being interchanged.", [
                _se("0065", "messageType", "M", "an", "6", "Code identifying a type of message."),
                _se("0052", "messageVersionNumber", "M", "an", "3", "Version number of a message type."),
                _se("0054", "messageReleaseNumber", "M", "an", "3", "Release number within the current message version number."),
                _se("0051", "controllingAgencyCoded", "M", "an", "3", "Code identifying a controlling agency."),
                _se("0057", "associationAssignedCode", "C", "an", "6", "Code assigned by the association responsible for the design and maintenance of the message type concerned."),
                _se("0110", "codeListDirectoryVersionNumber", "C", "an", "6", "Version number of the code list directory."),
                _se("0113", "messageTypeSubFunctionIdentification", "C", "an", "6", "Coded identification of a sub-function of a message type."),
            ]),
            _se("0068", "commonAccessReference", "C", "an", "35",
                "Reference serving as a key to relate all subsequent transfers of data to the same business case or file."),
            _sc("S010", "statusOfTheTransfer", "C", "Statement that the message is one in a sequence of transfers relating to the same subject.", [
                _se("0070", "sequenceOfTransfers", "M", "n", "2", "Number assigned by the sender indicating a transfer sequence."),
                _se("0073", "firstAndLastTransfer", "C", "a", "1", "Indication used for the first and last message in a sequence of messages."),
            ]),
            _sc("S016", "messageSubsetIdentification", "C", "Identification of a message subset.", [
                _se("0115", "messageSubsetIdentification", "M", "an", "14"),
                _se("0116", "messageSubsetVersionNumber", "C", "an", "3"),
                _se("0118", "messageSubsetReleaseNumber", "C", "an", "3"),
                _se("0051", "controllingAgencyCoded", "C", "an", "3"),
            ]),
            _sc("S017", "messageImplementationGuidelineIdentification", "C", "Identification of a message implementation guideline.", [
                _se("0121", "messageImplementationGuidelineIdentification", "M", "an", "14"),
                _se("0122", "messageImplementationGuidelineVersionNumber", "C", "an", "3"),
                _se("0124", "messageImplementationGuidelineReleaseNumber", "C", "an", "3"),
                _se("0051", "controllingAgencyCoded", "C", "an", "3"),
            ]),
            _sc("S018", "scenarioIdentification", "C", "Identification of a scenario.", [
                _se("0127", "scenarioIdentification", "M", "an", "14"),
                _se("0128", "scenarioVersionNumber", "C", "an", "3"),
                _se("0130", "scenarioReleaseNumber", "C", "an", "3"),
                _se("0051", "controllingAgencyCoded", "C", "an", "3"),
            ]),
        ],
    },
    "UNT": {
        "id": "UNT",
        "name": "messageTrailer",
        "desc": "To end and check the completeness of a message.",
        "items": [
            _se("0074", "numberOfSegmentsInAMessage", "M", "n", "10",
                "Control count of number of segments in a message."),
            _se("0062", "messageReferenceNumber", "M", "an", "14",
                "Unique message reference assigned by the sender."),
        ],
    },
}


def _load_codes(version: str, element_ids: set[str], refresh: bool) -> dict[str, list[dict]]:
    """Code lists restricted to the given data element ids. Best effort: the
    file is large (≈1.3 MB), so a failed download only drops the code tables."""
    path = os.path.join(CACHE_DIR, version, "codes.xml")
    try:
        root = _parse(
            _cached(path, _file_sources(f"{version}/codes.xml"), "codes.xml", refresh), "codes.xml"
        )
    except EdifactError:
        return {}
    codes: dict[str, list[dict]] = {}
    for de in root.findall("data_element"):
        de_id = de.get("id", "")
        if de_id not in element_ids:
            continue
        codes[de_id] = [
            {"id": c.get("id", ""), "title": c.get("title", ""), "desc": c.get("desc", "")}
            for c in de.findall("code")
        ]
    return codes


def _parse_structure(parent: ET.Element) -> list[dict]:
    nodes: list[dict] = []
    for child in parent:
        if child.tag == "segment":
            nodes.append({
                "type": "segment",
                "id": child.get("id", ""),
                "maxrepeat": int(child.get("maxrepeat") or 1),
                "required": child.get("required") == "true",
            })
        elif child.tag == "group":
            nodes.append({
                "type": "group",
                "id": child.get("id", ""),
                "maxrepeat": int(child.get("maxrepeat") or 1),
                "required": child.get("required") == "true",
                "children": _parse_structure(child),
            })
    return nodes


def _collect_segment_ids(nodes: list[dict], out: set[str]) -> None:
    for node in nodes:
        if node["type"] == "segment":
            out.add(node["id"])
        else:
            _collect_segment_ids(node["children"], out)


def load_message(
    version: str, message: str, include_codes: bool = True, refresh: bool = False
) -> dict:
    """Download (or read from cache) and parse one message definition.

    Returns ``{"version", "message", "structure", "segments", "codes"}`` where
    ``segments`` only holds the segments referenced by the message.
    """
    version = normalize_version(version)
    message = normalize_message(message)

    msg_path = os.path.join(CACHE_DIR, version, "messages", f"{message.lower()}.xml")
    msg_root = _parse(
        _cached(
            msg_path,
            _file_sources(f"{version}/messages/{message.lower()}.xml"),
            f"{version}/{message}.xml",
            refresh,
        ),
        f"{message}.xml",
    )
    structure = _parse_structure(msg_root)

    used: set[str] = set()
    _collect_segment_ids(structure, used)
    all_segments = {**_SERVICE_SEGMENTS, **_load_segments(version, refresh)}
    segments = {sid: all_segments[sid] for sid in used if sid in all_segments}

    codes: dict[str, list[dict]] = {}
    if include_codes:
        element_ids: set[str] = set()
        for seg in segments.values():
            for item in seg["items"]:
                if item["kind"] == "element":
                    element_ids.add(item["id"])
                else:
                    element_ids.update(e["id"] for e in item["elements"])
        codes = _load_codes(version, element_ids, refresh)

    return {
        "version": version,
        "message": message,
        "structure": structure,
        "segments": segments,
        "codes": codes,
    }
