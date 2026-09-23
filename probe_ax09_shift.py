# -*- coding: utf-8 -*-
"""AX-09 移调能力真机探针。背景：AX-09 说明书图表 RPN(100/101)与 Data Entry
(6/38)接收=O 但未写参数集；SysEx 接收=X。同引擎同代的 AX-Synth 官方 MIDI
Implementation 实锤接收 RPN 0,2 Channel Coarse Tuning（±48 半音）——本探针
在 AX-09 上逐项验证，听音高变化下结论：
  1 基准音（记住它）          2 RPN 0,2 Coarse +3 半音   ← 决定性
  3 RPN 0,1 Fine +100 音分    4 RPN 0,0 弯音范围=12 + 弯音满舵（升八度 hack）
  5 全复位
用法：py -u probe_ax09_shift.py [输出端口名子串，默认 AX-09]"""
import ctypes
import sys
import time
from ctypes import wintypes

import kbd_auto

CH = 0                                  # AX-09 默认接收通道 1


def rpn_msgs(msb, lsb, mm=None, ll=None):
    """RPN 设值序列（Bn 65/100 选参、Bn 6/38 送值、RPN null 收尾）。"""
    out = [(0xB0 | CH, 101, msb), (0xB0 | CH, 100, lsb)]
    if mm is not None:
        out.append((0xB0 | CH, 6, mm))
    if ll is not None:
        out.append((0xB0 | CH, 38, ll))
    out += [(0xB0 | CH, 101, 0x7F), (0xB0 | CH, 100, 0x7F)]
    return out


def _st(t):
    return t[0] | t[1] << 8 | t[2] << 16


def _selftest():
    m = rpn_msgs(0, 2, 0x43)
    assert _st(m[0]) == 0x65B0 and _st(m[1]) == 0x264B0 and _st(m[2]) == 0x4306B0
    assert _st(m[3]) == 0x7F65B0 and _st(m[4]) == 0x7F64B0
    assert len(rpn_msgs(0, 0)) == 4                     # 只选参不送值
    print("selftest OK")


def flush(h, msgs, gap=0.012):
    for t in msgs:
        kbd_auto._winmm.midiOutShortMsg(h, _st(t))
        time.sleep(gap)


def play(h, note=60, sec=1.5, vel=100):
    kbd_auto._winmm.midiOutShortMsg(h, _st((0x90 | CH, note, vel)))
    time.sleep(sec)
    kbd_auto._winmm.midiOutShortMsg(h, _st((0x80 | CH, note, 0)))
    time.sleep(0.3)


def step(title, fn):
    print("\n=== %s" % title)
    input("回车播放测试音…")
    fn()


def main():
    _selftest()
    hint = sys.argv[1] if len(sys.argv) > 1 else "AX-09"
    hits = [(i, n) for i, n in kbd_auto.mb._out_devices() if hint in n]
    assert hits, "未找到含「%s」的 MIDI 输出端口" % hint
    print("输出端口：%s" % hits[0][1])
    h = wintypes.HANDLE()
    r = kbd_auto._winmm.midiOutOpen(ctypes.byref(h), hits[0][0],
                                    kbd_auto.mb._Proc(), 0, 0)
    assert not r, "midiOutOpen 失败（code %d）" % r
    try:
        step("① 基准音（记住这个音高）", lambda: play(h))
        step("② RPN Coarse +3 半音：升了 3 个半音=✔ 可用，没变=✘ 不支持",
             lambda: (flush(h, rpn_msgs(0, 2, 0x43)), play(h),
                      flush(h, rpn_msgs(0, 2, 0x40))))
        step("③ RPN Fine +100 音分：升 1 个半音=✔（上限 ±100 音分，做不了八度）",
             lambda: (flush(h, rpn_msgs(0, 1, 0x60, 0x00)), play(h),
                      flush(h, rpn_msgs(0, 1, 0x40, 0x00))))
        step("④ 弯音范围 12 + 满舵：升 1 个八度=✔（须持续钉住弯音，松手回原调）",
             lambda: (flush(h, rpn_msgs(0, 0, 0x0C)),
                      flush(h, [(0xE0 | CH, 0x00, 0x7F)]), play(h),
                      flush(h, [(0xE0 | CH, 0x00, 0x40)]),
                      flush(h, rpn_msgs(0, 0, 0x02))))
        print("\n结束。②可用=半音/八度都能走 RPN Coarse（同 JUNO 方案）；"
              "②✘③✔=只能 ±1 半音；都✘=只能软件移调或弯音 hack。")
    finally:
        kbd_auto._winmm.midiOutClose(h)


if __name__ == "__main__":
    main()
