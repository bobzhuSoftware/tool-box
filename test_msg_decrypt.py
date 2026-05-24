"""Test MSG0.db decryption."""
import os
import sqlite3
import tempfile
from Crypto.Cipher import AES

PAGE_SIZE = 4096
RESERVE = 48
IV_SIZE = 16
KEY = bytes.fromhex("c33286e76a0dce862557b13e3d55a2212ddebb12db660896973d8c352f86e4ea")
SQLITE_HEADER = b"SQLite format 3\x00"

db_path = r"C:\Users\BOBZHU01\OneDrive - Schenker AG\Documents\WeChat Files\wxid_skrow63hdk2v12\Msg\Multi\MSG0.db"

with open(db_path, "rb") as f:
    file_data = f.read()

total_pages = len(file_data) // PAGE_SIZE
print(f"MSG0.db: {total_pages} pages")

output = os.path.join(tempfile.gettempdir(), "MSG0_decrypted.db")
with open(output, "wb") as f:
    for pn in range(1, total_pages + 1):
        offset = (pn - 1) * PAGE_SIZE
        page = file_data[offset:offset + PAGE_SIZE]
        if pn == 1:
            enc = page[16:PAGE_SIZE - RESERVE]
            reserve = page[PAGE_SIZE - RESERVE:]
        else:
            enc = page[:PAGE_SIZE - RESERVE]
            reserve = page[PAGE_SIZE - RESERVE:]
        iv = reserve[:IV_SIZE]
        cipher = AES.new(KEY, AES.MODE_CBC, iv)
        dec = cipher.decrypt(enc)
        if pn == 1:
            full = (SQLITE_HEADER + dec)[:PAGE_SIZE].ljust(PAGE_SIZE, b"\x00")
        else:
            full = dec[:PAGE_SIZE].ljust(PAGE_SIZE, b"\x00")
        f.write(full)

print(f"Decrypted to: {output}")

conn = sqlite3.connect(output)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables ({len(tables)}): {tables[:15]}")

# Find message tables
for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM [{t}]")
    cnt = cur.fetchone()[0]
    if cnt > 0:
        cur.execute(f"PRAGMA table_info([{t}])")
        cols = [c[1] for c in cur.fetchall()]
        print(f"\n{t}: {cnt} rows, Cols: {cols}")
        cur.execute(f"SELECT * FROM [{t}] LIMIT 2")
        for row in cur.fetchall():
            # Truncate long values
            display = []
            for v in row:
                s = str(v)
                display.append(s[:80] + "..." if len(s) > 80 else s)
            print(f"  {display}")
        if cnt > 5:
            break  # just show one table

conn.close()
os.unlink(output)
