import sqlite3, tempfile, os, re, sys
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

# Target: 2026-05-20 20:51:11 - convert to timestamp
target_ts = int(datetime(2026, 5, 20, 20, 51, 11).timestamp())
# Also check 20:54:23 
target_ts2 = int(datetime(2026, 5, 20, 20, 54, 23).timestamp())

contact_id = None  # Find it from the messages

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
    
    # Look for messages at that exact timestamp from this contact
    cur.execute("""SELECT StrContent, CompressContent, Type, CreateTime, IsSender, StrTalker 
                   FROM MSG WHERE CreateTime BETWEEN ? AND ? AND Type=49""",
                (target_ts - 2, target_ts + 2))
    rows = cur.fetchall()
    for content, compress, msg_type, ts, is_sender, talker in rows:
        print(f"=== Message at {datetime.fromtimestamp(ts)} from {talker} (type={msg_type}) ===")
        xml = _decompress_content(compress) if compress else content
        if xml:
            print(f"XML length: {len(xml)}")
            app_type = _xml_extract(xml, "type")
            print(f"App type: '{app_type}'")
            title = _xml_extract(xml, "title")
            print(f"Title: '{title}'")
            # Show first 2000 chars
            print(xml[:2000])
        else:
            print(f"No XML. Content: {content[:500] if content else 'None'}")
            print(f"CompressContent: {len(compress) if compress else 0} bytes")
        
        msg_dict = {'type': msg_type, 'content': content, 'compress': compress}
        result = _extract_content(msg_dict)
        print(f"\n_extract_content result: {result[:200]}")
        print()

    # Also check 20:54:23
    cur.execute("""SELECT StrContent, CompressContent, Type, CreateTime, IsSender, StrTalker 
                   FROM MSG WHERE CreateTime BETWEEN ? AND ? AND Type=49""",
                (target_ts2 - 2, target_ts2 + 2))
    rows2 = cur.fetchall()
    for content, compress, msg_type, ts, is_sender, talker in rows2:
        print(f"\n=== Message at {datetime.fromtimestamp(ts)} from {talker} (type={msg_type}) ===")
        xml = _decompress_content(compress) if compress else content
        if xml:
            app_type = _xml_extract(xml, "type")
            print(f"App type: '{app_type}'")
            title = _xml_extract(xml, "title")
            print(f"Title: '{title}'")
            print(xml[:2000])
        msg_dict = {'type': msg_type, 'content': content, 'compress': compress}
        result = _extract_content(msg_dict)
        print(f"\n_extract_content result: {result[:200]}")

    conn.close()
    os.unlink(tmp.name)
