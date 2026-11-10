# -*- coding: utf-8 -*-
"""MIDI Remote 部署核验探针：验「CubeSetlist Remote」口的 CC 送达。

用法：先启动 Cubase（MIDI Remote 引擎应用级常驻，Hub 页即可），然后
  py tools/probe_mr_echo.py
探针向口发 CC 102/104 各 press(127)→release(0)（即 mr_ctrl 的
start/rtz 动作号），同时监听同一口 2 秒。判定分两层：
  口上听到自己发的字节=线缆层 OK（loopMIDI 同缆自听，与 Cubase 无关）；
  Cubase 界面里走带回零/起停=driver 受理（人工确认，脚本验不了——
  实测 driver 对外来 CC 不回发 feedback，活/死 Cubase 口上字节全同，
  见 mr_ctrl 模块头「存活探针」段；这正是回声自动判死不可行的证据）。
独立工具不进测试套件；纯标准库，收发走 midi_bridge 的 winmm 专线设施。
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
import midi_bridge as mb          # noqa: E402
import kbd_auto                   # noqa: E402  仅触发其钩子白名单注册，探针不用

HINT = "CubeSetlist Remote"       # 端口名子串（in/out 同口同名）
ECHO_WAIT = 2.0                   # 发完等回声的窗口
GAP = 0.05                        # 相邻消息间隔（press→release 与 CC 间同距）
CCS = (102, 104)                  # driver 动作号（start/rtz），见 mr_ctrl
_got = []                         # 回声消息 (status, cc, val)，回调线程只 append
_proc = None                      # 持 _Proc 引用防 GC（回调被回收=进程崩）


def _on_msg(h, msg, inst, p1, p2):
    """winmm 回调线程：零重活，只解析+append（不打印不做 IO）"""
    if msg != mb._MIM_DATA:
        return
    status, d1, d2 = p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF
    if status & 0xF0 == 0xB0:                   # 只记 CC（回声即 B0 短消息）
        _got.append((status, d1, d2))


def _pick(devs):
    hits = [i for i, n in devs if HINT in n]
    return hits[0] if hits else None


def main():
    global _proc
    ins = mb._in_devices()
    i_in = _pick(ins)
    if i_in is None:
        sys.exit("没找到含 %r 的输入口：请先在 loopMIDI 建口（现有输入口：%s）"
                 % (HINT, "、".join(n for _, n in ins) or "无"))
    i_out = _pick(mb._out_devices())
    if i_out is None:
        sys.exit("没找到含 %r 的输出口" % HINT)
    _proc = mb._Proc(_on_msg)                   # 先建包装再开入口（持引用）
    h_in, err = mb.open_in(i_in, _proc)
    if h_in is None:
        sys.exit(err)
    h_out, err = mb.open_out(i_out)
    if h_out is None:
        mb.close_in(h_in)
        sys.exit(err)
    print("监听 %r，发 CC %s press→release…"
          % (HINT, "/".join(str(c) for c in CCS)), flush=True)
    for cc in CCS:
        for val in (127, 0):                    # press→release，同生产口径
            err = mb.send_cc_h(h_out, 0, cc, val)
            if err:
                print("发送失败 cc=%d val=%d：%s" % (cc, val, err), flush=True)
            time.sleep(GAP)
    time.sleep(ECHO_WAIT)                       # 等回声落进 _got
    mb.close_in(h_in)
    mb.close_out(h_out)
    time.sleep(0.3)         # 收口 fire-and-forget：略等专线收尾再退进程
    if not _got:
        print("零回声：driver 不在线（或 Cubase 未运行/驱动未加载）", flush=True)
        return
    print("收到 %d 条回声：" % len(_got), flush=True)
    for status, cc, val in _got:
        print("  status=0x%02X cc=%d val=%d" % (status, cc, val), flush=True)


if __name__ == "__main__":
    main()
