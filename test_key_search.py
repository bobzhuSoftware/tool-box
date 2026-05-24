"""Quick test: search WeChat memory for SQLCipher key patterns."""
import ctypes
import ctypes.wintypes as wintypes
import re
import subprocess
import sys

# Find WeChat PID
result = subprocess.run(
    ['tasklist', '/FI', 'IMAGENAME eq WeChat.exe', '/FO', 'CSV', '/NH'],
    capture_output=True, text=True
)
pid = None
for line in result.stdout.strip().split('\n'):
    parts = line.strip().strip('"').split('","')
    if len(parts) >= 2:
        try:
            pid = int(parts[1])
            break
        except ValueError:
            pass

if not pid:
    print("WeChat not running!")
    sys.exit(1)

print(f"PID: {pid}")

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class MBI(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD),
    ]


handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
if not handle:
    print(f"Failed to open process. Error: {ctypes.get_last_error()}")
    sys.exit(1)

print(f"Handle: {handle}")

# Search patterns:
# 1. x'<64hex>' — 32-byte key as hex string
# 2. x'<64hex><32hex>' — key + salt
# 3. Raw 32-byte sequences that look like keys
patterns = [
    re.compile(rb"x'([0-9a-fA-F]{64})'"),        # 32-byte key
    re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'"),  # key + salt
]

keys_found = set()
mbi = MBI()
address = 0
regions_scanned = 0
total_bytes = 0

print("Scanning memory...", flush=True)

while address < 0x7FFFFFFFFFFF:
    r = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0:
        break

    if (mbi.State == MEM_COMMIT and
        mbi.Protect not in (PAGE_NOACCESS, PAGE_GUARD, 0) and
        not (mbi.Protect & PAGE_GUARD) and
        mbi.RegionSize <= 50 * 1024 * 1024):

        buf = (ctypes.c_char * mbi.RegionSize)()
        br = ctypes.c_size_t(0)
        if kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, mbi.RegionSize, ctypes.byref(br)):
            data = bytes(buf[:br.value])
            total_bytes += br.value

            for pattern in patterns:
                for m in pattern.finditer(data):
                    hex_key = m.group(1).decode()
                    keys_found.add(hex_key)

            regions_scanned += 1

            # Print progress every 100 regions
            if regions_scanned % 200 == 0:
                print(f"  ...scanned {regions_scanned} regions ({total_bytes // 1024 // 1024} MB), found {len(keys_found)} keys so far", flush=True)

    address += mbi.RegionSize if mbi.RegionSize > 0 else 0x1000

kernel32.CloseHandle(handle)

print(f"\nDone! Scanned {regions_scanned} regions ({total_bytes // 1024 // 1024} MB)")
print(f"Unique keys found: {len(keys_found)}")
for hex_key in sorted(keys_found):
    print(f"  {hex_key}")
