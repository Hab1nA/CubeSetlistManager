# -*- coding: utf-8 -*-
"""CPR 节点流语法探针（只读）第 4 步：严格术语校验 + 全起点诊断扫描。
对 [s0, s0+0x1200) 每个字节起点试走（失败即停），按相对消费量排名，
输出 Top 与失败上下文，用于定位真正的文体起点与未覆盖语法。
用法: py out/probe_cpr_scan.py <cpr> [扫描终点=0x1200]
"""
import struct, sys

def u16be(b, o): return struct.unpack_from('>H', b, o)[0]
def u32be(b, o): return struct.unpack_from('>I', b, o)[0]

class Fail(Exception):
    pass

class Parser:
    def __init__(self, data, start):
        self.d = data
        self.p = start
        self.stats = {'terms': 0, 'i32': 0, 'f64': 0, 'str': 0,
                      'obj2': 0, 'obj0': 0, 'bom': 0}

    def fail(self, why):
        raise Fail('%s @%06x' % (why, self.p))

    def term(self):
        d, p = self.d, self.p
        if p + 4 > len(d):
            self.fail('term 头越界')
        n = u32be(d, p)
        p += 4
        if n < 2 or n > 260 or p + n > len(d):
            self.fail('term 长度非法 n=%d' % n)
        raw = d[p:p + n]
        if raw.endswith(b'\xef\xbb\xbf'):
            raw = raw[:-3]
            self.stats['bom'] += 1
        if not raw.endswith(b'\x00'):
            self.fail('term 不含结尾 \\0: %r' % raw[:20])
        body = raw[:-1]
        if b'\x00' in body:
            self.fail('term 含内部 \\0: %r' % raw[:20])
        try:
            name = body.decode('utf-8')
        except UnicodeDecodeError:
            self.fail('term 非 UTF-8: %r' % raw[:20])
        if any(c < ' ' for c in name):
            self.fail('term 含控制字符: %r' % raw[:20])
        self.p = p + n
        self.stats['terms'] += 1
        return name

    def value(self, depth=0):
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
            return 'i32'
        if tag == 4:
            self.p += 8
            self.stats['f64'] += 1
            return 'f64'
        if tag == 8:
            if self.p + 4 > len(d):
                self.fail('str 长度越界')
            slen = u32be(d, self.p)
            self.p += 4
            if slen > 300000 or self.p + slen > len(d):
                self.fail('str 长度非法 %d' % slen)
            self.p += slen
            self.stats['str'] += 1
            return 'str'
        if tag == 2:
            if self.p + 6 > len(d):
                self.fail('obj2 头越界')
            ver = u16be(d, self.p)
            cnt = u32be(d, self.p + 2)
            self.p += 6
            if cnt > 300000:
                self.fail('obj2 cnt 非法 %d' % cnt)
            self.stats['obj2'] += 1
            for _ in range(cnt):
                self.term()
                self.value(depth + 1)
            return 'obj2v%d' % ver
        if tag == 0:
            if self.p + 12 > len(d):
                self.fail('obj0 头越界')
            self.p += 8                  # [BE32 a][BE32 b]，值域不限
            cnt = u32be(d, self.p)
            if cnt > 100000:
                # 回退单子模型（MGroupEvent 型：8B 头 + 1 子）
                self.p -= 4
                self.stats['obj0'] += 1
                self.term()
                self.value(depth + 1)
                return 'obj0_1'
            self.p += 4
            self.stats['obj0'] += 1
            for _ in range(cnt):
                self.term()
                self.value(depth + 1)
            return 'obj0_n%d' % cnt
        if tag in (3, 6, 9, 10, 11, 12):     # 尺寸前缀块族（3 已实证）
            if self.p + 4 > len(d):
                self.fail('blob 头越界')
            blen = u32be(d, self.p)
            self.p += 4
            if blen > 400000 or self.p + blen > len(d):
                self.fail('blob 长 %d' % blen)
            self.p += blen
            self.stats['blob'] = self.stats.get('blob', 0) + 1
            return 'blob%d' % tag
        self.fail('未知标签 %d' % tag)

def walk(data, start):
    ps = Parser(data, start)
    names = []
    try:
        while ps.p < len(data):
            if len(names) < 8:
                names.append(ps.term())
                ps.value()
            else:
                ps.term()
                ps.value()
    except Fail as e:
        return False, str(e), names, ps
    return True, 'EOF', names, ps

def main():
    data = open(sys.argv[1], 'rb').read()
    s0 = 20 if data[:4] == b'RIF2' else 16
    end = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x1200
    lo = int(sys.argv[3], 0) if len(sys.argv) > 3 else s0
    results = []
    for st in range(lo, min(end, len(data))):
        ok, msg, names, ps = walk(data, st)
        results.append((ps.p - st, st, ok, msg, names, ps))
    results.sort(key=lambda r: -r[0])
    print('扫描起点数: %d  文件: %d 字节' % (len(results), len(data)))
    for consumed, st, ok, msg, names, ps in results[:8]:
        print('--- 起点=%06x 消费=%06x OK=%s 节点名=%s'
              % (st, consumed, ok, ','.join(names[:6])))
        print('    失败: %s' % msg)
        print('    统计: %s' % ps.stats)
        if not ok:
            fp = ps.p
            print('    现场: %s' % data[fp:fp + 40].hex())

if __name__ == '__main__':
    main()
