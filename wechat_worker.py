"""
WeChat Chat History Export Worker — run as a subprocess from server.py.

Usage:
    python wechat_worker.py contacts <wechat_data_dir>
    python wechat_worker.py export <wechat_data_dir> <contact_id> <output_path>

Progress is written to stdout as:
    STATUS:<message>
    DONE:<json_data>
    ERROR:<message>
"""
import ctypes
import ctypes.wintypes as wintypes
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime

from Crypto.Cipher import AES

try:
    import lz4.block as _lz4_block
except ImportError:
    _lz4_block = None

sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

PAGE_SIZE = 4096
RESERVE_3X = 48   # WeChat 3.x: IV(16) + HMAC-SHA1(20) + pad(12)
IV_SIZE = 16
SQLITE_HEADER = b"SQLite format 3\x00"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


# ---------------------------------------------------------------------------
# Process Memory Scanning
# ---------------------------------------------------------------------------
def _find_wechat_pids():
    """Find WeChat.exe process IDs."""
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq WeChat.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True
    )
    pids = []
    for line in result.stdout.strip().split("\n"):
        parts = line.strip().strip('"').split('","')
        if len(parts) >= 2:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _scan_memory_for_keys(pid):
    """
    Scan WeChat process memory for SQLCipher key patterns.
    Keys are stored as hex strings in format: x'<64 hex chars>'
    Returns a set of hex key strings.
    """
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return set()

    # Patterns: keys stored as x'<64hex_key><32hex_salt>' or x'<64hex_key>'
    patterns = [
        re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'"),  # key + salt (96 hex)
        re.compile(rb"x'([0-9a-fA-F]{64})'"),                     # key only (64 hex)
    ]

    keys_found = set()
    mbi = MEMORY_BASIC_INFORMATION()
    address = 0

    try:
        while address < 0x7FFFFFFFFFFF:
            r = kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if r == 0:
                break

            if (mbi.State == MEM_COMMIT and
                mbi.Protect not in (PAGE_NOACCESS, PAGE_GUARD, 0) and
                not (mbi.Protect & PAGE_GUARD) and
                mbi.RegionSize <= 50 * 1024 * 1024):

                buf = (ctypes.c_char * mbi.RegionSize)()
                br = ctypes.c_size_t(0)
                if kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(address), buf, mbi.RegionSize, ctypes.byref(br)
                ):
                    data = bytes(buf[:br.value])
                    for pattern in patterns:
                        for m in pattern.finditer(data):
                            keys_found.add(m.group(1).decode())

            address += mbi.RegionSize if mbi.RegionSize > 0 else 0x1000
    finally:
        kernel32.CloseHandle(handle)

    return keys_found


# ---------------------------------------------------------------------------
# Database Decryption (WeChat 3.x — direct AES key, no PBKDF2)
# ---------------------------------------------------------------------------
def _validate_key_for_db(db_path, key_bytes, reserve=RESERVE_3X):
    """
    Validate a key against page 1 of a database file.
    WeChat 3.x stores the key directly (used as AES-256-CBC key without PBKDF2).
    Returns True if decrypted page 1 starts with valid SQLite page_size bytes.
    """
    try:
        with open(db_path, "rb") as f:
            page1 = f.read(PAGE_SIZE)
        if len(page1) < PAGE_SIZE:
            return False

        # Page 1 layout: [16-byte salt] [encrypted content] [reserve]
        encrypted = page1[16:PAGE_SIZE - reserve]
        reserve_data = page1[PAGE_SIZE - reserve:]
        iv = reserve_data[:IV_SIZE]

        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypted[:32])

        # First 2 bytes should be page_size in big-endian (0x10 0x00 = 4096)
        return decrypted[:2] == b'\x10\x00'
    except Exception:
        return False


def _decrypt_database(db_path, key_bytes, output_path, reserve=RESERVE_3X):
    """
    Decrypt a full SQLCipher database to a plaintext SQLite file.
    WeChat 3.x: AES-256-CBC, key used directly, PAGE_SIZE=4096, RESERVE=48.
    """
    with open(db_path, "rb") as f:
        file_data = f.read()

    total_pages = len(file_data) // PAGE_SIZE

    with open(output_path, "wb") as f:
        for page_num in range(1, total_pages + 1):
            offset = (page_num - 1) * PAGE_SIZE
            page = file_data[offset:offset + PAGE_SIZE]

            if page_num == 1:
                # Page 1: first 16 bytes are salt (unencrypted)
                encrypted = page[16:PAGE_SIZE - reserve]
                reserve_data = page[PAGE_SIZE - reserve:]
            else:
                encrypted = page[:PAGE_SIZE - reserve]
                reserve_data = page[PAGE_SIZE - reserve:]

            iv = reserve_data[:IV_SIZE]
            cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted)

            if page_num == 1:
                # Prepend SQLite header (replaces the salt area)
                full_page = (SQLITE_HEADER + decrypted)[:PAGE_SIZE].ljust(PAGE_SIZE, b'\x00')
            else:
                full_page = decrypted[:PAGE_SIZE].ljust(PAGE_SIZE, b'\x00')

            f.write(full_page)


def _find_key_for_db(db_path, key_hex_set):
    """Try all candidate keys against a database. Returns the valid key bytes or None."""
    for hex_key in key_hex_set:
        key_bytes = bytes.fromhex(hex_key)
        if _validate_key_for_db(db_path, key_bytes):
            return key_bytes
    return None


# ---------------------------------------------------------------------------
# Data Directory Detection
# ---------------------------------------------------------------------------
def _find_wechat_data_dir(custom_path=None):
    """Find the WeChat data directory. Returns (version, data_dir)."""
    if custom_path and custom_path != "auto" and os.path.isdir(custom_path):
        if "xwechat_files" in custom_path:
            return "4.x", custom_path
        return "3.x", custom_path

    # Auto-detect
    docs = os.path.join(os.path.expanduser("~"), "Documents")

    # Check standard Documents
    for name, ver in [("xwechat_files", "4.x"), ("WeChat Files", "3.x")]:
        path = os.path.join(docs, name)
        if os.path.isdir(path):
            return ver, path

    # Check OneDrive (including org-suffixed folders)
    home = os.path.expanduser("~")
    for entry in os.listdir(home):
        if entry.lower().startswith("onedrive"):
            onedrive_docs = os.path.join(home, entry, "Documents")
            if os.path.isdir(onedrive_docs):
                for name, ver in [("xwechat_files", "4.x"), ("WeChat Files", "3.x")]:
                    path = os.path.join(onedrive_docs, name)
                    if os.path.isdir(path):
                        return ver, path

    # Check system MyDocuments via shell API
    try:
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_PERSONAL=5
        sys_docs = buf.value
        if sys_docs and os.path.isdir(sys_docs):
            for name, ver in [("xwechat_files", "4.x"), ("WeChat Files", "3.x")]:
                path = os.path.join(sys_docs, name)
                if os.path.isdir(path):
                    return ver, path
    except Exception:
        pass

    return None, None


def _find_wxid_dirs(version, data_dir):
    """Find wxid user directories."""
    wxid_dirs = []
    if not data_dir or not os.path.isdir(data_dir):
        return wxid_dirs

    skip = {"All Users", "Applet", "WMPF"}
    for name in os.listdir(data_dir):
        if name in skip:
            continue
        full = os.path.join(data_dir, name)
        if not os.path.isdir(full):
            continue
        if version == "4.x":
            if os.path.isdir(os.path.join(full, "db_storage")):
                wxid_dirs.append(full)
        else:
            if os.path.isdir(os.path.join(full, "Msg")):
                wxid_dirs.append(full)

    return wxid_dirs


def _find_db_files(wxid_dir):
    """Find all relevant database files for WeChat 3.x."""
    db_files = {}  # category -> list of paths

    msg_dir = os.path.join(wxid_dir, "Msg")
    if not os.path.isdir(msg_dir):
        return db_files

    # MicroMsg.db — contacts, chatrooms
    micro = os.path.join(msg_dir, "MicroMsg.db")
    if os.path.isfile(micro):
        db_files["contact"] = [micro]

    # MSG*.db in Multi/ — messages
    multi_dir = os.path.join(msg_dir, "Multi")
    if os.path.isdir(multi_dir):
        msg_dbs = sorted([
            os.path.join(multi_dir, f)
            for f in os.listdir(multi_dir)
            if f.startswith("MSG") and f.endswith(".db") and not f.startswith("FTSMSG")
        ])
        db_files["message"] = msg_dbs

    return db_files


# ---------------------------------------------------------------------------
# Contact Extraction
# ---------------------------------------------------------------------------
def _get_contacts(decrypted_db_path):
    """Extract contacts from decrypted MicroMsg.db."""
    contacts = []
    try:
        conn = sqlite3.connect(decrypted_db_path)
        cur = conn.cursor()

        # Get contacts from Contact table
        cur.execute("""
            SELECT UserName, NickName, Remark, Type
            FROM Contact
            WHERE Type != 4
            ORDER BY NickName
        """)
        for row in cur.fetchall():
            username, nickname, remark, contact_type = row
            if not username:
                continue
            # Filter out system accounts and service accounts
            if username.startswith("fake"):
                continue

            display_name = remark if remark else (nickname if nickname else username)
            contacts.append({
                "id": username,
                "name": display_name,
                "nickname": nickname or "",
                "remark": remark or "",
                "is_group": "@chatroom" in username,
            })

        conn.close()
    except Exception as e:
        print(f"STATUS:读取联系人时出错: {e}", flush=True)

    return contacts


# ---------------------------------------------------------------------------
# Message Extraction
# ---------------------------------------------------------------------------
def _get_messages_for_contact(decrypted_db_path, contact_id, start_ts=None, end_ts=None):
    """Extract messages for a specific contact/group from a decrypted MSG*.db."""
    messages = []
    try:
        conn = sqlite3.connect(decrypted_db_path)
        cur = conn.cursor()

        # Check if MSG table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='MSG'")
        if not cur.fetchone():
            conn.close()
            return messages

        conditions = ["StrTalker = ?"]
        params = [contact_id]
        if start_ts is not None:
            conditions.append("CreateTime >= ?")
            params.append(int(start_ts))
        if end_ts is not None:
            conditions.append("CreateTime <= ?")
            params.append(int(end_ts))

        cur.execute(f"""
            SELECT StrContent, CreateTime, IsSender, Type, StrTalker, CompressContent, BytesExtra, MsgSvrID
            FROM MSG
            WHERE {' AND '.join(conditions)}
            ORDER BY CreateTime ASC
        """, params)

        for row in cur.fetchall():
            content, create_time, is_sender, msg_type, talker, compress_content, bytes_extra, msg_svrid = row
            messages.append({
                "content": content or "",
                "time": create_time,
                "is_send": is_sender,
                "type": msg_type,
                "talker": talker,
                "compress": compress_content,
                "extra": bytes_extra,
                "svrid": str(msg_svrid) if msg_svrid is not None else "",
            })

        conn.close()
    except Exception:
        pass  # Some DBs may not have data for this contact

    return messages


def _decompress_content(data):
    """Decompress CompressContent (lz4 format)."""
    if not data or not _lz4_block:
        return ""
    try:
        xml_bytes = _lz4_block.decompress(data, uncompressed_size=len(data) * 10)
        return xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_content(msg):
    """Extract displayable content from a message dict."""
    msg_type = msg.get("type")
    content = msg.get("content", "")
    compress = msg.get("compress")

    # Type 1: plain text
    if msg_type is None or msg_type == 1:
        return content

    # Type 49: links, files, music, mini-programs, quotes, etc.
    if msg_type == 49:
        xml = _decompress_content(compress) if compress else content
        if not xml:
            return "[链接/文件]"
        title = _xml_extract(xml, "title")
        url = _xml_extract(xml, "url")
        des = _xml_extract(xml, "des")
        # Determine sub-type from XML
        app_type = _xml_extract(xml, "type")
        if app_type == "6":
            # File
            return f"[文件] {title}" if title else "[文件]"
        if app_type == "57":
            # Quote/reference - extract the quoted message
            refer_block = re.search(r'<refermsg>(.*?)</refermsg>', xml, re.DOTALL)
            refer_name = ""
            refer_content = ""
            if refer_block:
                refer_xml = refer_block.group(1)
                refer_name = _xml_extract(refer_xml, "displayname")
                refer_content = _xml_extract(refer_xml, "content")
                refer_type = _xml_extract(refer_xml, "type")
                # Determine display for referenced content based on type
                if refer_type == "3":
                    svrid_ref = _xml_extract(refer_xml, "svrid")
                    refer_content = f"[__IMG:{svrid_ref}__]" if svrid_ref else "[图片]"
                elif refer_type == "34":
                    refer_content = "[语音]"
                elif refer_type == "43":
                    refer_content = "[视频]"
                elif refer_type == "47":
                    svrid_ref47 = _xml_extract(refer_xml, "svrid")
                    refer_content = f"[__EMOJI:{svrid_ref47}__]" if svrid_ref47 else "[表情]"
                elif refer_content.startswith("&lt;") or refer_content.startswith("<?xml"):
                    # Nested XML (for type 49 quotes) - extract title
                    unescaped = _unescape_xml(refer_content)
                    nested_title = _xml_extract(unescaped, "title")
                    if nested_title:
                        refer_content = nested_title
                    else:
                        refer_content = "[消息]"
            reply_text = title or content or ""
            if refer_name and refer_content:
                return f"{reply_text}\n    ↩ {refer_name}: {refer_content}"
            elif refer_content:
                return f"{reply_text}\n    ↩ {refer_content}"
            return reply_text or "[引用]"
        if app_type == "33" or app_type == "36":
            # Mini-program
            return f"[小程序] {title}" if title else "[小程序]"
        if app_type == "3":
            # Music
            parts = ["[音乐]"]
            if title:
                parts.append(title)
            if des:
                parts.append(f"- {des}")
            if url:
                parts.append(f"\n    {_unescape_xml(url)}")
            return " ".join(parts)
        if app_type == "4" or app_type == "19":
            # Video sharing
            parts = ["[视频]"]
            if title:
                parts.append(title)
            if url:
                parts.append(f"\n    {_unescape_xml(url)}")
            return " ".join(parts)
        # Default: article/link
        parts = []
        if title:
            parts.append(title)
        if des and des != title:
            parts.append(f"({des[:80]})")
        if url:
            parts.append(f"\n    {_unescape_xml(url)}")
        if parts:
            return "[链接] " + " ".join(parts)
        return "[链接/文件]"

    # Type 3: image
    if msg_type == 3:
        return "[图片]"

    # Type 34: voice
    if msg_type == 34:
        # Try to extract duration
        dur = _xml_extract(content, "voicelength")
        if dur:
            try:
                secs = int(dur) // 1000
                return f"[语音 {secs}秒]"
            except ValueError:
                pass
        return "[语音]"

    # Type 43: video
    if msg_type == 43:
        return "[视频]"

    # Type 47: sticker/emoji
    if msg_type == 47:
        return "[表情]"

    # Type 48: location
    if msg_type == 48:
        poiname = _xml_attr(content, "poiname")
        label = _xml_attr(content, "label")
        x = _xml_attr(content, "x")
        y = _xml_attr(content, "y")
        loc_parts = ["[位置]"]
        if poiname:
            loc_parts.append(poiname)
        if label and label != poiname:
            loc_parts.append(f"({label})")
        if x and y:
            loc_parts.append(f"[{x},{y}]")
        return " ".join(loc_parts)

    # Type 42: contact card
    if msg_type == 42:
        nickname = _xml_extract(content, "nickname")
        return f"[名片] {nickname}" if nickname else "[名片]"

    # Type 50: voice/video call
    if msg_type == 50:
        return "[语音/视频通话]"

    # Type 10000: system
    if msg_type == 10000:
        return content or "[系统消息]"

    # Type 10002: recall
    if msg_type == 10002:
        return content or "[撤回消息]"

    return f"[类型:{msg_type}]"


def _xml_extract(xml_str, tag):
    """Extract first text content of an XML tag."""
    if not xml_str:
        return ""
    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', xml_str, re.DOTALL)
    return m.group(1).strip() if m else ""


def _xml_attr(xml_str, attr):
    """Extract an XML attribute value."""
    if not xml_str:
        return ""
    m = re.search(rf'{attr}="([^"]*)"', xml_str)
    return m.group(1) if m else ""


def _unescape_xml(s):
    """Unescape basic XML entities."""
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')


def _format_time(timestamp):
    """Format Unix timestamp."""
    if not timestamp:
        return ""
    try:
        t = int(timestamp)
        if t > 1e12:
            t = t // 1000
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return ""


def _export_to_txt(messages, contact_name, output_path):
    """Write messages to TXT file."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"微信聊天记录 — {contact_name}\n")
        f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"共 {len(messages)} 条消息\n")
        f.write("=" * 60 + "\n\n")

        for msg in messages:
            time_str = _format_time(msg.get("time"))
            is_send = msg.get("is_send")
            sender = "我" if is_send == 1 else contact_name

            content = _extract_content(msg)
            # Clean up any remaining raw XML
            if content and (content.startswith("<?xml") or content.startswith("<msg")):
                content = "[链接/文件]"

            parts = []
            if time_str:
                parts.append(f"[{time_str}]")
            parts.append(f"{sender}:")
            parts.append(content)

            f.write(" ".join(parts) + "\n")


# ---------------------------------------------------------------------------
# Image Handling
# ---------------------------------------------------------------------------
def _find_sticker_path(md5, wxid_dir):
    """Find local sticker/emoji file given md5 hash and wxid data dir."""
    if not md5 or len(md5) < 4 or not wxid_dir:
        return None
    emotion_dir = os.path.join(wxid_dir, "FileStorage", "Emotion")
    candidates = [
        os.path.join(emotion_dir, md5),
        os.path.join(emotion_dir, md5[:2], md5),
        os.path.join(emotion_dir, md5 + ".gif"),
        os.path.join(emotion_dir, md5[:2], md5 + ".gif"),
        os.path.join(emotion_dir, md5 + ".png"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _read_sticker_file(sticker_path):
    """Read a sticker/emoji file. Returns (mime_type, data) or (None, None).
    Stickers may be plain GIF/PNG or XOR-encrypted .dat files."""
    try:
        with open(sticker_path, "rb") as fh:
            data = fh.read()
        if not data:
            return None, None
        # Check raw magic bytes (unencrypted)
        if data[:3] == b'GIF':
            return "image/gif", data
        if data[:4] == b'\x89PNG':
            return "image/png", data
        if data[:2] == b'\xff\xd8':
            return "image/jpeg", data
        # Try XOR with single-byte key auto-detected from header
        header = data[:4]
        for expected, mime in [
            (b'GIF8', "image/gif"),
            (b'\x89PNG', "image/png"),
            (b'\xff\xd8\xff\xe0', "image/jpeg"),
            (b'\xff\xd8\xff\xe1', "image/jpeg"),
        ]:
            key = header[0] ^ expected[0]
            if key == 0:
                continue
            if bytes([b ^ key for b in header])[:len(expected)] == expected:
                return mime, bytes([b ^ key for b in data])
        return None, None
    except OSError:
        return None, None


def _extract_image_path(bytes_extra):
    """Extract image paths from BytesExtra protobuf. Returns (full_path, thumb_path)."""
    if not bytes_extra:
        return None, None
    all_paths = re.findall(rb'wxid_[^\x00\x01-\x1f]+\.dat', bytes_extra)
    full_path = None
    thumb_path = None
    for p in all_paths:
        p_str = p.decode("utf-8", errors="replace")
        if "\\Thumb\\" in p_str or "_t.dat" in p_str:
            if thumb_path is None:
                thumb_path = p_str
        elif "\\Image\\" in p_str:
            if full_path is None:
                full_path = p_str
    # If neither matched specifically, use first as full
    if not full_path and not thumb_path and all_paths:
        full_path = all_paths[0].decode("utf-8", errors="replace")
    return full_path, thumb_path


def _detect_xor_key(data_dir):
    """Auto-detect the XOR key by checking a .dat file header."""
    # Walk MsgAttach looking for any .dat file
    attach_dir = os.path.join(data_dir, "FileStorage", "MsgAttach")
    if not os.path.isdir(attach_dir):
        return None
    for root, dirs, files in os.walk(attach_dir):
        for f in files:
            if f.endswith(".dat"):
                path = os.path.join(root, f)
                try:
                    with open(path, "rb") as fh:
                        header = fh.read(4)
                    if len(header) < 4:
                        continue
                    # Try JPEG (ff d8 ff)
                    key = header[0] ^ 0xFF
                    if bytes([b ^ key for b in header[:3]]) == b'\xff\xd8\xff':
                        return key
                    # Try PNG (89 50 4e 47)
                    key = header[0] ^ 0x89
                    if bytes([b ^ key for b in header[:4]]) == b'\x89\x50\x4e\x47':
                        return key
                    # Try GIF (47 49 46)
                    key = header[0] ^ 0x47
                    if bytes([b ^ key for b in header[:3]]) == b'\x47\x49\x46':
                        return key
                    # Try BMP (42 4d)
                    key = header[0] ^ 0x42
                    if bytes([b ^ key for b in header[:2]]) == b'\x42\x4d':
                        return key
                except OSError:
                    continue
    return None


def _decode_image(dat_path, xor_key):
    """Decode a .dat image file and return (mime_type, image_bytes)."""
    try:
        with open(dat_path, "rb") as f:
            data = f.read()
        decoded = bytes([b ^ xor_key for b in data])
        # Detect format
        if decoded[:3] == b'\xff\xd8\xff':
            return "image/jpeg", decoded
        elif decoded[:4] == b'\x89\x50\x4e\x47':
            return "image/png", decoded
        elif decoded[:3] == b'GIF':
            return "image/gif", decoded
        elif decoded[:2] == b'BM':
            return "image/bmp", decoded
        else:
            return "image/jpeg", decoded  # fallback
    except OSError:
        return None, None


def _export_to_html(messages, contact_name, output_path, wxid_dir=None, xor_key=None):
    """Write messages to HTML file with embedded images."""
    import base64
    import html as html_mod

    # Build svrid -> msg lookup for resolving quoted images
    svrid_to_msg = {msg["svrid"]: msg for msg in messages if msg.get("svrid")}

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>微信聊天记录 — {html_mod.escape(contact_name)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
.header {{ text-align: center; padding: 20px 0; border-bottom: 2px solid #07c160; margin-bottom: 20px; }}
.header h1 {{ color: #333; margin: 0 0 5px; }}
.header p {{ color: #888; margin: 2px 0; font-size: 14px; }}
.msg {{ margin: 8px 0; display: flex; }}
.msg.sent {{ flex-direction: row-reverse; }}
.msg .bubble {{ max-width: 70%; padding: 10px 14px; border-radius: 12px; position: relative; word-break: break-word; }}
.msg.recv .bubble {{ background: #fff; border: 1px solid #e0e0e0; }}
.msg.sent .bubble {{ background: #95ec69; }}
.msg .meta {{ font-size: 11px; color: #999; margin: 2px 8px; }}
.msg.sent .meta {{ text-align: right; }}
.msg .bubble img {{ max-width: 100%; max-height: 300px; border-radius: 6px; display: block; margin: 4px 0; }}
.msg .bubble a {{ color: #576b95; word-break: break-all; }}
.msg .bubble .file-tag, .msg .bubble .loc-tag {{ background: #f0f0f0; padding: 6px 10px; border-radius: 6px; font-size: 13px; }}
.msg .bubble .quote-block {{ background: #f0f0f0; border-left: 3px solid #07c160; padding: 6px 10px; border-radius: 4px; margin-bottom: 6px; font-size: 13px; }}
.msg.sent .bubble .quote-block {{ background: rgba(0,0,0,0.05); }}
.msg .bubble .quote-block .quote-name {{ font-weight: 600; margin-right: 4px; color: #576b95; }}
.msg .bubble .quote-block .quote-name::after {{ content: ": "; }}
.msg .bubble .quote-block .quote-text {{ color: #666; }}
.system {{ text-align: center; color: #999; font-size: 12px; margin: 12px 0; }}
.date-sep {{ text-align: center; margin: 16px 0; font-size: 12px; color: #999; }}
</style>
</head>
<body>
<div class="header">
<h1>{html_mod.escape(contact_name)}</h1>
<p>共 {len(messages)} 条消息</p>
<p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
</div>
""")
        last_date = ""
        for msg in messages:
            time_str = _format_time(msg.get("time"))
            is_send = msg.get("is_send")
            msg_type = msg.get("type")
            date_str = time_str[:10] if time_str else ""

            # Date separator
            if date_str and date_str != last_date:
                f.write(f'<div class="date-sep">—— {date_str} ——</div>\n')
                last_date = date_str

            # System messages
            if msg_type in (10000, 10002):
                content = msg.get("content", "") or "[系统消息]"
                f.write(f'<div class="system">{html_mod.escape(content)}</div>\n')
                continue

            direction = "sent" if is_send == 1 else "recv"
            sender = "我" if is_send == 1 else contact_name
            time_short = time_str[11:] if time_str and len(time_str) > 11 else ""

            f.write(f'<div class="msg {direction}">\n')
            f.write(f'<div>\n')
            f.write(f'<div class="meta">{html_mod.escape(sender)} {time_short}</div>\n')
            f.write(f'<div class="bubble">\n')

            # Render content based on type
            if msg_type == 3:
                # Image - try to embed
                img_embedded = False
                if wxid_dir and xor_key is not None:
                    full_path, thumb_path = _extract_image_path(msg.get("extra"))
                    # Try full-size first, then thumbnail
                    for rel_path in (full_path, thumb_path):
                        if not rel_path:
                            continue
                        parts = rel_path.split("\\", 1)
                        if len(parts) == 2:
                            abs_path = os.path.join(wxid_dir, parts[1])
                        else:
                            abs_path = os.path.join(wxid_dir, rel_path)
                        if os.path.isfile(abs_path):
                            mime, img_data = _decode_image(abs_path, xor_key)
                            if img_data:
                                b64 = base64.b64encode(img_data).decode("ascii")
                                f.write(f'<img src="data:{mime};base64,{b64}" />\n')
                                img_embedded = True
                                break
                if not img_embedded:
                    f.write('<span class="file-tag">📷 [图片]</span>\n')

            elif msg_type == 49:
                # Reuse _extract_content which handles all decompression correctly
                content49 = _extract_content(msg)
                # Detect if it's a quote (contains ↩ separator inserted by _extract_content)
                if "\n    ↩ " in content49:
                    # Split into reply text and quoted part
                    reply_text, refer_part = content49.split("\n    ↩ ", 1)
                    # refer_part is "name: content" or just "content"
                    if ": " in refer_part:
                        refer_name, refer_content = refer_part.split(": ", 1)
                    else:
                        refer_name, refer_content = "", refer_part
                    f.write('<div class="quote-block">')
                    if refer_name:
                        f.write(f'<span class="quote-name">{html_mod.escape(refer_name)}</span>')
                    # Check if quoted content is an image reference
                    img_ref_m = re.match(r'\[__IMG:(\d+)__\]', refer_content)
                    emoji_ref_m = re.match(r'\[__EMOJI:(\d+)__\]', refer_content)
                    if img_ref_m and wxid_dir and xor_key is not None:
                        ref_svrid = img_ref_m.group(1)
                        ref_msg = svrid_to_msg.get(ref_svrid)
                        img_embedded_q = False
                        if ref_msg:
                            full_path_q, thumb_path_q = _extract_image_path(ref_msg.get("extra"))
                            for rel_path_q in (full_path_q, thumb_path_q):
                                if not rel_path_q:
                                    continue
                                parts_q = rel_path_q.split("\\", 1)
                                abs_path_q = os.path.join(wxid_dir, parts_q[1]) if len(parts_q) == 2 else os.path.join(wxid_dir, rel_path_q)
                                if os.path.isfile(abs_path_q):
                                    mime_q, img_data_q = _decode_image(abs_path_q, xor_key)
                                    if img_data_q:
                                        b64_q = base64.b64encode(img_data_q).decode("ascii")
                                        f.write(f'<img src="data:{mime_q};base64,{b64_q}" style="max-width:200px;max-height:200px;border-radius:4px;display:block;margin-top:4px;" />')
                                        img_embedded_q = True
                                        break
                        if not img_embedded_q:
                            f.write('<span class="quote-text">📷 [图片]</span>')
                    elif emoji_ref_m and wxid_dir:
                        ref_svrid_e = emoji_ref_m.group(1)
                        ref_msg_e = svrid_to_msg.get(ref_svrid_e)
                        emoji_embedded = False
                        if ref_msg_e:
                            md5_e = _xml_attr(ref_msg_e.get("content", ""), "md5")
                            sticker_path = _find_sticker_path(md5_e, wxid_dir)
                            if sticker_path:
                                mime_e, sticker_data = _read_sticker_file(sticker_path)
                                if sticker_data:
                                    b64_e = base64.b64encode(sticker_data).decode("ascii")
                                    f.write(f'<img src="data:{mime_e};base64,{b64_e}" style="max-width:120px;max-height:120px;border-radius:4px;display:block;margin-top:4px;" />')
                                    emoji_embedded = True
                        if not emoji_embedded:
                            f.write('<span class="quote-text">🎉 [表情]</span>')
                    else:
                        f.write(f'<span class="quote-text">{html_mod.escape(refer_content)}</span>')
                    f.write('</div>\n')
                    f.write(f'{html_mod.escape(reply_text)}\n')
                else:
                    # Not a quote - render as link/file/etc.
                    if content49.startswith("<?xml") or content49.startswith("<msg"):
                        content49 = "[链接/文件]"
                    lines = content49.split("\n")
                    for line in lines:
                        stripped = line.strip()
                        if stripped.startswith("http"):
                            f.write(f'<a href="{html_mod.escape(stripped)}" target="_blank">{html_mod.escape(stripped)}</a><br>\n')
                        else:
                            f.write(f'{html_mod.escape(stripped)}<br>\n')

            elif msg_type == 1:
                content = msg.get("content", "")
                # Check if content itself is a URL
                if content.strip().startswith("http"):
                    f.write(f'<a href="{html_mod.escape(content.strip())}" target="_blank">{html_mod.escape(content.strip())}</a>\n')
                else:
                    f.write(f'{html_mod.escape(content)}\n')

            elif msg_type == 48:
                content = _extract_content(msg)
                f.write(f'<span class="loc-tag">📍 {html_mod.escape(content)}</span>\n')

            else:
                content = _extract_content(msg)
                if content.startswith("<?xml") or content.startswith("<msg"):
                    content = f"[类型:{msg_type}]"
                f.write(f'{html_mod.escape(content)}\n')

            f.write('</div>\n</div>\n</div>\n')

        f.write("</body>\n</html>\n")
def cmd_contacts(data_dir_arg):
    """Find and return contacts list."""
    print("STATUS:正在查找微信数据目录...", flush=True)

    version, data_dir = _find_wechat_data_dir(data_dir_arg)
    if not data_dir:
        print("ERROR:找不到微信数据目录。请确认微信已安装并在输入框中指定正确路径。", flush=True)
        return

    print(f"STATUS:检测到微信 {version}，数据目录: {data_dir}", flush=True)

    wxid_dirs = _find_wxid_dirs(version, data_dir)
    if not wxid_dirs:
        print("ERROR:未找到微信用户数据目录。请确认账号已登录过微信。", flush=True)
        return

    print(f"STATUS:找到 {len(wxid_dirs)} 个用户目录", flush=True)

    # Find WeChat process and extract keys
    print("STATUS:正在搜索微信进程...", flush=True)
    pids = _find_wechat_pids()
    if not pids:
        print("ERROR:微信未在运行。请先启动微信并登录，然后重试。", flush=True)
        return

    print(f"STATUS:找到微信进程 (PID: {pids[0]})，正在扫描内存提取密钥...", flush=True)
    key_hex_set = set()
    for pid in pids:
        key_hex_set.update(_scan_memory_for_keys(pid))

    if not key_hex_set:
        print("ERROR:无法从微信进程内存中提取密钥。可能是权限不足或微信版本不兼容。", flush=True)
        return

    print(f"STATUS:找到 {len(key_hex_set)} 个密钥候选，正在解密联系人数据库...", flush=True)

    # Decrypt MicroMsg.db to get contacts
    wxid_dir = wxid_dirs[0]
    db_files = _find_db_files(wxid_dir)

    if "contact" not in db_files:
        print("ERROR:找不到联系人数据库 (MicroMsg.db)。", flush=True)
        return

    contact_db = db_files["contact"][0]
    key = _find_key_for_db(contact_db, key_hex_set)
    if not key:
        print("ERROR:无法找到联系人数据库的有效密钥。请确认微信已登录。", flush=True)
        return

    print("STATUS:成功找到密钥！正在解密联系人数据...", flush=True)

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    try:
        _decrypt_database(contact_db, key, tmp_db.name)
        contacts = _get_contacts(tmp_db.name)
    finally:
        try:
            os.unlink(tmp_db.name)
        except OSError:
            pass

    if not contacts:
        print("ERROR:联系人列表为空。", flush=True)
        return

    print(f"STATUS:共找到 {len(contacts)} 个联系人/群聊", flush=True)

    result = {
        "contacts": contacts,
        "version": version,
        "data_dir": data_dir,
        "wxid_dir": wxid_dir,
    }
    print(f"DONE:{json.dumps(result, ensure_ascii=False)}", flush=True)


def cmd_export(data_dir_arg, contact_id, output_path, start_date=None, end_date=None, fmt="txt"):
    """Export chat messages for a specific contact."""
    # Convert date strings (YYYY-MM-DD) to Unix timestamps
    start_ts = None
    end_ts = None
    if start_date:
        try:
            start_ts = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    if end_date:
        try:
            # End of the selected day (23:59:59)
            end_ts = datetime.strptime(end_date, "%Y-%m-%d").timestamp() + 86399
        except ValueError:
            pass

    date_desc = ""
    if start_date and end_date:
        date_desc = f"（{start_date} 至 {end_date}）"
    elif start_date:
        date_desc = f"（{start_date} 起）"
    elif end_date:
        date_desc = f"（至 {end_date}）"

    print(f"STATUS:正在导出聊天记录{date_desc}...", flush=True)

    version, data_dir = _find_wechat_data_dir(data_dir_arg)
    if not data_dir:
        print("ERROR:找不到微信数据目录。", flush=True)
        return

    wxid_dirs = _find_wxid_dirs(version, data_dir)
    if not wxid_dirs:
        print("ERROR:未找到微信用户数据目录。", flush=True)
        return

    # Extract keys
    print("STATUS:正在提取解密密钥...", flush=True)
    pids = _find_wechat_pids()
    if not pids:
        print("ERROR:微信未在运行。请先启动微信。", flush=True)
        return

    key_hex_set = set()
    for pid in pids:
        key_hex_set.update(_scan_memory_for_keys(pid))

    if not key_hex_set:
        print("ERROR:无法提取密钥。", flush=True)
        return

    # Find and decrypt message databases
    wxid_dir = wxid_dirs[0]
    db_files = _find_db_files(wxid_dir)
    msg_dbs = db_files.get("message", [])

    if not msg_dbs:
        print("ERROR:找不到消息数据库。", flush=True)
        return

    print(f"STATUS:找到 {len(msg_dbs)} 个消息数据库，正在解密...", flush=True)

    all_messages = []
    for i, db_path in enumerate(msg_dbs):
        db_name = os.path.basename(db_path)
        print(f"STATUS:正在处理 {db_name} ({i+1}/{len(msg_dbs)})...", flush=True)

        key = _find_key_for_db(db_path, key_hex_set)
        if not key:
            continue

        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        try:
            _decrypt_database(db_path, key, tmp_db.name)
            messages = _get_messages_for_contact(tmp_db.name, contact_id, start_ts, end_ts)
            all_messages.extend(messages)
            if messages:
                print(f"STATUS:  {db_name}: 找到 {len(messages)} 条消息", flush=True)
        except Exception as e:
            print(f"STATUS:  {db_name}: 处理出错 - {e}", flush=True)
        finally:
            try:
                os.unlink(tmp_db.name)
            except OSError:
                pass

    if not all_messages:
        print(f"ERROR:未找到与 {contact_id} 的聊天记录。", flush=True)
        return

    # Sort by time
    all_messages.sort(key=lambda m: m.get("time") or 0)

    print(f"STATUS:共找到 {len(all_messages)} 条消息，正在生成文件...", flush=True)

    # Get contact display name from MicroMsg.db
    contact_name = contact_id
    contact_db_list = db_files.get("contact", [])
    if contact_db_list:
        key = _find_key_for_db(contact_db_list[0], key_hex_set)
        if key:
            tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp_db.close()
            try:
                _decrypt_database(contact_db_list[0], key, tmp_db.name)
                conn = sqlite3.connect(tmp_db.name)
                cur = conn.cursor()
                cur.execute(
                    "SELECT Remark, NickName FROM Contact WHERE UserName = ?",
                    (contact_id,)
                )
                row = cur.fetchone()
                if row:
                    contact_name = row[0] if row[0] else (row[1] if row[1] else contact_id)
                conn.close()
            except Exception:
                pass
            finally:
                try:
                    os.unlink(tmp_db.name)
                except OSError:
                    pass

    if fmt == "html":
        print("STATUS:\u6b63\u5728\u5904\u7406\u56fe\u7247...", flush=True)
        xor_key = _detect_xor_key(wxid_dir)
        _export_to_html(all_messages, contact_name, output_path, wxid_dir=wxid_dir, xor_key=xor_key)
    else:
        _export_to_txt(all_messages, contact_name, output_path)

    print(f'DONE:{json.dumps({"count": len(all_messages), "path": output_path, "contact": contact_name}, ensure_ascii=False)}', flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("ERROR:Usage: wechat_worker.py <contacts|export> [args...]", flush=True)
        sys.exit(1)

    command = sys.argv[1]

    if command == "contacts":
        data_dir = sys.argv[2] if len(sys.argv) > 2 else "auto"
        cmd_contacts(data_dir)
    elif command == "export":
        if len(sys.argv) < 5:
            print("ERROR:Usage: wechat_worker.py export <data_dir> <contact_id> <output_path> [start_date] [end_date] [format]", flush=True)
            sys.exit(1)
        start_date = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
        end_date = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else None
        fmt = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else "txt"
        cmd_export(sys.argv[2], sys.argv[3], sys.argv[4], start_date, end_date, fmt)
    else:
        print(f"ERROR:Unknown command: {command}", flush=True)
        sys.exit(1)
