# -*- coding: utf-8 -*-
"""CPR 节点流语法探针（只读）第 2 步：头部转储 / 定向上下文 / 试走全文件。
用法:
  py out/probe_cpr_grammar.py head <cpr> [字节数=400]
  py out/probe_cpr_grammar.py ctx  <cpr> <名字,名字,...> [样本数=4]
  py out/probe_cpr_grammar.py walk <cpr> [起始偏移]
"""
import re, struct, sys

def u32be(b, o): return struct.unpack_from('>I', b, o)[0]
def u16be(b, o): return struct.unpack_from('>H', b, o)[0]

def stream_start(data):
    """容器头之后的节点流起点。"""
    if data[:4] == b'RIF2':
        return 20          # RIF2 + u64BE size + NUNDROOT
    if data[:4] == b'RIFX':
        return 16          # RIFF + u32BE size + NUNDROOT
    return 8

def head(path, n=400):
    data = open(path, 'rb').read()
    s = stream_start(data)
    print('文件:', path, ' 流起点:', s)
    for o in range(s, min(s + n, len(data)), 16):
        chunk = data[o:o + 16]
        hexs = ' '.join('%02x' % c for c in chunk)
        asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
        print('%06x  %-47s  %s' % (o, hexs, asc))

def find_names(data, want):
    """[BE32 strlen+1][name\\0] 记录定位。返回 [(name, off_after_null)]。"""
    out = []
    for m in re.finditer(rb'[\x20-\x7e]{2,80}\x00', data):
        o = m.start()
        if o < 4:
            continue
        if u32be(data, o - 4) == len(m.group()):
            name = m.group()[:-1].decode('latin1')
            if name in want:
                out.append((name, m.end()))
    return out

def ctx(path, names, k=4):
    data = open(path, 'rb').read()
    hits = find_names(data, set(names.split(',')))
    by = {}
    for name, after in hits:
        by.setdefault(name, []).append(after)
    for name, offs in by.items():
        print('== %s (%d 处)' % (name, len(offs)))
        for o in offs[:k]:
            frag = data[o:o + 32]
            print('  @%06x %s' % (o, frag.hex()))

TAGS = {0: 'obj0', 1: 'i32', 2: 'obj2', 4: 'f64', 8: 'str'}

def walk(path, start=None):
    data = open(path, 'rb').read()
    s = stream_start(data) if start is None else start
    pos = s
    depth = 0
    path_ctx = []

    def fail(why):
        frag = data[pos:pos + 48]
        print('FAIL @%06x depth=%d ctx=%s: %s' %
              (pos, depth, '/'.join(path_ctx[-4:]), why))
        print('  hex: %s' % frag.hex())
        print('  asc: %s' % ''.join(chr(c) if 32 <= c < 127 else '.'
                                    for c in frag))
        raise SystemExit(1)

    def rd_term():
        nonlocal pos
        if pos + 4 > len(data):
            fail('term 头越界')
        n = u32be(data, pos)
        pos += 4
        if n < 1 or n > 300 or pos + n > len(data):
            fail('term 长度非法 n=%d' % n)
        raw = data[pos:pos + n]
        pos += n
        if raw[-1:] != b'\x00':
            fail('term 未以 \\0 结尾: %r' % raw[:24])
        try:
            name = raw[:-1].decode('utf-8')
        except UnicodeDecodeError:
            fail('term 非 UTF-8: %r' % raw[:24])
        return name

    def rd_value():
        """读一个值；返回简述。type2/type0 递归消费子节点。"""
        nonlocal pos, depth
        if pos + 2 > len(data):
            fail('值标签越界')
        tag = u16be(data, pos)
        pos += 2
        if tag == 1:
            pos += 4
            return 'i32'
        if tag == 4:
            pos += 8
            return 'f64'
        if tag == 8:
            if pos + 4 > len(data):
                fail('str 长度越界')
            slen = u32be(data, pos)
            pos += 4
            if slen > 100000 or pos + slen > len(data):
                fail('str 长度非法 %d' % slen)
            pos += slen
            return 'str(%d)' % slen
        if tag == 2:
            if pos + 6 > len(data):
                fail('obj2 头越界')
            ver = u16be(data, pos)
            cnt = u32be(data, pos + 2)
            pos += 6
            if cnt > 100000:
                fail('obj2 数量非法 %d' % cnt)
            depth += 1
            for _ in range(cnt):
                name = rd_term()
                rd_value()
            depth -= 1
            return 'obj2(v%d,n%d)' % (ver, cnt)
        if tag == 0:
            if pos + 4 > len(data):
                fail('obj0 头越界')
            ver = u32be(data, pos)
            pos += 4
            # 假设 A：obj0 = [ver] + 恰好 1 个子 term+value
            depth += 1
            name = rd_term()
            rd_value()
            depth -= 1
            return 'obj0(v%d)+1(%s)' % (ver, name)
        fail('未知标签 %d' % tag)

    n_top = u32be(data, pos)
    pos += 4
    print('顶层条数假设: %d' % n_top)
    if n_top > 1000:
        print('顶层条数不合理——从头按无名记录试走')
        pos = s
    nodes = 0
    try:
        for i in range(n_top):
            name = rd_term()
            path_ctx.append(name)
            v = rd_value()
            path_ctx.pop()
            nodes += 1
    except SystemExit:
        raise
    print('OK: 走完 %d 个顶层节点, 终点 @%06x / 文件 %06x, 余 %d 字节'
          % (nodes, pos, len(data), len(data) - pos))

argv = sys.argv
if argv[1] == 'head':
    head(argv[2], int(argv[3]) if len(argv) > 3 else 400)
elif argv[1] == 'ctx':
    ctx(argv[2], argv[3], int(argv[4]) if len(argv) > 4 else 4)
elif argv[1] == 'walk':
    walk(argv[2], int(argv[3], 0) if len(argv) > 3 else None)
