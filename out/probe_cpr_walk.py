# -*- coding: utf-8 -*-
"""CPR 节点流语法探针（只读）第 3 步：变体矩阵 + 起点爆破 + 全文件走查。
语法假设：
  term  := [BE32 n][n 字节]（名字；n=strlen+1 含 \\0，容忍个别无 \\0）
  value := [u16 tag] + 载荷
     1: i32(4B BE)   4: f64(8B BE)   8: [BE32 len][len 字节]
     2: [u16 ver][BE32 cnt] × cnt (term+value)     —— 列表
     0: [BE32 ver] + 恰 1 个 (term+value)          —— 单子对象（变体 A）
        变体 B: [BE32 a][BE32 b] + 1 子
用法:
  py out/probe_cpr_walk.py <cpr>              # 起点爆破（前 2KB 内）+ 最佳走查
  py out/probe_cpr_walk.py <cpr> <起点十六进制>
"""
import struct, sys

def u16be(b, o): return struct.unpack_from('>H', b, o)[0]
def u32be(b, o): return struct.unpack_from('>I', b, o)[0]
def u64be(b, o): return struct.unpack_from('>Q', b, o)[0]

class Fail(Exception):
    pass

class Parser:
    def __init__(self, data, start, obj0_mode):
        self.d = data
        self.p = start
        self.obj0 = obj0_mode
        self.stats = {'terms': 0, 'i32': 0, 'f64': 0, 'str': 0,
                      'obj2': 0, 'obj0': 0, 'noname_terms': 0}
        self.maxdepth = 0

    def fail(self, why):
        raise Fail('%s @%06x' % (why, self.p))

    def term(self):
        d, p = self.d, self.p
        if p + 4 > len(d):
            self.fail('term 头越界')
        n = u32be(d, p)
        p += 4
        if n < 2 or n > 400 or p + n > len(d):
            self.fail('term 长度非法 n=%d' % n)
        raw = d[p:p + n]
        self.p = p + n
        self.stats['terms'] += 1
        if raw.endswith(b'\xef\xbb\xbf'):   # 术语可带 UTF-8 BOM 后缀
            raw = raw[:-3]
            self.stats['bom'] = self.stats.get('bom', 0) + 1
        try:
            name = raw.decode('utf-8')
        except UnicodeDecodeError:
            self.fail('term 非 UTF-8: %r' % raw[:24])
        if name.endswith('\x00'):
            return name[:-1], True
        return name, False

    def value(self, depth=0):
        self.maxdepth = max(self.maxdepth, depth)
        if depth > 80:
            self.fail('递归过深')
        d = self.d
        if self.p + 2 > len(d):
            self.fail('值标签越界')
        tag = u16be(d, self.p)
        self.p += 2
        if tag == 1:
            self.p += 4
            self.stats['i32'] += 1
            return
        if tag == 4:
            self.p += 8
            self.stats['f64'] += 1
            return
        if tag == 8:
            if self.p + 4 > len(d):
                self.fail('str 长度越界')
            slen = u32be(d, self.p)
            self.p += 4
            if slen > 300000 or self.p + slen > len(d):
                self.fail('str 长度非法 %d' % slen)
            self.p += slen
            self.stats['str'] += 1
            return
        if tag == 2:
            if self.p + 6 > len(d):
                self.fail('obj2 头越界')
            cnt = u32be(d, self.p + 2)
            self.p += 6
            if cnt > 300000:
                self.fail('obj2 cnt 非法 %d' % cnt)
            self.stats['obj2'] += 1
            for _ in range(cnt):
                self.term()
                self.value(depth + 1)
            return
        if tag == 0:
            if self.p + 8 > len(d):
                self.fail('obj0 头越界')
            ver = u32be(d, self.p)
            b2 = u32be(d, self.p + 4)
            self.p += 8
            self.stats['obj0'] += 1
            if self.obj0 == 'A' and not (ver in (0xFFFFFFFF,)
                                         and b2 in (0xFFFFFFFF, 0xFFFFFFFE)):
                # 宽容：其他组合也允许，只要子节点能走通
                pass
            self.term()
            self.value(depth + 1)
            return
        self.fail('未知标签 %d' % tag)

    def node(self, depth=0):
        name, _ = self.term()
        self.value(depth)
        return name

def walk(data, start, obj0_mode, verbose=False):
    ps = Parser(data, start, obj0_mode)
    n_nodes = 0
    last_names = []
    try:
        while ps.p < len(data):
            name = ps.node()
            n_nodes += 1
            last_names.append(name)
            if len(last_names) > 6:
                last_names.pop(0)
    except Fail as e:
        return False, str(e), n_nodes, last_names, ps
    return True, 'EOF', n_nodes, last_names, ps

def main():
    data = open(sys.argv[1], 'rb').read()
    s0 = 20 if data[:4] == b'RIF2' else 16
    if len(sys.argv) > 2:
        starts = [int(sys.argv[2], 0)]
    else:
        starts = range(s0, min(s0 + 4096, len(data)))
    best = None
    for mode in ('A', 'B'):
        for st in starts:
            ok, msg, n, last, ps = walk(data, st, mode)
            consumed = (ps.p - st) if ok else (ps.p - st)
            score = (1 if ok else 0, consumed)
            if best is None or score > best[0]:
                best = (score, st, mode, ok, msg, n, last, ps)
            if ok:
                break
        if best and best[3]:
            break
    (score, st, mode, ok, msg, n, last, ps) = best
    print('最佳: 起点=%s(mode %s) OK=%s 相对消费=%d/%d 节点=%d 终点=%06x'
          % (hex(st), mode, ok, score[1], len(data) - st, n, ps.p))
    print('原因: %s' % msg)
    print('统计: %s  最大深度=%d' % (ps.stats, ps.maxdepth))
    print('末尾节点: %s' % ' > '.join(last))

if __name__ == '__main__':
    main()
