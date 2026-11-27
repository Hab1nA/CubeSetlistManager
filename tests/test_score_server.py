# -*- coding: utf-8 -*-
"""score_server（谱面服务器启动信号）离线测试。

score_server 只用标准库，任何平台可跑；最后一条经 web_remote 的 MIDI 分流
需要 Windows（winmm），非 Windows 自动 skip。
"""
import http.server
import queue
import threading
import time

import pytest

import score_server as ss


class _Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.got.put((self.path, self.headers.get("X-Room-Token"),
                             time.monotonic()))
        code = self.server.code
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def fake():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    srv.got = queue.Queue()
    srv.code = 200
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _make(srv, **over):
    cfg = {"enabled": True, "url": "http://127.0.0.1:%d" % srv.server_port,
           "token": "tok"}
    cfg.update(over)
    logs = queue.Queue()
    return ss.ScoreServer(cfg, logs.put), logs


def test_parse_url():
    assert ss.parse_url("http://192.168.1.20:8765") == ("192.168.1.20", 8765, "")
    assert ss.parse_url(" http://h:1/x/ ") == ("h", 1, "/x")
    for bad in ("", "192.168.1.20:8765", "https://h:1", "http://"):
        with pytest.raises(ValueError):
            ss.parse_url(bad)


def test_build_path_encodes_song():
    p = ss.build_path("", "cue", "一体 いつから", 2)
    assert p.startswith("/api/room/cue?bars=2&song=")
    assert " " not in p
    assert ss.build_path("", "cue", None, 0) == "/api/room/cue?bars=0"


def test_disabled_or_bad_url_consumes_nothing():
    logs = queue.Queue()
    off = ss.ScoreServer({"enabled": False, "url": "http://h:1"}, logs.put)
    assert not off.active and not off.on_note(50)
    bad = ss.ScoreServer({"enabled": True, "url": "h:1"}, logs.put)
    assert not bad.active and not bad.on_note(50)
    assert "未启用" in logs.get_nowait()


def test_cue_note_sends_cue_with_song_and_token(fake):
    s, logs = _make(fake, bars=2)
    s.preload("一体いつから")
    path, tok, _ = fake.got.get(timeout=2)
    assert path.startswith("/api/room/song?song=") and tok == "tok"
    assert not s.on_note(49)            # 翻谱音符不归这里
    t0 = time.monotonic()
    assert s.on_note(50)
    path, tok, t1 = fake.got.get(timeout=2)
    assert path.startswith("/api/room/cue?bars=2&song=")
    assert tok == "tok"
    assert t1 - t0 < 0.1                # 不进 100ms 组合窗口
    msgs = [logs.get(timeout=2) for _ in range(2)]
    assert "预载" in msgs[0] and "已送达" in msgs[1]


def test_custom_note_bars_and_no_song(fake):
    s, _logs = _make(fake, note=51, bars=1, token="")
    assert not s.on_note(50)
    assert s.on_note(51)
    path, tok, _ = fake.got.get(timeout=2)
    assert path == "/api/room/cue?bars=1" and tok is None


def test_refusal_and_unreachable_are_logged(fake):
    fake.code = 404
    s, logs = _make(fake)
    s.on_note(50)
    assert "被拒（404）" in logs.get(timeout=2)
    dead = ss.ScoreServer({"enabled": True, "url": "http://127.0.0.1:1"},
                          logs.put)
    dead.on_note(50)
    assert "失败" in logs.get(timeout=4)


def test_web_remote_routes_cue_note_before_turn_hub():
    try:
        import web_remote
    except Exception as e:              # 非 Windows：winmm 不可用
        pytest.skip("web_remote 不可导入：%r" % (e,))

    class Score:
        def __init__(self):
            self.seen = []

        def on_note(self, n):
            self.seen.append(n)
            return n == 50

    class Hub:
        def __init__(self):
            self.seen = []

        def submit(self, n):
            self.seen.append(n)

    class App:
        q = queue.Queue()
        score_server = Score()

    wr = web_remote.WebRemote.__new__(web_remote.WebRemote)
    wr.app, wr.hub = App(), Hub()
    wr._on_midi(0x90, 50, 100)      # 启动音符：不进翻谱
    wr._on_midi(0x90, 49, 100)      # 翻谱音符：照旧
    wr._on_midi(0x90, 50, 0)        # note-on 力度 0 = note-off
    wr._on_midi(0x80, 50, 64)
    assert App.score_server.seen == [50, 49]
    assert wr.hub.seen == [49]
