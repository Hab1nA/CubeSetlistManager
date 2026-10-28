# -*- coding: utf-8 -*-
"""合并 v2: 轨道区整体搬运 + tick 域映射(按各工程主导速度换算)。
布局: [头TOC][轨道区(首 M*Track 起)][调音台 strip 区(首 InputFilter 起)][尾]。
B 的轨道区字节插入 A 的轨道区尾; 区内 [f64 pos][f64 len整][f64 0] 三连的
pos 按 carry + pos*spb_B/spb_A 重写(音频事件秒域正确; MIDI 内部时值随 A 速)。
用法: py out/cpr_merge2.py <A.cpr> <B.cpr> [C.cpr ...] <out.cpr>
"""
import re, struct, sys, collections
from pathlib import Path

U32 = lambda d, o: struct.unpack_from('>I', d, o)[0]
F64 = lambda d, o: struct.unpack_from('>d', d, o)[0]
GAP = 10.0

RE_TRACK = re.compile(rb'M[A-Za-z]{2,24}Track(?:Event)?\x00')
RE_MIXER = re.compile(rb'\x00\x00\x00\x0cInputFilter\x00\x00\x02')

def tempo_spb(d):
    """MTempoTrackEvent 值区前 4KB 内 f32(0.2..2.0) 众数=主导速度。"""
    m = re.search(rb'MTempoTrackEvent\x00', d)
    if not m:
        return 0.5
    o = m.end() + 1                      # 值区起点(tag 00,03 之后)
    blob = d[o:o + 4096]
    cnt = collections.Counter()
    for k in range(0, len(blob) - 4):
        v = struct.unpack_from('>f', blob, k)[0]
        if 0.2 <= v <= 2.0:
            cnt[round(v, 6)] += 1
    return cnt.most_common(1)[0][0] if cnt else 0.5

def regions(d):
    marks = []
    for m in RE_TRACK.finditer(d):
        o = m.start()
        nl = U32(d, o - 4)
        if nl in (len(m.group()), len(m.group()) + 3):
            marks.append(o - 4)
    if not marks:
        raise SystemExit('无轨道标记')
    start = marks[0]
    mm = RE_MIXER.search(d, marks[-1])
    end = mm.start() if mm else len(d)
    return start, end

def iter_triplets(d, lo, hi, len_max=60000000):
    """[lo,hi) 内 [f64 pos][f64 len 整][f64 0] 三连位置。"""
    for k in range(lo, min(hi, len(d)) - 24):
        pos = F64(d, k)
        if not (0 <= pos <= 700000):
            continue
        ln = F64(d, k + 8)
        if not (300 <= ln <= len_max) or ln != float(int(ln)):
            continue
        if F64(d, k + 16) != 0.0:
            continue
        yield k, pos, ln

def content_end_sec(d, lo, hi, spb):
    end = 0.0
    for _k, pos, ln in iter_triplets(d, lo, hi):
        end = max(end, (pos + ln / 48000.0 * 480.0 / spb) * spb / 480.0)
    return end

def absolutize_media(seg, audio_dir):
    """区内 [u32BE n]["Audio\\...\\0"] 相对媒体路径 → 绝对路径(改写长度前缀)。"""
    pre = audio_dir.rstrip('\\') + '\\'
    out = bytearray(seg)
    n_fix = 0
    for m in re.finditer(rb'Audio\\', bytes(out)):
        a = m.start()
        if a < 4:
            continue
        n = U32(out, a - 4)
        if not (6 <= n <= 260):
            continue
        end_nul = bytes(out).find(b'\x00', a)
        if end_nul < 0 or end_nul + 1 - a != n:
            continue
        rel = bytes(out[a:a + n - 1]).decode('utf-8', 'replace')
        if not rel.startswith('Audio\\'):
            continue
        new = pre.encode('utf-8') + bytes(out[a:a + n - 1])
        out[a - 4:a] = struct.pack('>I', len(new))
        out[a:a] = new
        n_fix += 1
    return bytes(out), n_fix

def main():
    args = sys.argv[1:]
    spb_ov = {}
    while args and args[0].startswith('--spb'):
        _, v = args[0].split('=')
        spb_ov[len(spb_ov)] = float(v)
        args = args[1:]
    if len(args) < 3:
        raise SystemExit(__doc__)
    fa, fout = args[0], args[-1]
    srcs = args[1:-1]
    da = Path(fa).read_bytes()
    sa, ea = regions(da)
    spb_a = spb_ov.get(0, tempo_spb(da))
    a_end = content_end_sec(da, sa, ea, spb_a)
    print('A 轨道区 %06x..%06x  spb=%.4f(%.2fBPM)  内容终点 %.2fs (tick %.0f)'
          % (sa, ea, spb_a, 60 / spb_a, a_end, a_end * 480 / spb_a))
    carry = a_end * 480.0 / spb_a          # tick 域
    out = bytearray(da)
    src_i = 0
    for fs in srcs:
        src_i += 1
        ds = Path(fs).read_bytes()
        ss, es = regions(ds)
        spb_b = spb_ov.get(src_i, tempo_spb(ds))
        seg = bytearray(ds[ss:es])
        n = 0
        for k, pos, ln in iter_triplets(ds, ss, es):
            struct.pack_into('>d', seg, k - ss, carry + pos * spb_b / spb_a)
            n += 1
        seg = bytearray(seg)
        audio_dir = Path(fs).resolve().parent / 'Audio'
        seg, nfix = absolutize_media(bytes(seg), str(audio_dir))
        out[ea:ea] = seg
        print('%s 轨道区 %06x..%06x (%dB)  spb=%.4f(%.2fBPM)  pos修正 %d  路径绝对化 %d → 插于 %06x'
              % (Path(fs).name, ss, es, len(seg), spb_b, 60 / spb_b, n, nfix, ea))
        ea += len(seg)
        b_end = content_end_sec(ds, ss, es, spb_b)
        carry = (carry * spb_a / 480.0 + GAP + b_end) * 480.0 / spb_a
        print('  B 终点 %.2fs  下一 carry tick=%.0f' % (b_end, carry))
    base = Path('.').resolve()
    dst = Path(fout).resolve()
    assert base == dst or base in dst.parents, '输出须在工程目录内'
    dst.write_bytes(bytes(out))
    print('写出 %s (%dB)' % (fout, len(out)))

if __name__ == '__main__':
    main()
