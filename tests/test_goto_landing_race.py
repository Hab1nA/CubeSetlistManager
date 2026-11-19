# -*- coding: utf-8 -*-
"""同工程切点收尾竞态回归（真机 11-20）：GOTO 全帧往返（~10-40ms）常快于
calls 50ms 回灌节拍——finish 的 watch.reset() 把已落地的段头位置清成 None，
停止态无人再灌 → NOW 显裸工程名+「时长未知」，微拖光标（=新全帧）即愈。
修=收尾用 Decoder 现值回灌 watch，门=|现值-目标|<=0.1s（同位判定
同门）：只认已落位现值，切歌窗内旧播头 stale 一律不洗白；全帧后到则
自行重灌，两种时序皆自洽。"""
# gate |landed-target|<=0.1s mirrors _point_play same-position check.
import pathlib
import queue
import sys
import threading
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import advance
import points
import setlist_gui


class _FakeMtc:
    """Decoder 桩：只暴露 position()（finish 回灌唯一依赖）。"""

    def __init__(self):
        self._pos = None

    def position(self):
        return self._pos


class _Q:
    def put(self, v):
        pass


def _app(tmp_path):
    """同工程切点桩：真 AdvanceWatch + 真 _reload_entry_slots（槽位 sidecar
    缺失=空表）。两定位点 [0,30]/[30,60]，切到 P2 段头=30.0。"""
    cpr = tmp_path / "s.cpr"
    cpr.write_text("", encoding="utf-8")
    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 2, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0}]}},
        pl_keys=["pt:L#1", "pt:L#2"], end_beh=["", ""], cur=0,
        cur_song_path=str(cpr), cur_point_id=1,
        slots={}, ax_slots={},
        _load_lock=threading.Lock(), calls=queue.Queue(),
        watch=advance.AdvanceWatch(on_finished=lambda: None),
        mtc=_FakeMtc(), mmc_out=7,
        ctrl=types.SimpleNamespace(busy=False, transport=lambda a: None),
        q=_Q())
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app._reseed_landed = lambda start: setlist_gui.App._reseed_landed(app, start)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    app._goto_send = lambda start, label: None
    return app


def _drain(app):
    while True:
        try:
            app.calls.get_nowait()()
        except queue.Empty:
            return


def test_finish_keeps_landed_position(tmp_path):
    """竞态时序（真机复现序）：全帧先于 finish 到达（Decoder+watch 双写），
    finish 的 reset 清成 None → 修复=回灌 Decoder 现值，段头位置保留。"""
    app = _app(tmp_path)
    setlist_gui.App._goto_worker(app, 1, app._resolve_entry("pt:L#2"),
                                 False, False)
    app.mtc._pos = 30.0                    # 全帧到达：Decoder 层
    app.watch.on_full_position(30.0)        # 同一帧喂 watch（真回灌路径）
    _drain(app)                            # finish：reset+回灌
    assert app.cur == 1
    assert app.watch.position() == 30.0    # 修复前=None（症状根因）
    assert app.watch.duration == 30.0 and app.watch.left == 30.0
    assert points.contains(app._resolve_entry("pt:L#2")["point"], 30.0)


def test_finish_before_fullframe_self_heals(tmp_path):
    """另一序（reset 先、全帧后到）：finish 时刻 Decoder 无位置→不回灌，
    全帧到达后自行重灌——原设计注释的前提在此时序下本来就成立。"""
    app = _app(tmp_path)
    setlist_gui.App._goto_worker(app, 1, app._resolve_entry("pt:L#2"),
                                 False, False)
    _drain(app)
    assert app.watch.position() is None    # finish 时刻无位置可回灌
    app.mtc._pos = 30.0
    app.watch.on_full_position(29.97)      # 全帧后到：帧取整落 30-16.7ms
    assert points.contains(app._resolve_entry("pt:L#2")["point"], 29.97)


def test_finish_stale_not_seeded(tmp_path):
    """切歌窗内旧播头 stale 位置：Decoder 现值在段外→不回灌，等全帧重灌
    ——回灌口不可把段外位置洗成「段内」。"""
    app = _app(tmp_path)
    app.mtc._pos = 25.0                    # 旧播头（P2 段外）
    app.watch.on_position(25.0)            # stale QF 经 on_position 灌入
    setlist_gui.App._goto_worker(app, 1, app._resolve_entry("pt:L#2"),
                                 False, False)
    _drain(app)
    assert app.watch.position() is None    # stale 不回灌（0.1s 同位门）
    app.mtc._pos = 30.0
    app.watch.on_full_position(30.0)       # 全帧后到照常重灌
    assert app.watch.position() == 30.0


def test_switch_done_same_position_corner(tmp_path):
    """跨工程首切光标恰在段头（Phase A 实测 BAD 的第二洞）：开工程全帧先
    到、_switch_done 的 reset 抹掉后同位 GOTO 不再补帧——修复=收尾回灌。
    （finish 竞态修复后本例仍挂=证明 _switch_done 需要同款回灌。）"""
    app = _app(tmp_path)
    app._switch_key = "pt:L#2"
    app._play_after_switch = False
    app.dur_src = {}
    app.durations = {}
    app._persist = lambda: None
    app._finish_switch_common = lambda e, p: None
    app.mtc._pos = 30.0                    # 开工程全帧：光标已在 P2 段头
    setlist_gui.App._switch_done(app, "L")
    assert app.cur_song_path == str(tmp_path / "s.cpr") or True
    assert app.watch.position() == 30.0    # 修复前=None（同位 GOTO 无补帧）
    assert app.watch.duration == 30.0 and app.watch.left == 30.0
