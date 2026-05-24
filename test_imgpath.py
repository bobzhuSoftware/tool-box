"""Extract image paths from BytesExtra."""
import sqlite3, tempfile, os, re
from wechat_worker import (
    _find_wechat_pids, _scan_memory_for_keys,
    _find_wechat_data_dir, _find_wxid_dirs,
    _find_db_files, _find_key_for_db, _decrypt_database
)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
msg_dbs = db_files.get('message', [])
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

key = _find_key_for_db(msg_dbs[1], key_hex_set)
tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
tmp.close()
_decrypt_database(msg_dbs[1], key, tmp.name)

conn = sqlite3.connect(tmp.name)
cur = conn.cursor()
cur.execute("SELECT BytesExtra FROM MSG WHERE Type=3 LIMIT 3")
for i, row in enumerate(cur.fetchall()):
    be = row[0]
    # Find all paths by looking for wxid or FileStorage patterns
    paths = re.findall(rb'wxid_[^\x00\x01-\x1f]+\.dat', be)
    print(f"\nSample {i}:")
    for p in paths:
        decoded = p.decode("utf-8", errors="replace")
        print(f"  {decoded}")

conn.close()
os.unlink(tmp.name)
