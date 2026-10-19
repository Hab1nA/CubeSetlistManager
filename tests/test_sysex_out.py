# -*- coding: utf-8 -*-
"""OUT 口 SysEx 发送测试：假 winmm 验证 prepare→longmsg→unprepare 全序、
字节内容、MOM_DONE 轮询与失败收尾。零设备（运行时替身换 mb._winmm）。"""
import ctypes
import pathlib
import sys
import time
from ctypes import wintypes

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import midi_bridge as mb


class _FakeWinmm:
    """调用序记录替身：prepare 置 DONE 旗模拟驱动即刻回收；可注入
    longmsg 失败码验证失败路径同样 Unprepare。"""

    def __init__(self, longmsg_err=0):
        self.calls = []
        self.longs = []
        self.longmsg_err = longmsg_err

    def midiOutOpen(self, ph, idx, proc, inst, flags):
        ph._obj.value = 1
        self.calls.append("open")
        return 0

    def midiOutClose(self, h):
        self.calls.append("close")

    def midiOutPrepareHeader(self, h, ph, sz):
        hdr = getattr(ph, "_obj", ph)       # byref 包裹：_obj 拿到底层头
        hdr.dwFlags = mb.MHDR_DONE          # 模拟驱动即刻 DONE
        self.calls.append("prepare")
        return 0

    def midiOutLongMsg(self, h, ph, sz):
        if self.longmsg_err:
            self.calls.append("longmsg!")
            return self.longmsg_err
        hdr = getattr(ph, "_obj", ph)
        self.longs.append(ctypes.string_at(hdr.lpData, hdr.dwBufferLength))
        self.calls.append("longmsg")
        return 0

    def midiOutUnprepareHeader(self, h, ph, sz):
        self.calls.append("unprepare")
        return 0


def _patched(fake):
    saved = (mb._winmm,)
    mb._winmm = fake
    return lambda: setattr(mb, "_winmm", saved[0])


def test_send_sysex_h_happy_path_order_and_bytes():
    fake = _FakeWinmm()
    undo = _patched(fake)
    try:
        err = mb.send_sysex_h(wintypes.HANDLE(1), b"\xf0\x7f\x7f\xf7")
        assert err is None
        assert fake.calls == ["prepare", "longmsg", "unprepare"]
        assert fake.longs == [b"\xf0\x7f\x7f\xf7"]
    finally:
        undo()


def test_send_sysex_h_longmsg_failure_still_unprepares():
    fake = _FakeWinmm(longmsg_err=5)
    undo = _patched(fake)
    try:
        err = mb.send_sysex_h(wintypes.HANDLE(1), b"\xf0\xf7")
        assert err and "code 5" in err
        assert fake.calls == ["prepare", "longmsg!", "unprepare"]
    finally:
        undo()


def test_send_sysex_one_shot_opens_and_closes():
    fake = _FakeWinmm()
    undo = _patched(fake)
    try:
        err = mb.send_sysex(0, points_goto := b"\xf0\x7f\x7f\x06\x44\xf7")
        assert err is None
        assert fake.calls[:4] == ["open", "prepare", "longmsg", "unprepare"]
        assert fake.longs == [points_goto]
        for _ in range(100):                # close 走 post_io fire-and-forget
            if "close" in fake.calls:
                break
            time.sleep(0.01)
        assert fake.calls[-1] == "close"
    finally:
        undo()
