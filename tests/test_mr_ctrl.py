# -*- coding: utf-8 -*-
"""MIDI Remote 直控测试：CC 打包字节/动作序列/冷却状态机（零设备，
假 winmm 替身，风格同 test_sysex_out）+ driver JS 与 Python 侧 CC-TABLE/
命令绑定交叉校验 + daw_ctrl Cubase 直控唯一路径与 S1 键盘隔离。"""
import pathlib
import re
import sys
import time
from ctypes import wintypes

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import daw_ctrl
import midi_bridge as mb
import mr_ctrl

_DRIVER = (pathlib.Path(__file__).resolve().parents[1] / "mr_driver"
           / "Local" / "CubeSetlistManager" / "CubeSetlistRemote"
           / "CubeSetlistManager_CubeSetlistRemote.js")


class _FakeWinmm:
    """MR 输出口替身：单输出设备名 name；记录 ShortMsg 打包字。
    short_err_at=第几条 ShortMsg（1 起）返回错误码（验证中途弃口）。"""

    def __init__(self, name="CubeSetlist Remote", short_err_at=0):
        self.name = name
        self.shorts = []
        self.short_err_at = short_err_at
        self.enum_calls = 0
        self.closed = 0

    def midiOutGetNumDevs(self):
        self.enum_calls += 1
        return 1

    def midiOutGetDevCapsA(self, i, caps, sz):
        getattr(caps, "_obj", caps).szPname = self.name.encode("mbcs")
        return 0

    def midiOutOpen(self, ph, idx, proc, inst, flags):
        ph._obj.value = 1
        return 0

    def midiOutClose(self, h):
        self.closed += 1

    def midiOutShortMsg(self, h, word):
        self.shorts.append(word)
        if self.short_err_at and len(self.shorts) == self.short_err_at:
            return 5
        return 0


def _reset(fake):
    """换假 winmm + 清 mr_ctrl 全局口状态。句柄是假的不经 close（close
    job 会打到已还原的真 winmm）；测试中途失败路径自会的异步 close 在
    undo 前由用例自行等到。"""
    saved = (mb._winmm,)
    mb._winmm = fake

    def _clear():
        mr_ctrl.set_alive_probe(None)   # 防前序用例构造 DawController 残留探针
        with mr_ctrl._lock:
            mr_ctrl._out = None
            mr_ctrl._next_try = 0.0
            mr_ctrl._port_hint = mr_ctrl.PORT_HINT_DEFAULT

    _clear()

    def undo():
        _clear()
        mb._winmm = saved[0]

    return undo


def _fast():
    """CC_GAP 归零：序列不发真实间隔。"""
    saved = (mr_ctrl.CC_GAP,)
    mr_ctrl.CC_GAP = 0.0
    return lambda: setattr(mr_ctrl, "CC_GAP", saved[0])


def test_send_cc_h_packs_status_cc_val():
    fake = _FakeWinmm()
    undo = _reset(fake)
    try:
        assert mb.send_cc_h(wintypes.HANDLE(1), 0, 102, 127) is None
        assert fake.shorts == [0xB0 | (102 << 8) | (127 << 16)]
        assert mb.send_cc_h(wintypes.HANDLE(1), 5, 30, 64) is None
        assert fake.shorts[-1] == 0xB5 | (30 << 8) | (64 << 16)
    finally:
        undo()


def test_send_cc_h_error_surfaced():
    fake = _FakeWinmm(short_err_at=1)
    undo = _reset(fake)
    try:
        err = mb.send_cc_h(wintypes.HANDLE(1), 0, 102, 127)
        assert err and "code 5" in err
    finally:
        undo()


def test_send_play_press_release_sequence():
    fake = _FakeWinmm()
    undo_w = _reset(fake)
    undo_g = _fast()
    try:
        assert mr_ctrl.available() is True
        assert mr_ctrl.send("play") is None
        trip = [(w & 0xFF, (w >> 8) & 0x7F, (w >> 16) & 0x7F)
                for w in fake.shorts]
        # 停→回零→播，每命令 press127+release0（命令在按下沿触发）
        assert trip == [(0xB0, 103, 127), (0xB0, 103, 0),
                        (0xB0, 104, 127), (0xB0, 104, 0),
                        (0xB0, 102, 127), (0xB0, 102, 0)]
    finally:
        undo_g()
        undo_w()


def test_port_missing_cooldown_then_reenum():
    fake = _FakeWinmm(name="loopMIDI Port")     # 名不含 hint=口缺失
    undo = _reset(fake)
    try:
        assert mr_ctrl.available() is False
        n = fake.enum_calls
        assert mr_ctrl.available() is False     # 冷却内不再枚举
        assert fake.enum_calls == n
        with mr_ctrl._lock:                     # 冷却到期=loopMIDI 迟到自愈
            mr_ctrl._next_try = 0.0
        fake.name = "CubeSetlist Remote"
        assert mr_ctrl.available() is True
        assert fake.enum_calls > n
    finally:
        undo()


def test_send_dead_daw_probe_blocks_send():
    """存活探针（Phase 2）拦截用例：探针 False=口在而 DAW 死——不发任何
    CC，报错+弃口+进冷却；DAW 恢复后冷却未到仍拦，不再假成功。"""
    fake = _FakeWinmm()
    undo_w = _reset(fake)
    undo_g = _fast()
    saved = mr_ctrl._alive_probe
    probe = [True]
    mr_ctrl.set_alive_probe(lambda: probe[0])
    try:
        assert mr_ctrl.send("stop") is None     # 先开好口（口在）
        n = len(fake.shorts)
        probe[0] = False                        # DAW 死（进程或界面不在）
        err = mr_ctrl.send("stop")
        assert err and "Cubase 不在线" in err
        assert len(fake.shorts) == n            # 一条 CC 都没发
        with mr_ctrl._lock:
            assert mr_ctrl._out is None         # 已弃口
        for _ in range(200):                    # 弃口 close 走专线异步
            if fake.closed:
                break
            time.sleep(0.01)
        assert fake.closed == 1
        probe[0] = True                         # DAW 恢复，冷却未到仍拦
        assert mr_ctrl.send("stop") == "输出口暂不可用，稍后自动重试"
        assert len(fake.shorts) == n            # 冷却期内零发送
    finally:
        mr_ctrl.set_alive_probe(saved)
        undo_g()
        undo_w()


def test_send_alive_probe_passes_send():
    """存活探针（Phase 2）放行用例：探针 True=DAW 在场，send 照常发 CC，
    口不弃不关（探针只拦「口在而 DAW 死」，健康路径与无探针时一致）。"""
    fake = _FakeWinmm()
    undo_w = _reset(fake)
    undo_g = _fast()
    saved = mr_ctrl._alive_probe
    mr_ctrl.set_alive_probe(lambda: True)
    try:
        assert mr_ctrl.send("stop") is None
        trip = [(w & 0xFF, (w >> 8) & 0x7F, (w >> 16) & 0x7F)
                for w in fake.shorts]
        assert trip == [(0xB0, 103, 127), (0xB0, 103, 0)]
        with mr_ctrl._lock:
            assert mr_ctrl._out is not None     # 健康路径不弃口
        assert fake.closed == 0
    finally:
        mr_ctrl.set_alive_probe(saved)
        undo_g()
        undo_w()


def test_send_failure_midway_drops_port():
    fake = _FakeWinmm(short_err_at=3)           # play 第 2 命令 press 失败
    undo_w = _reset(fake)
    undo_g = _fast()
    try:
        err = mr_ctrl.send("play")
        assert err and "code 5" in err
        with mr_ctrl._lock:
            assert mr_ctrl._out is None
        for _ in range(200):                    # 弃口 close 走专线异步
            if fake.closed:
                break
            time.sleep(0.01)
        assert fake.closed == 1
    finally:
        undo_g()
        undo_w()


def _ctl():
    logs = []
    return daw_ctrl.DawController(daw_ctrl.CUBASE, "x.exe",
                                  log=logs.append), logs


def _mr_ok(sent):
    saved = (mr_ctrl.send,)
    mr_ctrl.send = lambda a: sent.append(a) or None
    return lambda: setattr(mr_ctrl, "send", saved[0])


def _mr_down():
    saved = (mr_ctrl.send,)
    mr_ctrl.send = lambda a: "MR 口未就绪（测试注入）"
    return lambda: setattr(mr_ctrl, "send", saved[0])


def test_transport_cubase_mr_only():
    ctl, logs = _ctl()
    sent = []
    undo = _mr_ok(sent)
    try:
        # 完整迁移：无工程窗口也成功——Cubase 走带只有 MR 一条路
        assert ctl.transport("play") is True
        assert sent == ["play"]
        assert "走带 播放" in logs
    finally:
        undo()


def test_transport_mr_down_no_keyboard():
    ctl, logs = _ctl()
    undo = _mr_down()
    try:
        assert ctl.transport("play") is False
        assert any(x.startswith("走带控制：") for x in logs)
        assert not any("没有工程窗口" in x for x in logs)  # 键盘路径未进
    finally:
        undo()


def test_transport_studioone_keeps_keyboard():
    logs = []
    ctl = daw_ctrl.DawController(daw_ctrl.STUDIOONE, "x.exe", log=logs.append)
    sent = []
    undo = _mr_ok(sent)
    try:
        # S1 无 MR：本机无工程窗口→键盘路径失败，但绝不发 MR
        assert ctl.transport("play") is False
        assert sent == []
        assert any("没有工程窗口" in x for x in logs)
    finally:
        undo()


def test_panic_cubase_mr_only():
    ctl, logs = _ctl()
    sent = []
    undo = _mr_ok(sent)
    try:
        ctl.panic()
        assert sent == ["stop"]
        assert "全停：已发送" in logs
    finally:
        undo()


def test_panic_mr_down_no_sweep():
    ctl, logs = _ctl()
    undo = _mr_down()
    try:
        ctl.panic()
        assert any(x.startswith("全停：") for x in logs)
        assert not any("工程窗口发送停止" in x for x in logs)  # 逐窗扫描未进
    finally:
        undo()


def test_close_mr_down_fails_fast():
    ctl, _logs = _ctl()
    undo = _mr_down()
    try:
        try:
            ctl._close(0)
        except daw_ctrl.SwitchError as e:
            assert "关闭未送达" in str(e)
        else:
            raise AssertionError("未抛 SwitchError")
    finally:
        undo()


def test_close_mr_sent_then_window_poll():
    ctl, _logs = _ctl()
    sent = []
    undo = _mr_ok(sent)
    saved_t, saved_w = (daw_ctrl.CLOSE_TIMEOUT,), (daw_ctrl._win_alive,)
    daw_ctrl.CLOSE_TIMEOUT = 0.3
    daw_ctrl._win_alive = lambda h: True   # 假窗永不死：只验发送+轮询超时
    try:
        try:
            ctl._close(1)
        except daw_ctrl.SwitchError as e:
            assert "关闭超时" in str(e)
        else:
            raise AssertionError("未抛 SwitchError")
        assert sent == ["close"]
    finally:
        daw_ctrl.CLOSE_TIMEOUT = saved_t[0]
        daw_ctrl._win_alive = saved_w[0]
        undo()


def test_driver_js_cc_table_and_bindings_match_python():
    src = _DRIVER.read_text(encoding="utf-8")
    table = dict(kv.split("=") for kv in
                 re.search(r"CC-TABLE:\s*(.+)", src).group(1).split())
    expect = {"start": mr_ctrl.CC_START, "stop": mr_ctrl.CC_STOP,
              "rtz": mr_ctrl.CC_RTZ, "save": mr_ctrl.CC_SAVE,
              "close": mr_ctrl.CC_CLOSE}
    assert {k: int(v) for k, v in table.items()} == expect
    binds = re.findall(
        r"makeCommandBinding\(\w+\.mSurfaceValue,\s*'([^']+)',\s*'([^']+)'\)",
        src)
    assert binds == [("Transport", "Start"), ("Transport", "Stop"),
                     ("Transport", "Return to Zero"),
                     ("File", "Save"), ("File", "Close")]


def test_seq_covers_all_actions_with_semantics():
    assert set(mr_ctrl.SEQ) == set(daw_ctrl.ACTION_NAMES) | {"close"}
    assert mr_ctrl.SEQ["play"][0] == mr_ctrl.CC_STOP      # 先停防续播
    assert mr_ctrl.SEQ["resume"] == (mr_ctrl.CC_START,)   # 原地续播
    assert mr_ctrl.SEQ["rewind"] == (mr_ctrl.CC_STOP, mr_ctrl.CC_RTZ)
