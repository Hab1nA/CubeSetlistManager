# -*- coding: utf-8 -*-
"""Cube Setlist Manager 自检：python test_bridge.py。全 assert，无需 OBS/loopMIDI/Cubase 在场。"""
import os
import pathlib
import struct
import tempfile
import time

import advance
import cpr_meta
import cubase_ctrl
import kbd_auto
import midi_bridge as mb
import obs_ctrl
import pedal
from obs_ctrl import ObsController, natural_key

mb.CLOCK_TIMEOUT = 0.08


class FakeCtl:
    def __init__(self):
        self.calls = []
        self.state = None
    def pause_media(self):
        self.calls.append("pause")
        return True
    def resume_media(self):
        self.calls.append("resume")
        return True
    def media_state(self):
        return self.state


def test_transport_sync():
    events = []
    ctl = FakeCtl()
    s = mb.TransportSync(ctl, on_event=events.append)

    s.set_state("playing")
    s.poll()
    assert ctl.calls == []                       # 未见过时钟脉冲不得介入
    s.on_clock()
    s.poll()
    assert ctl.calls == []                       # 脉冲正常不暂停
    time.sleep(0.15)
    s.poll()
    assert ctl.calls == ["pause"] and s.video_state == "paused"
    assert events == ["走带暂停 → 视频已暂停"]

    ctl.state = "OBS_MEDIA_STATE_PAUSED"
    s.on_clock()
    s.poll()                                     # 走带恢复 → 继续
    assert ctl.calls == ["pause", "resume"] and s.video_state == "playing"

    time.sleep(0.15)
    s.poll()                                     # 再断流 → 再暂停
    ctl.state = "OBS_MEDIA_STATE_ENDED"
    s.on_clock()
    s.poll()                                     # 已播完不得复活
    assert s.video_state == "stopped"
    assert ctl.calls.count("resume") == 1

    s.set_state("paused")
    ctl.state = "OBS_MEDIA_STATE_PAUSED"
    s.note_seen()                                # 磁吸音符起播：抑制恢复
    s.on_clock()
    s.poll()
    assert ctl.calls.count("resume") == 1
    time.sleep(0.25)                             # 窗口过后兜底恢复
    s.on_clock()
    s.poll()
    assert ctl.calls.count("resume") == 2 and s.video_state == "playing"


def test_numbered_match():
    """打桩 scan_videos（零文件 IO），只测匹配规则本身。"""
    fake = ["4 开场钢琴.mp4", "10 xxx.mp4", "1.mp4", "nomatch.mp4"]
    orig = obs_ctrl.scan_videos
    obs_ctrl.scan_videos = lambda root: list(fake)
    try:
        ctl = ObsController({"videoRoot": "VR", "mediaInput": "x"})
        name = lambda p: os.path.basename(p) if p else None
        assert name(ctl._abs_path("4.mp4")) == "4 开场钢琴.mp4"   # 编号+空格
        assert name(ctl._abs_path("1.mp4")) == "1.mp4"            # 纯编号优先
        assert name(ctl._abs_path("10.mp4")) == "10 xxx.mp4"      # 不误配编号1
        assert ctl._abs_path("99.mp4") is None
        assert name(ctl._abs_path("nomatch.mp4")) == "nomatch.mp4"
    finally:
        obs_ctrl.scan_videos = orig


def test_natural_sort():
    assert sorted(["10 a", "2 a", "1 a"], key=natural_key) == \
        ["1 a", "2 a", "10 a"]


def _cycle_rec(side, sec):
    """按真实 .cpr 记录布局构造定位条记录（字节模式来自全库实测）。"""
    return ("Cycle %s" % side).encode() + b"\x00" \
        + b"\x00\x02\x00\x06\x00\x00\x00\x02" \
        + struct.pack(">I", 5) + b"Time\x00" + b"\x00\x04" \
        + struct.pack(">d", sec)


def _fake_cpr(magic, left, right):
    return magic + b"\x00" * 8 + _cycle_rec("Left", left) \
        + _cycle_rec("Right", right)


def test_cpr_duration():
    for magic in (b"RIF2", b"RIFF"):
        with tempfile.NamedTemporaryFile(suffix=".cpr", delete=False) as f:
            f.write(_fake_cpr(magic, 10.0, 250.5))
            path = f.name
        try:
            assert cpr_meta.read_duration(path) == 240.5   # 右-左=时长
        finally:
            os.unlink(path)

    with tempfile.NamedTemporaryFile(suffix=".cpr", delete=False) as f:
        f.write(_fake_cpr(b"RIF2", 0.0, 0.0))              # 未设定位条
        path = f.name
    try:
        assert cpr_meta.read_duration(path) is None
    finally:
        os.unlink(path)

    with tempfile.NamedTemporaryFile(suffix=".cpr", delete=False) as f:
        f.write(b"JUNKJUNKJUNK")
        path = f.name
    try:
        assert cpr_meta.read_duration(path) is None        # 非法头
    finally:
        os.unlink(path)

    assert cpr_meta.fmt_mmss(213.7) == "3:33"
    assert cpr_meta.fmt_mmss(None) == "未知"
    assert cpr_meta.parse_mmss("3:33") == 213
    assert cpr_meta.parse_mmss("213") == 213.0
    assert cpr_meta.parse_mmss("abc") is None
    assert cpr_meta.parse_mmss("") is None


def test_advance_watch():
    now = [1000.0]
    fired, stops = [], []
    w = advance.AdvanceWatch(lambda: fired.append(1),
                             on_stop_transport=lambda: stops.append(1),
                             clock_timeout=0.25, t=lambda: now[0])
    w.set_armed(True)
    w.poll()
    assert fired == [] and stops == []          # 没有时钟不介入
    w.set_duration(100.0)
    for i in range(101):                        # 播 100 秒（逐秒脉冲）
        now[0] = 1000.0 + i
        w.on_clock()
    w.poll()                                    # 仍在播但已到时长
    assert len(stops) == 1 and not fired        # 程序发停止键
    w.poll()
    assert len(stops) == 1                      # 3 秒内不重试
    for i in range(1, 5):                       # 停止没生效，走带还在走
        now[0] = 1100.0 + i
        w.on_clock()
    w.poll()
    assert len(stops) == 2                      # 3 秒后重试停止
    for i in range(5, 11):
        now[0] = 1100.0 + i
        w.on_clock()
    now[0] = 1112.0                             # 停止生效：断流
    w.poll()
    assert len(fired) == 1                      # 活跃 100 ≥ 95 → 推进
    w.poll()
    assert len(fired) == 1                      # 只触发一次

    w.set_duration(50.0)                        # 换歌 → 重新累计
    now[0] = 2000.0
    w.on_clock()
    for i in range(1, 11):                      # 播 10 秒
        now[0] = 2000.0 + i
        w.on_clock()
    now[0] = 2015.0                             # 断流（不到时长就停）
    w.poll()
    assert len(fired) == 1                      # 活跃 10 < 47.5 → 不推进

    w.set_armed(False)                          # 取消勾选 → 彻底不推进
    now[0] = 2116.0
    w.on_clock()
    now[0] = 2200.0
    w.poll()
    assert len(fired) == 1


def test_project_title():
    # 标题版本名随工程保存版本变：15 存的 Cubase Pro，13.0.40 存的 Cubase Version …
    assert cubase_ctrl.project_name_from_title(
        "Cubase Pro 工程 - アイドル") == "アイドル"
    assert cubase_ctrl.project_name_from_title(
        "Cubase Version 13.0.40 工程 - TAIDADA") == "TAIDADA"
    assert cubase_ctrl.project_name_from_title("记事本") is None
    assert cubase_ctrl.project_name_from_title("") is None
    assert cubase_ctrl.project_windows.__doc__  # 冒烟：识别函数可用


def test_kbd_auto():
    # 端口匹配收紧：loopMIDI 端口全名，不得再宽匹配误开 Keyboard Automation
    assert mb.PORT_HINT == "loopMIDI Port"
    assert mb._pick([(0, "Keyboard Automation"), (1, "loopMIDI Port")])[1] \
        == "loopMIDI Port"

    # 模式推断（官方 Bank Map：85=Performance→PERFORM，87=Patch→PATCH，GM→GM1）
    # Sound Mode 官方地址表：2=GM2、3=GM1（Roland Clan 帖记反，2026-09-25 真机实锤）
    assert kbd_auto.mode_for_msb(85) == 1
    assert kbd_auto.mode_for_msb(87) == 0
    assert kbd_auto.mode_for_msb(121) == 3
    assert kbd_auto.mode_for_msb(None) is None

    # 模式 SysEx：F0 41 dev 00 00 3A 12 01 00 00 00 mode ck F7（校验和实测值）
    for mode, ck in ((0, 0x7F), (1, 0x7E), (2, 0x7D), (3, 0x7C), (4, 0x7B)):
        sx = kbd_auto.mode_sysex(mode)
        assert len(sx) == 14 and sx[0] == 0xF0 and sx[-1] == 0xF7
        assert sx[7:11] == bytes([0x01, 0x00, 0x00, 0x00])
        assert sx[11] == mode and sx[12] == ck

    # 序列路由：Performance 组走 perfCh(16→状态字节 BF/CF)，Patch 组走 patchCh(1→B0/C0)
    cfg = dict(patchCh=1, perfCh=16, deviceId=0x10)
    m85 = kbd_auto.switch_msgs({"msb": 85, "lsb": 64, "pc": 3}, cfg)
    assert m85[0][0] == "long" and m85[0][1][12] == 0x7E        # PERFORM SysEx
    assert m85[1][1] == (0xBF | 85 << 16)                       # CC0 在 16 通道
    assert m85[2][1] == (0xBF | 32 << 8 | 64 << 16)             # CC32
    assert m85[3][1] == 0xCF | 3 << 8                           # PC 在 16 通道
    m87 = kbd_auto.switch_msgs({"msb": 87, "lsb": 73, "pc": 0}, cfg)
    assert m87[0][1][12] == 0x7F                                # PATCH SysEx
    assert m87[3][1] == 0xC0                                    # PC 在 1 通道
    mpc = kbd_auto.switch_msgs({"pc": 7}, cfg)                  # 纯 PC：无 SysEx
    assert len(mpc) == 1 and mpc[0][1] == 0xC0 | 7 << 8

    # 捕获配对：BS 先到；PC 持续覆盖（持续录制语义），停止时保存最后一个
    cap = kbd_auto.SlotCapture()
    cap.feed(0xB0, 0, 85)
    cap.feed(0xB0, 32, 64)
    assert cap.slot is None
    cap.feed(0xC0, 40, 0)
    assert cap.slot == {"pc": 40, "msb": 85, "lsb": 64}
    cap.feed(0xC0, 41, 0)               # 类别内滚动：覆盖为最新
    assert cap.slot == {"pc": 41, "msb": 85, "lsb": 64}
    cap2 = kbd_auto.SlotCapture()
    cap2.feed(0x90, 60, 100)            # 音符不收
    cap2.feed(0xB1, 7, 100)             # 无关 CC 不收
    cap2.feed(0xC5, 5, 0)               # 任意通道的 PC 都定格
    assert cap2.slot == {"pc": 5}

    # 持久化 roundtrip（tempdir 充当工程文件夹）
    with tempfile.TemporaryDirectory() as d:
        cpr = os.path.join(d, "x.cpr")
        pathlib.Path(cpr).write_bytes(b"")
        assert kbd_auto.load_slots(cpr) == {}
        kbd_auto.save_slots(cpr, {60: {"msb": 85, "lsb": 64, "pc": 3},
                                  63: {"pc": 5}})
        s = kbd_auto.load_slots(cpr)
        assert s[60] == {"msb": 85, "lsb": 64, "pc": 3} and s[63] == {"pc": 5}
        assert "keyboard_automation.json" in os.listdir(d)

    # 展示
    assert kbd_auto.describe_slot({"msb": 85, "lsb": 64, "pc": 3}) == \
        "PERF PRST:004"
    assert kbd_auto.describe_slot({"msb": 93, "pc": 4}) == "EXP:0005"
    assert kbd_auto.describe_slot({"msb": 0, "pc": 0}) == "GM:0001"
    assert kbd_auto.describe_slot(None) == "未设置"
    assert kbd_auto.note_name(60) == "C3" and kbd_auto.note_name(69) == "A3"


def test_pedal():
    st = {}
    assert pedal.fire(st, 4, 127, 100.0)            # 首踩：0→127 上穿
    assert not pedal.fire(st, 4, 127, 100.05)       # 保持 127 不重复触发
    assert not pedal.fire(st, 4, 0, 100.1)          # 松开（下穿）不触发
    assert not pedal.fire(st, 4, 127, 100.12)       # 去抖窗内的再次上穿被挡
    assert not pedal.fire(st, 4, 0, 100.13)
    assert pedal.fire(st, 4, 127, 100.3)            # 去抖窗后可再次触发
    assert pedal.fire(st, 7, 127, 100.31)           # 其他 CC 独立计数

    assert pedal.load_binding({}) == ("", {})
    hint, binds = pedal.load_binding(
        {"pedal": {"deviceHint": "M-Vave", "bindings": {"play": 4, "bad": 9}}})
    assert hint == "M-Vave" and binds == {"play": 4}   # 非法动作名被滤掉

    hits = []
    pl = pedal.PedalListener(hits.append)
    pl.binds = {4: "next"}
    pl._msg(0x90, 60, 100)              # 音符不触发
    pl._msg(0xB0, 60, 127)              # 未绑定的 CC 不触发
    pl._msg(0xB0, 4, 127)               # 绑定 CC 上升沿 → 回调
    pl._msg(0xB0, 4, 127)               # 保持不重复
    assert hits == ["next"]


if __name__ == "__main__":
    test_transport_sync()
    test_numbered_match()
    test_natural_sort()
    test_cpr_duration()
    test_advance_watch()
    test_project_title()
    test_kbd_auto()
    test_pedal()
    print("test_bridge：全部通过")
