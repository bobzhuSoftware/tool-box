import sqlite3, tempfile, os, sys, re
from datetime import datetime
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _decompress_content, _xml_extract,
                           _extract_content, _export_to_html, _detect_xor_key,
                           _get_messages_for_contact)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

# Get messages for this contact around the problem time
target_start = int(datetime(2026, 5, 20, 20, 50, 0).timestamp())
target_end = int(datetime(2026, 5, 20, 20, 55, 0).timestamp())

msg_dbs = db_files.get('message', [])
all_messages = []
for db_path in msg_dbs:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    msgs = _get_messages_for_contact(tmp.name, "dongyuheng123", target_start, target_end)
    all_messages.extend(msgs)
    os.unlink(tmp.name)

all_messages.sort(key=lambda m: m.get("time") or 0)
print(f"Got {len(all_messages)} messages in range")

# Export to HTML
output_path = r"c:\Users\BOBZHU01\Downloads\test_quote_html.html"
xor_key = _detect_xor_key(wxid_dir)
print(f"XOR key: {xor_key}")
_export_to_html(all_messages, "董玉衡 - constellation", output_path, wxid_dir=wxid_dir, xor_key=xor_key)
print(f"Exported to: {output_path}")

# Now read the HTML and check what the 20:51:11 message looks like
with open(output_path, "r", encoding="utf-8") as f:
    html_content = f.read()

# Search for "Bad one" or "链接/文件" near 20:51
import re as re2
# Find all occurrences around that time
for match in re2.finditer(r'20:5[0-5]:\d\d.*?</div>\s*</div>\s*</div>', html_content, re.DOTALL):
    text = match.group()
    if '51:11' in text or '链接' in text or 'Bad one' in text or 'quote' in text.lower():
        print(f"\n=== Found match ===")
        print(text[:500])
        print("...")
