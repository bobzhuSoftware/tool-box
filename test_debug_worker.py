"""Debug: why does importing from wechat_worker fail to find keys?"""
import sys
sys.path.insert(0, ".")
from wechat_worker import _find_wechat_pids, _scan_memory_for_keys, kernel32

pids = _find_wechat_pids()
print(f"PIDs: {pids}")
print(f"kernel32: {kernel32}")

if pids:
    # Test OpenProcess directly
    import ctypes
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pids[0])
    print(f"Direct OpenProcess handle: {handle}")
    print(f"Last error: {ctypes.get_last_error()}")
    if handle:
        kernel32.CloseHandle(handle)

    # Now call the function
    print("\nCalling _scan_memory_for_keys...")
    keys = _scan_memory_for_keys(pids[0])
    print(f"Keys found: {len(keys)}")
    for k in list(keys)[:3]:
        print(f"  {k}")
