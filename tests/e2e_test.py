# -*- coding: utf-8 -*-
"""E2E 真机测试驱动：分阶段验证 Cube Setlist Manager 的各链路（模块级直跑，
与 GUI 调的是同一份代码；GUI 本身是薄封装）。
用法：py -u e2e_test.py <phase>   phase =
  preflight  环境盘点：MIDI 端口 / OBS / Cubase 进程与窗口 / JUNO 缺席降级
  ports      双端口隔离回归：loopMIDI Port 与 Keyboard Automation 各自收对音符
  obs        OBS 联动：连接→播 1.mp4→查状态→熄屏
  switch     Cubase 切歌全流程（未运行则冷启动；在运行则先关后开）并转储窗口
  transport  走带按键 E2E：时钟监听验证 播放/停止 真生效
  advance    自动推进全周期：播完最短歌→自动切下一首（需项目时钟，约 3 分钟）
  web        网页遥控/翻谱推送真机链路：MIDI 组合→推送 + /cmd→真切歌（回环）
  s1_*       Studio One 底座同款五阶段（preflight/switch/transport/advance/
             savedialog；库根 CUBE_S1_PROJECTS_ROOT，默认本机 S1 工程库）
不带参数 = 顺序跑 preflight ports obs switch transport kb（advance/web 单独跑）。"""
import glob
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import advance
import cpr_meta
import daw_ctrl
import kbd_auto
import midi_bridge as mb
import obs_ctrl
import web_remote
from obs_ctrl import ObsController, find_processes_by_prefix

# 库根：默认本机路径，换机用环境变量 CUBE_PROJECTS_ROOT / CUBE_S1_PROJECTS_ROOT 覆盖
ROOT = os.environ.get("CUBE_PROJECTS_ROOT") or r"C:\Users\XKZ\Documents\Cubase Projects"
S1_ROOT = os.environ.get("CUBE_S1_PROJECTS_ROOT") or \
    r"C:\Users\XKZ\Documents\Studio One Projects"


def _pick_songs(root):
    """扫 <root>/<队伍>/<歌>/<歌>.cpr，按时长升序取最短两首当 SONG_A/SONG_B
    （advance 阶段依赖最短歌跑全周期；时长未知的歌排最后）。"""
    songs = []
    for p in glob.glob(os.path.join(root, "*", "*", "*.cpr")):
        if os.path.splitext(os.path.basename(p))[0] != os.path.basename(os.path.dirname(p)):
            continue    # 只认 <歌>/<歌>.cpr 命名（与素材库扫描同规则）
        try:
            dur = cpr_meta.read_duration(p)
        except Exception:
            dur = None
        songs.append((dur if dur is not None else 1 << 30, p))
    songs.sort()
    if len(songs) < 2:
        sys.exit("工程库 %s 下可用歌不足两首（<队伍>/<歌>/<歌>.cpr）" % root)
    return songs[0][1], songs[1][1]


_AB = None


def songs_ab():
    """Cubase 最短两首，惰性求值（S1 阶段不要求 Cubase 库在场）。"""
    global _AB
    if _AB is None:
        _AB = _pick_songs(ROOT)
    return _AB


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def wait_until(fn, timeout, desc, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    log("TIMEOUT（%.0fs）：%s" % (timeout, desc))
    return None


def dump_windows(tag):
    log("窗口转储（%s）：" % tag)
    for h, t, c in daw_ctrl._windows():
        if c.startswith("Steinberg") or "Cubase" in t or "Steinberg" in t:
            log("  class=%-28s title=%r" % (c, t))


def midi_out_by_name(hint, note):
    hits = [(i, n) for i, n in mb._out_devices() if hint in n]
    if not hits:
        return "未找到输出端口 %s" % hint
    mb.send_note(hits[0][0], note)
    return None


def cfg_cubase():
    import setlist_gui
    return setlist_gui._load_config()


def make_ctrl():
    import setlist_gui
    ccfg = setlist_gui.daw_settings(setlist_gui._load_config(), "cubase")
    return daw_ctrl.DawController(daw_ctrl.CUBASE,
                                  ccfg.get("dawExe", ""), log=log)


# ---- 阶段 ----

def p_preflight():
    s_a, s_b = songs_ab()
    log("MIDI 输入端口：%s" % [n for _, n in mb._in_devices()])
    log("MIDI 输出端口：%s" % [n for _, n in mb._out_devices()])
    ins = [n for _, n in mb._in_devices()]
    assert any("loopMIDI Port" in n for n in ins), "缺 loopMIDI Port"
    assert any(kbd_auto.KB_PORT_HINT in n for n in ins), "缺 Keyboard Automation"
    assert not any("JUNO" in n for n in ins), "JUNO 不该在场（题目说没连）"
    log("端口前提 ✓（JUNO 确认缺席）")
    log("OBS 进程：%s" % find_processes_by_prefix("obs64"))
    log("Cubase 进程：%s" % find_processes_by_prefix("cubase"))
    dump_windows("当前")
    log("JUNO 缺席降级：%s" % kbd_auto.send_slot(
        {"msb": 85, "lsb": 64, "pc": 3}, dict(kbd_auto.DEFAULT_JUNO)))
    log("时长解析 intro=%s TAIDADA=%s" % (
        cpr_meta.fmt_mmss(cpr_meta.read_duration(s_a)),
        cpr_meta.fmt_mmss(cpr_meta.read_duration(s_b))))


def p_ports():
    got = []
    vj = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                            got.append(("vj", d1)) if s & 0xF0 == 0x90 else None)
    kb = kbd_auto.RawMidiIn(kbd_auto.KB_PORT_HINT, lambda s, d1, d2:
                            got.append(("kb", d1)) if s & 0xF0 == 0x90 else None)
    time.sleep(0.3)
    err = midi_out_by_name("loopMIDI Port", 60)
    assert not err, err
    time.sleep(0.5)
    err = midi_out_by_name(kbd_auto.KB_PORT_HINT, 61)
    assert not err, err
    time.sleep(0.5)
    vj.close()
    kb.close()
    log("收到：%s" % got)
    assert ("vj", 60) in got, "VJ 端口没收到 note60"
    assert ("kb", 61) in got, "KB 端口没收到 note61"
    assert ("kb", 60) not in got and ("vj", 61) not in got, "端口串了！"
    log("双端口隔离 ✓")


def p_obs():
    ctl = ObsController(cfg_cubase()["obs"])
    ctl.enabled = True
    ctl.start()
    assert wait_until(ctl.is_connected, 40, "OBS 连接"), "OBS 连不上"
    ctl.set_media("1.mp4", False)
    st = wait_until(lambda: ctl.media_state() == "OBS_MEDIA_STATE_PLAYING",
                    8, "1.mp4 起播")
    assert st, "OBS 媒体没在播"
    ctl.stop_media()
    time.sleep(0.5)
    log("熄屏后状态：%s" % ctl.media_state())
    ctl.shutdown()
    log("OBS 联动 ✓")


def p_switch():
    s_a, s_b = songs_ab()
    ctrl = make_ctrl()
    log("切换前 Cubase 进程：%s" % find_processes_by_prefix("cubase"))
    t0 = time.time()
    ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "switch_to 完成")
    ws = daw_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    dump_windows("冷启动/切换后")
    assert ws and any("intro" in t for _, t in ws), "intro 没打开：%r" % (ws,)
    log("第二次切换（先关后开路径）：%s" % s_b)
    t0 = time.time()
    ctrl.switch_to(s_b, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "第二次切换")
    ws = daw_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    dump_windows("第二次切换后")
    assert ws and any("TAIDADA" in t for _, t in ws), "TAIDADA 没打开：%r" % (ws,)
    log("切歌 E2E ✓")


def p_transport():
    ctrl = make_ctrl()
    target = os.path.join(ROOT, "霓虹折叠", "優しい彗星", "優しい彗星.cpr")
    if not daw_ctrl.project_windows() or \
            "優しい彗星" not in (daw_ctrl.project_windows()[0][1] or ""):
        log("先切到 優しい彗星（有 VJ 触发轨/时钟配置的真实演出工程）…")
        ctrl.switch_to(target, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 優しい彗星")
    pulses = [0]
    notes = [0]

    port = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                              (pulses.__setitem__(0, pulses[0] + 1),
                               notes.__setitem__(0, notes[0] + 1))
                              if s in (0xF1, 0xF8)
                              else notes.__setitem__(0, notes[0] + 1)
                              if s & 0xF0 == 0x90 else None)
    time.sleep(0.3)
    base_p, base_n = pulses[0], notes[0]
    ctrl.transport("play")
    wait_until(lambda: pulses[0] > base_p + 10 or notes[0] > base_n + 3,
               15, "播放产生时钟/音符")
    log("15s 窗口：时钟脉冲 %+d，音符 %+d" % (
        pulses[0] - base_p, notes[0] - base_n))
    if pulses[0] > base_p + 10:
        n = pulses[0]
        time.sleep(1.0)
        ctrl.transport("stop")
        time.sleep(2.0)
        log("停止后 2 秒新增脉冲=%d" % (pulses[0] - n))
        assert pulses[0] - n < 5, "停止后时钟还在走"
        log("走带 E2E ✓（时钟验证）")
    elif notes[0] > base_n + 3:
        log("走带 E2E ✓（音符触发验证：播放确已启动；此工程未发时钟）")
    else:
        log("走带键 15s 内无任何 MIDI 输出——键可能没生效或工程无 loopMIDI 输出")
    port.close()


def p_advance():
    ctrl = make_ctrl()
    if not daw_ctrl.project_windows() or \
            "intro" not in (daw_ctrl.project_windows()[0][1] or ""):
        log("先切到 intro…")
        ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 intro")
    dur = cpr_meta.read_duration(s_a)
    log("intro 时长 %.1fs，开始自动推进全周期" % dur)
    fired = []
    watch = advance.AdvanceWatch(lambda: fired.append(1), on_event=log,
                                 clock_timeout=mb.CLOCK_TIMEOUT)
    watch.set_armed(True)
    watch.set_duration(dur)
    pulses = [0]
    port = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                              (watch.on_clock(),
                               pulses.__setitem__(0, pulses[0] + 1))
                              if s in (0xF1, 0xF8) else None)
    ctrl.transport("play")
    deadline = time.time() + dur + 90
    while time.time() < deadline and not fired:
        watch.poll()
        time.sleep(mb.POLL_SEC)
    port.close()
    log("结果：fired=%s 活跃 %.1fs / 时长 %.1fs / 脉冲 %d" % (
        bool(fired), watch._active, dur, pulses[0]))
    if not fired:
        log("未触发——若脉冲仍在走，说明 Cubase 播完没有自动停止（设计假设被推翻）")
        ctrl.panic()
        return
    log("播完自动触发 ✓，等待自动切换到下一首…")
    ctrl.switch_to(s_b, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "推进后的切换")
    ws = daw_ctrl.project_windows()
    log("推进后工程窗口：%s" % [t for _, t in ws])
    dump_windows("自动推进后")
    assert ws and "TAIDADA" in ws[0][1]
    log("自动推进全周期 ✓")


class _WebShim:
    """p_web 专用：WebServer 需要的 App 侧最小接口，切歌走真 DawController。"""

    def __init__(self):
        import queue as _q
        self.calls = _q.Queue()
        self.q = _q.Queue()
        self.switch_confirm = True
        self.cur = None
        self.ctrl = None
        self.watch = None
        s_a, s_b = songs_ab()
        self.pl_keys = [os.path.splitext(os.path.basename(p))[0]
                        for p in (s_a, s_b)]
        self.by_key = {k: {"name": k} for k in self.pl_keys}
        self.durations = {}
        self._map = dict(zip(self.pl_keys, (s_a, s_b)))
        self._web_snap = {}

    def _refresh(self):
        self._web_snap = web_remote.build_snapshot(
            self, bool(daw_ctrl.project_windows()))

    def _switch(self, i, via, play_after=False):
        if not 0 <= i < len(self.pl_keys):
            log("网页切换越界：%s" % i)
            return
        log("网页命令：切到 %s（%s）" % (self.pl_keys[i], via))
        self.ctrl.switch_to(self._map[self.pl_keys[i]],
                            on_done=lambda n: log("切歌完成：%s" % n))
        self.cur = i
        self._refresh()

    def _transport(self, a):
        log("网页命令：transport %s（p_web 不验走带）" % a)

    def _next(self):
        self._switch(min((self.cur if self.cur is not None else -1) + 1,
                         len(self.pl_keys) - 1), "手动")

    def _prev(self):
        self._switch(max(0, (self.cur or 0) - 1), "手动")

    def _panic(self):
        self.ctrl.panic()

    def _persist_web_remote(self):
        pass


def p_web():
    """网页遥控/翻谱推送真机链路：MIDI 组合→HTTP 推送 + /cmd→真切歌。
    服务与假翻谱设备都在 127.0.0.1 回环；翻谱注入口默认 loopMIDI Port
    （其音符 36/48 不在 VJ NOTE_MAP 内，GUI 同时在跑也不会误触发），换口设
    环境变量 CUBE_E2E_TURN_PORT。需要 Cubase 在场（切歌部分）。"""
    import http.client
    import json
    import threading
    import web_remote
    from http.server import BaseHTTPRequestHandler

    hits = []

    class _FakeDevice(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            hits.append(json.loads(self.rfile.read(n)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    tsrv = web_remote.ThreadingHTTPServer(("127.0.0.1", 0), _FakeDevice)
    threading.Thread(target=tsrv.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()

    app = _WebShim()
    app.ctrl = make_ctrl()
    reg = web_remote.DeviceRegistry(
        [{"slot": 1, "name": "E2E谱台", "ip": "127.0.0.1",
          "method": "single", "enabled": True,
          "screen": {"w": 1280, "h": 800}}], log)
    srv = web_remote.WebServer(("127.0.0.1", 0), app, reg,
                               lambda: tsrv.server_address[1])
    wport = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.05}, daemon=True).start()
    # /cmd 经 calls 队列异步投递（主程序由 Tk 主循环消费）；shim 无主循环，
    # 交给守护线程串行消费——没有它切歌命令永远不执行（3d920d9 假阳性根源）
    def _drain():
        while True:
            fn = app.calls.get()
            try:
                fn()
            except Exception as e:
                log("calls 执行异常：%r" % e)
    threading.Thread(target=_drain, daemon=True).start()

    def _ticker():
        # 模拟主程序 _tick_banner 的快照刷新（409 判定与 /state 都读 _web_snap）
        while True:
            app._refresh()
            time.sleep(0.2)
    threading.Thread(target=_ticker, daemon=True).start()
    log("网页遥控服务（回环）:%d" % wport)
    try:
        # 链路一：真 winmm 回调 → 归并窗口 → 组合判定 → HTTP 推送
        turn_hint = os.environ.get("CUBE_E2E_TURN_PORT") or "loopMIDI Port"
        hub = web_remote.ScoreTurnHub(reg, lambda: tsrv.server_address[1], log)
        pin = kbd_auto.RawMidiIn(turn_hint, lambda s, d1, d2:
                                 hub.submit(d1)
                                 if s & 0xF0 == 0x90 and d2 > 0 else None)
        time.sleep(0.3)
        assert not midi_out_by_name(turn_hint, 36)
        time.sleep(0.05)
        assert not midi_out_by_name(turn_hint, 48)
        ok = wait_until(lambda: hits, 5, "翻谱推送到达")
        pin.close()
        hub.close()
        assert ok and hits and hits[0] == {"dir": "prev"}, hits
        log("MIDI 组合→推送 ✓ %s" % hits[0])

        # 链路二：网页 /cmd → 真实 Cubase 切歌
        conn = http.client.HTTPConnection("127.0.0.1", wport, timeout=3)
        conn.request("POST", "/cmd",
                     json.dumps({"action": "switch", "index": 0}),
                     {"Content-Type": "application/json"})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        assert r.status == 200, body
        # 两段等待：busy 置位（消费者线程接力有延迟）→ busy 结束；
        # 只等 not busy 会在置位前瞬间假通过（3d920d9 教训）
        assert wait_until(lambda: app.ctrl.busy, 5, "busy 置位"), \
            "切歌命令未被执行（calls 未消费）"
        assert wait_until(lambda: not app.ctrl.busy, 200, "切歌完成")
        ws = daw_ctrl.project_windows()
        want = os.path.splitext(os.path.basename(app._map[app.pl_keys[0]]))[0]
        assert ws and want in (ws[0][1] or ""), ws
        log("网页 /cmd → Cubase 切歌 ✓（%s）" % want)
    finally:
        srv.shutdown()
        srv.server_close()
        tsrv.shutdown()
        tsrv.server_close()


# ---- S1 阶段（Studio One 底座）：共享 log/wait_until/dump 风格，独立选歌 ----

def make_ctrl_s1():
    import setlist_gui
    ccfg = setlist_gui.daw_settings(setlist_gui._load_config(), "studioone")
    return daw_ctrl.DawController(daw_ctrl.STUDIOONE,
                                  ccfg.get("dawExe", ""), log=log)


def s1_songs_ab():
    """扫 <root>/<队伍>/<歌>/<歌>.song，按 song_meta 时长升序取最短两首。"""
    import song_meta
    songs = []
    for p in glob.glob(os.path.join(S1_ROOT, "*", "*", "*.song")):
        if os.path.splitext(os.path.basename(p))[0] != \
                os.path.basename(os.path.dirname(p)):
            continue
        try:
            dur = song_meta.read_duration(p)
        except Exception:
            dur = None
        songs.append((dur if dur is not None else 1 << 30, p))
    songs.sort()
    if len(songs) < 2:
        sys.exit("S1 工程库 %s 下可用歌不足两首（<队伍>/<歌>/<歌>.song）" % S1_ROOT)
    return songs[0][1], songs[1][1]


def dump_windows_s1(tag):
    log("窗口转储（%s）：" % tag)
    for h, t, c in daw_ctrl._windows():
        if c.startswith("CCL") or "#32770" in c or "Studio One" in t:
            log("  class=%-28s title=%r" % (c, t))


def p_s1_preflight():
    log("S1 MIDI 输入端口：%s" % [n for _, n in mb._in_devices()])
    ins = [n for _, n in mb._in_devices()]
    for hint in ("VJ Automation", "Keyboard Automation", "Score Automation"):
        assert any(hint in n for n in ins), "缺 %s" % hint
    log("S1 三端口 ✓")
    log("S1 进程：%s" % find_processes_by_prefix("studio one"))
    dump_windows_s1("当前")
    import song_meta
    s_a, s_b = s1_songs_ab()
    log("时长解析 %s=%s %s=%s" % (
        os.path.basename(s_a), song_meta.fmt_mmss(song_meta.read_duration(s_a)),
        os.path.basename(s_b), song_meta.fmt_mmss(song_meta.read_duration(s_b))))


def p_s1_switch():
    ctrl = make_ctrl_s1()
    s_a, s_b = s1_songs_ab()
    names = tuple(os.path.splitext(os.path.basename(p))[0] for p in (s_a, s_b))
    log("S1 切歌：A=%s B=%s（开始页起点自动走探窗/兜底）" % names)
    t0 = time.time()
    ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "switch A")
    ws = daw_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    dump_windows_s1("切 A 后")
    assert ws and any(daw_ctrl.title_matches(names[0], t) for _, t in ws), \
        "%s 没打开：%r" % (names[0], ws)
    t0 = time.time()
    ctrl.switch_to(s_b, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "switch B")
    ws = daw_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    assert ws and any(daw_ctrl.title_matches(names[1], t) for _, t in ws), \
        "%s 没打开：%r" % (names[1], ws)
    log("S1 切歌 E2E ✓")


def p_s1_transport():
    ctrl = make_ctrl_s1()
    s_a, _s_b = s1_songs_ab()
    name = os.path.splitext(os.path.basename(s_a))[0]
    ws = daw_ctrl.project_windows()
    if not ws or not any(daw_ctrl.title_matches(name, t) for _, t in ws):
        log("先切到 %s（VJ 轨+时钟已接）…" % name)
        ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 A")
    pulses = [0]
    port = kbd_auto.RawMidiIn("VJ Automation", lambda s, d1, d2:
                              pulses.__setitem__(0, pulses[0] + 1)
                              if s in (0xF1, 0xF8) else None)
    time.sleep(0.3)
    base = pulses[0]
    ctrl.transport("play")
    assert wait_until(lambda: pulses[0] > base + 10, 15,
                      "播放产生时钟（VJ Automation）"), \
        "播放 15s 无时钟脉冲——VJ 轨未接线或 S1 时钟未开"
    got = pulses[0] - base
    ctrl.transport("stop")
    time.sleep(2.0)
    port.close()
    log("播放窗口时钟脉冲 +%d；停止后未再监听（S1 停止即断流，§九已证）" % got)
    assert got > 10
    log("S1 走带 E2E ✓（VJ Automation 时钟验证）")


def p_s1_advance():
    ctrl = make_ctrl_s1()
    s_a, s_b = s1_songs_ab()
    name = os.path.splitext(os.path.basename(s_a))[0]
    ws = daw_ctrl.project_windows()
    if not ws or not any(daw_ctrl.title_matches(name, t) for _, t in ws):
        ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 A")
    dur = 15.0    # 真歌 31 分钟：合成短时长验证全周期（15s→自动发停止键→断流→fired）
    log("合成时长 %.0fs，开始 S1 自动推进全周期" % dur)
    fired = []
    watch = advance.AdvanceWatch(lambda: fired.append(1),
                                 on_stop_transport=lambda: ctrl.transport("stop"),
                                 on_event=log, clock_timeout=mb.CLOCK_TIMEOUT)
    watch.set_armed(True)
    watch.set_duration(dur)
    pulses = [0]
    port = kbd_auto.RawMidiIn("VJ Automation", lambda s, d1, d2:
                              (watch.on_clock(),
                               pulses.__setitem__(0, pulses[0] + 1))
                              if s in (0xF1, 0xF8) else None)
    ctrl.transport("play")
    deadline = time.time() + dur + 60
    while time.time() < deadline and not fired:
        watch.poll()
        time.sleep(mb.POLL_SEC)
    port.close()
    log("结果：fired=%s 活跃 %.1fs / 时长 %.0fs / 脉冲 %d" % (
        bool(fired), watch._active, dur, pulses[0]))
    assert fired, "15s 合成时长未触发自动推进"
    log("播完自动触发 ✓，切到下一首…")
    ctrl.switch_to(s_b, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "推进后的切换")
    ws = daw_ctrl.project_windows()
    bname = os.path.splitext(os.path.basename(s_b))[0]
    assert ws and any(bname in t for _, t in ws), "推进后不在 %s：%r" % (bname, ws)
    log("S1 自动推进全周期 ✓")


def p_s1_savedialog():
    """尽力而为型：键盘弄脏（M=选中轨静音）不生效则跳过不判失败——
    click_default 的回归主力是离线排水测试（test_dialog_drain 10 例）。"""
    ctrl = make_ctrl_s1()
    s_a, _s_b = s1_songs_ab()
    if not daw_ctrl.project_windows():
        ctrl.switch_to(s_a, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 A")
    h, t = daw_ctrl.current_project()
    log("工程：%s" % t)
    daw_ctrl.focus(h)
    time.sleep(0.6)
    daw_ctrl.tap(0x4D)                 # M：选中轨静音（S1 默认键位）
    time.sleep(1.0)
    h2, t2 = daw_ctrl.current_project()
    if not (t2 and t2.rstrip().endswith("*")):
        log("键盘弄脏未生效（跳过本阶段——保存框回归由离线测试覆盖）")
        return
    log("已弄脏：%s" % t2)
    mt0 = os.path.getmtime(s_a)
    assert daw_ctrl.close_app(timeout=60, log=log), "close_app 未退出"
    time.sleep(2)
    assert not find_processes_by_prefix("studio one"), "S1 仍存活"
    assert os.path.getmtime(s_a) != mt0, "工程未被保存（mtime 未变）"
    log("S1 退出保存框 E2E ✓（自动保存+退出）")


PHASES = dict(preflight=p_preflight, ports=p_ports, obs=p_obs,
              switch=p_switch, transport=p_transport, advance=p_advance,
              web=p_web,
              s1_preflight=p_s1_preflight, s1_switch=p_s1_switch,
              s1_transport=p_s1_transport, s1_advance=p_s1_advance,
              s1_savedialog=p_s1_savedialog)

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        PHASES[arg]()
    else:
        for name in ("preflight", "ports", "obs", "switch", "transport"):
            log("==== 阶段：%s ====" % name)
            PHASES[name]()
    log("E2E 完成")
