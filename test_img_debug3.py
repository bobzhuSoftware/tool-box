import sqlite3, tempfile, os, re, sys
sys.path.insert(0, '.')
from wechat_worker import (_find_wechat_data_dir, _find_wxid_dirs, _find_wechat_pids,
                           _scan_memory_for_keys, _find_db_files, _find_key_for_db,
                           _decrypt_database)

version, data_dir = _find_wechat_data_dir('auto')
wxid_dirs = _find_wxid_dirs(version, data_dir)
wxid_dir = wxid_dirs[0]

db_files = _find_db_files(wxid_dir)
pids = _find_wechat_pids()
key_hex_set = set()
for pid in pids:
    key_hex_set.update(_scan_memory_for_keys(pid))

msg_dbs = db_files.get('message', [])

total_img = 0
full_exists = 0
thumb_exists = 0
either_exists = 0

for db_path in msg_dbs[:2]:
    key = _find_key_for_db(db_path, key_hex_set)
    if not key:
        continue
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    _decrypt_database(db_path, key, tmp.name)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute("SELECT BytesExtra FROM MSG WHERE Type=3 LIMIT 100")
    rows = cur.fetchall()
    for (extra,) in rows:
        if not extra:
            continue
        total_img += 1
        
        # Extract ALL .dat paths from BytesExtra
        all_paths = re.findall(rb'wxid_[^\x00\x01-\x1f]+\.dat', extra)
        
        has_full = False
        has_thumb = False
        for p in all_paths:
            p_str = p.decode('utf-8', errors='replace')
            parts = p_str.split("\\", 1)
            abs_path = os.path.join(wxid_dir, parts[1]) if len(parts) == 2 else os.path.join(wxid_dir, p_str)
            if os.path.isfile(abs_path):
                if '\\Thumb\\' in p_str or '_t.dat' in p_str:
                    has_thumb = True
                else:
                    has_full = True
        
        if has_full:
            full_exists += 1
        if has_thumb:
            thumb_exists += 1
        if has_full or has_thumb:
            either_exists += 1
    conn.close()
    os.unlink(tmp.name)

print(f'Total images: {total_img}')
print(f'Full-size exists: {full_exists} ({100*full_exists//total_img}%)')
print(f'Thumbnail exists: {thumb_exists} ({100*thumb_exists//total_img}%)')
print(f'Either exists: {either_exists} ({100*either_exists//total_img}%)')

# Also check: does WeChat store images in other locations?
print('\n--- Checking alternative image storage ---')
attach_dir = os.path.join(wxid_dir, 'FileStorage')
if os.path.isdir(attach_dir):
    for item in os.listdir(attach_dir):
        path = os.path.join(attach_dir, item)
        if os.path.isdir(path):
            # Count .dat files in first subfolder level
            count = 0
            for root, dirs, files in os.walk(path):
                count += sum(1 for f in files if f.endswith('.dat'))
                if count > 100:
                    break
            if count > 0:
                print(f'  {item}: {count}+ .dat files')

# Check if there's a separate Image folder
image_dir = os.path.join(wxid_dir, 'FileStorage', 'Image')
if os.path.isdir(image_dir):
    total_dat = 0
    for root, dirs, files in os.walk(image_dir):
        total_dat += sum(1 for f in files if f.endswith('.dat'))
    print(f'\nImage dir total .dat files: {total_dat}')
