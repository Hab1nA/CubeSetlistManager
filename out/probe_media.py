# -*- coding: utf-8 -*-
"""查源工程内 Audio\\ 相对媒体路径的存储格式。"""
import re, struct, sys
from pathlib import Path

BS = b'Audio' + b'\x5c'
d = Path(sys.argv[1]).read_bytes()
hits = [m.start() for m in re.finditer(re.escape(BS), d)]
print('Audio反斜杠 出现', len(hits), '处')
ok = 0
for a in hits[:300]:
    n = struct.unpack_from('>I', d, a - 4)[0]
    e = d.find(b'\x00', a)
    if 6 <= n <= 260 and e > 0 and e + 1 - a == n:
        ok += 1
print('带合法长度前缀:', ok)
a = hits[0]
print('第一处 @%06x 前20B:' % a, d[a - 20:a].hex())
print('串:', d[a:a + 80].split(b'\x00')[0])
# 带前缀的第一处
for a in hits:
    n = struct.unpack_from('>I', d, a - 4)[0]
    e = d.find(b'\x00', a)
    if 6 <= n <= 260 and e > 0 and e + 1 - a == n:
        print('首个合法 @%06x 串=%r' % (a, d[a:e].decode('utf-8', 'replace')))
        break
