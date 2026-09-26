# -*- coding: utf-8 -*-
"""Cube Setlist Manager 自检：python test_bridge.py。全 assert，无需 OBS/loopMIDI/Cubase 在场。"""
import http.client
import json
import os
import pathlib
import queue
import struct
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

import advance
import cpr_meta
import daw_ctrl
import hotspot
import kbd_auto
import midi_bridge as mb
import obs_ctrl
import pedal
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


def test_daw_backends():
    # 双底座事实表：Cubase 默认激活；S1 换表后标题解析跟随，用时还原
    assert daw_ctrl.ACTIVE is daw_ctrl.CUBASE
    assert daw_ctrl.FACTS["cubase"]["song_ext"] == ".cpr"
    s1 = daw_ctrl.FACTS["studioone"]
    assert s1["song_ext"] == ".song" and not s1["probe_duration"]
    assert set(s1["transport"]) == set(daw_ctrl.CUBASE["transport"])  # 动作齐
    daw_ctrl.set_active(s1)
    try:
        assert daw_ctrl.project_name_from_title("優しい彗星 — Studio One") \
            == "優しい彗星"
        assert daw_ctrl.project_name_from_title("记事本") is None
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
    assert sg.scan_library.__doc__  # 冒烟：库扫描签名可用


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
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[void][scriptblock]::Create((Get-Content -Raw -LiteralPath "
             "'%s')); 'SYNTAX_OK'" % path],
            capture_output=True, timeout=90)
        assert b"SYNTAX_OK" in p.stdout, p.stderr.decode("utf-8", "replace")
    finally:
        os.unlink(path)
    r = hotspot._run("state", timeout=60)
    assert isinstance(r, dict) and isinstance(r.get("ok"), bool), r


class _FakeWebApp:
    """web_remote 需要的 App 侧接口：calls/q 队列 + 状态属性 + 控制方法。"""

    def __init__(self):
        self.calls = queue.Queue()
        self.q = queue.Queue()
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
        app.calls.get_nowait()()
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


if __name__ == "__main__":
    test_transport_sync()
    test_numbered_match()
    test_natural_sort()
    test_cpr_duration()
    test_advance_watch()
    test_project_title()
    test_kbd_auto()
    test_pedal()
    test_score_combo()
    test_score_push_ip()
    test_score_window()
    test_hotspot_logic()
    test_web_api()
    test_bind_retry()
    test_webremote_lifecycle()
    test_hotspot_script()
    print("test_bridge：全部通过")
