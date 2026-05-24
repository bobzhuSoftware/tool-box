import sqlite3, tempfile, os, re, sys
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _extract_image_path, _detect_xor_key, _decode_image)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

xor_key = _detect_xor_key(wxid_dir)
print(f'XOR key: {xor_key:#x}' if xor_key else 'XOR key: None')

msg_dbs = db_files.get('message', [])
total_img = 0
embedded = 0
fallback_thumb = 0
no_file = 0

for db_path in msg_dbs[:2]:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute("SELECT BytesExtra FROM MSG WHERE Type=3 LIMIT 200")
    rows = cur.fetchall()
    for (extra,) in rows:
        total_img += 1
        full_path, thumb_path = _extract_image_path(extra)
        found = False
        for rel_path in (full_path, thumb_path):
            if not rel_path:
                continue
            parts = rel_path.split("\\", 1)
            abs_path = os.path.join(wxid_dir, parts[1]) if len(parts) == 2 else os.path.join(wxid_dir, rel_path)
            if os.path.isfile(abs_path):
                mime, img_data = _decode_image(abs_path, xor_key)
                if img_data:
                    if rel_path == thumb_path and rel_path != full_path:
                        fallback_thumb += 1
                    embedded += 1
                    found = True
                    break
        if not found:
            no_file += 1
    conn.close()
    os.unlink(tmp.name)

print(f'\nTotal images: {total_img}')
print(f'Successfully embedded: {embedded} ({100*embedded//total_img}%)')
print(f'  - from full-size: {embedded - fallback_thumb}')
print(f'  - from thumbnail fallback: {fallback_thumb}')
print(f'No file found: {no_file} ({100*no_file//total_img}%)')
