import sqlite3, tempfile, os, sys
from datetime import datetime
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _decompress_content, _xml_extract, _extract_content)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

target_ts = int(datetime(2026, 5, 20, 20, 51, 11).timestamp())

msg_dbs = db_files.get('message', [])
for db_path in msg_dbs:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute("""SELECT StrContent, CompressContent, Type, CreateTime, IsSender
                   FROM MSG WHERE CreateTime BETWEEN ? AND ? AND Type=49""",
                (target_ts - 2, target_ts + 2))
    rows = cur.fetchall()
    for content, compress, msg_type, ts, is_sender in rows:
        print(f"Timestamp: {datetime.fromtimestamp(ts)}")
        print(f"Content is None: {content is None}")
        print(f"Content length: {len(content) if content else 0}")
        print(f"CompressContent is None: {compress is None}")
        print(f"CompressContent length: {len(compress) if compress else 0}")
        
        # Simulate exactly what _export_to_txt does
        msg_dict = {
            'type': msg_type,
            'content': content or "",
            'compress': compress,
        }
        result = _extract_content(msg_dict)
        print(f"_extract_content returns: {repr(result[:200])}")
        
        # Check if it starts with XML (which would trigger the fallback)
        if result.startswith("<?xml") or result.startswith("<msg"):
            print("WARNING: result starts with XML - will be replaced with [链接/文件]!")
        else:
            print("OK: result does NOT start with XML")
        
        # Double check: what does the XML look like?
        xml = _decompress_content(compress) if compress else (content or "")
        app_type = _xml_extract(xml, "type")
        print(f"App type from XML: '{app_type}'")
        
    conn.close()
    os.unlink(tmp.name)
