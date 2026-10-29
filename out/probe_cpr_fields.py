# -*- coding: utf-8 -*-
"""CPR 语义提取探针（只读）第 5 步：锚点定向子树解析。
从已知类名术语（M*Track / MTempoTrackEvent / MGroupEvent 等）引导，
把每个锚点的子树按 (term+value)* 解析，收集 (字段名, 值) 平面清单。
整文件语法不完备无妨——锚点子树走通即可产出真实语义数据。
输出: 每文件的 轨道类序列 / 锚点子树字段 / 媒体路径 / 循环定位条。
用法: py out/probe_cpr_fields.py <cpr...>
"""
import re, struct, sys, collections

def u16be(b, o): return struct.unpack_from('>H', b, o)[0]
def u32be(b, o): return struct.unpack_from('>I', b, o)[0]
def f64be(b, o): return struct.unpack_from('>d', b, o)[0]

class Stop(Exception):
    pass

class Sub:
    """锚点子树解析器：value 标签 1/2/4/8 已证实；0=对象头8B+1子；
    未知标签即停（记录）。"""
    MAXN = 20000

    def __init__(self, d, p):
        self.d, self.p = d, p
        self.fields = []
        self.n = 0

    def term(self):
        d, p = self.d, self.p
        if p + 4 > len(d):
            raise Stop('越界')
        n = u32be(d, p)
        if n < 2 or n > 300 or p + 4 + n > len(d):
            raise Stop('term 长度 n=%d' % n)
        raw = d[p + 4:p + 4 + n]
        if raw.endswith(b'\xef\xbb\xbf'):
            raw = raw[:-3]
        if not raw.endswith(b'\x00') or b'\x00' in raw[:-1]:
            raise Stop('term 形态')
        try:
            name = raw[:-1].decode('utf-8')
        except UnicodeDecodeError:
            raise Stop('term 编码')
        if any(c < ' ' for c in name):
            raise Stop('term 控制符')
        self.p = p + 4 + n
        self.n += 1
        if self.n > self.MAXN:
            raise Stop('超限')
        return name

    def value(self, depth=0):
        if depth > 48:
            raise Stop('深')
        d = self.d
        if self.p + 2 > len(d):
            raise Stop('越界')
        tag = u16be(d, self.p)
        self.p += 2
        if tag == 1:
            v = struct.unpack_from('>i', d, self.p)[0]
            self.p += 4
            return ('i32', v)
        if tag == 4:
            v = f64be(d, self.p)
            self.p += 8
            return ('f64', round(v, 6))
        if tag == 8:
            if self.p + 4 > len(d):
                raise Stop('越界')
            slen = u32be(d, self.p)
            if slen > 100000 or self.p + 4 + slen > len(d):
                raise Stop('str 长')
            raw = d[self.p + 4:self.p + 4 + slen]
            self.p += 4 + slen
            try:
                return ('str', raw.rstrip(b'\x00\xef\xbb\xbf').decode('utf-8',
                       'replace'))
            except Exception:
                return ('str', repr(raw[:24]))
        if tag == 2:
            if self.p + 6 > len(d):
                raise Stop('越界')
            ver = u16be(d, self.p)
            cnt = u32be(d, self.p + 2)
            self.p += 6
            if cnt > 100000:
                raise Stop('obj2 cnt')
            out = []
            for _ in range(cnt):
                nm = self.term()
                v = self.value(depth + 1)
                out.append((nm, v))
                self.fields.append((nm, v))
            return ('obj2v%d' % ver, out)
        if tag == 0:
            if self.p + 8 > len(d):
                raise Stop('越界')
            self.p += 8
            nm = self.term()
            v = self.value(depth + 1)
            self.fields.append((nm, v))
            return ('obj0', [(nm, v)])
        raise Stop('未知标签 %d' % tag)

TERM = re.compile(rb'[\x20-\x7e]{2,80}\x00')

def find_terms(d, pred):
    """全部 [BE32 strlen+1][name\\0] 记录（可带 BOM 后缀），按偏移。"""
    out = []
    for m in TERM.finditer(d):
        o = m.start()
        if o < 4:
            continue
        n = u32be(d, o - 4)
        raw = m.group()
        if n == len(raw) or n == len(raw) + 3:   # 含 BOM 的记录 +3
            name = raw[:-1].decode('utf-8', 'ignore')
            if pred(name):
                out.append((o - 4, name, m.end()
                            + (3 if n == len(raw) + 3 else 0)))
    return out

def parse_anchor(d, off, name, after):
    """锚点术语之后的值；返回 (字段平面清单, 停止原因)。"""
    s = Sub(d, after)
    why = None
    try:
        v = s.value()
        s.fields.insert(0, (name, v))
    except Stop as e:
        why = str(e)
    return s.fields, why, s.p

TRACK_RE = re.compile(r'^M[A-Za-z]{2,26}Track$')
KEY_ANCHORS = {'MTempoTrackEvent', 'MSignatureTrackEvent', 'MGroupEvent',
               'MTimeSignatureEvent', 'MTempoTrack', 'MSignatureTrack'}

def brief(v, depth=0):
    k, x = v
    if k in ('i32', 'f64', 'str'):
        return '%s(%r)' % (k, x)
    if isinstance(x, list):
        if depth > 2:
            return '%s[…%d]' % (k, len(x))
        return '%s[%s]' % (k, ' '.join(
            '%s=%s' % (nm, brief(vv, depth + 1)) for nm, vv in x[:14]))
    return k

def main():
    for path in sys.argv[1:]:
        d = open(path, 'rb').read()
        print('=' * 76)
        print('文件:', path.split('/')[-1], '(%d 字节)' % len(d))
        tracks = find_terms(d, lambda n: bool(TRACK_RE.match(n)))
        print('轨道类术语序列:', collections.Counter(n for _, n, _ in tracks))
        print('  顺序:', [n for _, n, _ in tracks][:24])
        # 循环定位条（cpr_meta 已证语法）
        m = re.search(rb'Cycle (Left|Right)\x00\x00\x02\x00\x06\x00\x00\x00\x02'
                      rb'\x00\x00\x00\x05Time\x00\x00\x04(.{8})', d, re.S)
        if m:
            print('  定位条: L/R 存在，左=%s' % struct.unpack('>d', m.group(2))[0])
        else:
            print('  定位条: 未设')
        # 媒体路径
        paths = find_terms(d, lambda n: n.count('\\') >= 2 and ':' in n)
        print('  绝对路径引用数:', len(paths),
              '样例:', [n[-40:] for _, n, _ in paths[:3]])
        # 锚点子树
        for _, name, after in find_terms(d, lambda n: n in KEY_ANCHORS)[:6]:
            fields, why, endp = parse_anchor(d, _, name, after)
            print('  锚点 %s @%06x → %d 字段, 止因=%s'
                  % (name, _, len(fields), why))
            for nm, v in fields[:18]:
                print('      %-24s %s' % (nm, brief(v) if isinstance(v, tuple)
                                          else v))

if __name__ == '__main__':
    main()
