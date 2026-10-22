# -*- coding: utf-8 -*-
"""新前端端点测试：/playlist /points /log /plcmd /ptcmd 走真 WebServer +
stub App（test_manual_adopt/_FakeWebApp 同款手法，不启 Tk）。"""
import http.client
import json
import pathlib
import queue
import sys
import threading
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


class _SwitcherStub:
    """ToneSwitcher 替身：记录 submit_msgs / submit_shift 调用。"""

    def __init__(self):
        self.calls = []

    def submit_msgs(self, msgs, why=""):
        self.calls.append(("msgs", msgs, why))

    def submit_shift(self, total, why=""):
        self.calls.append(("shift", total, why))


def test_kbd_shift_web_ax_uses_pitch_msgs():
    """A5：ax 分支发弯音钉位序列（AX-09 不收 SysEx），JUNO 分支不变。"""
    ax, juno = _SwitcherStub(), _SwitcherStub()
    fake = types.SimpleNamespace(ax_switcher=ax, switcher=juno,
                                 ax_shift=0, juno_shift=0, axcfg={"ch": 1})
    assert sg.App._kbd_shift_web(fake, "ax", 3) is None
    assert ax.calls and ax.calls[0][0] == "msgs"
    assert ax.calls[0][1] == kbd_auto.ax_shift_msgs(3, 1)
    assert all(k == "short" for k, _p, _g in ax.calls[0][1])
    assert fake.ax_shift == 3
    assert sg.App._kbd_shift_web(fake, "juno", 2) is None
    assert juno.calls[0][0] == "shift"


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
                                                "to": 0}) is None  # 越界幂等
    assert sg.App._pl_cmd_web(app, "redict", "X/无", {}) is not None
    assert sg.App._pl_cmd_web(app, "redict", "pt:A/歌一#1", {}) is not None


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
