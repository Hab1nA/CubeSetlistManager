# -*- coding: utf-8 -*-
"""CPR 结构树解析器 v2(实测语法全集) + 工程索引提取。
语法:
  item := [u32BE nl][name nl B][u16BE tag][value]
  tag1/4: 8B 值          tag3/8: [u32BE n][n B]
  tag0: [u32BE ln]; ln==FFFFFFFF -> [u32BE sub]+恰1子项; 否则 ln 字节 blob
  tag2: [u16BE ver][u32BE cnt]
    ver2 -> cnt*i64 | ver0 -> cnt*[u32 n][nB] | ver4/5 -> cnt*([u32 m]+m子项)
    ver7 -> cnt 字节原始块 | ver20 -> 回退4B按cnt个项解析(首项前缀=cnt)
    其余 -> cnt*子项
用法:
  py out/cpr_tree2.py dump <cpr>          # 轨道/部分索引
  py out/cpr_tree2.py walk <cpr> [start]  # 试走
"""
import struct, sys
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

def parse_item(d, o, depth=0):
    if depth > 80:
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
            raise Fail('tag%d len %x @%06x' % (tag, n, o))
        nd.value = d[o + 4:o + 4 + n]; o += 4 + n
    elif tag == 2:
        ver = U16(d, o); cnt = U32(d, o + 2); o += 6
        nd.value = (ver, cnt)
        nd.kids = []
        if cnt > 500000:
            raise Fail('cnt=%x @%06x' % (cnt, o))
        if ver == 2:
            nd.value = (ver, [I64(d, o + k * 8) for k in range(cnt)])
            o += cnt * 8
        elif ver == 0:
            for _ in range(cnt):
                n = U32(d, o)
                if o + 4 + n > len(d):
                    raise Fail('chunk %x @%06x' % (n, o))
                o += 4 + n
        elif ver in (4, 5):
            for _ in range(cnt):
                m = U32(d, o); o += 4
                for _ in range(m):
                    c, o = parse_item(d, o, depth + 1)
                    nd.kids.append(c)
        elif ver == 7:
            if o + cnt > len(d):
                raise Fail('v7 blob %x @%06x' % (cnt, o))
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
            sub = U32(d, o); o += 4
            c, o = parse_item(d, o, depth + 1)
            nd.kids = [c]
            nd.value = ('obj', sub)
        else:
            if o + ln > len(d):
                raise Fail('t0 len %x @%06x' % (ln, o))
            nd.value = d[o:o + ln]; o += ln
    else:
        raise Fail('tag%04x @%06x name=%r' % (tag, o - 2, name))
    nd.end = o
    return nd, o

def stream_start(d):
    return 0x510 if len(d) > 0x600 else 20

def parse_file(path):
    d = Path(path).read_bytes()
    st = stream_start(d)
    tops = []
    o = st
    while o < len(d):
        try:
            nd, o = parse_item(d, o)
            tops.append(nd)
        except Fail:
            break
    return d, st, tops, o

def find_all(node, pred, out):
    if pred(node):
        out.append(node)
    for k in (node.kids or []):
        find_all(k, pred, out)

def track_index(tops):
    """M*Track 节点 -> (名, node)。名字取其后代 Name/tag8。"""
    tracks = []
    def is_track(n):
        return n.tag == 0 and n.name.startswith('M') and n.name.endswith('Track')
    found = []
    for t in tops:
        find_all(t, is_track, found)
    for tr in found:
        nm = ''
        def get_name(n):
            nonlocal nm
            if nm:
                return
            if n.name == 'Name' and n.tag == 8 and isinstance(n.value, bytes):
                nm = n.value.rstrip(b'\x00').decode('utf-8', 'replace')
        find_all(tr, get_name, [])
        tracks.append((nm, tr))
    return tracks

def main():
    mode = sys.argv[1]
    path = sys.argv[2]
    d, st, tops, o = parse_file(path)
    if mode == 'walk':
        print('流起点 %s, 顶层 %d 项, 走到 %06x/%06x' %
              (hex(st), len(tops), o, len(d)))
    elif mode == 'dump':
        print('顶层 %d 项, 走到 %06x/%06x' % (len(tops), o, len(d)))
        for nm, tr in track_index(tops):
            print('轨 %-22r @%06x..%06x' % (nm, tr.start, tr.end))

if __name__ == '__main__':
    main()
