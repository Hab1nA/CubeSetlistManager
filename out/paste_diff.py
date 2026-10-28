# -*- coding: utf-8 -*-
"""对比 C15 粘贴前后(ss_t0.orig vs ss_t0)的轨道区结构:
每个 Name 锚点后到下一锚点前的 [pos][len][0] 三连数与首末 pos,
以及锚点间距变化 → 学 C15 的部分插入位置。
"""
import re, struct, sys
from pathlib import Path

F64 = lambda d, o: struct.unpack_from('>d', d, o)[0]

TRACKNAMES = {'cue', 'click', 'original', 'lead vocal', 'back vocal', 'guitar',
              'bass', 'drum', 'piano', 'other', 'vo effect 1', 'vo effect 2',
              'automation', 'visual', 'keyboard', 'score', 'program',
              'program record', 'track'}

def track_map(d, lo, hi):
    """返回 [(name, anchor_off, run_end, triplets)]。找 Name\x00\x00\x08 术语,
    名字取 tag8 值; 仅保留模板轨名。"""
    anchors = []
    for m in re.finditer(rb'Name\x00\x00\x08\x00\x00\x00[\x00-\x20]', d[lo:hi]):
        v = lo + m.end()
        n = struct.unpack_from('>I', d, v - 4)[0]
        nm = d[v:v + n - 1].rstrip(b'\x00\xef\xbb\xbf').decode(
            'utf-8', 'replace').lstrip('\ufeff')
        if nm in TRACKNAMES:
            anchors.append((v + n, nm))
    # 去重相邻同名
    out = []
    for off, nm in anchors:
        if out and out[-1][1] == nm and off - out[-1][0] < 200:
            continue
        out.append((off, nm))
    runs = []
    for i, (off, nm) in enumerate(out):
        end = out[i + 1][0] if i + 1 < len(out) else hi
        tri = []
        for k in range(off, min(end, len(d)) - 24):
            pos = F64(d, k)
            if not (0 <= pos <= 700000):
                continue
            ln = F64(d, k + 8)
            if not (300 <= ln <= 60000000) or ln != float(int(ln)):
                continue
            if F64(d, k + 16) != 0.0:
                continue
            tri.append((k, pos, ln))
        runs.append((nm, off, end, tri))
    return runs

def show(tag, d, lo, hi):
    print('== %s 轨道区 %06x..%06x' % (tag, lo, hi))
    for nm, off, end, tri in track_map(d, lo, hi):
        poss = [p for _k, p, _l in tri]
        print('  %-14r @%06x..%06x 三连%d pos[%.0f..%.0f]' %
              (nm, off, end, len(tri), min(poss) if poss else -1,
               max(poss) if poss else -1))

a = Path(sys.argv[1]).read_bytes()
b = Path(sys.argv[2]).read_bytes()
show('前', a, 0x274, len(a))
print()
show('后', b, 0x274, len(b))
