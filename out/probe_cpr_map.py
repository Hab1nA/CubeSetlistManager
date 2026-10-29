# -*- coding: utf-8 -*-
"""CPR 结构映射探针（只读）第 7 步：模板样式核查。
对每个文件输出：
1) 类词汇术语宏布局（偏移+名，M*/C*/P*/G* 类与关键术语）
2) 每条 M*Track 锚点后的字段名序列与轨道名（Name 字段值）
3) 内容盘点：音频事件/媒体路径/速度块/拍号/标记类术语计数
用法: py out/probe_cpr_map.py <cpr...>
"""
import re, struct, sys, collections

TERM = re.compile(rb'[\x20-\x7e]{2,80}\x00')
CLS = re.compile(r'^(?:[MCPGU][A-Za-z]{3,40}|[A-Za-z]+Component|audioComponent'
                 r'|editController|JUCEPrivateData|ROOT|GDocument)$')
U32 = lambda b, o: struct.unpack_from('>I', b, o)[0]

def terms(d):
    """全部 [BE32 len][名\0] 记录（含 BOM 变体）：[(头偏移, 名, 值起点)]。"""
    out = []
    for m in TERM.finditer(d):
        o = m.start()
        if o < 4:
            continue
        n = U32(d, o - 4)
        if n in (len(m.group()), len(m.group()) + 3):
            name = m.group()[:-1].decode('utf-8', 'ignore')
            out.append((o - 4, name, m.end() + (3 if n == len(m.group()) + 3
                                                else 0)))
    return out

def main():
    for path in sys.argv[1:]:
        d = open(path, 'rb').read()
        ts = terms(d)
        print('=' * 78)
        print('文件: %s (%dB, 术语 %d)' % (path.split('/')[-1], len(d), len(ts)))
        cls = [(o, n, v) for o, n, v in ts if CLS.match(n)]
        print('-- 类术语布局 (%d) --' % len(cls))
        for o, n, _v in cls[:70]:
            print('  %06x %s' % (o, n))
        if len(cls) > 70:
            print('  … 后 %d 个类: %s' % (len(cls) - 70,
                  ' '.join(n for _o, n, _v in cls[70:110])))
        # 轨道锚点区段
        print('-- 轨道锚点区段 --')
        for o, n, v in cls:
            if not re.match(r'^M[A-Za-z]{2,26}Track$', n):
                continue
            follow = [x[1] for x in ts if o < x[0] < o + 400][:14]
            nm = ''
            for i, (o2, n2, v2) in enumerate(ts):
                if o2 <= o or o2 > o + 400:
                    continue
                if n2 == 'Name':
                    nm = d[v2:v2 + 60].split(b'\x00')[0].decode('utf-8',
                                                                'ignore')
                    break
            print('  %06x %-22s 名=%r 后续=%s' % (o, n, nm, ' '.join(follow)))
        # 内容盘点
        cnt = collections.Counter(n for _o, n, _v in ts)
        keys = ['MAudioEvent', 'MAudioPart', 'MMidiTrackEvent', 'MMidiPart',
                'MGroupEvent', 'MTrackEvent', 'MAudioTrackEvent',
                'MTempoTrackEvent', 'MSignatureTrackEvent',
                'MTimeSignatureEvent', 'MMarkerTrack', 'MHitPointEvent',
                'RuntimeID', 'CmIDLink', 'MListNode', 'MDataNode']
        print('-- 内容盘点 --')
        print('  ' + '  '.join('%s=%d' % (k, cnt.get(k, 0))
                               for k in keys if cnt.get(k)))
        paths = [n for _o, n, _v in ts if n.count('\\') >= 2 and ':' in n]
        print('  媒体路径 %d 条, 样例: %s' % (len(paths),
              [p.split('\\')[-1] for p in paths[:4]]))

if __name__ == '__main__':
    main()
