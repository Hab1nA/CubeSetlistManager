# -*- coding: utf-8 -*-
"""CPR 节点流语法普查（只读探针）：
1) 容器头/尾分析（RIF2/RIFX size 字段口径）
2) 字段名记录普查：[BE32 n][name\\0] 假设全文件验证（n=strlen+1? strlen?）
3) 类型标签推断：每个名字后随字节的签名直方图（按语义族分组）
用法: py out/probe_cpr_census.py <cpr路径...>
"""
import re, struct, sys, collections

PRINT = re.compile(rb'[\x20-\x7e]{2,80}\x00|(?:(?:[\xe0-\xef][\x80-\xbf]{2}){1,30})\x00')

def u32be(b, o): return struct.unpack_from('>I', b, o)[0]
def u16be(b, o): return struct.unpack_from('>H', b, o)[0]

def container(data):
    head = data[:4]
    info = {'magic': head.decode('latin1')}
    if head == b'RIF2':
        size = struct.unpack_from('<Q', data, 4)[0]
        info['size_u64le'] = size
    elif head == b'RIFX':
        size = struct.unpack_from('>I', data, 4)[0]
        info['size_u32be'] = size
    info['file'] = len(data)
    info['root'] = data[8:16].decode('latin1') if head in (b'RIF2', b'RIFX') else '?'
    info['tail64'] = data[-64:].hex()
    return info

def census(path):
    data = open(path, 'rb').read()
    print('=' * 72)
    print('文件:', path)
    print('容器:', container(data))
    hits = []          # (name, off_after_null)
    n_len1 = n_len0 = n_other = 0
    for m in PRINT.finditer(data):
        raw = m.group()[:-1]                     # 去 \0
        try:
            name = raw.decode('utf-8')
        except UnicodeDecodeError:
            n_other += 1
            continue
        o = m.start()
        if o < 4:
            continue
        v4 = u32be(data, o - 4)
        if v4 == len(raw) + 1:
            n_len1 += 1
            hits.append((name, m.end(), o - 4))
        elif v4 == len(raw):
            n_len0 += 1
        else:
            n_other += 1
    tot = n_len1 + n_len0 + n_other
    print('可打印串总数=%d  [BE32=strlen+1]=%d  [BE32=strlen]=%d  其他=%d'
          % (tot, n_len1, n_len0, n_other))
    # 每个名字的后随字节签名（前 6 字节）直方图
    sig = collections.defaultdict(collections.Counter)
    for name, after, _o in hits:
        sig[name][data[after:after + 6].hex()] += 1
    print('\n-- 高频名字的后随签名（前 50 个名字，每名最多 3 个签名）--')
    for name, c in sorted(sig.items(), key=lambda kv: -sum(kv[1].values()))[:50]:
        tot_n = sum(c.values())
        tops = ', '.join('%s×%d' % (s, k) for s, k in c.most_common(3))
        print('%-28s n=%-5d %s' % (name, tot_n, tops))

for p in sys.argv[1:]:
    census(p)
