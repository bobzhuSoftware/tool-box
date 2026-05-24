"""Full MSG0.db decrypt and read messages."""
import os
import sqlite3
import tempfile
from Crypto.Cipher import AES

PAGE_SIZE = 4096
RESERVE = 48
IV_SIZE = 16
SQLITE_HEADER = b"SQLite format 3\x00"
KEY = bytes.fromhex("c6286fdd031488c9e57de4672368aec89ebf9146c4966f1360cff6f8d9852264")

db_path = r"C:\Users\BOBZHU01\OneDrive - Schenker AG\Documents\WeChat Files\wxid_skrow63hdk2v12\Msg\Multi\MSG0.db"

with open(db_path, "rb") as f:
    file_data = f.read()

total_pages = len(file_data) // PAGE_SIZE
print(f"MSG0.db: {total_pages} pages ({len(file_data) // 1024 // 1024} MB)")

output = os.path.join(tempfile.gettempdir(), "MSG0_dec.db")
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
print(f"Tables: {tables[:20]}")

# Check message tables
for t in tables[:5]:
    cur.execute(f"PRAGMA table_info([{t}])")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM [{t}]")
    cnt = cur.fetchone()[0]
    print(f"\n{t} ({cnt} rows): {cols}")
    if cnt > 0:
        cur.execute(f"SELECT * FROM [{t}] LIMIT 2")
        for row in cur.fetchall():
            display = []
            for v in row:
                s = str(v) if v else "NULL"
                display.append(s[:60] + "..." if len(s) > 60 else s)
            print(f"  {display}")

conn.close()
os.unlink(output)
