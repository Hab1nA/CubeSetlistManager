# -*- coding: utf-8 -*-
"""attack2_switch_lambda.py —— 攻击面一：/cmd switch 主线程解析 lambda 的防线检验
1) lambda 返回值在排空循环的下场（真实 App._run_call）
2) coalesce 是否会吞/动 lambda（真实 stallguard.coalesce）
3) 「k in pl_keys」与「pl_keys.index(k)」两读之间被后台线程改表的反例推演
   （_load_songs 在后台线程重建 pl_keys：setlist_gui.py:530 startup 线程、2045 重扫线程）
4) _drain_calls 的 for 循环里不泵 Tk 事件（真实源码文本检验）
"""
import inspect
import re
import sys
import types

sys.path.insert(0, ".")
import stallguard
import setlist_gui

# ---------- 1) 真实 _run_call：返回值丢弃 + BaseException 兜底 ----------
class FakeLog:
    def __init__(self): self.lines = []
    def insert(self, *_a, **_k): self.lines.append(_a[-1])
    def itemconfigure(self, *_a, **_k): pass
    def see(self, *_a, **_k): pass
    @property
    def size(self): return lambda: len(self.lines)

fake = types.SimpleNamespace(_hb_tag="", log=FakeLog())
print("== 1) 真实 App._run_call 对 lambda 返回值/异常的处理 ==")
setlist_gui.App._run_call(fake, lambda: False)          # key 已被移除：lambda 返回 False
print("  返回 False → 无日志无异常，_hb_tag=%r（返回值被丢弃）" % fake._hb_tag)
setlist_gui.App._run_call(fake, lambda: (_ for _ in ()).throw(ValueError("'x' is not in list")))
print("  抛 ValueError → 被捕获记日志：", fake.log.lines[-1][:60], "…（不外泄）")

# ---------- 2) 真实 coalesce：lambda 不在幂等名单，永不吞 ----------
print("== 2) 真实 stallguard.coalesce ==")
calls = []
lam = lambda k="K": calls.append(k)
batch = [lam, setlist_gui.App._refresh, setlist_gui.App._refresh, lam]
kept = stallguard.coalesce(batch, {"_refresh", "_persist_web_remote"})
print("  入批 [lambda, _refresh, _refresh, lambda] → 出批",
      [getattr(f, "__name__", f) for f in kept])
print("  lambda×2 全保留（<lambda> 不在幂等名名单），_refresh 合流到末次 →",
      len(kept) == 3 and kept.count(lam) == 2)

# ---------- 3) 两读之间被改表：三种交错的结果 ----------
print("== 3) 「in 检查」与「index/执行」之间 pl_keys 被换的反例 ==")
src = open("web_remote.py", encoding="utf-8").read()
m = re.search(r"def _go\(k=key\):.*?return False", src, re.S)
lam_src = " ".join(m.group(0).split())
print("  现行 _go 闭包：", lam_src)
print("  index 带 try/except ValueError 兜底 =", "except ValueError" in lam_src)

switched = []
class Flip:
    """模拟后台 _load_songs 线程在第 n 次属性读时把表换成重建后的新表。"""
    def __init__(self, tables): self.tables, self.i = tables, 0
    def next(self):
        t = self.tables[min(self.i, len(self.tables) - 1)]; self.i += 1
        return t

def mk_app(flip, cur_holder):
    class A:                       # 需要真属性协议：pl_keys 按 Flip 逐次换表
        pass
    A.pl_keys = property(lambda self: flip.next())
    a = A()
    # 仿真实 _switch（setlist_gui.py:1580/1588）：入口再读一次 pl_keys[i]
    a._switch = lambda i, via: switched.append((i, a.pl_keys[i]))
    a.calls = types.SimpleNamespace(
        put=lambda fn: setlist_gui.App._run_call(
            types.SimpleNamespace(_hb_tag="", log=FakeLog()), fn))
    return a

# 3a) key 在 index 时已被过滤掉 → ValueError → 被 _go 兜底，不切歌
def _go(k):                       # 与产品现行 _go 同构（原文见上）
    try:
        app._switch(app.pl_keys.index(k), "手动")
        return True
    except ValueError:
        return False
app = mk_app(Flip([["A", "K"], ["A"]]), None)
app.calls.put(lambda k="K": _go(k))
print("  3a index 时表已无 K：切歌记录 =", switched, "（ValueError 被 _go 兜底）")

# 3b) index 读到 1 后、_switch 内部重读 pl_keys[1] 之前表被重建换序 → 理论残余
#     （_go 单读单用消掉了 in→index 窗；index→_switch 重读窗需主线程侧
#      对 pl 变更加锁才能闭合——已知未闭合的理论残余，见报告）
switched.clear()
app = mk_app(Flip([["A", "K"], ["A", "K"], ["K", "A"]]), None)
app.calls.put(lambda k="K": _go(k))
print("  3b index=1 与 _switch 重读之间表被换：实际切 =", switched,
      "（理论残余窗：后台重扫恰落两字节码间）")

# ---------- 4) _drain_calls 不泵 Tk 事件（源码文本检验） ----------
print("== 4) 真实 App._drain_calls 源码 ==")
code = inspect.getsource(setlist_gui.App._drain_calls)
pumps = [w for w in ("update(", "update_idletasks", "event_generate",
                     "after(", "mainloop") if w in code]
print("  for 批内逐个 _run_call(fn)；出现的事件泵调用：", pumps or "无",
      "→ 单个 after 回调内的同步循环，循环体内不会插入其它 Tk 回调")

print("\n结论：主线程批内两步原子成立；跨线程重建窗口 → ValueError 路径有兜底，"
      "\n      index→_switch 重读窗口存在错曲理论窗（罕见：仅后台重扫恰落其间）")
