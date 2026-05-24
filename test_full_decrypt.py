"""Full decryption test of MicroMsg.db with the found key."""
import os
import sqlite3
import struct
import tempfile
from Crypto.Cipher import AES

PAGE_SIZE = 4096
RESERVE = 48  # WeChat 3.x: IV(16) + HMAC(20) + pad(12)
IV_SIZE = 16
SQLITE_HEADER = b"SQLite format 3\x00"

KEY_HEX = "c33286e76a0dce862557b13e3d55a2212ddebb12db660896973d8c352f86e4ea"
KEY = bytes.fromhex(KEY_HEX)

db_path = r'C:\Users\BOBZHU01\OneDrive - Schenker AG\Documents\WeChat Files\wxid_skrow63hdk2v12\Msg\MicroMsg.db'

print(f"Decrypting: {db_path}")
print(f"File size: {os.path.getsize(db_path)} bytes")

with open(db_path, 'rb') as f:
    file_data = f.read()

total_pages = len(file_data) // PAGE_SIZE
print(f"Total pages: {total_pages}")

# Decrypt all pages
output_path = os.path.join(tempfile.gettempdir(), "MicroMsg_decrypted.db")
decrypted_pages = []

for page_num in range(1, total_pages + 1):
    page_offset = (page_num - 1) * PAGE_SIZE
    page_data = file_data[page_offset:page_offset + PAGE_SIZE]

    if page_num == 1:
        # Page 1: [16-byte salt] [encrypted data] [reserve]
        salt = page_data[:16]
        encrypted = page_data[16:PAGE_SIZE - RESERVE]
        reserve = page_data[PAGE_SIZE - RESERVE:]
    else:
        # Other pages: [encrypted data] [reserve]
        encrypted = page_data[:PAGE_SIZE - RESERVE]
        reserve = page_data[PAGE_SIZE - RESERVE:]

    iv = reserve[:IV_SIZE]

    # Decrypt
    cipher = AES.new(KEY, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(encrypted)

    if page_num == 1:
        # Reconstruct: SQLite header replaces salt area
        full_page = SQLITE_HEADER + decrypted
    else:
        full_page = decrypted

    # Pad to fill the page (reserve area is not part of content)
    decrypted_pages.append(full_page)

# Write output - each page should be (PAGE_SIZE - RESERVE) bytes of content
# But SQLite expects PAGE_SIZE bytes per page in the file
# Actually for SQLCipher, the logical page size IS PAGE_SIZE, so the decrypted
# database should use page_size = PAGE_SIZE - RESERVE... no.
# Let's try writing with the raw decrypted content first.

# Actually: In SQLCipher, the user-facing page_size is 4096, but on disk each page
# is also 4096 bytes: [content (4096-reserve)] [reserve (48)]
# The decrypted logical page is (4096-48) = 4048 bytes... but SQLite header says page=4096
# 
# Wait - from the decrypted header: 0x10 0x00 = 4096 page size
# This means the OUTPUT sqlite db should have 4096-byte pages
# So each decrypted content block (4048 bytes for page 1, or 4048 for others)
# needs to be padded to 4096 bytes

with open(output_path, 'wb') as f:
    for i, page_content in enumerate(decrypted_pages):
        # Pad to 4096 bytes
        padded = page_content[:PAGE_SIZE].ljust(PAGE_SIZE, b'\x00')
        f.write(padded)

print(f"Written decrypted DB to: {output_path}")
print(f"Output size: {os.path.getsize(output_path)} bytes")

# Try to open with sqlite3
try:
    conn = sqlite3.connect(output_path)
    cursor = conn.cursor()
    
    # List tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    print(f"\nTables found: {len(tables)}")
    for t in tables[:20]:
        print(f"  {t[0]}")
    
    # Check Contact table
    if any('Contact' in t[0] for t in tables):
        contact_table = [t[0] for t in tables if 'Contact' in t[0]][0]
        cursor.execute(f"SELECT COUNT(*) FROM {contact_table}")
        count = cursor.fetchone()[0]
        print(f"\n{contact_table} has {count} rows")
        
        cursor.execute(f"PRAGMA table_info('{contact_table}')")
        cols = cursor.fetchall()
        print(f"Columns: {[c[1] for c in cols]}")
        
        # Show first 5 contacts
        cursor.execute(f"SELECT * FROM {contact_table} LIMIT 5")
        for row in cursor.fetchall():
            print(f"  {row[:3]}...")
    
    conn.close()
except Exception as e:
    print(f"\nSQLite error: {e}")
    # Try checking the file header
    with open(output_path, 'rb') as f:
        header = f.read(100)
    print(f"Output header: {header[:32].hex()}")
    print(f"Header text: {repr(header[:16])}")
