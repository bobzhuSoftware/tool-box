import sqlite3, tempfile, os, re, sys
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database, _extract_image_path, _detect_xor_key)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]
print(f'wxid_dir: {wxid_dir}')

db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

xor_key = _detect_xor_key(wxid_dir)
print(f'XOR key: {xor_key:#x}' if xor_key else 'XOR key: None')

# Use the specific contact from the open file (董玉衡)
contact_id = None
msg_dbs = db_files.get('message', [])

# Count how many images have valid paths vs existing files
total_img = 0
path_found = 0
file_exists = 0
file_missing_examples = []

for db_path in msg_dbs:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    # Get image messages (type=3) for all contacts to test broadly
    cur.execute("SELECT BytesExtra FROM MSG WHERE Type=3 LIMIT 50")
    rows = cur.fetchall()
    for (extra,) in rows:
        total_img += 1
        rel_path = _extract_image_path(extra)
        if rel_path:
            path_found += 1
            # Build absolute path same as _export_to_html does
            parts = rel_path.split("\\", 1)
            if len(parts) == 2:
                abs_path = os.path.join(wxid_dir, parts[1])
            else:
                abs_path = os.path.join(wxid_dir, rel_path)
            if os.path.isfile(abs_path):
                file_exists += 1
            else:
                if len(file_missing_examples) < 5:
                    file_missing_examples.append(abs_path)
    conn.close()
    os.unlink(tmp.name)

print(f'\nResults:')
print(f'  Total image messages checked: {total_img}')
print(f'  Path extracted from BytesExtra: {path_found}')
print(f'  .dat file exists on disk: {file_exists}')
print(f'  .dat file MISSING: {path_found - file_exists}')
print(f'\nMissing file examples:')
for p in file_missing_examples:
    print(f'  {p}')
    # Check if parent dir exists
    parent = os.path.dirname(p)
    print(f'    parent exists: {os.path.isdir(parent)}')
    if os.path.isdir(parent):
        files_in_dir = os.listdir(parent)[:5]
        print(f'    files in parent: {files_in_dir}')
