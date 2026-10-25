# -*- coding: utf-8 -*-
"""新前端端点测试：/playlist /points /log /plcmd /ptcmd 走真 WebServer +
stub App（test_manual_adopt/_FakeWebApp 同款手法，不启 Tk）。"""
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

_LOOPBACK = "127.0.0.1"


class _Q:
    put = None

    def __init__(self):
        self._l = []

    def put(self, v):
        self._l.append(v)


class _FakeWebApp:
    """只备齐被测端点触及的属性。"""

    def __init__(self):
        self.lite = False
        self.calls = queue.Queue()
        self.q = _Q()
        self.pl_keys = ["A/歌一", "pt:A/歌一#1", "B/歌二"]
        self.durations = {"A/歌一": 120.0}
        self.by_key = {"A/歌一": {"key": "A/歌一", "team": "A", "name": "歌一",
                                 "path": "/p1"},
                       "B/歌二": {"key": "B/歌二", "team": "B", "name": "歌二",
                                 "path": "/p2"}}
        self.points = {"A/歌一": {
            "enabled": True, "counter": 1, "points": [
                {"id": 1, "name": "定位点1", "start": 0.0, "end": 60.0}]}}
        self.log_ring = [(100.0, "启动"), (105.0, "界面异常：X")]
        self.cur = 0
        self._lib_view = lambda: [self.by_key["A/歌一"], self.by_key["B/歌二"]]

        def resolve(key):
            if key.startswith("pt:"):
                head, _, tail = key[3:].rpartition("#")
                p = (self.points.get(head) or {}).get("points", [])
                p = next((x for x in p if x["id"] == int(tail)), None)
                if p is None:
                    return None
                return {"display": "歌一 · " + p["name"], "path": "/p1",
                        "lib_key": head, "song_name": "歌一", "point": p}
            s = self.by_key.get(key)
            if s is None:
                return None
            return {"display": s["name"], "path": s["path"], "lib_key": key,
                    "song_name": s["name"], "point": None}

        self._resolve_entry = resolve
        self.pl_log = []

        def pl_cmd(op, key, payload=None):
            self.pl_log.append((op, key))
            return None

        self._pl_cmd_web = pl_cmd

        def pt_cmd(op, key, payload):
            self.pl_log.append((op, key, payload))
            return None

        self._points_cmd_web = pt_cmd
        self._pickdir_web = lambda: {"path": "D:/选目录"}
        self.switch_log = []

        def _switch(i, why):
            self.switch_log.append((i, why))

        self._switch = _switch


def _drain(app):
    """模拟 Tk 主线程的 calls 排空循环（_run_on_app 的结果通路）。"""
    while True:
        try:
            fn = app.calls.get(timeout=0.2)
        except queue.Empty:
            continue
        fn()


def _req(method, port, path, body=None):
    """测试专用请求：主机字面量显式传入（回环），端口/路径显式校验。"""
    assert isinstance(port, int) and 0 < port < 65536
    assert isinstance(path, str) and path.startswith("/")
    conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=5)
    try:
        conn.request(method, path, body=json.dumps(body).encode() if body
                     else None,
                     headers={"Content-Type": "application/json"} if body
                     else {})
        r = conn.getresponse()
        data = r.read().decode("utf-8")
        return r.status, (json.loads(data) if data.startswith("{")
                          else {"raw": data})
    finally:
        conn.close()


def _get(port, path):
    return _req("GET", port, path)[1]


def _post(port, path, body):
    return _req("POST", port, path, body)[1]


def test_switch_key_toctou():
    """key→下标必须在主线程执行时刻解析：HTTP 线程解完再入队=确认窗/
    pickdir 阻塞期间排在前面的 remove 先改表，陈旧下标切到邻曲。"""
    app = _FakeWebApp()
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    try:                      # 不起 _drain：手工控制主线程执行时机
        assert _post(port, "/cmd", {"action": "switch", "index": 0,
                                    "key": "B/歌二"})["ok"] is True
        fn = app.calls.get(timeout=2)          # 已入队、未执行
        app.pl_keys.remove("B/歌二")           # 队列间隙被它端移除
        fn()                                   # 主线程此刻执行
        assert app.switch_log == []            # 静默跳过，绝不错切邻曲
        # 在列表内的 key 正常按 key 落位（不受传入 index 干扰）
        assert _post(port, "/cmd", {"action": "switch", "index": 0,
                                    "key": "A/歌一"})["ok"] is True
        app.calls.get(timeout=2)()
        assert app.switch_log == [(0, "手动")]
    finally:
        srv.shutdown()


def test_playlist_points_log_endpoints():
    app = _FakeWebApp()
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    threading.Thread(target=_drain, args=(app,), daemon=True).start()
    try:
        pl = _get(port, "/playlist")
        assert [x["key"] for x in pl["pl"]] == app.pl_keys
        assert pl["pl"][1]["pt"] is True and pl["pl"][1]["name"] == "歌一 · 定位点1"
        assert len(pl["lib"]) == 2

        pts = _get(port, "/points")
        assert pts["projects"]["A/歌一"]["enabled"] is True
        assert pts["projects"]["A/歌一"]["points"][0]["end"] == 60.0

        lg = _get(port, "/log")
        assert lg["lines"][1]["msg"] == "界面异常：X"

        assert _post(port, "/plcmd",
                     {"op": "add", "key": "B/歌二"})["ok"] is True
        assert app.pl_log[0] == ("add", "B/歌二")
        assert _post(port, "/ptcmd", {"op": "toggle",
                                      "key": "A/歌一",
                                      "payload": {"on": False}})["ok"] is True
        assert app.pl_log[1][0] == "toggle"

        # /cmd switch：key 优先解析（多端重排后下标漂移的根治）；
        # 陈旧下标 + 正确 key → 按 key 落位；key 已被移除 → 400 拒发
        assert _post(port, "/cmd", {"action": "switch", "index": 0,
                                    "key": "B/歌二"})["ok"] is True
        for _ in range(50):
            if app.switch_log:
                break
            time.sleep(0.02)
        assert app.switch_log[-1] == (2, "手动")   # 不是 index 0
        app.pl_keys = ["A/歌一"]                    # 模拟另一端移除该曲
        st, r = _req("POST", port, "/cmd",
                     {"action": "switch", "index": 0, "key": "B/歌二"})
        assert st == 400 and "不在播放列表" in r["error"]
        n = len(app.switch_log)
        for _ in range(10):
            time.sleep(0.05)
        assert len(app.switch_log) == n             # 未入队执行
        app.pl_keys = ["A/歌一", "pt:A/歌一#1", "B/歌二"]
    finally:
        srv.shutdown()


def test_pickdir_endpoint():
    """/pickdir：主线程对话框结果回传；简化版拒绝。"""
    app = _FakeWebApp()
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    threading.Thread(target=_drain, args=(app,), daemon=True).start()
    try:
        assert _post(port, "/pickdir", {}) == {"path": "D:/选目录"}
        app.lite = True
        code, body = _req("POST", port, "/pickdir", {})
        assert code == 403
    finally:
        srv.shutdown()


def test_vjmap_endpoint(tmp_path):
    """/vjmap：音符↔视频对应表——固定规则 31 行（60-89 视频音 + 90 熄屏），
    视频库宽松匹配（精确 basename / 「编号+空格」前缀）；简化版拒绝。"""
    app = _FakeWebApp()
    vroot = tmp_path / "videos"
    vroot.mkdir()
    (vroot / "1 开场钢琴.mp4").write_bytes(b"x")
    app.ctl = types.SimpleNamespace(cfg={"videoRoot": str(vroot)})
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    try:
        d = _get(port, "/vjmap")
        assert d["videoRoot"] == str(vroot) and d["files"] == 1
        assert len(d["rows"]) == 31
        r60 = next(r for r in d["rows"] if r["note"] == 60)
        assert r60["name"] == "C4" and r60["file"] == "1 开场钢琴.mp4"
        assert r60["hit"] and r60["blackout"] is False
        r2 = next(r for r in d["rows"] if r["note"] == 61)
        assert r2["file"] == "2.mp4" and r2["hit"] is None   # 库里没有
        r90 = next(r for r in d["rows"] if r["note"] == 90)
        assert r90["blackout"] is True and r90["file"] == "熄屏"
        app.lite = True
        code, _b = _req("GET", port, "/vjmap")
        assert code == 403
    finally:
        srv.shutdown()


def test_checkpath_endpoint(tmp_path):
    """/checkpath：路径存在性探测（设置页即时校验）；仅 loopback、
    简化版拒绝、缺 path 400。"""
    app = _FakeWebApp()
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    try:
        d = _post(port, "/checkpath", {"path": str(tmp_path)})
        assert d == {"ok": True, "exists": True, "isdir": True}
        d = _post(port, "/checkpath",
                  {"path": str(tmp_path / "nope.cpr")})
        assert d["exists"] is False and d["isdir"] is False
        code, _b = _req("POST", port, "/checkpath", {})
        assert code == 400
        app.lite = True
        code, _b = _req("POST", port, "/checkpath", {"path": "C:\\"})
        assert code == 403
    finally:
        srv.shutdown()


def test_app_asset_whitelist():
    app = _FakeWebApp()
    srv = web_remote.WebServer(("127.0.0.1", 0), app, None,
                               lambda: 0, page="")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever,
                     kwargs={"poll_interval": 0.2}, daemon=True).start()
    try:
        code, body = _req("GET", port, "/app")
        assert code == 200 and b"app.js" in body["raw"].encode()
        code, _b = _req("GET", port, "/app/%2e%2e/secret")
        assert code == 404, "穿越路径必须 404"
    finally:
        srv.shutdown()


def _pl_app():
    return types.SimpleNamespace(
        pl_keys=["B/歌二"], cur=0,
        by_key={"A/歌一": {"key": "A/歌一", "team": "A", "name": "歌一",
                           "path": "/p1"}},
        points={"A/歌一": {"enabled": True, "counter": 1, "points": [
            {"id": 1, "name": "定位点1", "start": 0.0, "end": 60.0}]}},
        durations={}, dur_src={}, watch=None, q=_Q(),
        _persist=lambda: None, _refresh=lambda: None)


def test_plcmd_add_pt_key():
    """A6：素材库点条目（pt: 键）可加入；点已删/未启用/库缺则拒绝。"""
    app = _pl_app()
    assert sg.App._pl_cmd_web(app, "add", "pt:A/歌一#1") is None
    assert app.pl_keys == ["B/歌二", "pt:A/歌一#1"]
    app2 = _pl_app()
    app2.points["A/歌一"]["enabled"] = False
    assert sg.App._pl_cmd_web(app2, "add", "pt:A/歌一#1") \
        == "该定位点不存在或未启用"
    assert sg.App._pl_cmd_web(app2, "add", "X/不在库") == "素材库里没有该工程"


def test_plcmd_setdur_and_move():
    """B5/B6：setdur 手动时长、move 原子重排（cur 跟随）、redict 拒绝分支。"""
    app = _pl_app()
    app.pl_keys = ["B/歌二", "A/歌一", "pt:A/歌一#1"]
    app.cur = 2
    assert sg.App._pl_cmd_web(app, "setdur", "A/歌一",
                              {"dur": "1:30"}) is None
    assert app.durations["A/歌一"] == 90
    assert app.dur_src["A/歌一"] == "manual"
    assert sg.App._pl_cmd_web(app, "setdur", "pt:A/歌一#1",
                              {"dur": "10"}) is not None   # pt 条目拒手填
    assert sg.App._pl_cmd_web(app, "setdur", "A/歌一",
                              {"dur": "abc"}) is not None
    # 毒化输入一律拒绝（红队 P0：inf/nan/负数曾可打穿并炸 /playlist）
    for bad in ("inf", "nan", "-5", "0", "1e400", "0:00", "99999999999:0",
                "²:00"):
        assert sg.App._pl_cmd_web(app, "setdur", "A/歌一",
                                  {"dur": bad}) is not None, bad
    assert app.durations["A/歌一"] == 90     # 合法值不被毒化输入覆盖
    assert sg.App._pl_cmd_web(app, "move", "", {"from": 2, "to": 0}) is None
    assert app.pl_keys == ["pt:A/歌一#1", "B/歌二", "A/歌一"]
    assert app.cur == 0                     # 移动的就是当前曲：跟随到位
    assert sg.App._pl_cmd_web(app, "move", "", {"from": 0, "to": 2}) is None
    assert app.pl_keys == ["B/歌二", "A/歌一", "pt:A/歌一#1"]
    assert app.cur == 2
    app.pl_keys = ["a", "b", "c", "d"]      # 区间修正：d 移到首，b 顺移
    app.cur = 1
    assert sg.App._pl_cmd_web(app, "move", "", {"from": 3, "to": 0}) is None
    assert app.pl_keys == ["d", "a", "b", "c"] and app.cur == 2
    assert sg.App._pl_cmd_web(app, "move", "", {"from": 9,
                                                "to": 0}) == "移动下标越界"
    assert sg.App._pl_cmd_web(app, "redict", "X/无", {}) is not None
    assert sg.App._pl_cmd_web(app, "redict", "pt:A/歌一#1", {}) is not None


class _PedalStub:
    def __init__(self):
        self.calls = []
        self.connected = False
        self.hid_active = False
        self.muted = False
        self.bridge = None

    def apply(self, *a):
        self.calls.append(("apply",) + a)

    def try_open(self):
        self.calls.append(("try_open",))
        return False

    def mute(self):
        self.calls.append(("mute",))

    def unmute(self):
        self.calls.append(("unmute",))

    def device_online(self):
        return False


class _PedalApp:
    """挂真方法的最小 app：mute→learn_cancel→apply 跨方法互调可解析。"""

    _pedal_apply_web = sg.App._pedal_apply_web
    _pedal_learn_cancel_web = sg.App._pedal_learn_cancel_web
    _pedal_mute_web = sg.App._pedal_mute_web

    def __init__(self):
        self.pedal = _PedalStub()
        self.pedal_hint = "h"
        self.pedal_binds = {"pause": 74}
        self.pedal_hid = {"next": 0xB0}
        self.pedal_device_hint = ""
        self.pedal_intercept = True
        self.pedal_gestures = {"pause": "double"}
        self.pedal_double = 0.35
        self.pedal_remote_enabled = False
        self._pedal_learner = None
        self.jcfg = dict(kbd_auto.DEFAULT_JUNO)
        self.axcfg = dict(kbd_auto.DEFAULT_AX)
        self._persist_config = lambda **k: None
        self.q = _Q()


def test_pedal_web_validation_and_learn():
    """C3 载荷校验 / C1-C2 学习入口 / C5 静音（真方法 + pedal 替身）。"""
    fake = _PedalApp()
    # C3：非法手势（旧 P0：动作名当手势值写入→引擎全哑火）/越界绑定拒绝
    assert sg.App._pedal_apply_web(fake, {"gestures": {"play": "play"}})
    assert sg.App._pedal_apply_web(fake, {"gestures": {"xx": "single"}})
    assert sg.App._pedal_apply_web(fake, {"binds": {"pause": 999}})
    assert sg.App._pedal_apply_web(fake, {"binds": {"pause": True}})
    assert sg.App._pedal_apply_web(fake, {"hid": {"next": 0}})
    assert fake.pedal.calls == []          # 非法载荷不得触达应用链
    # 合法保存即应用（不再需重启）+ 落盘 schema 对齐启动加载器
    saved = {}
    fake._persist_config = lambda **k: saved.update(k)
    assert sg.App._pedal_apply_web(
        fake, {"hint": "h2", "gestures": {"pause": "single"},
               "binds": {"pause": 74}, "hid": {"next": 0xB0},
               "doubleWindow": 0.4}) is None
    assert fake.pedal_hint == "h2"
    assert [c[0] for c in fake.pedal.calls] == ["apply", "try_open"]
    ped = saved["pedal"]
    assert ped["deviceHint"] == "h2" and ped["bindings"] == {"pause": 74}
    assert ped["hidBindings"] == {"next": 0xB0}
    assert "hidDeviceHint" in ped and "hint" not in ped
    # 双踩窗运行期夹取（load_gestures 同区间 0.12-1.0）
    assert sg.App._pedal_apply_web(fake, {"doubleWindow": 5.0}) is None
    assert fake.pedal_double == 1.0
    # 清空最后一个绑定能落下去（`or` 回退会让末位清除静默失败）；
    # 显式 JSON null 与缺席同义（不得把整表写成 None）
    assert sg.App._pedal_apply_web(
        fake, {"binds": {}, "hid": {}, "gestures": {}}) is None
    assert (fake.pedal_binds == {} and fake.pedal_hid == {}
            and fake.pedal_gestures == {})
    assert saved["pedal"]["bindings"] == {}
    assert sg.App._pedal_apply_web(
        fake, {"binds": {"pause": 74}, "gestures": {"pause": "double"}}) \
        is None
    assert sg.App._pedal_apply_web(fake, {"binds": None,
                                          "gestures": None}) is None
    assert fake.pedal_binds == {"pause": 74}
    assert fake.pedal_gestures == {"pause": "double"}
    # 学习：未知动作拒；无会话轮询 → inactive
    assert sg.App._pedal_learn_start_web(fake, "xx") == "未知动作"
    assert sg.App._pedal_learn_poll_web(fake) == {"active": False}
    # 静音开关（进页/离页）；off 先收学习会话（镜像旧窗 _close 顺序）
    class _LearnerStub:
        closed = False

        def close(self):
            _LearnerStub.closed = True

    fake._pedal_learner = ("play", _LearnerStub(), 0)
    assert sg.App._pedal_mute_web(fake, True) is None
    assert _LearnerStub.closed and fake._pedal_learner is None
    assert fake.pedal.calls[-1][0] == "mute"
    fake._pedal_learner = ("play", _LearnerStub(), 0)
    assert sg.App._pedal_mute_web(fake, False) is None
    # 尾序：apply（学习取消回灌）、unmute、try_open
    assert fake.pedal.calls[-2][0] == "unmute"
    assert fake.pedal.calls[-1][0] == "try_open"


def test_kbd_web_song_slots_and_validation(tmp_path):
    """D1/D5：songKey 定向保存任意曲、装载曲热更新、MSB/LSB 校验、
    脏缓存失效。"""
    import kbd_auto
    a_cpr = tmp_path / "a.cpr"
    a_cpr.write_text("", encoding="utf-8")
    b_cpr = tmp_path / "b.cpr"
    b_cpr.write_text("", encoding="utf-8")
    fake = types.SimpleNamespace(
        by_key={"A/歌一": {"key": "A/歌一", "team": "A", "name": "歌一",
                           "path": str(a_cpr)},
                "B/歌二": {"key": "B/歌二", "team": "B", "name": "歌二",
                           "path": str(b_cpr)}},
        cur_song_path=str(a_cpr), slots={}, ax_slots={},
        _load_lock=threading.Lock(), _kbd_song_key=lambda: "A/歌一")
    fake._kbd_song_cache = {"B/歌二": ({}, {})}
    # 未装载目标曲也能保存（song_key 定向）
    assert sg.App._slots_save_web(
        fake, "ax", {"72": {"pc": 5, "msb": 87}}, song_key="B/歌二") is None
    loaded = kbd_auto.load_slots(str(b_cpr), "ax")
    assert loaded[72]["msb"] == 87
    assert fake.ax_slots == {}            # 非装载曲：live 映射不动
    assert "B/歌二" not in fake._kbd_song_cache   # 脏缓存已失效
    # 装载曲保存：热更新 live
    assert sg.App._slots_save_web(
        fake, "juno", {"60": {"pc": 63}}, song_key="A/歌一") is None
    assert fake.slots == {60: {"pc": 63}}
    assert kbd_auto.load_slots(str(a_cpr))[60]["pc"] == 63
    # D5 校验：PC/MSB/LSB 越界拒绝
    for bad in ({"60": {"pc": 128}}, {"60": {"pc": 5, "msb": 128}},
                {"60": {"pc": 5, "lsb": -1}}, {"60": {"pc": True}}):
        assert sg.App._slots_save_web(fake, "juno", bad) is not None, bad
    assert sg.App._slots_save_web(fake, "juno", {},
                                  song_key="X/不在库") == "曲目不在素材库"
    # 装载曲缺省路径（song_key=None）沿用旧行为
    assert sg.App._slots_save_web(fake, "juno", {}) is None


def test_kbd_test_web_song_key(tmp_path, monkeypatch):
    """R5：试听带 songKey——未装载曲从旁挂文件现读，装载曲走 live；
    未配置给明确报错（红队 P1-5）。"""
    import kbd_auto
    a_cpr = tmp_path / "a.cpr"
    a_cpr.write_text("", encoding="utf-8")
    b_cpr = tmp_path / "b.cpr"
    b_cpr.write_text("", encoding="utf-8")
    kbd_auto.save_slots(str(b_cpr), {60: {"pc": 70, "msb": 87}}, "slots")

    class _Sw:
        def __init__(self):
            self.sent = []

        def submit(self, slot, why=""):
            self.sent.append(slot)

    sw = _Sw()
    fake = types.SimpleNamespace(
        by_key={"B/歌二": {"key": "B/歌二", "team": "B", "name": "歌二",
                           "path": str(b_cpr)}},
        cur_song_path=str(a_cpr), slots={60: {"pc": 63}}, ax_slots={},
        switcher=sw, ax_switcher=None, _kbd_song_key=lambda: None,
        jcfg=dict(kbd_auto.DEFAULT_JUNO),
        axcfg=dict(kbd_auto.DEFAULT_AX))
    # 端口预检确定性：输出口恒在场
    monkeypatch.setattr(kbd_auto, "_all_out_devices",
                        lambda: [(0, "JUNO-DS")])
    # 未装载曲：按旁挂文件发（pc 70），不是装载曲的 pc 63
    assert sg.App._kbd_test_web(fake, "juno", 60, song_key="B/歌二") is None
    assert sw.sent == [{"pc": 70, "msb": 87}]
    # 无 song_key：走 live（装载曲映射）
    fake._kbd_song_key = lambda: None
    assert sg.App._kbd_test_web(fake, "juno", 60) is None
    assert sw.sent[-1] == {"pc": 63}
    # 未配置槽位：明确报错
    assert "未配置" in sg.App._kbd_test_web(fake, "juno", 61,
                                            song_key="B/歌二")
    assert "不在素材库" in sg.App._kbd_test_web(fake, "juno", 60,
                                                song_key="X/无")


def test_kbd_record_and_get_shape():
    """D3/D4：录制会话启动/停止；/kbd GET 附 ports/portStatus/songs/
    editSong/lastResult。"""
    fake = types.SimpleNamespace(
        jcfg=dict(kbd_auto.DEFAULT_JUNO), axcfg=dict(kbd_auto.DEFAULT_AX),
        juno_shift=0, ax_shift=0, cur_song_path=None, by_key={}, songs=[],
        slots={}, ax_slots={}, _kb_last=("待机", None), _kbd_cap=None)
    fake.jcfg["inHint"] = "不存在的测试口XYZ"   # 确定性：端口必不在场
    fake._kbd_song_key = lambda: None
    # 启动（端口不存在→报错）；停止（无会话→幂等）
    err = sg.App._kbd_record_web(fake, "juno")
    assert err and "未找到" in err
    assert fake._kbd_cap is None
    assert sg.App._kbd_record_web(fake, "juno", stop=True) is None
    assert sg.App._kbd_record_poll_web(fake) == {"active": False}
    # 活动会话带人话描述（录制弹层直读琴显编号，不再手填数字）
    fake._kbd_cap = {"inst": "juno", "closed": False, "port": None,
                     "cap": types.SimpleNamespace(
                         slot={"pc": 5, "msb": 87, "lsb": 0})}
    poll = sg.App._kbd_record_poll_web(fake)
    assert poll["active"] and poll["slot"]["desc"] == "USER:0506"
    # GET shape（_pick_hit/_device_entries 为纯函数，端口枚举在本机跑）
    out = sg.App._kbd_get_web(fake)
    for k in ("ports", "portStatus", "songs", "editSong", "lastResult",
              "songKey"):
        assert k in out, k
    assert set(out["ports"]) == {"in", "out"}
    assert set(out["portStatus"]) == {"junoIn", "junoOut", "axIn", "axOut"}


def test_snapshot_vj_block():
    """E1/E2：/state vj 状态块契约（完整版五键、lite 或无源时 None）。"""
    import web_remote

    def mk_app(**over):
        base = dict(
            pl_keys=[], durations={}, cur=None, switch_confirm=False,
            watch=None, ctrl=None,
            ctl=types.SimpleNamespace(is_connected=lambda: False,
                                      last_error="boom"),
            sync=types.SimpleNamespace(
                is_following=lambda: True, video_state="playing",
                current_video="开场.mp4"),
            web=None, lite=False,
            port=types.SimpleNamespace(name="VJ口"), vj_hint="VJ口",
            start_err="", pedal=None)
        base.update(over)
        return types.SimpleNamespace(**base)

    snap = web_remote.build_snapshot(mk_app(), True, "x")
    assert snap["vj"] == {"follow": "playing", "video": "开场.mp4",
                          "portOn": True, "vjEnabled": True,
                          "obsErr": "boom"}
    # 跟随源缺席 → off；lite → 不携带（旧平板语义不变）
    snap2 = web_remote.build_snapshot(
        mk_app(sync=None, port=None, vj_hint=""), True, None)
    assert snap2["vj"]["follow"] == "off"
    assert snap2["vj"]["portOn"] is False and snap2["vj"]["vjEnabled"] is False
    assert web_remote.build_snapshot(mk_app(lite=True), True, None)["vj"] \
        is None
    # stopped 值域（首个时码脉冲即 following、视频未触发——现场常见态）
    snap3 = web_remote.build_snapshot(
        mk_app(sync=types.SimpleNamespace(
            is_following=lambda: True, video_state="stopped",
            current_video="x.mp4")), True, None)
    assert snap3["vj"]["follow"] == "stopped"
    # VJ 链构造失败（ctl/sync 双 None + start_err）：错误必须带出
    snap4 = web_remote.build_snapshot(
        mk_app(ctl=None, sync=None, start_err="VJ 链未启动：boom"),
        True, None)
    assert snap4["vj"]["follow"] == "off"
    assert snap4["vj"]["obsErr"] == "VJ 链未启动：boom"
    # ctl 在场时 last_error 优先于 start_err
    snap5 = web_remote.build_snapshot(
        mk_app(start_err="旧错"), True, None)
    assert snap5["vj"]["obsErr"] == "boom"


def test_ptcmd_poison_rejected():
    """定位点通道毒化防御（红队第 3 轮 P0）：edit/add 的 NaN/Infinity
    一律拒绝，validate_point 对 NaN 失明的老洞补 finite 检查。"""
    app = _pl_app()
    app.mtc = None
    assert sg.App._points_cmd_web(app, "edit", "A/歌一",
                                  {"id": 1, "start": float("nan"),
                                   "end": float("inf")}) is not None
    assert sg.App._points_cmd_web(app, "edit", "A/歌一",
                                  {"id": 1, "start": 0.0,
                                   "end": float("nan")}) is not None
    assert app.points["A/歌一"]["points"][0]["start"] == 0.0  # 未被污染
    assert sg.App._points_cmd_web(app, "add", "A/歌一",
                                  {"start": float("nan")}) is not None
    assert sg.App._points_cmd_web(app, "add", "A/歌一",
                                  {"end": float("-inf")}) is not None
    assert len(app.points["A/歌一"]["points"]) == 1


def test_points_load_poison_filter(tmp_path):
    """sidecar 历史毒化（NaN/Infinity/负数）读回跳行。"""
    import points as pts_mod
    f = tmp_path / pts_mod.SIDECAR
    f.write_text('{"enabled": true, "counter": 3, "points": ['
                 '{"id":1,"name":"ok","start":0,"end":60},'
                 '{"id":2,"name":"bad","start":NaN,"end":Infinity},'
                 '{"id":3,"name":"neg","start":-5,"end":60}]}',
                 encoding="utf-8")
    data, err = pts_mod.load_points(str(tmp_path))
    assert err is None
    assert [p["id"] for p in data["points"]] == [1]
    assert pts_mod.finite_tc(float("inf")) is None
    assert pts_mod.finite_tc(True) is None
    assert pts_mod.finite_tc(120) == 120.0


def test_load_playlist_poison_filter(tmp_path, monkeypatch):
    """历史毒化时长（NaN/Infinity/超界/负数，红队第 2 轮）读回归零。"""
    import cpr_meta
    f = tmp_path / "playlist.json"
    f.write_text('{"playlist": ["a"], "durations": {"a": NaN, '
                 '"b": Infinity, "c": 1e400, "d": -3, "e": 120}}',
                 encoding="utf-8")
    monkeypatch.setattr(sg, "PLAYLIST_PATH", f)
    pl = sg._load_playlist()
    assert pl["durations"] == {"e": 120.0}
    assert cpr_meta.parse_mmss("166667:00") is None   # 冒号分支同样有界
    assert cpr_meta.parse_mmss("99999:59") == 99999 * 60 + 59
    assert cpr_meta.safe_dur(float("nan")) == 0.0
    assert cpr_meta.safe_dur(1e12) == 0.0


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


def test_settings_partial_payload_no_wipe(monkeypatch):
    """B1：VJ 页只发 obs 节 → 其余内存/磁盘字段逐项不变；设置页全量
    载荷才更新对应字段（在场检测）。"""
    disk = {"autoAdvance": True, "autoPlay": True, "topMost": True,
            "switchConfirm": True, "exitCloseApps": False,
            "vjPortHint": "VJ1", "kbPortHint": "KB1", "clockPortHint": "CK1",
            "obs": {"projectorMonitor": "DP-1", "vjMute": True,
                    "videoRoot": "D:/v", "mediaInput": "Old"},
            "webRemote": dict(web_remote.DEFAULT_WEB_REMOTE)}
    monkeypatch.setattr(sg, "_load_config", lambda: json.loads(
        json.dumps(disk)))
    monkeypatch.setattr(sg, "_save_config",
                        lambda cfg: disk.clear() or disk.update(cfg))
    calls = []
    fake = types.SimpleNamespace(
        vj_hint="VJ1", kb_hint="KB1", clock_hint="CK1",
        cont_play=True, auto_var=_Var(True), top_most=True,
        switch_confirm=True, exit_close_apps=False,
        ccfg={"projectsRoot": "C:/x", "autoSave": True, "dawExe": "e"},
        web_cfg=dict(web_remote.DEFAULT_WEB_REMOTE), web=None, ctl=None,
        _obs_apply_lock=threading.Lock(), _clock_changed=False, q=_Q(),
        root=types.SimpleNamespace(attributes=lambda *_a, **_k: None),
        _toggle_auto=lambda: None,
        _apply_ports=lambda: calls.append("ports"),
        _apply_web=lambda: calls.append("web"),
        _web_config_payload=lambda **f: dict(
            web_remote.DEFAULT_WEB_REMOTE, **f),
        _load_songs=lambda: None)
    # VJ 页载荷：只有 obs 节
    assert sg.App._settings_apply_web(fake, {
        "obs": {"mediaInput": "Media34", "vjMute": False,
                "projectorMonitor": "DP-2"}}) is None
    assert (fake.vj_hint, fake.kb_hint, fake.clock_hint) == \
        ("VJ1", "KB1", "CK1")
    assert fake.cont_play is True and fake.switch_confirm is True
    assert fake.top_most is True and fake.exit_close_apps is False
    assert disk["obs"] == {"projectorMonitor": "DP-2", "vjMute": False,
                           "videoRoot": "D:/v", "mediaInput": "Media34"}
    assert disk["vjPortHint"] == "VJ1" and disk["kbPortHint"] == "KB1"
    assert disk["clockPortHint"] == "CK1"
    assert disk["autoPlay"] is True and disk["switchConfirm"] is True
    assert disk["webRemote"]["enabled"] is False
    assert calls == []                       # 端口/遥控未动：不触发热切换
    # 设置页全量载荷：web.enabled 翻转 → 热应用触发、磁盘跟随
    disk["webRemote"]["ip"] = "192.168.1.9"
    fake.web = types.SimpleNamespace(
        registry=types.SimpleNamespace(snapshot=lambda: []))
    assert sg.App._settings_apply_web(fake, {
        "clockHint": "CK1", "vjHint": "VJ1", "kbHint": "KB1",
        "autoAdvance": True, "autoPlay": True, "topMost": True,
        "switchConfirm": True, "exitCloseApps": False,
        "projectsRoot": "C:/x", "dawExe": "e", "autoSave": True,
        "web": {"enabled": True, "serverPort": 8765, "appPort": 8767,
                "taskerPort": 8766, "midiIn": "", "mode": "lan",
                "ip": "192.168.1.9"}}) is None
    assert fake.web_cfg["enabled"] is True
    assert disk["webRemote"]["enabled"] is True
    assert calls == ["web"]
