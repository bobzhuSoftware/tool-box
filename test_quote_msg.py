import sqlite3, tempfile, os, re, sys
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _decompress_content, _xml_extract)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

msg_dbs = db_files.get('message', [])
found = 0
for db_path in msg_dbs:
    if found >= 3:
        break
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute("SELECT StrContent, CompressContent FROM MSG WHERE Type=49 LIMIT 500")
    rows = cur.fetchall()
    for content, compress in rows:
        xml = _decompress_content(compress) if compress else content
        if not xml:
            continue
        app_type = _xml_extract(xml, "type")
        if app_type == "57":
            found += 1
            print(f"=== Quote message {found} ===")
            print(xml[:3000])
            print("\n\n")
            if found >= 3:
                break
    conn.close()
    os.unlink(tmp.name)

if found == 0:
    print("No quote messages found")
