import sqlite3, tempfile, os, re, sys
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _extract_image_path)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))
print(f'Keys found: {len(key_hex_set)}')

# Decrypt first MSG db and check type=3 messages
msg_dbs = db_files.get('message', [])
for db_path in msg_dbs[:1]:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        print(f'No key for {db_path}')
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute("SELECT StrContent, BytesExtra, CreateTime FROM MSG WHERE Type=3 LIMIT 10")
    rows = cur.fetchall()
    print(f'Image msgs found: {len(rows)}')
    for i, (content, extra, ts) in enumerate(rows[:5]):
        print(f'\n  Msg {i}: content_len={len(content) if content else 0}, extra_type={type(extra).__name__}, extra_len={len(extra) if extra else 0}')
        if extra:
            # Use the same function as the export
            path = _extract_image_path(extra)
            print(f'    _extract_image_path result: {path}')
            
            # Try different regex patterns
            paths_img = re.findall(rb'wxid_[^\x00\x01-\x1f]+\\Image\\[^\x00\x01-\x1f]+\.dat', extra)
            paths_any_dat = re.findall(rb'[^\x00\x01-\x1f]{10,}\.dat', extra)
            print(f'    regex wxid..Image..dat: {[p.decode("utf-8", errors="replace") for p in paths_img[:2]]}')
            print(f'    regex any long .dat: {[p.decode("utf-8", errors="replace")[:100] for p in paths_any_dat[:3]]}')
            
            # Show all printable strings > 10 chars from extra
            strings = re.findall(rb'[\x20-\x7e]{10,}', extra)
            print(f'    printable strings: {[s.decode("ascii", errors="replace") for s in strings[:5]]}')
            
            # Show raw bytes around any path separator
            for sep in [b'\\', b'/', b'Image', b'Thumb', b'.dat']:
                idx = extra.find(sep)
                if idx >= 0:
                    snippet = extra[max(0, idx-20):idx+30]
                    print(f'    near "{sep}": {snippet.hex()} = {snippet}')
                    break
        else:
            print(f'    BytesExtra is None/empty')
    conn.close()
    os.unlink(tmp.name)
