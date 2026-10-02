# -*- coding: utf-8 -*-
"""Cube Setlist Manager 自检：python -m pytest tests/。全 assert，无需 OBS/loopMIDI/Cubase 在场。"""
import http.client
import json
import os
import pathlib
import queue
import struct
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler

import advance
import cpr_meta
import daw_ctrl
import hotspot
import kbd_auto
import midi_bridge as mb
import obs_ctrl
import pedal
import song_meta
import web_remote
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


def _fake_song(path, tracks, segs=(("TempoMapSegment", "start", "0", "0.5"),),
               bpm=None):
    """构造最小 .song（ZIP 容器 + song.xml/metainfo.xml）。

    tracks: [(轨名, [(tag, start|None, length), ...]), ...]；start=None 模拟
    Part 内部相对事件（应被跳过）。segs: 节拍图段 (标签, 起点属性名, 值,
    tempo 秒/拍)；bpm 非 None 时写 metainfo 兜底、且节拍图留空。"""
    ev = ""
    for name, events in tracks:
        body = "".join(
            '<%s %s length="%s" name="e%d"/>' % (
                tag, "" if s is None else 'start="%s"' % s, l, i)
            for i, (tag, s, l) in enumerate(events))
        ev += ('<MediaTrack mediaType="Audio" name="%s">'
               '<List x:id="Events">%s</List></MediaTrack>' % (name, body))
    if segs:
        tm = "".join('<%s %s="%s" tempo="%s"/>' % (t, attr, v, spb)
                     for t, attr, v, spb in segs)
        tempo_xml = "<TempoMap>%s</TempoMap>" % tm
    else:
        tempo_xml = "<TempoMap/>"
    song_xml = ('<Song><Attributes x:id="Root" length="300"/>%s%s</Song>'
                % (tempo_xml, ev))
    meta = ('<Info><Attribute id="Media:Tempo" value="%s"/></Info>' % bpm
            if bpm else "<Info/>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Song/song.xml", song_xml)
        zf.writestr("metainfo.xml", meta)


def test_song_duration():
    # 基准：两轨事件取最大终点 × 秒/拍（120BPM=0.5）；占位 Root length=300
    # （无 start，不构成事件）不参与
    with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
        path = f.name
    try:
        _fake_song(path, [
            ("Piano", [("AudioEvent", "100", "200"), ("AudioEvent", "400", "40")]),
            ("Syn", [("AudioEvent", "50", "60")]),
        ])
        assert song_meta.read_duration(path) == 440 * 0.5
    finally:
        os.unlink(path)

    # Part 语义：容器（AudioPartEvent 有 start+length）计入；内部相对
    # 事件（无 start）跳过——容器终点已是绝对口径
    with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
        path = f.name
    try:
        _fake_song(path, [("Piano", [
            ("AudioPartEvent", "1000", "500"),
            ("AudioEvent", None, "9999")])])
        assert song_meta.read_duration(path) == 1500 * 0.5
    finally:
        os.unlink(path)

    # 节拍图兼容两代标签/属性名；多段线性换算
    for tag, attr in (("TempoMapSegment", "start"),
                      ("AudioTempoMapSegment", "offset")):
        with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
            path = f.name
        try:
            _fake_song(path, [("Piano", [("AudioEvent", "300", "100")])],
                       segs=((tag, attr, "0", "0.5"),
                             (tag, attr, "200", "0.25")))
            # 0-200 拍 @0.5s/拍 =100s；200-400 拍 @0.25 =50s → 150
            assert song_meta.read_duration(path) == 150.0
        finally:
            os.unlink(path)

    # 无节拍图时退回 metainfo BPM（60/90=0.6667s/拍）
    with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
        path = f.name
    try:
        _fake_song(path, [("Piano", [("AudioEvent", "0", "90")])],
                   segs=(), bpm=90)
        assert abs(song_meta.read_duration(path) - 60.0) < 1e-9
    finally:
        os.unlink(path)

    # 节拍与 BPM 都缺 → None（手填兜底）；坏包 → None
    with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
        path = f.name
    try:
        _fake_song(path, [("Piano", [("AudioEvent", "0", "10")])], segs=())
        assert song_meta.read_duration(path) is None
    finally:
        os.unlink(path)
    with tempfile.NamedTemporaryFile(suffix=".song", delete=False) as f:
        f.write(b"JUNK")
        path = f.name
    try:
        assert song_meta.read_duration(path) is None
    finally:
        os.unlink(path)

    # 真歌冒烟（存在才验）：数值须落在勘查实证区间
    real = pathlib.Path(r"C:\Users\XKZ\Documents\Studio One Projects\LinGo"
                        r"\3.21演出\3.21演出.song")
    if real.exists():
        d = song_meta.read_duration(str(real))
        assert d and 1500 < d < 2200     # 25~37 分钟界内（对表前宽松界）


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
    assert daw_ctrl.project_name_from_title(
        "Cubase Pro 工程 - アイドル") == "アイドル"
    assert daw_ctrl.project_name_from_title(
        "Cubase Version 13.0.40 工程 - TAIDADA") == "TAIDADA"
    assert daw_ctrl.project_name_from_title("记事本") is None
    assert daw_ctrl.project_name_from_title("") is None
    assert daw_ctrl.project_windows.__doc__  # 冒烟：识别函数可用


def test_transport_blocked():
    """走带状态互锁：S1/Cubase 的 SPACE 系键序是播放⇄停止开关，状态盲发
    会反向作用（已暂停按「暂停」起播、播放中按「播放/继续」反停、已停止
    按「停止/全停」起播）。rewind 永不拦（停止态由 rewind_stopped 键序兜）。"""
    import setlist_gui as sg
    for ts in ("playing", "paused", "stopped"):
        assert not sg.transport_blocked("rewind", ts)
    assert sg.transport_blocked("pause", "playing") is False
    assert sg.transport_blocked("pause", "paused")
    assert sg.transport_blocked("pause", "stopped")
    assert sg.transport_blocked("play", "stopped") is False
    assert sg.transport_blocked("play", "paused") is False
    assert sg.transport_blocked("play", "playing")
    assert sg.transport_blocked("resume", "paused") is False
    assert sg.transport_blocked("resume", "stopped") is False
    assert sg.transport_blocked("resume", "playing")
    assert sg.transport_blocked("stop", "playing") is False
    assert sg.transport_blocked("stop", "paused")
    assert sg.transport_blocked("stop", "stopped")
    # S1 事实表带停止态回零键序（纯 NUMDOT，不发空格）
    assert daw_ctrl.STUDIOONE.get("rewind_stopped") == ("NUMDOT",)


def test_pedal_pause_toggle():
    """踩钉「暂停/继续」按走带态选发：playing→暂停、paused→继续、
    stopped→发「暂停」（走互锁拦截）——踩钉不意外起播（起播有「开始」）。"""
    import setlist_gui as sg
    app = sg.App.__new__(sg.App)         # 绕构造：只测分发选发
    sent, navigated, panicked = [], [], []
    app._transport = sent.append
    app._prev = lambda: navigated.append("prev")
    app._next = lambda: navigated.append("next")
    app._panic = lambda: panicked.append("panic")
    for ts, want in (("playing", ["pause"]), ("paused", ["resume"]),
                     ("stopped", ["pause"])):
        app._transport_state = lambda ts=ts: ts
        app._pedal_action("pause")
        assert sent == want, (ts, sent)
        sent.clear()
    for action, nav in (("prev", "prev"), ("next", "next")):
        app._pedal_action(action)
        assert navigated == [nav]
        navigated.clear()
    app._pedal_action("panic")
    assert panicked == ["panic"]


def test_clock_port():
    """时钟端口独立配置：预置配置必须直填时钟端口（空/缺键=时钟监听停用，
    走带三态/已播/自动推进全不可用——不许手滑清空静默下线）。另以源码
    锚断言双端接线（持久化/热切换/设置页行/基线复位）不回归。"""
    src_set = open(pathlib.Path(__file__).resolve().parents[1]
                   / "setlist_gui.py", encoding="utf-8").read()
    src_auto = open(pathlib.Path(__file__).resolve().parents[1]
                    / "automator_gui.py", encoding="utf-8").read()
    for src in (src_set, src_auto):
        # hint 读取 + 持久化 + 热切换 + 设置页行
        assert 'cfg.get("clockPortHint")' in src
        assert '"clockPortHint": ck' in src
        assert 'menu_row("时钟端口名称"' in src
        assert "self.clock_port = mb.MidiIn(" in src
        assert "self.sync.reset()" in src            # 换口走带基线复位
    for f in ("config.example.json", "config.studioone.json",
              "config.automator.json"):
        cfg = json.load(open(pathlib.Path(__file__).resolve().parents[1] / f,
                             encoding="utf-8"))
        assert cfg.get("clockPortHint"), "%s 缺 clockPortHint" % f


def test_daw_backends():
    # 双底座事实表：Cubase 默认激活；S1 换表后标题解析跟随，用时还原
    assert daw_ctrl.ACTIVE is daw_ctrl.CUBASE
    assert daw_ctrl.FACTS["cubase"]["song_ext"] == ".cpr"
    s1 = daw_ctrl.FACTS["studioone"]
    assert s1["song_ext"] == ".song" and s1["probe_duration"]  # song_meta ZIP 解析
    assert set(s1["transport"]) == set(daw_ctrl.CUBASE["transport"])  # 动作齐
    daw_ctrl.set_active(s1)
    try:
        assert daw_ctrl.project_name_from_title("Studio One - 優しい彗星") \
            == "優しい彗星"
        assert daw_ctrl.project_name_from_title("Studio One") is None  # Start 页
        assert daw_ctrl.project_name_from_title("记事本") is None
        # 切歌完成判定容忍脏星：手动改过的工程切走再切回，S1 保留未保存
        # 修改、连星一起恢复（真机 2026-09-29）——按星零容忍会卡满 120s
        assert daw_ctrl.title_matches("優しい彗星", "Studio One - 優しい彗星")
        assert daw_ctrl.title_matches("優しい彗星", "Studio One - 優しい彗星*")
        assert not daw_ctrl.title_matches("優しい彗星",
                                          "Studio One - 優しい彗星X")
        assert not daw_ctrl.title_matches("優しい彗星", "Studio One - 優しい")
    finally:
        daw_ctrl.set_active(daw_ctrl.CUBASE)


def test_daw_settings_compat():
    # config 兼容：旧 cubase 段（cubaseExe 键）→ dawSettings 优先、键名迁移
    import setlist_gui as sg
    s = sg.daw_settings({"cubase": {"cubaseExe": "C:/x.exe",
                                    "projectsRoot": "P"}}, "cubase")
    assert s["dawExe"] == "C:/x.exe" and s["projectsRoot"] == "P"
    s = sg.daw_settings({"cubase": {"cubaseExe": "OLD"},
                         "dawSettings": {"dawExe": "NEW"}}, "studioone")
    assert s["dawExe"] == "NEW"
    # 底座隔离回归：cubase 遗留键（旧段与 cubaseExe 别名）绝不能泄漏给
    # S1 底座——否则 S1 版启动自检会把 Cubase 拉起来（真机事故）
    s = sg.daw_settings({"dawSettings": {"cubaseExe": "C:/Cubase.exe"}},
                        "studioone")
    assert "Cubase" not in s["dawExe"]
    s = sg.daw_settings({"cubase": {"cubaseExe": "C:/Cubase.exe",
                                    "projectsRoot": "CubaseLib"}},
                        "studioone")
    assert "Cubase" not in s["dawExe"] and s["projectsRoot"] != "CubaseLib"
    assert sg.scan_library.__doc__  # 冒烟：库扫描签名可用


def test_automator_lite():
    # Cube Automator（简化版）：模块可导入、web lite 双页面、无控制器快照容错
    import automator_gui as ag
    assert ag.daw_settings.__doc__ and ag.scan_library.__doc__
    import web_remote as wr
    # APP 端 lite 页=完整版骨架+注入：设置面板（m-dev）强制常显为主页
    assert "#m-dev{display:flex!important" in wr.PAGE_LITE_APP
    # 手机屏内容超高：面板顶部锚定+覆盖层滚动（底部锚定会顶出标题无处滚）
    assert "align-items:flex-start" in wr.LITE_CSS
    assert "overflow-y:auto" in wr.LITE_CSS
    assert 'id="m-dev"' in wr.PAGE_LITE_APP and 'id="apps-list"' in \
        wr.PAGE_LITE_APP
    # 回归锚：lite 页无 b-dev 按钮，DEV_JS 的接线必须判空——否则整个
    # script 块抛 TypeError（地址框/版本号/认领块/保存全死，真机踩过）
    assert 'id="b-dev"' not in wr.PAGE_LITE_APP
    assert 'if(_bd)_bd.addEventListener' in wr.PAGE_LITE_APP
    assert "openDev()" in wr.LITE_JS            # 主页初始化走 openDev
    # 无障碍状态+跳转按钮：顶栏被全屏面板盖住，并入「设置」标题行靠右（唯一）
    assert wr.PAGE_LITE_APP.count('id="acc-ind"') == 1
    assert wr.PAGE_LITE_APP.count('id="b-acc"') == 1
    assert '<h2 style="display:flex;align-items:center">设置' \
        '<span class="ind" id="acc-ind"' in wr.PAGE_LITE_APP
    assert wr.PAGE_LITE_APP.find('id="m-dev"') \
        < wr.PAGE_LITE_APP.find('id="acc-ind"')  # 位于面板 HTML 内
    # 网页端 lite 页=极简提示页（一行标题+下载 APP，不带控制页公共 JS）
    assert "电脑端正在运行Cube Automator" in wr.PAGE_LITE_BROWSER
    assert "下载Cube Remote APP" in wr.PAGE_LITE_BROWSER
    assert "/app.apk" in wr.PAGE_LITE_BROWSER
    assert "render()" not in wr.PAGE_LITE_BROWSER

    class _FakeApp:                 # lite：无切歌控制器（ctrl=None）
        lite = True
        watch = None
        ctrl = None
        pl_keys = []
        by_key = {}
        durations = {}
        cur = None
        switch_confirm = False
        web = None
    snap = wr.build_snapshot(_FakeApp(), False)
    assert snap["ready"] is True and snap["tstate"] == "stopped" \
        and snap["songs"] == [] and snap["busy"] is False


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

    # 同名多口消歧：两只同型号无线 MIDI 盒在 winmm 是两个完全同名的口，
    # dev 序号决定落位（键盘自动化窗设备下拉选择即写此键）
    devs = [(0, "Rubix24"), (1, "USB-Midi"), (2, "USB-Midi"), (3, "JUNO-DS")]
    assert kbd_auto._pick_hit(devs, "USB-Midi") == (1, "USB-Midi")
    assert kbd_auto._pick_hit(devs, "USB-Midi", 1) == (2, "USB-Midi")
    assert kbd_auto._pick_hit(devs, "USB-Midi", 5) == (2, "USB-Midi")   # 越界收敛
    assert kbd_auto._pick_hit(devs, "USB-Midi", -1) == (1, "USB-Midi")  # 负数钳0
    assert kbd_auto._pick_hit(devs, "JUNO-DS") == (3, "JUNO-DS")   # 有线全名不误配
    assert kbd_auto._pick_hit(devs, "AX-09") is None
    # 前缀重名口（"USB-Midi" 与 "USB-Midi 2" 并存）：下拉存的全名必须
    # 精确命中，不被子串语义抢到对方口上；宽 hint（JUNO）走子串回退
    pv = [(0, "USB-Midi 2"), (1, "USB-Midi")]
    assert kbd_auto._pick_hit(pv, "USB-Midi") == (1, "USB-Midi")
    assert kbd_auto._pick_hit(pv, "USB-Midi 2") == (0, "USB-Midi 2")
    assert kbd_auto._pick_hit([(0, "JUNO-DS")], "JUNO") == (0, "JUNO-DS")
    # 手编 config 的敌意 dev 收敛：脏类型不炸、不静默错绑（bool 排除——
    # true 静默等于 1 会错绑第 2 个同名口）
    assert kbd_auto._norm_dev("1") == 0 and kbd_auto._norm_dev(None) == 0
    assert kbd_auto._norm_dev(True) == 0 and kbd_auto._norm_dev(0.5) == 0
    assert kbd_auto._norm_dev(2) == 2
    assert kbd_auto._pick_hit(devs, "USB-Midi", "1") == (1, "USB-Midi")
    assert kbd_auto._pick_hit(devs, "USB-Midi", True) == (1, "USB-Midi")
    # 空/非字符串 hint = 显式未配置，不命中任何口（"" 子串恒真曾打到第一个口）
    assert kbd_auto._pick_hit(devs, "") is None
    assert kbd_auto._pick_hit(devs, None) is None
    assert kbd_auto._name_pos(devs, "USB-Midi", 2) == (2, 2)
    assert kbd_auto._name_pos(devs, "JUNO-DS", 3) == (1, 1)
    es = kbd_auto._device_entries(["Rubix24", "USB-Midi", "USB-Midi"])
    assert [e[0] for e in es] == ["Rubix24", "USB-Midi（第1个）",
                                  "USB-Midi（第2个）"]
    assert [e[2] for e in es] == [0, 0, 1]
    assert kbd_auto._device_entries([]) == []
    # 默认值带 dev=0：旧 config 无此键时单设备行为不变
    assert kbd_auto.DEFAULT_JUNO["dev"] == 0 and kbd_auto.DEFAULT_AX["dev"] == 0

    # 踩钉学习候选按琴口位次精确排除（同名盒世界按名排除会连同名真踩钉
    # 口一起误伤）：kb_ports=(inHint, dev) 在同一次枚举内经 _pick_hit
    # 解析设备号排除；返回三元组（设备号, 名, 同名内位次）供 Learner
    # 重开 RawMidiIn 时原位落位——重开丢位次会把排除原样打回（R2-P2）；
    # EXCLUDE 兜底名单继续滤 JUNO/AX-09 系旧接法
    orig_in = pedal.mb._in_devices
    pedal.mb._in_devices = lambda: [(0, "USB-Midi"), (1, "USB-Midi"),
                                    (2, "loopMIDI Port"), (3, "踩钉CC")]
    try:
        assert pedal.learning_candidates(
            kb_ports=(("USB-Midi", 0), ("USB-Midi", 1))) == \
            [(3, "踩钉CC", 0)]
        assert pedal.learning_candidates() == \
            [(0, "USB-Midi", 0), (1, "USB-Midi", 1), (3, "踩钉CC", 0)]
        assert pedal.learning_candidates(
            kb_ports=(("JUNO", 0),)) == \
            [(0, "USB-Midi", 0), (1, "USB-Midi", 1), (3, "踩钉CC", 0)]
        # 位次与 RawMidiIn._pick_hit 精确命中闭合：候选 (1,"USB-Midi",1)
        # 重开时落到同一设备号
        assert kbd_auto._pick_hit(pedal.mb._in_devices(), "USB-Midi", 1) \
            == (1, "USB-Midi")
    finally:
        pedal.mb._in_devices = orig_in
    # IN 侧位次：dev 只在与 outHint 同名（同一台盒）时作用于 IN 组；
    # 异名手工配置退回首命中——一个序号不跨两组用（R2-P3）
    assert kbd_auto._in_dev({"inHint": "A", "outHint": "A", "dev": 1}) == 1
    assert kbd_auto._in_dev({"inHint": "B", "outHint": "A", "dev": 1}) == 0


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
    pl.apply("", {"next": 4}, {})       # 生产契约：{动作: CC}（首版测试直塞
    pl._msg(0x90, 60, 100)              #   {CC: 动作} 掩盖了 apply 漏反转的 P0）
    pl._msg(0xB0, 60, 127)              # 未绑定的 CC 不触发
    pl._msg(0xB0, 4, 127)               # 绑定 CC 上升沿 → 回调
    pl._msg(0xB0, 4, 127)               # 保持不重复
    assert hits == ["next"]

    # 页面静音：停触发（silent 独立于学习通道）、让出 MIDI 口、挂起重连；
    # 取消后恢复。学习器 end_capture 只动 learning，静音不得因此失效
    hits2 = []
    pl2 = pedal.PedalListener(hits2.append)
    pl2.apply("无此口", {"next": 4}, {"next": 0xB0})
    assert pl2.bridge.binds == {0xB0: "next"}       # 换绑同步到设备桥（VK→动作）
    assert not pl2.try_open() and pl2.hid_active    # MIDI 口没有，HID 在听
    pl2.mute()
    assert pl2.muted and pl2.bridge.silent and not pl2.bridge.learning
    assert not pl2.try_open()                       # 静音期重连被挂起
    pl2.bridge._feed(0xB0, True)                    # 静音期按键：不触发
    pl2.bridge._feed(0xB0, False)
    assert hits2 == []
    cap = []
    pl2.bridge.begin_capture(lambda vk, d, t: cap.append((vk, d)))
    pl2.bridge._feed(0xB0, True)                    # 静音中的学习捕获照常转投
    pl2.bridge._feed(0xB0, False)
    assert [(v, d) for v, d in cap] == [(0xB0, True), (0xB0, False)]
    pl2.bridge.end_capture()
    pl2.bridge._feed(0xB0, True)                    # 学习结束：静音仍压制
    pl2.bridge._feed(0xB0, False)
    assert hits2 == [] and pl2.bridge.silent
    pl2.unmute()
    pl2.apply("无此口", {"next": 4}, {"next": 0xB0})
    assert not pl2.try_open() and pl2.hid_active    # 恢复后 HID 回来
    pl2.bridge._feed(0xB0, True)
    time.sleep(0.05)
    pl2.bridge._feed(0xB0, False)
    assert hits2 == ["next"]                        # 解除静音恢复触发（快路径）
    pl2.shutdown()
    assert not pl2.bridge.running

    # 配置加载：JSON true 是 bool（int 子类），不能混进 CC/VK；hint 挡 null
    assert pedal.load_binding({"pedal": {"bindings": {"play": True}}}) == ("", {})
    assert pedal.load_binding({"pedal": {"deviceHint": None}}) == ("", {})
    assert pedal.load_hid({}) == {}
    assert pedal.load_hid(
        {"pedal": {"hidBindings": {"next": 0xB0, "bad": 9}}}) == {"next": 0xB0}
    assert pedal.load_hid({"pedal": {"hidBindings": {"play": True}}}) == {}
    assert pedal.load_device_cfg({}) == ("", True)
    assert pedal.load_device_cfg(
        {"pedal": {"hidDeviceHint": None, "intercept": False}}) == ("", False)
    # 手改配置的类型错不炸启动路径：pedal 段非 dict 按空段、intercept 非
    # bool（如字符串 "false"，bool() 会变 True）回退默认；子键层同样只认 dict
    assert pedal.load_binding({"pedal": ["x"]}) == ("", {})
    assert pedal.load_hid({"pedal": ["x"]}) == {}
    assert pedal.load_device_cfg({"pedal": ["x"]}) == ("", True)
    assert pedal.load_device_cfg({"pedal": {"intercept": "false"}}) == ("", True)
    assert pedal.load_device_cfg({"pedal": {"intercept": 0}}) == ("", True)
    assert pedal.load_device_cfg({"pedal": {"intercept": ""}}) == ("", True)
    for junk in ("junk", 5, [4], True):
        assert pedal.load_binding({"pedal": {"bindings": junk}}) == ("", {})
        assert pedal.load_hid({"pedal": {"hidBindings": junk}}) == {}
    hs = {}
    assert pedal.hid_fire(hs, 0xB0, True, 200.0)    # 首按
    assert not pedal.hid_fire(hs, 0xB0, True, 200.05)   # 按住不重复
    assert not pedal.hid_fire(hs, 0xB0, False, 200.1)   # 松开不触发
    assert not pedal.hid_fire(hs, 0xB0, True, 200.12)   # 去抖窗内再按被挡
    assert not pedal.hid_fire(hs, 0xB0, False, 200.13)  # 松开
    assert pedal.hid_fire(hs, 0xB0, True, 200.3)    # 释放后重踩可再触发
    assert pedal.hid_fire(hs, 0xB1, True, 200.31)   # 其他键独立计数
    assert pedal.hid_name(0xB0) == "下一曲" and pedal.hid_name(0x70) == "F1"
    assert 0xB0 in pedal.LEARN_VKS                  # 多媒体键可学
    assert 0x0D in pedal.LEARN_VKS                  # 回车可学（首版漏掉的坑）
    assert 0x01 not in pedal.LEARN_VKS              # 鼠标键不可学
    assert 0x11 not in pedal.LEARN_VKS              # 修饰键（Ctrl）不可学
    assert 0x14 not in pedal.LEARN_VKS              # CapsLock 不可学
    assert 0xA0 not in pedal.LEARN_VKS              # 修饰键左变体不可学

    # 设备身份解析（本机真实路径形态；BLE 取 MAC，USB 取 VID&PID+接口）
    p_ble = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}"
             r"_9df17da3c702&Col02#9&7bdb7a&0&0001"
             r"#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}")
    p_usb = (r"\\?\HID#VID_32D7&PID_0001&MI_00&Col02#7&42fb74b&0&0001"
             r"#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}")
    assert pedal.device_identity(p_ble) == "9DF17DA3C702"
    assert pedal.device_identity(p_usb) == "VID_32D7&PID_0001&MI_00"

    # ---- 设备桥：归属判定（_on_raw 过滤后 _feed 只收所选设备）+ 学习捕获 ----
    hits3 = []
    br = pedal.DeviceBridge(hits3.append)
    br.configure(binds={0x0D: "play"}, device_hint="9DF17DA3C702")
    br._feed(0x0D, True)                            # 所选设备回车按下 → 触发
    assert hits3 == ["play"]

    # 学习捕获：完整踩法（down+up），不触发动作
    br.learning = True
    cap = []
    br.capture = lambda vk, d, t: cap.append((vk, d))
    br._feed(0x0D, True)
    br._feed(0x0D, False)
    assert [(v, d) for v, d in cap] == [(0x0D, True), (0x0D, False)]
    assert hits3 == ["play"]
    br.learning = False
    br.capture = None

    # 拦截=系统热键注册：绑定键集合；未选设备=键留给系统；关拦截=不注册
    br6 = pedal.DeviceBridge(None)
    br6.configure(binds={0xB0: "next"}, device_hint="X", block=True)
    assert br6._hotkey_vks() == [0xB0]
    br6.configure(binds={0xB0: "next", 0xB1: "prev"}, block=True)
    assert br6._hotkey_vks() == [0xB0, 0xB1]        # 单踩+时序键全部注册
    br6.configure(device_hint="")                   # 未选设备：不注册
    assert br6._hotkey_vks() == []
    br6.configure(device_hint="X", block=False)     # 关拦截：不注册
    assert br6._hotkey_vks() == []
    br6.stop()                                      # 未 start 时 stop 安全

    # 拦截开启的触发路径：按下沿被热键消费（WM_HOTKEY 回执记 pending），
    # 松开沿带归属 → 合成按压对；单踩在松开沿触发
    hitsb = []
    brb = pedal.DeviceBridge(hitsb.append)
    brb.configure(binds={0xB0: "next"}, device_hint="X", block=True)
    brb._hotkeys[1] = 0xB0                          # 桩：热键已注册
    brb._wndproc(None, pedal.WM_HOTKEY, 1, 0)       # 热键回执（真 down 已被消费）
    time.sleep(0.06)                                # 真实按压时长
    brb._feed(0xB0, False)                          # 归属松开沿到达
    assert hitsb == ["next"]                        # 合成对 → 松开沿触发
    # 学习捕获同路径：合成对进示范序列
    capb = []
    brb.learning = True
    brb.capture = lambda vk, d, t: capb.append((vk, d))
    brb._wndproc(None, pedal.WM_HOTKEY, 1, 0)
    time.sleep(0.05)
    brb._feed(0xB0, False)
    assert [(v, d) for v, d in capb] == [(0xB0, True), (0xB0, False)]
    brb.learning = False
    # 失联保护：pending 超 2 秒的松开沿，按压起点不采信（折算成当下的
    # 新按压——陈旧回执不得把按压起点拉回两秒前）
    hitsb2 = []
    brb2 = pedal.DeviceBridge(hitsb2.append)
    brb2.configure(binds={}, device_hint="X", block=True, temporal={0xB0})
    brb2._hotkeys[1] = 0xB0
    capb2 = []
    brb2.learning = True
    brb2.capture = lambda vk, d, t: capb2.append((vk, d, t))
    brb2._wndproc(None, pedal.WM_HOTKEY, 1, 0)
    time.sleep(0.05)
    brb2._feed(0xB0, False)                         # 50ms 正常回执
    brb2._pending[0xB0] = time.monotonic() - 2.5    # 注入陈旧回执（2.5 秒前）
    brb2._feed(0xB0, False)
    assert [(v, d) for v, d, _t in capb2] == [(0xB0, True), (0xB0, False)] * 2
    assert 0 <= capb2[1][2] - capb2[0][2] < 0.2     # 陈旧回执：按压起点折到当下
    assert capb2[3][2] - capb2[2][2] < 0.2
    brb2.learning = False
    brb2.stop()

    # ---- 手势引擎：表驱动状态机（假定时器，手动推进） ----
    hitsg = []
    spawned = []

    class _FT:
        def __init__(s, delay, cb):
            s.delay, s.cb, s.dead = delay, cb, False

        def cancel(s):
            s.dead = True

    def fake_spawn(delay, cb):
        h = _FT(delay, cb)
        spawned.append(h)
        return h

    eng = pedal.GestureEngine(hitsg.append, spawn_timer=fake_spawn)
    eng.configure({(("hid", 0xB0), "double"): "next",
                   (("hid", 0xB0), "single"): "play",
                   (("hid", 0xB1), "double"): "panic",
                   (("midi", 4), "double"): "rewind"})
    assert eng.is_temporal(("hid", 0xB0))
    assert eng.temporal_keys("hid") == {0xB0, 0xB1}
    assert eng.temporal_keys("midi") == {4}
    # 双踩：窗内第二踩落下触发，第一踩绝不即发
    eng.feed(("hid", 0xB0), True, 100.0)
    eng.feed(("hid", 0xB0), False, 100.1)
    assert hitsg == []                              # 等双踩窗判定
    eng.feed(("hid", 0xB0), True, 100.3)
    assert hitsg == ["next"]                        # 第二踩落下即触发
    eng.feed(("hid", 0xB0), False, 100.4)           # 已消费：松开归位无输出
    # 单踩：松脚后双踩窗平静过期（假定时器推进）
    eng.feed(("hid", 0xB0), True, 106.0)
    eng.feed(("hid", 0xB0), False, 106.1)
    assert hitsg == ["next"]
    pending = [h for h in spawned if not h.dead]
    assert len(pending) == 1 and pending[0].delay == pedal.DOUBLE_WINDOW
    pending[0].cb()
    assert hitsg == ["next", "play"]
    # 单踩窗先到期结算，随后的踩踏=新序列（两次单踩不并成双踩）
    eng.feed(("hid", 0xB0), True, 200.0)
    eng.feed(("hid", 0xB0), False, 200.12)
    wt = [h for h in spawned if not h.dead][-1]
    wt.cb()                                     # 窗到期 → 单踩
    assert hitsg == ["next", "play", "play"]
    eng.feed(("hid", 0xB0), True, 201.0)        # 新序列开头（远超双踩窗）
    eng.feed(("hid", 0xB0), False, 201.12)
    assert hitsg == ["next", "play", "play"]
    # 被取消/过期的旧定时器误触发：token 静默
    for h in spawned:                           # 被取消的过期定时器
        if h.dead:
            h.cb()                              #   误触发：token 静默
    assert hitsg == ["next", "play", "play"]
    pending2 = [h for h in spawned if not h.dead][-1]
    pending2.cb()                               # 本序列窗到期 → 单踩
    assert hitsg == ["next", "play", "play", "play"]
    # 按住不放：长踩手势已废除——松脚前零动作、零定时器（连长踩窗都不挂）
    n0 = len(spawned)
    eng.feed(("hid", 0xB1), True, 300.0)
    eng.feed(("hid", 0xB1), True, 300.2)            # 固件按住重发：忽略
    assert hitsg == ["next", "play", "play", "play"]
    assert len(spawned) == n0                       # 按住中零定时器
    eng.feed(("hid", 0xB1), False, 300.6)           # 松开 → wait2（唯一定时器=窗）
    assert len(spawned) == n0 + 1
    eng.feed(("hid", 0xB1), True, 300.8)            # 窗内第二踩=双踩
    assert hitsg[-1] == "panic"
    eng.feed(("hid", 0xB1), False, 301.0)           # held 归位
    # 单+双组合：短按松脚进窗，窗过期结算单踩、窗内第二踩=双踩
    hitsg2 = []
    eng2 = pedal.GestureEngine(hitsg2.append, spawn_timer=fake_spawn)
    eng2.configure({(("midi", 4), "single"): "play",
                    (("midi", 4), "double"): "panic"})
    eng2.feed(("midi", 4), True, 400.0)
    eng2.feed(("midi", 4), False, 400.12)
    assert hitsg2 == []                             # 进窗等待判定
    w21 = [h for h in spawned if not h.dead][-1]
    w21.cb()                                        # 窗过期 → 单踩
    assert hitsg2 == ["play"]
    eng2.feed(("midi", 4), True, 401.0)
    eng2.feed(("midi", 4), False, 401.1)
    eng2.feed(("midi", 4), True, 401.2)             # 窗内第二踩=双踩
    assert hitsg2 == ["play", "panic"]
    eng2.feed(("midi", 4), False, 401.4)            # held 归位
    # 边界重置：未决手势连同定时器一并作废（reset 后 token 失效）
    eng2.feed(("midi", 4), True, 402.0)             # 新序列开头
    eng2.reset()
    for h in spawned:
        if h.dead:
            h.cb()                                  # 过期定时器 token 静默
    assert hitsg2 == ["play", "panic"]
    eng2.feed(("midi", 4), False, 402.1)            # reset 后游离 up：忽略
    assert hitsg2 == ["play", "panic"]
    # 双踩只绑（无单踩）：短按平静过期=无输出，且不卡死后续序列
    hitsg3 = []
    eng3 = pedal.GestureEngine(hitsg3.append, spawn_timer=fake_spawn)
    eng3.configure({(("hid", 0xB2), "double"): "next"})
    eng3.feed(("hid", 0xB2), True, 500.0)
    eng3.feed(("hid", 0xB2), False, 500.1)
    wt = [h for h in spawned if not h.dead][-1]
    wt.cb()                                         # 窗过期：无单踩=无输出
    assert hitsg3 == []
    eng3.feed(("hid", 0xB2), True, 502.0)           # 不卡 wait2：新序列正常
    eng3.feed(("hid", 0xB2), False, 502.1)
    eng3.feed(("hid", 0xB2), True, 502.3)           # 窗内第二踩=双踩
    assert hitsg3 == ["next"]
    # 分断弹跳爆发：回弹重压（距上次被受理松开 <30ms）整体拒收——wait2
    # 窗保持武装，最终仍是一次完整单踩（旧「还原 down 相位」方案会在收尾
    # 弹开沿上留僵尸 down=假触发/单踩丢失，审计实测否决）
    hitsg6 = []
    eng6 = pedal.GestureEngine(hitsg6.append, spawn_timer=fake_spawn)
    eng6.configure({(("hid", 0xB0), "single"): "play",
                    (("hid", 0xB0), "double"): "next"})
    eng6.feed(("hid", 0xB0), True, 900.0)
    eng6.feed(("hid", 0xB0), False, 900.10)         # 松开 → wait2+窗定时器
    w_keep = [h for h in spawned if not h.dead][-1]
    assert eng6.feed(("hid", 0xB0), True, 900.103) is False   # 回弹重压拒收
    assert w_keep.dead is False                     # 窗定时器未被弹跳撤掉
    eng6.feed(("hid", 0xB0), False, 900.104)        # 收尾弹开沿：wait2 容错忽略
    w_keep.cb()
    assert hitsg6 == ["play"]                       # 仍是一次完整单踩
    # 窗过期后的真新序列不受弹跳史影响
    eng6.feed(("hid", 0xB0), True, 900.2)           # 距上次松开 100ms：真新踩
    eng6.feed(("hid", 0xB0), False, 900.3)
    w2 = [h for h in spawned if not h.dead][-1]
    w2.cb()
    assert hitsg6 == ["play", "play"]
    # 分叉带消除：松开后 15ms 重压拒收（=学习器同边界）、40ms 重压=真双踩
    hitsg7 = []
    eng7 = pedal.GestureEngine(hitsg7.append, spawn_timer=fake_spawn)
    eng7.configure({(("hid", 0xB0), "double"): "next"})
    eng7.feed(("hid", 0xB0), True, 950.0)
    eng7.feed(("hid", 0xB0), False, 950.10)
    assert eng7.feed(("hid", 0xB0), True, 950.115) is False   # 15ms：弹跳
    assert eng7.feed(("hid", 0xB0), True, 950.14) is True     # 40ms：真双踩
    assert hitsg7 == ["next"]
    # 仅双踩键 × 弹跳爆发：不重挂窗、不开新序列
    hitsg8 = []
    eng8 = pedal.GestureEngine(hitsg8.append, spawn_timer=fake_spawn)
    eng8.configure({(("hid", 0xB1), "double"): "panic"})
    eng8.feed(("hid", 0xB1), True, 960.0)
    eng8.feed(("hid", 0xB1), False, 960.10)         # 真松开 → wait2 挂窗
    n8 = len(spawned)
    assert eng8.feed(("hid", 0xB1), True, 960.103) is False   # 回弹重压：拒收
    assert hitsg8 == []
    assert len(spawned) == n8                       # 不重挂窗
    spawned[-1].cb()                                # 窗过期：仅双踩绑=无输出
    assert hitsg8 == []
    # 双踩触发后的 held 释放沿同理
    hitsg10 = []
    eng10 = pedal.GestureEngine(hitsg10.append, spawn_timer=fake_spawn)
    eng10.configure({(("hid", 0xB0), "double"): "next"})
    eng10.feed(("hid", 0xB0), True, 1700.0)
    eng10.feed(("hid", 0xB0), False, 1700.1)
    eng10.feed(("hid", 0xB0), True, 1700.2)         # 双踩触发 → held
    assert hitsg10 == ["next"]
    eng10.feed(("hid", 0xB0), False, 1700.218)      # 释放（设锚）
    eng10.feed(("hid", 0xB0), True, 1700.23)        # 回弹重压：拒收
    eng10.feed(("hid", 0xB0), False, 1700.24)
    assert hitsg10 == ["next"]                      # 无假双踩
    # apply(hid_binds=None) 沿用现值重分流：同键单+双不得错落快路径
    #（修复前单踩错落桥快路径而 temporal 键整体转投引擎=死绑）。
    # 动作用中性名（引擎层动作名任意字符串，不依赖 ACTIONS 白名单）
    hitsg11 = []
    pl7 = pedal.PedalListener(hitsg11.append)
    pl7.apply("无此口", {}, {"cue": 0xB2, "panic": 0xB2}, "X", True,
              gestures={"cue": "single", "panic": "double"})
    assert pl7.bridge.binds == {}                   # 同键双绑：整键进引擎
    pl7.apply("无此口", {}, None, "X", True,        # hid_binds=None：沿用现值
              gestures={"cue": "single", "panic": "double"})
    assert pl7.bridge.binds == {}                   # 修复前单踩错落快路径
    assert pl7.engine.binds.get((("hid", 0xB2), "single")) == "cue"
    pl7.bridge._feed(0xB2, True)
    time.sleep(0.05)
    pl7.bridge._feed(0xB2, False)
    time.sleep(0.45)                                # 双踩窗过期 → 单踩结算
    assert hitsg11 == ["cue"]                       # 单踩经引擎正常触发
    pl7.shutdown()
    # 非弹跳的正常第二踩（40ms，浮点安全间距）仍是双踩
    hitsg7b = []
    eng7b = pedal.GestureEngine(hitsg7b.append, spawn_timer=fake_spawn)
    eng7b.configure({(("hid", 0xB0), "double"): "next"})
    eng7b.feed(("hid", 0xB0), True, 970.0)
    eng7b.feed(("hid", 0xB0), False, 970.10)
    eng7b.feed(("hid", 0xB0), True, 970.14)         # 40ms 后：真双踩
    assert hitsg7b == ["next"]

    # 示范式学习分类（双踩/单踩/超窗二踩/MIDI 通道）
    ln = object.__new__(pedal.Learner)              # 绕过 __init__ 不开真端口
    ln.cc = None
    ln.events = []
    ln.double_window = pedal.DOUBLE_WINDOW
    ln._clock = lambda: 300.2                       # 假钟：按下后 0.2s
    ln._raw_capture(0xB0, True, 300.0)
    assert ln.result() is None                      # 还按着：等松脚再判
    ln._raw_capture(0xB0, False, 300.12)
    assert ln.result() is None                      # 等双踩窗平静过期
    ln._raw_capture(0xB0, True, 300.3)              # 窗内第二踩 → 双踩
    assert ln.result() == ("hid", "下一曲", 0xB0, "double")
    ln.cc = None
    ln.events = [(("hid", 0x0D), True, 400.0)]      # 只按住未松
    ln._clock = lambda: 400.55
    assert ln.result() is None                # 长踩已废除：按住不产生手势
    ln.cc = None
    ln.events = [(("hid", 0x0D), True, 500.0),
                 (("hid", 0x0D), False, 500.1),
                 (("hid", 0x0D), True, 502.0)]      # 超窗的第二踩=误触
    ln._clock = lambda: 502.1
    assert ln.result() == ("hid", "回车", 0x0D, "single")
    ln.cc = None
    ln.events = [(("midi", 4), True, 600.0),
                 (("midi", 4), False, 600.1),
                 (("midi", 4), True, 600.25)]
    ln._midi_name = "Rubix USB"
    ln._clock = lambda: 600.3
    assert ln.result() == ("midi", "Rubix USB", 4, "double")  # MIDI 同款

    # 手势配置加载：只认真动作名/手势名；阈值夹区间
    g, dw = pedal.load_gestures(
        {"pedal": {"gestures": {"next": "double", "bad": "double",
                                "next2": "triple"},
                   "longPress": 0.6, "doubleWindow": "junk"}})
    assert g == {"next": "double"} and dw == pedal.DOUBLE_WINDOW
    assert pedal.load_gestures({}) == ({}, pedal.DOUBLE_WINDOW)
    # 已废除的长踩：手势与绑定一并作废（降级成单踩会演出误触发）
    assert pedal.load_hid({"pedal": {"hidBindings": {"panic": 0xB1},
                                     "gestures": {"panic": "long"}}}) == {}
    assert pedal.load_binding({"pedal": {"bindings": {"panic": 7},
                                         "gestures": {"panic": "long"}}}) \
        == ("", {})
    # 同码去重按 (码, 手势) 身份：同键不同手势共存是特性
    binds_g = {"play": 0xB0, "next": 0xB0}
    assert pedal.load_hid({"pedal": {"hidBindings": binds_g,
                                     "gestures": {"play": "single",
                                                  "next": "double"}}}) \
        == {"play": 0xB0, "next": 0xB0}             # 同键不同手势都保留
    assert pedal.load_hid({"pedal": {"hidBindings": binds_g}}) \
        == {"play": 0xB0}                           # 同码同手势：保首个
    # 监听器分流：仅单踩走桥快路径，时序键进引擎（HID+MIDI 双通道）
    hits7 = []
    pl4 = pedal.PedalListener(hits7.append)
    pl4.apply("无此口", {"rewind": 4}, {"next": 0xB0}, "X", True,
              gestures={"next": "double", "rewind": "double"},
              double_window=0.35)
    assert pl4.bridge.binds == {}                   # 无单踩快路径键
    assert pl4.engine.temporal_keys("hid") == {0xB0}
    assert pl4.engine.temporal_keys("midi") == {4}
    pl4._msg(0xB0, 4, 127)                          # MIDI 上升沿=按下
    assert hits7 == []                              # 时序键不再按下即发
    time.sleep(0.05)                                # 按压时长（松开沿同龄闸）
    pl4._msg(0xB0, 4, 0)                            # 下降沿=松开
    time.sleep(0.06)                                # 抖动闸 30ms：真双踩间隔
    pl4._msg(0xB0, 4, 127)                          # 窗内第二踩 → 双踩
    assert hits7 == ["rewind"]
    pl4.shutdown()
    assert not pl4.bridge.running

    # 同键「单踩+双踩」共存：单踩兄弟必须随键进引擎窗后触发（P1 回归——
    # 修复前单踩留在桥快路径被 temporal 分流整体吞掉=死绑）
    hits8 = []
    pl5 = pedal.PedalListener(hits8.append)
    pl5.apply("无此口", {}, {"play": 0xB0, "next": 0xB0}, "X", True,
              gestures={"play": "single", "next": "double"})
    assert pl5.bridge.binds == {}                   # temporal 键不进快路径
    assert pl5.engine.temporal_keys("hid") == {0xB0}
    assert pl5.engine.binds.get((("hid", 0xB0), "single")) == "play"
    assert pl5.engine.binds.get((("hid", 0xB0), "double")) == "next"
    pl5.bridge._feed(0xB0, True)                    # 按下：不即发
    assert hits8 == []
    time.sleep(0.05)                                # 真实踩踏的按压时长
    pl5.bridge._feed(0xB0, False)                   # 松开，等双踩窗
    time.sleep(0.45)
    assert hits8 == ["play"]                        # 窗过期 → 单踩经引擎
    pl5.bridge._feed(0xB0, True)                    # 双踩：down/up/down
    time.sleep(0.05)
    pl5.bridge._feed(0xB0, False)
    time.sleep(0.05)
    pl5.bridge._feed(0xB0, True)
    time.sleep(0.15)
    assert hits8 == ["play", "next"]
    pl5.shutdown()
    assert not pl5.bridge.running
    # 脉冲式固件（TurnerPro 实测）：真松开沿距按下仅 7-16ms——8ms 的 up
    # 是真松开，快踩进窗后照常结算单踩（同龄闸已按校准探针结论删除）
    hitsg4 = []
    eng4 = pedal.GestureEngine(hitsg4.append, spawn_timer=fake_spawn)
    eng4.configure({(("hid", 0xB0), "single"): "play",
                    (("hid", 0xB0), "double"): "panic"})
    eng4.feed(("hid", 0xB0), True, 600.0)
    eng4.feed(("hid", 0xB0), False, 600.008)        # 8ms 快踩：真松开 → 进窗
    assert hitsg4 == []                             # 等窗判定
    spawned[-1].cb()
    assert hitsg4 == ["play"]                       # 窗过期结算单踩
    eng4.feed(("hid", 0xB0), True, 601.0)           # 真按住：无长踩定时器
    n4 = len(spawned)
    eng4.feed(("hid", 0xB0), False, 601.6)          # 按住 0.6s 松开 → 进窗
    assert hitsg4 == ["play"] and len(spawned) == n4 + 1
    spawned[-1].cb()
    assert hitsg4 == ["play", "play"]               # 无第二踩：单踩
    # 学习器同款抖动闸：一次触点抖动不得学成双踩
    ln2 = object.__new__(pedal.Learner)
    ln2.cc = None
    ln2.events = []
    ln2.double_window = pedal.DOUBLE_WINDOW
    ln2._clock = lambda: 800.4
    ln2._raw_capture(0xB0, True, 800.0)
    ln2._raw_capture(0xB0, False, 800.008)          # 假 up：闸掉
    ln2._raw_capture(0xB0, True, 800.016)           # 密集重 down：闸掉
    ln2._raw_capture(0xB0, False, 800.3)            # 真松开
    ln2._clock = lambda: 800.7                      # 窗平静过期后判定
    assert ln2.result() == ("hid", "下一曲", 0xB0, "single")
    # 分断弹跳：回弹重压距上一松开 <30ms，不并成假双踩
    ln3 = object.__new__(pedal.Learner)
    ln3.cc = None
    ln3.events = []
    ln3.double_window = pedal.DOUBLE_WINDOW
    ln3._raw_capture(0xB0, True, 950.0)
    ln3._raw_capture(0xB0, False, 950.10)
    ln3._raw_capture(0xB0, True, 950.103)           # 3ms 回弹重压：闸掉
    ln3._raw_capture(0xB0, False, 950.3)            # 真松开
    ln3._clock = lambda: 950.8
    assert ln3.result() == ("hid", "下一曲", 0xB0, "single")
    # F1 回归：≥30ms 的迟到弹跳 down（引擎当按住重发、不后移 up 闸锚），
    # 学习器同语义——真 up 按「按压武装时刻」判龄，不误拆按压
    ln4 = object.__new__(pedal.Learner)
    ln4.cc = None
    ln4.events = []
    ln4.double_window = pedal.DOUBLE_WINDOW
    ln4._raw_capture(0xB0, True, 1000.0)
    ln4._raw_capture(0xB0, True, 1000.0308)         # 迟到弹跳重压：按住重发
    ln4._raw_capture(0xB0, False, 1000.053)         # 真 up（距武装 53ms）
    ln4._clock = lambda: 1000.5
    assert ln4.result() == ("hid", "下一曲", 0xB0, "single")
    # F2 回归：点学习时脚已在踏板上——游离首 up 不入账，双踩照常可学
    ln5 = object.__new__(pedal.Learner)
    ln5.cc = None
    ln5.events = []
    ln5.double_window = pedal.DOUBLE_WINDOW
    ln5._raw_capture(0xB0, False, 1100.0)           # 游离首 up：不入账
    ln5._raw_capture(0xB0, True, 1100.2)
    ln5._raw_capture(0xB0, False, 1100.3)
    ln5._raw_capture(0xB0, True, 1100.5)            # 距真松开 0.2s：双踩
    ln5._clock = lambda: 1100.6
    assert ln5.result() == ("hid", "下一曲", 0xB0, "double")
    # F3 回归：学习器 MIDI 同款迟滞——阈值附近振荡零假边沿（按压不拆碎）
    ln6 = object.__new__(pedal.Learner)
    ln6.cc = None
    ln6.events = []
    ln6.double_window = pedal.DOUBLE_WINDOW
    f = ln6._make("T")
    f(0xB0, 4, 70)                                  # 按下
    for v in (60, 70, 60, 70):                      # 44-63 悬停振荡
        f(0xB0, 4, v)
    assert ln6.events == [(("midi", 4), True, ln6.events[0][2])]
    f(0xB0, 4, 30)                                  # 真松开（真时刻入账）
    assert len(ln6.events) == 2                     # 一次干净按压、零假边沿
    # 游离 up 不后移抖动闸锚：其后 20ms 的真 down 仍受理（P3 回归）
    hitsga = []
    enga = pedal.GestureEngine(hitsga.append, spawn_timer=fake_spawn)
    enga.configure({(("hid", 0xB0), "single"): "play"})
    enga.feed(("hid", 0xB0), True, 1400.0)
    enga.feed(("hid", 0xB0), False, 1400.1)         # 正常松开（锚=1400.1）
    enga.feed(("hid", 0xB0), False, 1400.2)         # 游离 up：不后移锚
    assert enga.feed(("hid", 0xB0), True, 1400.22) is True   # 距锚 120ms
    enga.feed(("hid", 0xB0), False, 1400.5)
    assert hitsga == ["play", "play"]   # 受理的新踩松开照常触发（两次单踩）
    # MIDI 手势键迟滞：44-63 悬停区间振荡零假边沿（迟滞与抖动闸不失步）
    hitsm = []
    plm = pedal.PedalListener(hitsm.append)
    plm.apply("无此口", {"next": 4}, {}, "X", True,
              gestures={"next": "double"})
    plm._msg(0xB0, 4, 70)                           # 按下（≥64）
    time.sleep(0.05)                                # 按压时长
    for v in (60, 70, 60, 70):                      # 悬停区间振荡
        plm._msg(0xB0, 4, v)
    assert hitsm == []                              # 零假边沿
    time.sleep(0.02)                                # 按压时长 ≥30ms
    plm._msg(0xB0, 4, 0)                            # <44 才算松开
    time.sleep(0.06)                                # 双踩间隔
    plm._msg(0xB0, 4, 70)                           # 第二踩 → 双踩
    time.sleep(0.15)
    assert hitsm == ["next"]
    plm.shutdown()
    # 对照：纯单踩键仍走快路径零延迟（不受同库其他 temporal 键牵连）
    hits9 = []
    pl6 = pedal.PedalListener(hits9.append)
    pl6.apply("无此口", {}, {"play": 0xB0, "next": 0xB1}, "X", True,
              gestures={"play": "single", "next": "double"})
    assert pl6.bridge.binds == {0xB0: "play"}       # 0xB0 纯单踩=快路径
    pl6.bridge._feed(0xB1, True)                    # temporal 键：不即发
    time.sleep(0.05)
    assert hits9 == []
    pl6.bridge._feed(0xB0, True)                    # 纯单踩键：按下即发
    assert hits9 == ["play"]
    pl6.shutdown()

    # Learner hint 分支三元组（R3-P1 回归：三元组改造漏改本分支时，
    # 已选踩钉设备的学习主流程构造即 ValueError）。mock 枚举+open_in
    # 不触真设备（假口开不成→PortNotFound→跳过）。
    orig_in = pedal.mb._in_devices
    orig_open = pedal.mb.open_in
    pedal.mb._in_devices = lambda: [(0, "M-Vave"), (1, "loopMIDI Port")]
    pedal.mb.open_in = lambda idx, cb: (None, "mocked")
    try:
        lr = pedal.Learner("M-Vave", "", kb_ports=(("JUNO", 0),))
        assert lr.ports == []                # 构造本身不炸
    finally:
        pedal.mb._in_devices = orig_in
        pedal.mb.open_in = orig_open


def test_hotspot_logic():
    """ensure_on 状态机：未开→start→was_on=False；已开→只查→was_on=True。"""
    fake = {"ok": True, "on": False, "ssid": "X", "key": "K", "ip": None}
    calls = []

    def fake_run(mode, timeout):
        calls.append(mode)
        if mode == "start":
            fake["on"] = True
            fake["ip"] = "192.168.137.1"
        return dict(fake)       # state：返回当前 fake（start 后即 on）

    orig = hotspot._run
    hotspot._run = fake_run
    try:
        st = hotspot.ensure_on()
        assert st["ok"] and st["on"] and st["was_on"] is False and st["ip"]
        calls.clear()
        st = hotspot.ensure_on()
        assert st["was_on"] is True and calls == ["state"]
    finally:
        hotspot._run = orig

    # 结构化失败透传
    hotspot._run = lambda mode, timeout: {"ok": False, "err": "无网卡"}
    try:
        assert hotspot.ensure_on().get("err") == "无网卡"
    finally:
        hotspot._run = orig


def test_hotspot_script():
    """PS 脚本语法可编译（CI/本机都跑）；state 查询只读，失败也须结构化。"""
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8-sig") as f:
        f.write(hotspot._PS)
        path = f.name
    try:
        import subprocess
        ok = False
        detail = ""
        for attempt in range(2):    # 本机突发窗口下 CreateProcess 偶发
            try:                    #   WinError 50/6（EDR 交互），重试一次
                p = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "[void][scriptblock]::Create((Get-Content -Raw "
                     "-LiteralPath '%s')); 'SYNTAX_OK'" % path],
                    capture_output=True, timeout=90,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = b"SYNTAX_OK" in p.stdout
                detail = p.stderr.decode("utf-8", "replace")
            except OSError as e:
                detail = str(e)
            if ok:
                break
            time.sleep(0.5)
        assert ok, detail
    finally:
        os.unlink(path)
    r = hotspot._run("state", timeout=60)
    assert isinstance(r, dict) and isinstance(r.get("ok"), bool), r


class _FakeWebApp:
    """web_remote 需要的 App 侧接口：calls/q 队列 + 状态属性 + 控制方法。"""

    def __init__(self):
        self.calls = queue.Queue()
        self.q = queue.Queue()
        self.calls_urgent = queue.Queue()   # App.urgent 替身通道
        self.web_cfg = dict(web_remote.DEFAULT_WEB_REMOTE)
        self.switch_confirm = True
        self.cur = None
        self.ctrl = None
        self.watch = None
        self.pl_keys = ["A队/歌一", "B队/歌二"]
        self.by_key = {"A队/歌一": {"name": "歌一"},
                       "B队/歌二": {"name": "歌二"}}
        self.durations = {"A队/歌一": 213.0}
        self._web_snap = {}
        self.done = []
        # 远程踩钉转发（/pedal/event）所需状态
        self.pedal = None
        self.pedal_remote_enabled = False
        self._pedal_seq = {}
        self._pedal_rate = {}
        self._pedal_gwin = [0.0, 0]
        self._pedal_gwin_lock = threading.Lock()
        self.inj = []

    def pedal_remote_inject(self, payload):
        self.inj.append(payload)
        return {"ok": True}

    def _transport(self, a):
        self.done.append(("transport", a))

    def _next(self):
        self.done.append("next")

    def _prev(self):
        self.done.append("prev")

    def _panic(self):
        self.done.append("panic")

    def _switch(self, i, via, play_after=False):
        self.done.append(("switch", i, via))

    def _persist_web_remote(self):
        pass                        # 测试里绝不写真 config.json

    def urgent(self, fn):
        """App.urgent 替身：紧急调用（全停）走独立通道。"""
        self.calls_urgent.put(fn)


class _FakeDevice(BaseHTTPRequestHandler):
    """假翻谱设备：收 /turn 推送并记录 body。"""
    hits = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        _FakeDevice.hits.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


_LOOPBACK = "127.0.0.1"     # 测试请求只打本机回环：主机名钉死，端口/路径显式校验


def _http_req(method, port, path, body=None):
    """测试专用请求：主机字面量钉死回环，端口/路径显式校验后直连。"""
    assert isinstance(port, int) and 0 < port < 65536
    assert isinstance(path, str) and path.startswith("/")
    conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=3)
    try:
        conn.request(method, path, body=body,
                     headers={"Content-Type": "application/json"}
                     if body else {})
        r = conn.getresponse()
        data = r.read().decode("utf-8")
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, {"raw": data}
    finally:
        conn.close()


def _http_get(port, path):
    return _http_req("GET", port, path)


def _http_post(port, path, obj=None, raw=None):
    return _http_req("POST", port, path,
                     raw if raw is not None else json.dumps(obj or {}))


def _start_fake_device():
    srv = web_remote.ThreadingHTTPServer((_LOOPBACK, 0), _FakeDevice)
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv, srv.server_address[1]


def test_web_api():
    """HTTP API 全链路（离线）：state/cmd/claim/update/test + 假设备收包。"""
    app = _FakeWebApp()
    app._web_snap = web_remote.build_snapshot(app, True, "歌一")
    thsrv = _start_fake_device()         # 假翻谱设备（回环随机端口）
    reg = web_remote.DeviceRegistry([], app.q.put)
    srv = web_remote.WebServer((_LOOPBACK, 0), app, reg, lambda: thsrv[1])
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        # 页面可取、APK 下载桩验证、未知路径 404
        assert _http_get(port, "/")[0] == 200
        real_apk = web_remote._apk_file
        web_remote._apk_file = lambda: b"PKxx"
        try:
            code, j = _http_get(port, "/app.apk")
            assert code == 200 and j["raw"].startswith("PK")
        finally:
            web_remote._apk_file = real_apk
        web_remote._apk_file = lambda: None
        try:
            assert _http_get(port, "/app.apk")[0] == 404
        finally:
            web_remote._apk_file = real_apk
        assert _http_get(port, "/nope")[0] == 404
        for icon_path in ("/favicon.ico", "/apple-touch-icon.png"):
            conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=3)
            conn.request("GET", icon_path)
            r = conn.getresponse()
            raw = r.read()
            conn.close()
            assert r.status == 200 and raw[:4] == b"\x89PNG", icon_path
        # 双版页面：浏览器版无翻谱面板、APP 版承载翻谱设置
        assert "/app.apk" in web_remote.PAGE_BROWSER
        assert "翻谱设置" not in web_remote.PAGE_BROWSER
        assert "翻谱设置" in web_remote.PAGE_APP
        assert "取消登记" in web_remote.PAGE_APP
        # /state 快照
        code, snap = _http_get(port, "/state")
        assert code == 200 and snap["open"] and snap["confirm"]
        assert snap["projName"] == "歌一"
        assert snap["tstate"] == "stopped"   # 假 app 无 watch → 未在播放
        assert snap["pos"] == 0 and snap["dur"] == 0
        assert snap["songs"][0]["name"] == "歌一"
        assert snap["songs"][0]["dur"] == "3:33"
        # NOW/弹窗「从」名用真实工程名（对齐 PC 横幅），不再按 cur 查《？》
        assert "st.projName" in web_remote.PAGE_COMMON
        # /cmd：入队后在"主线程"执行
        assert _http_post(port, "/cmd", {"action": "next"})[1]["ok"]
        app.calls.get_nowait()()
        assert app.done == ["next"]
        assert _http_post(port, "/cmd",
                          {"action": "switch", "index": 1})[0] == 200
        app.calls.get_nowait()()
        assert app.done == ["next", ("switch", 1, "手动")]
        # 非法输入
        assert _http_post(port, "/cmd",
                          {"action": "switch", "index": 9})[0] == 400
        assert _http_post(port, "/cmd",
                          {"action": "switch", "index": True})[0] == 400
        assert _http_post(port, "/cmd", {"action": "wat"})[0] == 400
        assert _http_post(port, "/cmd", raw=b"not json")[0] == 400
        # 切换中：普通动作 409，全停放行
        app._web_snap = dict(app._web_snap, busy=True)
        assert _http_post(port, "/cmd", {"action": "play"})[0] == 409
        assert _http_post(port, "/cmd", {"action": "panic"})[0] == 200
        app.calls_urgent.get_nowait()()     # 全停走紧急通道（App.urgent）
        assert app.done[-1] == "panic"
        app._web_snap = dict(app._web_snap, busy=False)
        # 设备认领：自动槽位 + IP 来自连接 + 幂等
        code, j = _http_post(port, "/device/claim",
                             {"name": "主谱台", "screen": {"w": 1280, "h": 800}})
        assert code == 200 and j["device"]["slot"] == 1
        assert j["device"]["ip"] == _LOOPBACK
        assert len(reg.snapshot()) == 1
        code, j = _http_post(port, "/device/claim", {"name": "主谱台2"})
        assert len(reg.snapshot()) == 1 and j["device"]["name"] == "主谱台2"
        # 改名字 + 测试推送（假设备收包）：语义协议 body 只有 dir+test，
        # 翻页方法与坐标组装在 APP 端本机设置里（/device/test 是测试按钮
        # → test:1，APP 端先切后台再执行）
        assert _http_post(port, "/device/update", {"name": "主谱台X"})[0] == 200
        assert reg.snapshot()[0]["name"] == "主谱台X"
        _FakeDevice.hits = []
        assert _http_post(port, "/device/test", {"dir": "next"})[0] == 200
        time.sleep(0.5)
        assert _FakeDevice.hits == [{"dir": "next", "test": 1}]
        _FakeDevice.hits = []
        assert _http_post(port, "/device/test", {"dir": "prev"})[0] == 200
        time.sleep(0.5)
        assert _FakeDevice.hits == [{"dir": "prev", "test": 1}]
        assert _http_post(port, "/device/test", {"dir": "bad"})[0] == 400
        # 取消登记：记录删除、幂等二次 400
        assert _http_post(port, "/device/unregister", {})[0] == 200
        assert len(reg.snapshot()) == 0
        assert _http_post(port, "/device/unregister", {})[0] == 400
        # 未认领设备不能改
        reg2 = web_remote.DeviceRegistry([], app.q.put)
        srv2 = web_remote.WebServer((_LOOPBACK, 0), app, reg2,
                                    lambda: thsrv[1])
        port2 = srv2.server_address[1]
        threading.Thread(target=srv2.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        try:
            assert _http_post(port2, "/device/update",
                              {"name": "x"})[0] == 400
        finally:
            srv2.shutdown()
            srv2.server_close()
    finally:
        srv.shutdown()
        srv.server_close()
        thsrv[0].shutdown()
        thsrv[0].server_close()


def test_bind_retry():
    """热点 IP 未就位（bind 报 WinError 10049）应限期重试而非放弃。"""
    real = web_remote.WebServer
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(10049, "在其上下文中，该请求的地址无效")
        return real(*a, **k)

    web_remote.WebServer = flaky
    try:
        srv = web_remote._bind_web(None, None, lambda: 0, _LOOPBACK, 0,
                                   web_remote.PAGE_BROWSER)
        srv.server_close()
        assert len(calls) == 2, calls
    finally:
        web_remote.WebServer = real


def test_webremote_lifecycle():
    """总开关生命周期：开=起服务；关=服务停；热点按所有权关。
    hotspot 打桩，服务绑 127.0.0.1，翻谱端口未配=优雅降级。"""
    st = {"was_on": False, "stops": []}

    def fake_ensure():
        return {"ok": True, "on": True, "was_on": st["was_on"],
                "ssid": "S", "key": "K", "ip": _LOOPBACK}

    def fake_stop():
        st["stops"].append(1)
        return {"ok": True}

    orig = (hotspot.ensure_on, hotspot.stop)
    hotspot.ensure_on, hotspot.stop = fake_ensure, fake_stop
    app = _FakeWebApp()
    try:
        wr = web_remote.WebRemote(
            app, {"enabled": True, "serverPort": 0, "taskerPort": 8766,
                  "midiIn": "", "devices": []})
        wr.apply()                      # was_on=False → 热点是我们开的
        assert wr.server is not None and wr.hotspot_owner
        assert _http_get(wr.server.server_address[1], "/state")[0] == 200
        # 总开关关：服务停、热点按所有权关闭
        wr.apply(enabled=False)
        assert wr.server is None and wr.hotspot_owner is False
        assert st["stops"] == [1]
        # 热点开不出来：整组降级且不崩
        hotspot.ensure_on = lambda: {"ok": False, "err": "无网卡"}
        wr = web_remote.WebRemote(
            app, {"enabled": True, "serverPort": 0, "taskerPort": 8766,
                  "midiIn": "", "devices": []})
        wr.apply()
        assert wr.server is None
        assert any("无网卡" in str(m) for m in list(app.q.queue))
    finally:
        hotspot.ensure_on, hotspot.stop = orig


def test_score_combo():
    """判定矩阵（设计第五节）：恰一命令+≥1设备=合法；其余非法。"""
    f = web_remote.combo_evaluate
    assert f({36, 48}) == (36, [48])
    assert f({37, 50, 57}) == (37, [50, 57])
    assert f({36, 48, 49, 57}) == (36, [48, 49, 57])   # 窗口内多设备
    for bad in ({36}, {48, 57}, {36, 37, 48}, {37, 60}):
        try:
            f(bad)
            raise AssertionError("应判非法：%r" % bad)
        except ValueError:
            pass


def test_score_push_ip():
    """推送收口：只认私网/环回点分 IPv4，公网/链路本地/主机名全拒。"""
    ok = web_remote.valid_push_ip
    assert ok("192.168.137.2") and ok("10.0.0.5") and ok("127.0.0.1")
    assert ok("172.16.1.1") and ok("172.31.255.255")
    for bad in ("8.8.8.8", "169.254.169.254", "172.32.0.1",
                "abc", "", None, "300.1.1.1", "192.168.1", "pad.local"):
        assert not ok(bad), bad


def test_score_window():
    """归并窗口：同窗归并去重、跨窗成新组合、非法组合只报不推。"""
    now = [0.0]
    pushed, reports = [], []
    reg = web_remote.DeviceRegistry(
        [{"slot": 1, "name": "A", "ip": "192.168.137.2",
          "method": "single", "enabled": True, "screen": {"w": 1000, "h": 500}},
         {"slot": 2, "name": "B", "ip": "192.168.137.3",
          "enabled": False, "screen": {"w": 800, "h": 600}}],
        reports.append)
    hub = web_remote.ScoreTurnHub(reg, lambda: 8766, reports.append,
                                  clock=lambda: now[0])
    orig = web_remote.push_async
    web_remote.push_async = lambda dev, d, port, rep: pushed.append(
        (dev["name"], d))
    try:
        # 同窗 36+48+49 → A 上一页；B（音符49/槽位2）停用跳过
        hub.submit(36)
        time.sleep(0.04)
        now[0] = 0.05
        hub.submit(48)
        hub.submit(49)
        time.sleep(0.25)
        assert pushed == [("A", "prev")], pushed
        assert any("停用" in m for m in reports)
        # 窗口外重复命令 = 新组合；37 → 下一页
        now[0] = 0.5
        hub.submit(37)
        hub.submit(48)              # 去重：同窗重复设备音符只发一次
        hub.submit(48)
        time.sleep(0.25)
        assert pushed == [("A", "prev"), ("A", "next")], pushed
        # 非法：仅设备音符 / 36+37 同发
        now[0] = 1.0
        hub.submit(48)
        time.sleep(0.25)
        now[0] = 1.5
        hub.submit(36)
        hub.submit(37)
        time.sleep(0.25)
        assert pushed == [("A", "prev"), ("A", "next")]
        assert sum("非法" in m for m in reports) >= 2
    finally:
        web_remote.push_async = orig
        hub.close()


def test_pedal_remote():
    """远程踩钉转发：LKC/AKC 双表映射 + DeviceBridge.inject（重定基准注入/
    学习捕获/静音/跨源去重/pending 隔离）+ RemoteClock 跨包锚定 +
    HTTP /pedal/event 端点 + /state 快照 pedalRemote。"""
    # ---- 键码映射：期望值取自 AOSP《Keyboard devices》码表与 Windows VK
    # 常量（外部知识），不取自被测表自身——表错测试必须红 ----
    # scanCode = Linux 键码（getScanCode 实况）：
    assert pedal.remote_key_to_vk(164, 85) == 0xB3   # KEY_PLAYPAUSE → 播放暂停
    assert pedal.remote_key_to_vk(163, 87) == 0xB0   # KEY_NEXTSONG → 下一曲
    assert pedal.remote_key_to_vk(165, 88) == 0xB1   # KEY_PREVIOUSSONG → 上一曲
    assert pedal.remote_key_to_vk(166, 86) == 0xB2   # KEY_STOPCD → 停止
    assert pedal.remote_key_to_vk(104, 92) == 0x21   # KEY_PAGEUP → 上翻页
    assert pedal.remote_key_to_vk(109, 93) == 0x22   # KEY_PAGEDOWN → 下翻页
    assert pedal.remote_key_to_vk(28, 66) == 0x0D    # KEY_ENTER → 回车
    assert pedal.remote_key_to_vk(1, 111) == 0x1B    # KEY_ESC
    assert pedal.remote_key_to_vk(14, 67) == 0x08    # KEY_BACKSPACE
    assert pedal.remote_key_to_vk(15, 61) == 0x09    # KEY_TAB
    assert pedal.remote_key_to_vk(57, 62) == 0x20    # KEY_SPACE
    assert pedal.remote_key_to_vk(30, 29) == 0x41    # KEY_A → 'A'（LKC 30 非 usage '1'）
    assert pedal.remote_key_to_vk(2, 8) == 0x31      # KEY_1 → '1'（LKC 2 非 usage '2'）
    assert pedal.remote_key_to_vk(11, 7) == 0x30     # KEY_0 → '0'
    assert pedal.remote_key_to_vk(59, 131) == 0x70   # F1
    assert pedal.remote_key_to_vk(68, 140) == 0x79   # F10
    assert pedal.remote_key_to_vk(87, 143) == 0x7A   # LKC 87=F11（≠AKC 87=下一曲）
    assert pedal.remote_key_to_vk(183, 143) == 0x7C  # F13（kc=143 是 NumLock，
    assert pedal.remote_key_to_vk(None, 143) is None  #   绝不映射成 F13——
    assert pedal.remote_key_to_vk(None, 149) is None  #   小键盘键码非 F 键）
    assert pedal.remote_key_to_vk(None, 326) == 0x7C  # AKC F13（Android 16+）
    assert pedal.remote_key_to_vk(None, 337) == 0x87  # AKC F24
    assert pedal.remote_key_to_vk(194, None) == 0x87  # LKC F24
    assert pedal.remote_key_to_vk(113, 164) == 0xAD  # KEY_MUTE → 静音
    assert pedal.remote_key_to_vk(115, 24) == 0xAF   # KEY_VOLUMEUP
    assert pedal.remote_key_to_vk(119, 121) == 0x13  # KEY_PAUSE/BREAK → VK_PAUSE
    # scanCode 不可用时按 Android keycode 兜底（个别栈 scanCode 报 0）：
    assert pedal.remote_key_to_vk(None, 85) == 0xB3
    assert pedal.remote_key_to_vk(None, 92) == 0x21
    assert pedal.remote_key_to_vk(None, 29) == 0x41
    assert pedal.remote_key_to_vk(None, 7) == 0x30
    assert pedal.remote_key_to_vk(None, 131) == 0x70
    # 命名空间隔离：LKC 87=F11，绝不能落进 AKC 87=下一曲的映射
    assert pedal.remote_key_to_vk(87, None) == 0x7A
    assert pedal.remote_key_to_vk(True, None) is None
    assert pedal.remote_key_to_vk(0x999, 0x999) is None

    # ---- RemoteClock：跨包锚定（APP 单调时戳差值外推，抖动/重试不进窗） ----
    clk = pedal.RemoteClock()
    t0 = 100.0
    assert clk.anchor(10000, t0) == t0              # 首包锚在到达时刻
    assert clk.anchor(10200, t0 + 0.5) == t0 + 0.2  # +200ms 外推（到达抖动 0.3s 被剔）
    assert clk.anchor(10500, t0 + 1.0) == t0 + 0.5  # 累计单调
    assert clk.anchor(4000, t0 + 2.0) == t0 + 2.0   # et 回跳=APP 重启：重锚在到达时刻
    clk2 = pedal.RemoteClock()
    assert clk2.anchor(None, 7.0) == 7.0            # et 缺失退回到达时刻
    assert clk2.anchor(True, 8.0) == 8.0            # bool 拒收

    # ---- inject：单踩快路径（重定基准时间戳，去抖一致） ----
    hits = []
    br = pedal.DeviceBridge(hits.append)
    br.configure(binds={0xB0: "next"}, device_hint="X")
    now = time.monotonic()
    assert br.inject(0xB0, True, now - 0.010)       # down 沿（10ms 前）即触发
    assert hits == ["next"]
    assert br.inject(0xB0, False, now)              # up 沿照常喂入（返回值≠触发）
    assert hits == ["next"]
    br.inject(0xB0, True, now + 0.05)               # 去抖窗内第二踩：喂入但不触发
    assert hits == ["next"]
    br.inject(0xB0, False, now + 0.06)              # 松开沿归位（真实序列必有 up）
    assert hits == ["next"]
    time.sleep(0.16)
    assert br.inject(0xB0, True)                    # t 缺省=当下；窗后可再触发
    assert hits == ["next", "next"]

    # 跨源去重：同键 down 沿距本地蓝牙沿 <0.15s → 丢弃远程份（双输出保险）
    hits4 = []
    br4 = pedal.DeviceBridge(hits4.append)
    br4.configure(binds={0xB0: "next"}, device_hint="X")
    br4._last_hid_down = (0xB0, time.monotonic() - 0.05)
    assert not br4.inject(0xB0, True)
    assert hits4 == [] and br4.remote_event_t == 0.0  # 被去重不算事件（健康口径）
    br4._last_hid_down = (0xB0, time.monotonic() - 0.5)
    assert br4.inject(0xB0, True)
    assert hits4 == ["next"] and br4.remote_event_t > 0.0

    # 学习捕获：注入走 _feed 同层 → capture 直传（重定基准 t，包内间距保留）
    cap = []
    br.learning = True
    br.capture = lambda vk, d, t: cap.append((vk, d, t))
    base = time.monotonic()
    br.inject(0x0D, True, base - 0.012)
    br.inject(0x0D, False, base)
    assert [(v, d) for v, d, _t in cap] == [(0x0D, True), (0x0D, False)]
    assert 0.010 <= cap[1][2] - cap[0][2] <= 0.014  # 12ms 按压间距不被抹平
    # pending 隔离：拦截开启时本地蓝牙按下沿留有回执配对，远程 up 绝不
    # 消费它合成出从未发生的按压对（P2-7）
    br.capture = lambda vk, d, t: cap.append((vk, d, t))
    br._pending[0x0D] = time.monotonic()            # 伪造本地热键回执
    br.inject(0x0D, False, base)
    assert cap[-1] == (0x0D, False, base)           # 直传，未配对成按压对
    br.learning = False
    br.capture = None

    # 静音：注入被丢（与本地路径同语义），事件时间戳仍更新（健康显示）
    hits5 = []
    br5 = pedal.DeviceBridge(hits5.append)
    br5.configure(binds={0xB0: "next"}, device_hint="X")
    br5.silent = True
    br5.inject(0xB0, True)
    assert hits5 == [] and br5.remote_event_t > 0.0

    # temporal 键：注入进引擎，包内 dt 重定基准供双踩窗判定
    hitsg = []

    class _FT:
        def __init__(s, delay, cb):
            s.delay, s.cb, s.dead = delay, cb, False

        def cancel(s):
            s.dead = True

    eng = pedal.GestureEngine(hitsg.append,
                              spawn_timer=lambda d, c: _FT(d, c))
    eng.configure({(("hid", 0xB0), "double"): "next",
                   (("hid", 0xB0), "single"): "play"})
    br6 = pedal.DeviceBridge(hitsg.append)
    br6.configure(binds={}, device_hint="X", engine=eng, temporal={0xB0})
    t1d = time.monotonic() - 0.2
    br6.inject(0xB0, True, t1d)                     # 第一踩
    br6.inject(0xB0, False, t1d + 0.012)
    assert hitsg == []                              # 等双踩窗判定
    br6.inject(0xB0, True)                          # 0.188s 后第二踩落下
    assert hitsg == ["next"]                        # 双踩即触发（单踩定时器被取消）

    # ---- HTTP /pedal/event 端点 ----
    app = _FakeWebApp()
    reg = web_remote.DeviceRegistry([], app.q.put)
    srv = web_remote.WebServer((_LOOPBACK, 0), app, reg, lambda: 8766)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        pkt = {"device": "tp", "seq": 5, "et": 12345,
               "events": [{"vk": 163, "kc": 87, "down": True, "dt": 0},
                          {"vk": 163, "kc": 87, "down": False, "dt": 12}]}
        # 开关关闭 → 403（转发端识别后停发）
        assert _http_post(port, "/pedal/event", pkt)[0] == 403
        app.pedal_remote_enabled = True
        code, j = _http_post(port, "/pedal/event", pkt)
        assert code == 200 and j["ok"] and len(app.inj) == 1
        assert app.inj[0]["events"][0]["vk"] == 163      # LKC 原样透传给 App 层
        assert app.inj[0]["et"] == 12345
        app._pedal_rate.clear()         # 逐包隔离 20ms 限流（不赌真实连发间隔）
        code, j = _http_post(port, "/pedal/event", dict(pkt))
        assert code == 200 and j.get("dup") is True and len(app.inj) == 1
        app._pedal_rate.clear()
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 6, "hb": True})
        assert code == 200 and j["ok"] and len(app.inj) == 2
        app._pedal_rate.clear()
        # seq 回退=APP 进程重启：不判 dup，作为新会话照常收包
        app._pedal_rate.clear()
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 2, "hb": True})
        assert code == 200 and not j.get("dup") and len(app.inj) == 3
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 2, "hb": True})
        assert code == 200 and j.get("dup") is True and len(app.inj) == 3
        app._pedal_rate.clear()
        # et 上界=JSON 安全整数 2^53（uptimeMillis 是 long）：长醒时设备
        # （累计 >24.8 天后 et 超 2^31）不得被拒——否则每脚 400 静默丢弃
        # 而心跳照常 200，链路绿灯踩钉全灭
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 13, "sid": "oldtab",
                              "et": 2 ** 31,
                              "events": [{"vk": 163, "kc": 87, "down": True,
                                          "dt": 0}]})
        assert code == 200 and j["ok"], (code, j)
        app._pedal_rate.clear()
        # 上界本身钉住：2^53（JSON 安全整数）放行、2^53+1 拒收
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 14, "sid": "oldtab",
                              "et": 2 ** 53,
                              "events": [{"vk": 163, "kc": 87, "down": True,
                                          "dt": 0}]})
        assert code == 200 and j["ok"], (code, j)
        code, j = _http_post(port, "/pedal/event",
                             {"device": "tp", "seq": 15, "sid": "oldtab",
                              "et": 2 ** 53 + 1,
                              "events": [{"vk": 163, "kc": 87, "down": True,
                                          "dt": 0}]})
        assert code == 400, (code, j)
        # 坏结构 400：vk/et 传 JSON true（bool 是 int 子类，必须显式拒）
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 7, "et": 1,
                               "events": [{"vk": True, "kc": 87, "down": True}]})
        assert code == 400
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 8, "et": True,
                               "events": [{"vk": 163, "kc": 87, "down": True}]})
        assert code == 400
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 9, "events": []})
        assert code == 400
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 10, "events":
                               [{"vk": 163, "kc": 87, "down": 1}]})
        assert code == 400
        # 限流 429：伪造成 20ms 内刚发过包（确定性，不赌真实连发间隔）
        app._pedal_rate.clear()
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 11, "hb": True})
        assert code == 200
        app._pedal_rate["tp"] = time.monotonic()
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "tp", "seq": 12, "hb": True})
        assert code == 429
        # 设备数上限：填满 32 席后第 33 个 device 起 429（防未知设备名刷爆
        # 去重/限流表）
        time.sleep(1.05)                # 越过全局限流窗（40 包/秒），隔离断言
        app._pedal_seq.clear()
        for i in range(32):
            code, _j = _http_post(port, "/pedal/event",
                                  {"device": "d%d" % i, "seq": 1, "hb": True})
            assert code == 200, i
        code, _j = _http_post(port, "/pedal/event",
                              {"device": "overflow", "seq": 1, "hb": True})
        assert code == 429
    finally:
        srv.shutdown()

    # ---- 生产路径 App.pedal_remote_inject 直测（绕过 Tk 构造） ----
    import setlist_gui as sg
    app3 = object.__new__(sg.App)
    app3.q = queue.Queue()
    hits7 = []
    br7 = pedal.DeviceBridge(hits7.append)
    br7.configure(binds={0xB0: "next"}, device_hint="X")
    app3.pedal = pedal.PedalListener.__new__(pedal.PedalListener)
    app3.pedal.bridge = br7
    # 媒体键实况值（LKC 163）→ VK_NEXT → next；et 锚定不抛错
    r = app3.pedal_remote_inject({"device": "tp", "et": 5000, "events": [
        {"vk": 163, "kc": 87, "down": True, "dt": 0},
        {"vk": 163, "kc": 87, "down": False, "dt": 10}]})
    assert r["ok"] and hits7 == ["next"]
    hb_before = br7.remote_hb_t
    app3.pedal_remote_inject({"device": "tp", "hb": True})
    assert br7.remote_hb_t >= hb_before
    n0 = br7.remote_unknown
    app3.pedal_remote_inject({"device": "tp", "et": 9000, "events": [
        {"vk": 555, "kc": 555, "down": True, "dt": 0},
        {"vk": 555, "kc": 555, "down": False, "dt": 5}]})
    assert br7.remote_unknown == n0 + 2 and hits7 == ["next"]   # 丢弃且计数
    # 跨包锚定：包间到达隔 ≥300ms，但 APP 时差 250ms——捕获到的 down/up
    # 间距=250ms（网络抖动/延迟不进手势窗）
    cap2 = []
    br7.learning = True
    br7.capture = lambda vk, d, t: cap2.append((vk, d, t))
    app3.pedal_remote_inject({"device": "tp", "et": 20000, "events": [
        {"vk": 163, "kc": 87, "down": True, "dt": 0}]})
    time.sleep(0.3)
    app3.pedal_remote_inject({"device": "tp", "et": 20250, "events": [
        {"vk": 163, "kc": 87, "down": False, "dt": 0}]})
    gap = cap2[1][2] - cap2[0][2]
    assert 0.24 <= gap <= 0.26, gap

    # ---- /state 快照 pedalRemote 三态 ----
    app2 = _FakeWebApp()
    app2._web_snap = web_remote.build_snapshot(app2, True, "歌一")
    assert app2._web_snap["pedalRemote"] is None    # 无 pedal 对象：字段缺省
    app2.pedal = type("P", (), {"bridge": pedal.DeviceBridge(None)})()
    snap = web_remote.build_snapshot(app2, True, "歌一")
    assert snap["pedalRemote"] == {"enabled": False, "link": "never",
                                   "unknown": 0, "lastEventAge": None}
    app2.pedal.bridge.note_remote_hb()
    app2.pedal.bridge.inject(0xB0, True)
    snap = web_remote.build_snapshot(app2, True, "歌一")
    pr = snap["pedalRemote"]
    assert pr["link"] == "ok" and 0.0 <= pr["lastEventAge"] <= 1.0
    app2.pedal.bridge.remote_unknown = 3
    app2.pedal.bridge.remote_hb_t = time.monotonic() - 20.0
    app2.pedal.bridge.remote_event_t = time.monotonic() - 20.0
    assert web_remote.build_snapshot(app2, True,
                                     "歌一")["pedalRemote"]["link"] == "stale"


if __name__ == "__main__":
    test_transport_sync()
    test_numbered_match()
    test_natural_sort()
    test_cpr_duration()
    test_advance_watch()
    test_project_title()
    test_kbd_auto()
    test_pedal()
    test_pedal_remote()
    test_score_combo()
    test_score_push_ip()
    test_score_window()
    test_hotspot_logic()
    test_web_api()
    test_bind_retry()
    test_webremote_lifecycle()
    test_hotspot_script()
    print("test_bridge：全部通过")
