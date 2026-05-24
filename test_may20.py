import sys, subprocess
sys.path.insert(0, '.')

worker = r"c:\Users\BOBZHU01\Projects\Video Transcript\wechat_worker.py"
output = r"c:\Users\BOBZHU01\Downloads\董玉衡_5月20日.html"

proc = subprocess.Popen(
    [sys.executable, worker, "export", "auto", "dongyuheng123", output, "2026-05-20", "2026-05-20", "html"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8"
)
for line in proc.stdout:
    print(line.rstrip())
proc.wait()
print(f"\nFile saved to: {output}")
