# -*- coding: utf-8 -*-
"""全局移调回归：触发轨控制器直设（绝对值）/ 增量音符兼容 / 同值重发限流 /
跨工程归零（切歌收尾、手动跟随、全停）/ 网页步进端点。桩 App=
types.SimpleNamespace + 假收发器（只记提交），真实现经
setlist_gui.App.<方法>(app, …) 调起（test_manual_adopt 同款）。"""
import http.client
import json
import pathlib
import queue
import sys
import threading
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import kbd_auto
import setlist_gui as sg
import web_remote

LOOP = "127.0.0.1"


class _Q(list):
    put = list.append


class _Sw:
    """假收发器：只记提交。"""

    def __init__(self):
        self.shifts, self.msgs, self.bumps = [], [], 0

    def submit_shift(self, total, why="", quiet=False):
        self.shifts.append((total, quiet))

    def submit_msgs(self, msgs, why="", quiet=False):
        self.msgs.append(msgs)

    def bump(self):
        self.bumps += 1


def _app():
    app = types.SimpleNamespace()
    app.q = _Q()
    app.ctrl = None
    app.juno_shift = 0
    app.ax_shift = 0
    app._juno_shift_at = 0.0
    app.jcfg = dict(kbd_auto.DEFAULT_JUNO)
    app.axcfg = dict(kbd_auto.DEFAULT_AX)
    app.switcher, app.ax_switcher = _Sw(), _Sw()
    app.slots, app.ax_slots = {}, {}
    app.cur_song_path = "/p1"
    app.cur_point_id = None
    app.cur = 0
    app._load_lock = threading.Lock()
    app._load_gen = 0
    # SimpleNamespace 无类方法解析：显式绑定真实现（test_manual_adopt 同款）
    for name in ("_juno_shift_set", "_ax_shift_set", "_shift_reset",
                 "_shift_step"):
        app.__dict__[name] = (lambda *a, _n=name, **kw:
                              getattr(sg.App, _n)(app, *a, **kw))
    return app


def _cc(app, cc, val, ch=0):
    sg.App._on_kb_msg(app, 0xB0 | ch, cc, val)


def _note(app, note, on=True):
    sg.App._on_kb_msg(app, 0x90 if on else 0x80, note, 100 if on else 0)


def _aged(app):
    """上次发出时刻拨到限流窗之前。"""
    app._juno_shift_at = time.monotonic() - sg.SHIFT_RESEND_SEC - 0.01


# ---- 纯换算 ----

def test_cc_shift_mapping():
    assert kbd_auto.cc_shift(kbd_auto.JUNO_SHIFT_CC, 64) == ("juno", 0)
    assert kbd_auto.cc_shift(kbd_auto.JUNO_SHIFT_CC, 78) == ("juno", 14)
    assert kbd_auto.cc_shift(kbd_auto.AX_SHIFT_CC, 52) == ("ax", -12)
    assert kbd_auto.cc_shift(64, 66) is None            # 延音等他人控制器
    assert kbd_auto.cc_shift(16, 127) == ("juno", 63)   # 只换算，钳位在调用方


# ---- 控制器直设：绝对值；旧音符仍是增量 ----

def test_cc_sets_absolute_value():
    app = _app()
    _cc(app, 16, 66)
    assert app.juno_shift == 2 and app.switcher.shifts == [(2, False)]
    _cc(app, 16, 64)                                   # 段尾写回 64=归零
    assert app.juno_shift == 0 and app.switcher.shifts[-1] == (0, False)
    _cc(app, 16, 127)                                  # 越界钳位
    assert app.juno_shift == 24
    _cc(app, 16, 0)
    assert app.juno_shift == -24
    assert app.ax_shift == 0 and app.ax_switcher.msgs == []


def test_cc_channel_ignored_and_other_cc_dropped():
    app = _app()
    _cc(app, 16, 70, ch=5)                             # 通道不参与（同音符口径）
    assert app.juno_shift == 6
    _cc(app, 64, 127)                                  # 非移调控制器：丢弃
    _cc(app, 1, 100)
    assert app.juno_shift == 6 and len(app.switcher.shifts) == 1


def test_legacy_notes_still_delta_on_top_of_cc():
    app = _app()
    _cc(app, 16, 70)                                   # 控制器设底：+6
    _note(app, 49)                                     # C#2 升半音
    assert app.juno_shift == 7
    _note(app, 48)                                     # C2 降半音
    assert app.juno_shift == 6
    _note(app, 51)                                     # D#2 升八度
    assert app.juno_shift == 18
    _note(app, 50)                                     # D2 降八度
    assert app.juno_shift == 6
    _note(app, 49, on=False)                           # 音符抬起不触发
    assert app.juno_shift == 6 and len(app.switcher.shifts) == 5
    assert [t for t, _q in app.switcher.shifts] == [6, 7, 6, 18, 6]
    assert not any(q for _t, q in app.switcher.shifts)  # 变值从不静默


def test_ax_cc_clamps_and_uses_bend_sequence():
    app = _app()
    _cc(app, 17, 52)
    assert app.ax_shift == -12
    _cc(app, 17, 127)
    assert app.ax_shift == 12
    _note(app, 54)                                     # F#2 升半音：顶在 +12
    assert app.ax_shift == 12
    assert app.ax_switcher.msgs == [kbd_auto.ax_shift_msgs(-12, 1),
                                    kbd_auto.ax_shift_msgs(12, 1)]
    assert app.juno_shift == 0


def test_cc_not_gated_by_busy_but_notes_are():
    """切歌中控制器照收（新工程起播的补发不能丢）；旧音符仍被「切歌中」拦。"""
    app = _app()
    app.ctrl = types.SimpleNamespace(busy=True)
    _cc(app, 16, 67)
    assert app.juno_shift == 3
    _note(app, 49)
    assert app.juno_shift == 3
    assert any("切歌中，忽略移调音符" in m for m in app.q)


# ---- 同值重发 ----

def test_juno_same_value_resend_throttled_and_quiet():
    app = _app()
    _cc(app, 16, 66)
    _cc(app, 16, 66)                                   # 拖光标连发：窗内丢弃
    assert app.switcher.shifts == [(2, False)]
    _aged(app)
    _cc(app, 16, 66)                                   # 窗外：静默重发（自愈通道）
    assert app.switcher.shifts == [(2, False), (2, True)]
    assert not any("全局移调" in m for m in app.q)       # 不走日志队列
    _cc(app, 16, 68)
    assert app.switcher.shifts[-1] == (4, False)
    _cc(app, 16, 66)                                   # 变回：值变了，不限流
    assert app.switcher.shifts[-1] == (2, False)


def test_ax_same_value_skipped():
    """AX 弯音序列 0.25s 且与音色切换同队列：同值补发一个都不发。"""
    app = _app()
    _cc(app, 17, 70)
    _cc(app, 17, 70)
    _aged(app)
    _cc(app, 17, 70)
    assert len(app.ax_switcher.msgs) == 1
    _cc(app, 17, 64)
    assert len(app.ax_switcher.msgs) == 2


def test_switcher_absent_logs_once_and_heals_later():
    app = _app()
    app.switcher = None
    _cc(app, 16, 66)
    assert app.juno_shift == 2
    assert app.q == [sg.ui_text.TRANSPOSE_NOT_READY % 2]
    _cc(app, 16, 66)                                   # 同值窗内：连日志都没有
    assert app.q == [sg.ui_text.TRANSPOSE_NOT_READY % 2]
    app.switcher = _Sw()                               # 端口配上了：补发落到琴
    _aged(app)
    _cc(app, 16, 66)
    assert app.switcher.shifts == [(2, True)]


# ---- 归零 ----

def test_shift_reset_only_sends_when_nonzero():
    app = _app()
    sg.App._shift_reset(app)                           # 两琴都是 0：零发包
    assert app.switcher.shifts == [] and app.ax_switcher.msgs == []
    app.juno_shift, app.ax_shift = 7, 5
    sg.App._shift_reset(app)
    assert (app.juno_shift, app.ax_shift) == (0, 0)
    assert app.switcher.shifts == [(0, False)]
    assert app.ax_switcher.msgs == [kbd_auto.ax_shift_msgs(0, 1)]


def test_finish_switch_common_resets(monkeypatch):
    """跨工程切换收尾：旧歌移调不带进新歌。"""
    monkeypatch.setattr(sg.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: {})
    app = _app()
    app.juno_shift, app.ax_shift = 7, 5
    sg.App._finish_switch_common(
        app, {"path": "/p2", "song_name": "S", "point": None}, False)
    assert (app.juno_shift, app.ax_shift) == (0, 0)
    assert app.switcher.shifts == [(0, False)]
    assert len(app.ax_switcher.msgs) == 1
    assert app.cur_song_path == "/p2"


def test_reload_entry_slots_never_resets(monkeypatch):
    """同工程定位点跳转/对位只重装槽位：移调不动。"""
    monkeypatch.setattr(sg.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: {})
    app = _app()
    app.juno_shift, app.ax_shift = 7, 5
    sg.App._reload_entry_slots(app, {"path": "/p1", "point": None})
    assert (app.juno_shift, app.ax_shift) == (7, 5)
    assert app.switcher.shifts == [] and app.ax_switcher.msgs == []
    assert app.switcher.bumps == 1


def _adopt_app():
    app = _app()
    app.pl_keys = ["a", "b"]
    ents = {"a": {"song_name": "X", "path": "/p1", "point": None},
            "b": {"song_name": "Y", "path": "/p2", "point": None}}
    app._resolve_entry = lambda k: ents.get(k)
    app.watch = types.SimpleNamespace(reset=lambda: None,
                                      set_duration=lambda d, left=None: None)
    app.loaded = []
    app._adopted_load = lambda k, gen: app.loaded.append(k)
    return app


def test_adopt_resets_only_when_project_changed():
    app = _adopt_app()
    app.juno_shift = 4
    sg.App._adopt(app, "Y", True)                      # 人工在 DAW 开了别的工程
    assert app.loaded == ["b"] and app.juno_shift == 0
    assert app.switcher.shifts == [(0, False)]

    app = _adopt_app()                                 # 工程没变、播放列表被编辑
    app.juno_shift = 4
    sg.App._adopt(app, "Y", False)
    assert app.loaded == ["b"] and app.juno_shift == 4
    assert app.switcher.shifts == []

    app = _adopt_app()                                 # 开了歌单外的工程：游离也归零
    app.juno_shift = 4
    sg.App._adopt(app, "Z", True)
    assert app.loaded == [] and app.cur is None and app.juno_shift == 0


def _panic_app():
    app = _app()
    app.ctrl = types.SimpleNamespace(panic=lambda: None)
    app.ctl = app.sync = None
    app.clock_hint, app.clock_port = "", None
    app.live = False
    app.watch = types.SimpleNamespace(
        is_transport_live=lambda: app.live, ever_live=lambda: False,
        suppress_advance=lambda: None)
    app._delay_play_cancel = lambda: None
    app._transport_state = lambda: sg.App._transport_state(app)
    return app


def _wait(cond):
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_panic_resets_shift():
    app = _panic_app()
    app.juno_shift, app.ax_shift = 7, 5
    sg.App._panic(app)
    assert _wait(lambda: app.juno_shift == 0 and app.ax_shift == 0)
    assert app.switcher.shifts == [(0, False)]


def test_panic_keeps_shift_if_transport_restarted():
    """全停后 2 秒内核验窗内用户重新起播：段内移调是应有状态，不得归零。"""
    app = _panic_app()
    app.juno_shift, app.ax_shift = 7, 5
    live_after_quiet = False
    orig = app.ax_switcher.submit_msgs

    def quiet_then_live(msgs, why="", quiet=False):
        nonlocal live_after_quiet
        orig(msgs, why, quiet)
        live_after_quiet = True                   # 静音序列发完=走带已恢复
        app.live = True

    app.ax_switcher.submit_msgs = quiet_then_live
    sg.App._panic(app)
    assert _wait(lambda: live_after_quiet)
    time.sleep(0.2)
    assert (app.juno_shift, app.ax_shift) == (7, 5)
    assert app.switcher.shifts == []


# ---- 发送器：静默重发 ----

def test_tone_switcher_quiet_resend(monkeypatch):
    logs, results, ret = [], [], {"v": None}
    sw = kbd_auto.ToneSwitcher(dict(kbd_auto.DEFAULT_JUNO), logs.append,
                               on_result=lambda d, e: results.append((d, e)))
    monkeypatch.setattr(kbd_auto, "send_slot", lambda *a, **kw: ret["v"])
    try:
        sw.submit_shift(2, why="JUNO 全局移调 +2 半音", quiet=True)   # 静默成功
        sw.submit_shift(3, why="JUNO 全局移调 +3 半音")                # 变值照常
        assert _wait(lambda: len(results) >= 1)   # 前两单已消费（FIFO）
        assert logs == ["JUNO 全局移调 +3 半音"]    # 静默成功那单无痕
        ret["v"] = "端口没了"
        sw.submit_shift(3, why="JUNO 全局移调 +3 半音", quiet=True)   # 静默失败必报
        assert _wait(lambda: len(results) >= 2)
        assert logs == ["JUNO 全局移调 +3 半音",
                        "JUNO 全局移调 +3 半音失败：端口没了"]
        assert results == [("JUNO 全局移调 +3 半音", None),
                           ("JUNO 全局移调 +3 半音", "端口没了")]
    finally:
        sw.stop()


def test_tone_switcher_survives_malformed_item(monkeypatch):
    """队列里混进非 3 元组的项：解包在 try 外，此前一个畸形项就让发送线程
    永久死亡（此后音色/移调全哑且零日志）。现在跳过并继续消费。"""
    logs = []
    sw = kbd_auto.ToneSwitcher(dict(kbd_auto.DEFAULT_JUNO), logs.append)
    monkeypatch.setattr(kbd_auto, "send_slot", lambda *a, **kw: None)
    try:
        sw._q.put(((0.0, None), "quit-marker"))
        sw.submit_shift(2, why="t-after-malformed")
        assert _wait(lambda: logs == ["t-after-malformed"])
    finally:
        sw.stop()


# ---- 「最近切换」卡：失败原因只留短句 ----

def test_brief_err():
    be = kbd_auto.brief_err
    assert be(None) is None and be("") == ""
    assert be("未找到含「JUNO」的 MIDI 输出端口；现有：A、B") == "输出端口未找到"
    assert be("SysEx 发送失败") == "SysEx 发送失败"
    assert be("短消息发送失败，code 5") == "短消息发送失败，code 5"
    assert be("超时；现有：X") == "超时"


def test_shift_why_zero_says_reset():
    assert sg._shift_why("JUNO", 0) == "JUNO 移调归零"
    assert sg._shift_why("JUNO", 2) == "JUNO 全局移调 +2 半音"
    assert sg._shift_why("AX-09", -12) == "AX-09 全局移调 -12 半音"


def test_kb_result_short_for_status_card():
    app = _app()
    sg.App._on_kb_result(app, "JUNO 移调归零",
                         "未找到含「JUNO」的 MIDI 输出端口；现有：A、B、C")
    assert app._kb_last == "JUNO 移调归零失败：输出端口未找到"
    sg.App._on_kb_result(app, "音色切换", "SysEx 发送失败")
    assert app._kb_last == "音色切换失败：SysEx 发送失败"
    sg.App._on_kb_result(app, "JUNO 移调归零", None)
    assert app._kb_last == "已发送：JUNO 移调归零"


def test_port_missing_failure_keeps_card_short_log_full(monkeypatch):
    """端到端：真 ToneSwitcher + 真发送器找不到端口的全长错误 → 运行日志留
    全文，「最近切换」卡只拿短句。整句进卡有卡片四倍宽，会被跑马灯带着
    整行滑走（11-25 真机实测 3.5s 后卡片空白）。"""
    monkeypatch.setattr(kbd_auto, "all_out_devices",
                        lambda: [(0, "Loop A"), (1, "Loop B")])
    app = _app()
    logs = []
    sw = kbd_auto.ToneSwitcher(dict(kbd_auto.DEFAULT_JUNO), logs.append,
                               on_result=lambda d, e: sg.App._on_kb_result(
                                   app, d, e))
    try:
        app.switcher = sw
        sg.App._shift_step(app, "juno", 0)
        assert _wait(lambda: bool(logs))
    finally:
        sw.stop()
    assert logs == ["JUNO 移调归零失败：未找到含「JUNO」的 MIDI 输出端口；"
                    "现有：Loop A、Loop B"]
    assert app._kb_last == "JUNO 移调归零失败：输出端口未找到"


# ---- 网页步进 ----

def test_shift_step_direct():
    app = _app()
    assert sg.App._shift_step(app, "juno", 1) is None
    assert app.juno_shift == 1
    sg.App._shift_step(app, "juno", 12)
    sg.App._shift_step(app, "juno", -1)
    assert app.juno_shift == 12
    sg.App._shift_step(app, "juno", 0)                 # 归零
    assert app.juno_shift == 0 and app.switcher.shifts[-1] == (0, False)
    sg.App._shift_step(app, "ax", 12)
    assert app.ax_shift == 12
    sg.App._shift_step(app, "ax", 0)
    assert app.ax_shift == 0 and len(app.ax_switcher.msgs) == 2
    app.ax_switcher = None                             # 没配输出口：报错不记账
    assert "AX-09 Lucina" in sg.App._shift_step(app, "ax", 1)
    assert app.ax_shift == 0


def _drain(app):
    while True:
        try:
            fn = app.calls.get(timeout=0.2)
        except queue.Empty:
            continue
        fn()


def _req(port, path, body):
    conn = http.client.HTTPConnection(LOOP, port, timeout=5)
    try:
        conn.request("POST", path, body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        data = r.read().decode("utf-8")
        return r.status, (json.loads(data) if data.startswith("{")
                          else {"raw": data})
    finally:
        conn.close()


def test_shift_endpoint():
    """/shift：合法载荷经 calls 投递到 App 方法（真 _shift_step）；非法
    inst/delta、bool、浮点、旧形状 400；简化版 403；方法回错误串=400。"""
    app = _app()
    app.lite = False
    app.calls = queue.Queue()
    app._shift_step = lambda inst, delta: sg.App._shift_step(app, inst, delta)
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None, lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    threading.Thread(target=_drain, args=(app,), daemon=True).start()
    try:
        code, body = _req(port, "/shift", {"inst": "juno", "delta": 1})
        assert code == 200 and body["ok"] is True and app.juno_shift == 1
        code, _b = _req(port, "/shift", {"inst": "juno", "delta": 12})
        assert code == 200 and app.juno_shift == 13
        code, _b = _req(port, "/shift", {"inst": "ax", "delta": -12})
        assert code == 200 and app.ax_shift == -12
        code, _b = _req(port, "/shift", {"inst": "juno", "delta": 0})
        assert code == 200 and app.juno_shift == 0
        for bad in ({"inst": "x", "delta": 1}, {"inst": "juno", "delta": 2},
                    {"inst": "juno", "delta": True}, {"inst": "juno"},
                    {"inst": "juno", "delta": 1.0}, {"delta": 1},
                    {"note": 49, "on": True}):
            code, _b = _req(port, "/shift", bad)
            assert code == 400, bad
        assert (app.juno_shift, app.ax_shift) == (0, -12)   # 非法载荷不动记账
        app.ax_switcher = None
        code, body = _req(port, "/shift", {"inst": "ax", "delta": 1})
        assert code == 400 and "AX-09 Lucina" in body["error"]
        app.lite = True
        code, _b = _req(port, "/shift", {"inst": "juno", "delta": 1})
        assert code == 403
    finally:
        srv.shutdown()
