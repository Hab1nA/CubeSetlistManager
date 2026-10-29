# -*- coding: utf-8 -*-
"""minidump 全量读入解析: 每线程 RIP 模块分布。"""
import struct, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
d = open(r'C:\Users\XKZ\AppData\Local\Temp\c15_wedge.dmp', 'rb').read()
print('loaded', len(d))
sig, ver, nstreams, rva = struct.unpack_from('<4sIII', d, 0)
streams = {}
for i in range(nstreams):
    t, sz, r = struct.unpack_from('<III', d, rva + 12 * i)
    streams[t] = (sz, r)
mods = []
sz, r = streams[4]
cnt = struct.unpack_from('<I', d, r)[0]
for i in range(cnt):
    mbase, msize = struct.unpack_from('<QQ', d, r + 4 + i * 108)
    nlen, noff = struct.unpack_from('<II', d, r + 4 + i * 108 + 24)
    name = d[noff:noff + nlen].decode('utf-16-le', 'replace')
    name = name.rsplit('\\', 1)[-1]
    mods.append((mbase, msize, name))
mods.sort()
def which(a):
    for mb, ms, nm in mods:
        if mb <= a < mb + ms:
            return nm + '+0x' + format(a - mb, 'x')
    return 'unknown'
sz, r = streams[3]
cnt = struct.unpack_from('<I', d, r)[0]
res = collections.Counter()
rips = []
for i in range(cnt):
    row = d[r + 4 + i * 48: r + 4 + (i + 1) * 48]
    tid, susp = struct.unpack_from('<II', row, 0)
    csz, crva = struct.unpack_from('<II', row, 40)
    if csz >= 0x100:
        rip = struct.unpack_from('<Q', d, crva + 0xF8)[0]
        res[which(rip)] += 1
        rips.append((tid, susp, which(rip)))
for k, v in res.most_common(20):
    print(v, k)
print('---主线程/高suspend---')
for tid, susp, w in rips[:15]:
    print(tid, susp, w)
