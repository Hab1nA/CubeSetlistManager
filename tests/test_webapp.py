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

        def pl_cmd(op, key):
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
        durations={}, dur_src={},
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
