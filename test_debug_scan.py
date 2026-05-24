"""Debug: trace what happens inside _scan_memory_for_keys."""
import ctypes
import ctypes.wintypes as wintypes
import re
import subprocess

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

# Get PID
result = subprocess.run(
    ["tasklist", "/FI", "IMAGENAME eq WeChat.exe", "/FO", "CSV", "/NH"],
    capture_output=True, text=True
)
pid = int(result.stdout.strip().split("\n")[0].strip('"').split('","')[1])
print(f"PID: {pid}")

handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
print(f"Handle: {handle}")

key_pattern = re.compile(rb"x'([0-9a-fA-F]{64})'")
keys_found = set()
mbi = MEMORY_BASIC_INFORMATION()
address = 0
regions_total = 0
regions_read = 0
read_errors = 0

while address < 0x7FFFFFFFFFFF:
    r = kernel32.VirtualQueryEx(
        handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
    )
    if r == 0:
        break

    regions_total += 1

    if (mbi.State == MEM_COMMIT and
        mbi.Protect not in (PAGE_NOACCESS, PAGE_GUARD, 0) and
        not (mbi.Protect & PAGE_GUARD) and
        mbi.RegionSize <= 50 * 1024 * 1024):

        buf = (ctypes.c_char * mbi.RegionSize)()
        br = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(address), buf, mbi.RegionSize, ctypes.byref(br)
        )
        if ok and br.value > 0:
            data = bytes(buf[:br.value])
            for m in key_pattern.finditer(data):
                keys_found.add(m.group(1).decode())
            regions_read += 1
        else:
            read_errors += 1

    address += mbi.RegionSize if mbi.RegionSize > 0 else 0x1000

    # Progress check at some intervals
    if regions_total == 10:
        print(f"  After 10 regions: address=0x{address:x}, read={regions_read}, keys={len(keys_found)}")

kernel32.CloseHandle(handle)
print(f"\nTotal regions: {regions_total}")
print(f"Regions read: {regions_read}")
print(f"Read errors: {read_errors}")
print(f"Keys found: {len(keys_found)}")
for k in sorted(keys_found)[:5]:
    print(f"  {k}")
