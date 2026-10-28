# -*- coding: utf-8 -*-
"""合并 v3: Name 锚点分段同名轨拼接。
轨道区内按模板轨名 Name 术语切段 [anchor_i..anchor_{i+1});
B 同名段 pos 重写后插到 A 同名段末尾(=A 下一锚点前), B 独有段追加流尾。
速度: CLI --spb=N 依次指定 A,B,C.. 的秒/拍。
用法: py out/cpr_merge3.py [--spb=N]... <A.cpr> <B.cpr> [C.cpr ...] <out.cpr>
"""
import re, struct, sys, collections
from pathlib import Path

U32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
F64 = lambda d, o: struct.unpack_from('>d', d, o)[0]
GAP = 10.0
RE_MIXER = re.compile(rb'\x00\x00\x00\x0cInputFilter\x00\x00\x02')
RE_TEMPO = re.compile(rb'MTempoTrackEvent\x00')
RE_NAME = re.compile(rb'Name\x00\x00\x08\x00\x00\x00[\x00-\x20]')
TRACKNAMES = {'cue', 'click', 'original', 'lead vocal', 'back vocal',
              'guitar', 'bass', 'drum', 'piano', 'other', 'vo effect 1',
              'vo effect 2', 'automation', 'visual', 'keyboard', 'score',
              'program', 'program record', 'track'}

def tempo_spb(d):
    m = RE_TEMPO.search(d)
    if not m:
        return 0.5
    o = m.end() + 1
    for k in range(o, min(o + 64, len(d) - 4)):
        v = struct.unpack_from('>f', d, k)[0]
        if 0.2 <= v <= 2.0:
            return v
    return 0.5

def regions(d):
    marks = []
    for m in re.finditer(rb'M[A-Za-z]{2,24}Track(?:Event)?\x00', d):
        o = m.start()
        nl = U32(d, o - 4)
        if nl in (len(m.group()), len(m.group()) + 3):
            marks.append(o - 4)
    mm = RE_MIXER.search(d, marks[-1])
    return marks[0], (mm.start() if mm else len(d))

def track_segments(d, lo, hi):
    """模板轨名锚点切段: [(名, 段起, 段止)] 保序。
    锚点=[u32BE n][名\\0(\\xef\\xbb\\bf)?] 且名字恰为模板轨名
    (兼容 Name+tag8 与裸术语两种存储)。"""
    anchors = []
    for nm in TRACKNAMES:
        nb = nm.encode('utf-8')
        for m in re.finditer(re.escape(nb) + b'\x00(?:\xef\xbb\xbf)?', d[lo:hi]):
            a = lo + m.start()
            if a < 4:
                continue
            n = U32(d, a - 4)
            if n in (len(m.group()), len(m.group()) + 3):
                anchors.append((a - 4, nm))
    anchors.sort()
    # 同名相邻去重(同一轨被多次引用, 只保留段长>200 的)
    segs = []
    for i, (off, nm) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else hi
        if segs and segs[-1][0] == nm and off - segs[-1][1] < 200:
            continue
        segs.append((nm, off, end))
    return segs

def patch_pos(buf, f_map):
    n = 0
    for k in range(0, len(buf) - 24):
        pos = F64(buf, k)
        if not (0 <= pos <= 700000):
            continue
        ln = F64(buf, k + 8)
        if not (300 <= ln <= 60000000) or ln != float(int(ln)):
            continue
        if F64(buf, k + 16) != 0.0:
            continue
        struct.pack_into('>d', buf, k, f_map(pos))
        n += 1
    return n

def seg_end_sec(d, s, e, spb):
    end = 0.0
    for k in range(s, min(e, len(d)) - 24):
        pos = F64(d, k)
        if not (0 <= pos <= 700000):
            continue
        ln = F64(d, k + 8)
        if not (300 <= ln <= 60000000) or ln != float(int(ln)):
            continue
        if F64(d, k + 16) != 0.0:
            continue
        end = max(end, pos * spb / 480.0 + ln / 48000.0)
    return end

def main():
    args = sys.argv[1:]
    spb_ov = {}
    while args and args[0].startswith('--spb'):
        spb_ov[len(spb_ov)] = float(args[0].split('=')[1])
        args = args[1:]
    if len(args) < 3:
        raise SystemExit(__doc__)
    fa, fout = args[0], args[-1]
    srcs = args[1:-1]
    da = Path(fa).read_bytes()
    sa, ea = regions(da)
    spb_a = spb_ov.get(0, tempo_spb(da))
    aseg = track_segments(da, sa, ea)
    print('A 轨道区 %06x..%06x spb=%.4f(%.2fBPM) 段 %d: %s'
          % (sa, ea, spb_a, 60 / spb_a, len(aseg),
             ' '.join('%s@%06x' % (nm, off) for nm, off, _e in aseg)))
    # A 末段终点秒
    a_end = max((seg_end_sec(da, s, e, spb_a) for _n, s, e in aseg),
                default=0.0)
    carry = a_end * 480.0 / spb_a
    print('A 内容终点 %.2fs (tick %.0f)' % (a_end, carry))
    out = bytearray(da[:ea])
    for fs in srcs:
        si = srcs.index(fs) + 1
        ds = Path(fs).read_bytes()
        ss, es = regions(ds)
        spb_b = spb_ov.get(si, tempo_spb(ds))
        bseg = track_segments(ds, ss, es)
        print('%s 轨道区 %06x..%06x 段 %d spb=%.4f(%.2fBPM)'
              % (Path(fs).name, ss, es, len(bseg), spb_b, 60 / spb_b))
        # A 各段末(=下一段起)在 out 中的偏移 = e - sa
        ins_at = {nm: (e - sa) for nm, _s, e in aseg}
        b_by_name = {}
        for nm, s, e in bseg:
            b_by_name.setdefault(nm, []).append((s, e))
        merged_names, tail = [], []
        b_end = 0.0
        for nm, lst in b_by_name.items():
            chunks = []
            for s, e in lst:
                seg = bytearray(ds[s:e])
                patch_pos(seg, lambda p, c=carry, sb=spb_b, sa_=spb_a:
                          c + p * sb / sa_)
                chunks.append(bytes(seg))
                b_end = max(b_end, seg_end_sec(ds, s, e, spb_b))
            if nm in ins_at:
                ins_at[nm] = (ins_at[nm], chunks)
                merged_names.append(nm)
            else:
                tail.extend(chunks)
        # 从后往前插
        for nm in sorted(merged_names, key=lambda n: -ins_at[n][0]):
            pos, chunks = ins_at[nm]
            blob = b''.join(chunks)
            out[pos:pos] = blob
            print('  拼 %s: %d 块 %dB @out%06x' % (nm, len(chunks),
                  len(blob), pos))
        if tail:
            out.extend(b''.join(tail))
            print('  B 独有段追加 %dB' % len(b''.join(tail)))
        carry = (carry * spb_a / 480.0 + GAP + b_end) * 480.0 / spb_a
        print('  B 终点 %.2fs 下一 carry tick=%.0f' % (b_end, carry))
    out.extend(da[ea:])
    base = Path('.').resolve()
    dst = Path(fout).resolve()
    assert base == dst or base in dst.parents, '输出须在工程目录内'
    dst.write_bytes(bytes(out))
    print('写出 %s (%dB)' % (fout, len(out)))

if __name__ == '__main__':
    main()
