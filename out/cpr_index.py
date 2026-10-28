# -*- coding: utf-8 -*-
"""CPR 结构索引器: 带重同步的走查 + 轨道/部分索引提取。
重同步: 解析失败处向前扫描下一个可连续解析 ≥2KB 的合法术语, 记录 gap。
输出: 每轨 (名, 轨字节区, 部分列表容器区, 各部分字节区+pos)。
用法:
  py out/cpr_index.py <cpr> [start]
"""
import re, struct, sys
from pathlib import Path

U16 = lambda d, o: struct.unpack_from('>H', d, o)[0]
U32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
I64 = lambda d, o: struct.unpack_from('>q', d, o)[0]
F64 = lambda d, o: struct.unpack_from('>d', d, o)[0]

class Fail(Exception):
    pass

class Node:
    __slots__ = ('name', 'tag', 'start', 'end', 'value', 'kids')
    def __init__(self, name, tag, start):
        self.name, self.tag, self.start = name, tag, start
        self.value = None
        self.kids = None
        self.end = 0

SANE = re.compile(r'^[\x20-\x7e]{1,60}$')

def parse_item(d, o, depth=0):
    if depth > 100:
        raise Fail('deep @%06x' % o)
    st = o
    nl = U32(d, o)
    if nl > 0x7FFFFFFF or o + 4 + nl + 2 > len(d):
        raise Fail('nl=%x @%06x' % (nl, o))
    name = d[o + 4:o + 4 + nl].rstrip(b'\x00').decode('utf-8', 'replace')
    o += 4 + nl
    tag = U16(d, o)
    o += 2
    nd = Node(name, tag, st)
    if tag == 1:
        nd.value = I64(d, o); o += 8
    elif tag == 4:
        nd.value = F64(d, o); o += 8
    elif tag in (8, 3):
        n = U32(d, o)
        if o + 4 + n > len(d):
            raise Fail('len %x @%06x' % (n, o))
        nd.value = d[o + 4:o + 4 + n]; o += 4 + n
    elif tag == 2:
        ver = U16(d, o); cnt = U32(d, o + 2); o += 6
        nd.kids = []
        if cnt > 500000:
            raise Fail('cnt @%06x' % o)
        if ver == 2:
            nd.value = [I64(d, o + k * 8) for k in range(cnt)]; o += cnt * 8
        elif ver == 0:
            for _ in range(cnt):
                n = U32(d, o)
                if o + 4 + n > len(d):
                    raise Fail('chunk @%06x' % o)
                o += 4 + n
        elif ver in (4, 5):
            for _ in range(cnt):
                m = U32(d, o); o += 4
                for _ in range(m):
                    c, o = parse_item(d, o, depth + 1)
                    nd.kids.append(c)
        elif ver == 7:
            if o + cnt > len(d):
                raise Fail('v7 @%06x' % o)
            o += cnt
        elif ver == 20:
            o -= 4
            for _ in range(cnt):
                c, o = parse_item(d, o, depth + 1)
                nd.kids.append(c)
        else:
            for _ in range(cnt):
                c, o = parse_item(d, o, depth + 1)
                nd.kids.append(c)
    elif tag == 0:
        ln = U32(d, o); o += 4
        if ln == 0xFFFFFFFF:
            c, o = parse_item(d, o + 4, depth + 1)
            nd.kids = [c]
        elif ln >= 1024 and depth < 40:
            # 大 blob: 可能为 [小头部][术语子流]。先 4B 头快筛, 再全量试解析。
            cands = []
            for skip in range(0, 17):
                q = o + skip
                if q + 8 > len(d):
                    break
                n2 = U32(d, q)
                if not (2 <= n2 <= 300) or q + 4 + n2 + 2 > len(d):
                    continue
                raw = d[q + 4:q + 4 + n2]
                if not raw.endswith(b'\x00'):
                    continue
                t2 = U16(d, q + 4 + n2)
                if t2 not in (0, 1, 2, 3, 4, 7, 8, 20, 34, 162, 512):
                    continue
                cands.append(skip)
            best = None
            for skip in cands:
                q = o + skip
                items, q2 = [], q
                try:
                    while q2 < o + ln:
                        c, q2 = parse_item(d, q2, depth + 1)
                        items.append(c)
                except (Fail, struct.error, IndexError, RecursionError):
                    continue
                if q2 != o + ln:
                    continue
                cov = sum(c.end - c.start for c in items)
                if cov >= (ln - skip) * 0.9 and (best is None or
                                                 cov > best[0]):
                    best = (cov, skip, items)
            if best:
                nd.value = ('sub', o, best[1], ln)
                nd.kids = best[2]
                o += ln
            else:
                if o + ln > len(d):
                    raise Fail('t0 @%06x' % o)
                nd.value = d[o:o + ln]; o += ln
        else:
            if o + ln > len(d):
                raise Fail('t0 @%06x' % o)
            nd.value = d[o:o + ln]; o += ln
    else:
        raise Fail('tag%04x @%06x' % (tag, o - 2))
    nd.end = o
    return nd, o

def probe(d, o, depth_limit=3, budget=2048):
    """试从 o 解析一个项; 成功且推进>=budget 视为好起点。"""
    try:
        nd, end = parse_item(d, o, 0)
    except (Fail, struct.error, IndexError):
        return None
    if end - o < budget:
        return None
    if not SANE.match(nd.name[:60]):
        return None
    return nd, end

def walk_resync(d, start):
    tops, gaps = [], []
    o = start
    while o < len(d):
        r = probe(d, o, budget=16)
        if r:
            nd, o2 = r
            tops.append(nd)
            o = o2
            continue
        # 该位置不能干净起步 → 从这里找下一个能连续走 >=2KB 的术语
        found = None
        q = o + 1
        limit = min(len(d) - 4, o + 400000)
        while q < limit:
            r2 = probe(d, q, budget=2048)
            if r2:
                found = (q, r2[1])
                break
            q += 1
        if not found:
            gaps.append((o, len(d)))
            break
        gaps.append((o, found[0]))
        tops.append(('GAP', found[0] - o))
        o = found[0]
    return tops, gaps, o

def collect(node, pred, out):
    if pred(node):
        out.append(node)
    for k in (node.kids or []):
        collect(k, pred, out)

def find_name(node):
    best = None
    def rec(n, depth):
        nonlocal best
        if n.name == 'Name' and n.tag == 8 and isinstance(n.value, bytes):
            if best is None or depth < best[0]:
                best = (depth, n.value.rstrip(b'\x00')
                        .decode('utf-8', 'replace').lstrip('\ufeff'))
        for k in (n.kids or []):
            rec(k, depth + 1)
    rec(node, 0)
    return best[1] if best else None

def main():
    path = sys.argv[1]
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x548
    d = Path(path).read_bytes()
    tops, gaps, end = walk_resync(d, start)
    real = [t for t in tops if not (isinstance(t, tuple) and t[0] == 'GAP')]
    ngap = len(tops) - len(real)
    print('顶层项 %d (gap %d), 走到 %06x/%06x' % (len(real), ngap, end, len(d)))
    for g in gaps[:10]:
        print('GAP %06x..%06x (%dB)' % (g[0], g[1], g[1] - g[0]))
    # 轨道索引
    tracks = []
    for t in real:
        collect(t, lambda n: n.name.endswith('Track') and
                n.name.startswith('M'), tracks)
    print('-- 轨道 %d --' % len(tracks))
    for tr in tracks:
        print('%06x..%06x %-24r 名=%r' % (tr.start, tr.end, tr.name, find_name(tr)))

if __name__ == '__main__':
    main()
