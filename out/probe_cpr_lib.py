# -*- coding: utf-8 -*-
"""CPR 全库结构提取（只读）第 6 步：为合并设计产出实测数据。
每文件: 轨道类序列 / 循环定位条(秒) / 拍号事件 / 速度块尺寸 / 媒体路径
统计（含工程外路径数）/ 结构标记。
用法: py out/probe_cpr_lib.py <工程根目录>
"""
import os, re, struct, sys, collections

TERM = re.compile(rb'[\x20-\x7e]{2,80}\x00')
TRACK_RE = re.compile(r'^M[A-Za-z]{2,26}Track$')
RE_CYCLE = re.compile(rb'Cycle (Left|Right)\x00\x00\x02\x00\x06\x00\x00\x00\x02'
                      rb'\x00\x00\x00\x05Time\x00\x00\x04(.{8})', re.S)

def u16be(b, o): return struct.unpack_from('>H', b, o)[0]
def u32be(b, o): return struct.unpack_from('>I', b, o)[0]

def find_terms(d, pred):
    out = []
    for m in TERM.finditer(d):
        o = m.start()
        if o < 4:
            continue
        n = u32be(d, o - 4)
        raw = m.group()
        if n in (len(raw), len(raw) + 3):
            name = raw[:-1].decode('utf-8', 'ignore')
            if pred(name):
                out.append((o, name))
    return out

def sig_events(d):
    """MTimeSignatureEvent 子树提取（回溯两种头部形态）：
    A: [u16 0][8B][1 子]   B: [u16 0][6B][count][子×count]"""
    out = []
    for o, nm in find_terms(d, lambda n: n == 'MTimeSignatureEvent'):
        base = o + len(nm.encode('utf-8')) + 1
        for mid in (6, 8, 10, 4):
            p = base + 2 + mid
            try:
                cnt = u32be(d, p)
                if cnt > 32:
                    continue
                p += 4
                fld = {}
                ok = True
                for _ in range(cnt):
                    n2 = u32be(d, p)
                    if not (2 <= n2 <= 100):
                        ok = False
                        break
                    key = d[p + 4:p + 4 + n2 - 1].decode('utf-8', 'ignore')
                    p += 4 + n2
                    tag = u16be(d, p)
                    p += 2
                    if tag == 1:
                        fld[key] = struct.unpack_from('>i', d, p)[0]
                        p += 4
                    elif tag == 4:
                        fld[key] = round(struct.unpack_from('>d', d, p)[0], 6)
                        p += 8
                    else:
                        ok = False
                        break
                if ok and 'Numerator' in fld:
                    out.append(fld)
                    break
            except struct.error:
                continue
    return out

def main():
    root = sys.argv[1]
    rows = []
    seq_counter = collections.Counter()
    ext_media = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith('.cpr'):
                continue
            path = os.path.join(dirpath, fn)
            d = open(path, 'rb').read()
            tracks = [n for _o, n in find_terms(d, lambda n: bool(TRACK_RE.match(n)))]
            seq_counter[tuple(tracks)] += 1
            loc = {}
            for m in RE_CYCLE.finditer(d):
                loc.setdefault(m.group(1).decode(),
                               struct.unpack('>d', m.group(2))[0])
            sigs = sig_events(d)
            tempo_blob = [u32be(d, o + len(n) + 5 + 2)
                          for o, n in find_terms(
                              d, lambda x: x == 'MTempoTrackEvent')]
            paths = [n for _o, n in find_terms(
                d, lambda n: n.count('\\') >= 2 and ':' in n)]
            ext = sum(1 for p in paths
                      if 'Cubase Projects' not in p)
            ext_media += ext
            rows.append((fn[:-4], tracks, loc, sigs, tempo_blob,
                         len(paths), ext))
    tpl = None
    print('== 轨道序列分布 ==')
    for seq, n in seq_counter.most_common():
        print('%2d × %s' % (n, ' > '.join(seq)))
    print('\n== 逐文件 ==')
    for fn, tracks, loc, sigs, tb, np_, ext in rows:
        l, r = loc.get('Left'), loc.get('Right')
        dur = ('%.1f' % (r - l)) if (l is not None and r is not None
                                     and r > l) else '—'
        left = ('%.1f' % l) if l is not None else '—'
        sigtxt = ';'.join('%d/%d@b%d' % (s.get('Numerator', -1),
                                         s.get('Denominator', -1),
                                         s.get('Bar', -1)) for s in sigs[:4])
        print('%-28s 轨%d 时长=%-7s 左=%-7s 拍号[%s] 速度块%s 媒体%d(外%d)'
              % (fn[:28], len(tracks), dur, left, sigtxt, tb[:2], np_, ext))
    print('\n工程外媒体引用总数: %d' % ext_media)

if __name__ == '__main__':
    main()
