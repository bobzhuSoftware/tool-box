import sys
sys.argv = ['wechat_worker.py', 'export', 'auto', 'dongyuheng123', 'out.html', '', '', 'html']
print(f'len(argv): {len(sys.argv)}')
print(f'argv[7]: {repr(sys.argv[7])}')
fmt = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else 'txt'
print(f'fmt: {repr(fmt)}')
print(f'eq: {fmt == "html"}')
