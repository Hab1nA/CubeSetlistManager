# -*- coding: utf-8 -*-
"""手动跟随（_adopt）回归：Cubase 里人工开别的工程时横幅自动对位播放
列表——命中=采纳（指针/映射/时长/计时器就位，自动推进照常）；未命中=
游离（清指针回第一首兜底、计时器清零防旧时长误切）。设计拍板 2026-09-29：
采纳自动推进生效；歌单外清指针不推进。"""
import pathlib
import queue
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import setlist_gui


class _FakeWatch:
    def __init__(self):
        self.durs, self.resets = [], 0

    def set_duration(self, d):
        self.durs.append(d)

    def reset(self):
        self.resets += 1


class _Q(list):
    put = list.append


def _app(name="X", durs=None, pl=("a", "b")):
    """stub App：只供 _adopt 触及的属性。"""
    import types
    app = types.SimpleNamespace()
    app.pl_keys = list(pl)
    app.by_key = {"a": {"name": "X", "path": "/p1"},
                  "b": {"name": "Y", "path": "/p2"}}
    app.durations = durs if durs is not None else {}
    app.watch = _FakeWatch()
    app.slots, app.ax_slots = {"keep": 1}, {"keep": 1}
    app.cur_song_path = "/old"
    app.cur = 99
    app.q = app.logs = _Q()
    app.calls = queue.Queue()       # 装载完成回调（_refresh）走这里
    import threading as _th
    app._load_lock = _th.Lock()     # 装载代际守卫锁（_adopt/_switch 触及）
    app._refresh = lambda: app.logs.append("<refresh>")
    # SimpleNamespace 无类方法解析：显式绑定真实现（装载半程要真跑）
    app._adopted_load = lambda key, gen: \
        setlist_gui.App._adopted_load(app, key, gen)
    return app


def _drain(app):
    while True:
        try:
            app.calls.get_nowait()()
        except queue.Empty:
            return


def _wait(app, cond, timeout=3.0):
    """等后台装载完成并排空其回调（_adopt 的装载半程已在后台线程跑）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _drain(app)
        if cond():
            return True
        time.sleep(0.02)
    _drain(app)
    return cond()


def test_adopt_match(monkeypatch):
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_duration",
                        lambda p: 120.0)
    loaded = []
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None: loaded.append(p) or {"s": 1})
    app = _app()
    setlist_gui.App._adopt(app, "Y")
    assert app.cur == 1                      # 指针指到命中的那首（立即）
    assert _wait(app, lambda: app.cur_song_path == "/p2"
                 and app.slots == {"s": 1})
    _drain(app)
    assert app.durations["b"] == 120.0
    assert app.watch.durs == [120.0]         # 时长就位（内部 reset 重新累计）
    assert loaded == ["/p2", "/p2"]          # JUNO + AX 槽都按新工程装载
    assert app.ax_slots == {"s": 1}
    assert any("已跟随" in m for m in app.logs)


def test_adopt_match_keeps_manual_duration(monkeypatch):
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_duration",
                        lambda p: 120.0)
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None: {})
    app = _app(durs={"a": 42.0})
    setlist_gui.App._adopt(app, "X")
    assert _wait(app, lambda: app.cur_song_path == "/p1")
    assert app.durations["a"] == 42.0 and app.watch.durs == [42.0]


def test_adopt_miss_clears(monkeypatch):
    """游离：清指针回第一首兜底、清映射、计时器清零（永不推进）。"""
    fired = []
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_duration",
                        lambda p: fired.append(p))    # 不该被探时长
    app = _app()
    setlist_gui.App._adopt(app, "别的歌")
    assert app.cur is None and app.cur_song_path is None
    assert app.slots == {} and app.ax_slots == {}
    assert app.watch.resets == 1 and app.watch.durs == [0]
    assert fired == [] and any("不在播放列表" in m for m in app.logs)


def test_adopt_duplicate_name_takes_first(monkeypatch):
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_duration", lambda p: 0)
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None: {})
    app = _app(pl=("a", "b"))
    app.by_key["b"] = {"name": "X", "path": "/p2"}   # 同名歌取靠前项
    setlist_gui.App._adopt(app, "X")
    assert app.cur == 0
    assert _wait(app, lambda: app.cur_song_path == "/p1")


def test_adopt_empty_playlist_noop():
    """播放列表未载入（启动竞态）不动任何状态。"""
    app = _app(pl=())
    app.cur, app.cur_song_path = 5, "/keep"
    setlist_gui.App._adopt(app, "X")
    assert app.cur == 5 and app.cur_song_path == "/keep"
    assert app.watch.durs == [] and app.logs == []
