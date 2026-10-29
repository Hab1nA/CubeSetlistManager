# -*- coding: utf-8 -*-
"""CPR 模板-工程字节一致性核查（只读）第 8 步。
1) 64B 窗口采样：模板每 512B 取一窗 → 在各工程中精确查找命中率
2) 命中窗口中含 GUID/含 BOM 的比例（GUID 是否逐工程再生）
3) 反向：工程独有内容量估计
4) Name 值正确解析（Name→String→tag8 字符串）
用法: py out/probe_cpr_diff.py <模板> <工程...>
"""
import re, struct, sys

TERM = re.compile(rb'[\x20-\x7e]{2,80}\x00')
U16 = lambda b, o: struct.unpack_from('>H', b, o)[0]
U32 = lambda b, o: struct.unpack_from('>I', b, o)[0]

ALLOWED = (r'C:\Users\XKZ\Documents\Cubase Projects',
           r'C:\Users\XKZ\AppData\Roaming\Steinberg')

def ok_dir(p):
    """只读白名单：探针只允许读工程库与 Cubase 配置目录。"""
    return any(p.lower().startswith(a.lower()) for a in ALLOWED)

def find_name_values(d, limit=8):
    """Name 术语 → 正确解析其值（tag2 obj{String: tag8 str} 或直 tag8）。"""
    out = []
    for m in TERM.finditer(d):
        if m.group() != b'Name\x00':
            continue
        o = m.start()
        if o < 4 or U32(d, o - 4) != 5:
            continue
        p = m.end()
        try:
            tag = U16(d, p)
            if tag == 8:
                ln = U32(d, p + 2)
                out.append(d[p + 6:p + 6 + ln].rstrip(b'\x00\xef\xbb\xbf')
                           .decode('utf-8', 'ignore'))
            elif tag == 2:                      # obj2{...String...}
                q = p + 6
                for _ in range(4):
                    n2 = U32(d, q)
                    if not (2 <= n2 <= 64):
                        break
                    nm = d[q + 4:q + 4 + n2 - 1].decode('utf-8', 'ignore')
                    q += 4 + n2
                    t2 = U16(d, q)
                    q += 2
                    if t2 == 8:
                        ln = U32(d, q)
                        out.append(d[q + 4:q + 4 + ln]
                                   .rstrip(b'\x00\xef\xbb\xbf')
                                   .decode('utf-8', 'ignore'))
                        q += 4 + ln
                    elif t2 == 1:
                        q += 4
                    else:
                        break
        except struct.error:
            pass
        if len(out) >= limit:
            break
    return out

def main():
    assert len(sys.argv) >= 3, '用法: probe_cpr_diff.py <模板> <工程...>'
    assert all(ok_dir(p) for p in sys.argv[1:]), '目录外'
    tpl = None
    for path in sys.argv[1:]:
        d = open(path, 'rb').read()
        if tpl is None:
            tpl = d
            print('模板: %dB' % len(tpl))
            print('模板 Name 值样例:', find_name_values(tpl))
            wins = [(o, tpl[o:o + 64])
                    for o in range(0x180, len(tpl) - 64, 512)]
            print('采样窗口: %d（模板每 512B 一窗）' % len(wins))
            continue
        hits = 0
        guid_hits = bom_hits = 0
        buckets = {}
        for o, w in wins:
            i = d.find(w)
            if i >= 0:
                hits += 1
                if b'GUID' in w:
                    guid_hits += 1
                if b'\xef\xbb\xbf' in w:
                    bom_hits += 1
                b = '%dK-%dK' % (o // 262144, o // 262144 + 1)
                buckets[b] = buckets.get(b, 0) + 1
        print('%-24s 命中 %d/%d (%.0f%%)  含GUID窗命中 %d  含BOM窗命中 %d  区段 %s'
              % (path.split('/')[-1], hits, len(wins), 100.0 * hits / len(wins),
                 guid_hits, bom_hits,
                 ' '.join('%s:%d' % kv for kv in sorted(buckets.items()))))
        miss = 0
        total = 0
        for o in range(0x180, len(d) - 64, 4096):
            total += 1
            if tpl.find(d[o:o + 64]) < 0:
                miss += 1
        print('    工程每 4KB 采样中模板不含的窗口: %d/%d（≈独有内容占比）'
              % (miss, total))

if __name__ == '__main__':
    main()
