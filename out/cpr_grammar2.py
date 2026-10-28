# -*- coding: utf-8 -*-
"""全文件标签词汇普查：对每个 [u32BE nl][name\\0] 记录统计其后的 u16BE tag
分布，以及 tag0/tag1/tag2 后的字节形态。只读。
用法: py out/cpr_grammar2.py <cpr...>"""
import re, struct, sys, collections
from pathlib import Path

TERM = re.compile(rb'[\x20-\x7e]{1,120}\x00')

def main():
    for arg in sys.argv[1:]:
        d = Path(arg).read_bytes()
        tagcnt = collections.Counter()
        tag0_next = collections.Counter()
        tag1_next = collections.Counter()
        tag2_ver = collections.Counter()
        samples = {}
        for m in TERM.finditer(d):
            o = m.start()
            if o < 4:
                continue
            n = struct.unpack_from('>I', d, o - 4)[0]
            if n not in (len(m.group()), len(m.group()) + 3):
                continue
            v = o + len(m.group()) + (3 if n == len(m.group()) + 3 else 0)
            if v + 2 > len(d):
                continue
            tag = struct.unpack_from('>H', d, v)[0]
            tagcnt[tag] += 1
            if tag == 0:
                nxt = d[v + 2:v + 6].hex()
                tag0_next[nxt] += 1
                samples.setdefault(('tag0', nxt[:4]), (o, d[v - 4 - n:v + 24].hex()))
            elif tag == 1:
                nxt = struct.unpack_from('>Q', d, v + 2)[0]
                tag1_next['v=%d' % nxt] += 1
            elif tag == 2:
                ver = struct.unpack_from('>H', d, v + 2)[0]
                cnt = struct.unpack_from('>I', d, v + 4)[0]
                tag2_ver['ver=%d cnt=%d' % (ver, cnt)] += 1
        print('== %s (%dB)' % (Path(arg).name, len(d)))
        print('tag 分布:', dict(sorted(tagcnt.items())))
        print('tag0 后 4B:', dict(tag0_next.most_common(8)))
        print('tag1 值分布(前8):', dict(tag1_next.most_common(8)))
        print('tag2 ver/cnt(前10):', dict(tag2_ver.most_common(10)))
        for k, (o, h) in list(samples.items())[:6]:
            print('  样本 %s @%06x: %s' % (k, o, h))

if __name__ == '__main__':
    main()
