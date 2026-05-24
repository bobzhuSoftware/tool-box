import sys, subprocess, tempfile, os
sys.path.insert(0, '.')

worker = r"c:\Users\BOBZHU01\Projects\Video Transcript\wechat_worker.py"
output = r"c:\Users\BOBZHU01\Downloads\test_html_final.html"

proc = subprocess.Popen(
    [sys.executable, worker, "export", "auto", "dongyuheng123", output, "", "", "html"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8"
)
for line in proc.stdout:
    print(line.rstrip())
proc.wait()

# Check the result
with open(output, "r", encoding="utf-8") as f:
    content = f.read()

# Check first line
first_line = content.splitlines()[0]
print(f"\nFirst line: {first_line[:100]}")

# Find the 20:51:11 message
import re
matches = list(re.finditer(r'20:51:11', content))
print(f"\n20:51:11 occurrences: {len(matches)}")
for m in matches:
    snippet = content[max(0, m.start()-20):m.start()+400]
    print(snippet[:400])

# Find the 20:54:23 message
matches2 = list(re.finditer(r'20:54:23', content))
print(f"\n20:54:23 occurrences: {len(matches2)}")
for m in matches2:
    snippet = content[max(0, m.start()-20):m.start()+300]
    print(snippet[:300])
