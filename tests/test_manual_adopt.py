# -*- coding: utf-8 -*-
"""手动跟随（_adopt）回归：Cubase 里人工开别的工程时横幅自动对位播放
列表——命中=采纳（指针/映射/时长/计时器就位，自动推进照常）；未命中=
游离（清指针回第一首兜底、计时器清零防旧时长误切）。设计拍板 2026-09-29：
采纳自动推进生效；歌单外清指针不推进。"""
import pathlib
import queue
import sys
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import setlist_gui


class _FakeWatch:
    def __init__(self):
        self.durs, self.lefts, self.resets = [], [], 0
        self.ops = []                   # reset/dur 顺序（P0-1 纪元闭合锚）
        self.pos = None                 # position()：对位测试用
        self.duration = 0               # 现值镜像（闸门/恢复断言用）
        self.armed = False
        self._fired = False
        self._live = False
        self._since_live = 1e9          # 距最后 QF 秒数（默认=从未/久远）

    def set_duration(self, d, left=None):
        self.durs.append(d)
        self.lefts.append(left)
        self.ops.append("dur")
        self.duration = d

    def reset(self):
        self.resets += 1
        self.ops.append("reset")

    def set_armed(self, on):
        self.armed = bool(on)

    def position(self):
        return self.pos

    def played(self):
        return self.pos or 0.0

    def fired(self):
        return self._fired

    def is_transport_live(self):
        return self._live

    def since_live(self):
        return self._since_live


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
    app.points = {}                 # _resolve_entry 触及（pt 条目解析）
    app.end_beh = {}                # 播完行为表（_sync_watch_armed 触及）
    app.auto_advance = False
    # SimpleNamespace 无类方法解析：显式绑定真实现（装载半程要真跑）
    app._sync_watch_armed = lambda: setlist_gui.App._sync_watch_armed(app)
    app._adopted_load = lambda key, gen: \
        setlist_gui.App._adopted_load(app, key, gen)
    app._resolve_entry = lambda key: \
        setlist_gui.App._resolve_entry(app, key)
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
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_locators",
                        lambda p: (0.0, 120.0))
    loaded = []
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: loaded.append(p) or {"s": 1})
    app = _app()
    setlist_gui.App._adopt(app, "Y")
    assert app.cur == 1                      # 指针指到命中的那首（立即）
    assert _wait(app, lambda: app.cur_song_path == "/p2"
                 and app.slots == {"s": 1})
    _drain(app)
    assert app.durations["b"] == 120.0
    assert app.watch.durs == [120.0]         # 时长就位（不复位：位置自证）
    assert app.watch.lefts == [0.0]
    assert app.watch.ops == ["reset", "dur"]  # P0-1：装载提交时刻先纪元复位
    #   ——装载 IO 窗口里旧工程的时码尾流重灌的位置，必须在此时兜住，
    #   否则旧位置穿越新歌坐标系（旧位置≥新时长 95% 会确定性跳歌）
    assert loaded == ["/p2", "/p2"]          # JUNO + AX 槽都按新工程装载
    assert app.ax_slots == {"s": 1}
    assert any("已跟随" in m for m in app.logs)


def test_adopt_match_keeps_manual_duration(monkeypatch):
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_locators",
                        lambda p: (5.0, 120.0))
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: {})
    app = _app(durs={"a": 42.0})
    setlist_gui.App._adopt(app, "X")
    assert _wait(app, lambda: app.cur_song_path == "/p1")
    assert app.durations["a"] == 42.0 and app.watch.durs == [42.0]
    assert app.watch.lefts == [5.0]          # 手动时长也吃文件里的左定位条


def test_adopt_miss_clears(monkeypatch):
    """游离：清指针回第一首兜底、清映射、计时器清零（永不推进）。"""
    fired = []
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_locators",
                        lambda p: fired.append(p))    # 不该被探时长
    app = _app()
    setlist_gui.App._adopt(app, "别的歌")
    assert app.cur is None and app.cur_song_path is None
    assert app.slots == {} and app.ax_slots == {}
    assert app.watch.resets == 1 and app.watch.durs == [0]
    assert fired == [] and any("不在播放列表" in m for m in app.logs)


def test_adopt_duplicate_name_takes_first(monkeypatch):
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_locators",
                        lambda p: (0.0, 0.0))
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: {})
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


def _pt_app(pl, pts, cur=0, pos=None):
    """定位点对位 stub：pl=键序列，pts={lib_key: points data}。"""
    import types
    app = types.SimpleNamespace()
    app.pl_keys = list(pl)
    app.by_key = {"A": {"name": "工程A", "path": "/pA"},
                  "B": {"name": "工程B", "path": "/pB"}}
    app.points = pts
    app.durations = {}
    app.watch = _FakeWatch()
    app.watch.pos = pos
    app.cur = cur
    app.q = app.logs = _Q()
    app.calls = queue.Queue()
    app.cur_song_path = "/pA"
    app.cur_point_id = None            # _repoint 点粒度槽位重装载触及
    app.slots, app.ax_slots = {}, {}
    import threading as _th
    app._load_lock = _th.Lock()
    app._goto_ack = None               # GOTO 在途闸（repick 触及）
    app._refresh = lambda: app.logs.append("<refresh>")
    app.end_beh = {}                # 播完行为表（_sync_watch_armed 触及）
    app.auto_advance = False
    app._resolve_entry = lambda key: \
        setlist_gui.App._resolve_entry(app, key)
    app._sync_watch_armed = lambda: setlist_gui.App._sync_watch_armed(app)
    app._repick_by_position = lambda name, pos_: \
        setlist_gui.App._repick_by_position(app, name, pos_)
    app._repoint = lambda j, e, why: \
        setlist_gui.App._repoint(app, j, e, why)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    return app


def _pts(data_points, enabled=True):
    return {"enabled": enabled, "counter": len(data_points),
            "points": data_points}


def test_repick_keeps_when_current_contains_position():
    """当前段含位置：指针不动（重复区间设计的稳定锚）。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 30.0,
                                   "end": 90.0}])},
                  cur=0, pos=45.0)
    assert not app._repick_by_position("工程A", 45.0)
    # 45 在段1 [0,60] 内：指针不动（duration 从 0 起步→静默补段坐标）
    assert app.cur == 0 and app.watch.durs == [60.0]
    assert app.logs == []


def test_repick_moves_to_containing_point():
    """位置落在后面的段：指针移过去并换段坐标（同路径，映射缓存复用）。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 30.0,
                                   "end": 90.0}])},
                  cur=0, pos=70.0)
    assert app._repick_by_position("工程A", 70.0) is True
    assert app.cur == 1
    assert app.watch.durs == [60.0] and app.watch.lefts == [30.0]
    assert any("按位置对位" in m for m in app.logs)


def test_repick_identical_ranges_no_oscillation():
    """两段完全同区间（重复设计）：当前段含位置即不动，不振荡。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 30.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 30.0,
                                   "end": 60.0}])},
                  cur=1, pos=45.0)
    assert not app._repick_by_position("工程A", 45.0)
    assert app.cur == 1 and app.watch.durs == [30.0]   # 静默补坐标，指针不动


def test_repick_gap_follows_last_entered():
    """空档（位置不在任何段内）：指针=起点≤位置的播放列表序最后一个；
    当前已是它则不动——拖到段间空档 NOW 显裸工程名、NEXT 不倒退。空档
    内段坐标清零（推进暂闭：空档起播不属于任何段，防「已过段尾」被误判
    唱完而杀掉手动播放）。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 30.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 90.0}])},
                  cur=0, pos=45.0)
    assert not app._repick_by_position("工程A", 45.0)   # 段1 已是最后进入者
    assert app.cur == 0 and app.watch.durs == []
    # 位置越过全部段尾：指到最靠后的已进入段，坐标清零（推进暂闭）
    assert app._repick_by_position("工程A", 95.0) is True
    assert app.cur == 1
    assert app.watch.durs == [0] and app.watch.lefts == [0.0]
    assert any("空档对位" in m for m in app.logs)
    # 指针已在最后进入段、位置在段后空档：不移动但同样清零
    app2 = _pt_app(pl=("pt:A#1", "pt:A#2"),
                   pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                    "end": 30.0},
                                   {"id": 2, "name": "定位点2", "start": 60.0,
                                    "end": 90.0}])},
                   cur=1, pos=95.0)
    app2.watch.set_duration(30.0, 60.0)     # 段2 坐标在场
    assert not app2._repick_by_position("工程A", 95.0)
    assert app2.cur == 1
    assert app2.watch.durs == [30.0, 0] and app2.watch.duration == 0


def test_repick_heals_after_gap_zeroing():
    """空档清零后位置回段内：①静默恢复段坐标（指针不动、不刷日志）。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 30.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 90.0}])},
                  cur=0, pos=10.0)
    app.watch.set_duration(30.0, 0.0)       # 段1 坐标在场
    assert not app._repick_by_position("工程A", 45.0)   # 空档：清零
    assert app.watch.duration == 0
    assert not app._repick_by_position("工程A", 10.0)   # 回段内：静默恢复
    assert app.cur == 0
    assert app.watch.durs == [30.0, 0, 30.0]
    assert app.watch.duration == 30.0
    assert not any("按位置对位" in m for m in app.logs)


def test_repick_advance_protocol_gate():
    """自动推进协议在途（armed、未 fire、播过段尾仍在播）：位置对位让路
    ——此刻换段坐标会把「停止→已播 0 秒不切换」打成 tick/poll 相位赌局
    的随机死锁；推进闩已闭合（fired，全停抑制/已推进）则放行。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 120.0}])},
                  cur=0, pos=70.0)
    app.watch.set_duration(60.0, 0.0)
    app.watch.armed, app.watch._live = True, True
    assert not app._repick_by_position("工程A", 70.0)   # 协议在途：不动
    assert app.cur == 0 and app.watch.durs == [60.0]
    # QF 刚断（0.25s 翻假）但推进仍在收尾（poll 100ms+calls 50ms 交接缝）：
    # 0.5s 缓冲窗内同样让路——否则 tick 落在缝里换段坐标=推进随机死锁
    app.watch._live, app.watch._since_live = False, 0.2
    assert not app._repick_by_position("工程A", 70.0)
    assert app.cur == 0
    # 停止态拖拽（久无 QF）：不在协议窗内，正常走空档规则
    app.watch._since_live = 30.0
    assert app._repick_by_position("工程A", 70.0) is True
    assert app.cur == 1
    app.watch._fired = True                    # 闩闭合（全停抑制）：放行
    app2 = _pt_app(pl=("pt:A#1", "pt:A#2"),
                   pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                    "end": 60.0},
                                   {"id": 2, "name": "定位点2", "start": 60.0,
                                    "end": 120.0}])},
                   cur=0, pos=70.0)
    app2.watch.set_duration(60.0, 0.0)
    app2.watch.armed, app2.watch._live = True, True
    app2.watch._fired = True
    assert app2._repick_by_position("工程A", 70.0) is True
    assert app2.cur == 1


def test_repick_before_all_starts_clears_pointer():
    """位置在本工程全部点起点之前：指针置空（NOW 裸名/NEXT 空，「下一
    首」回首个点条目），进入任一段后自动恢复。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 50.0,
                                   "end": 100.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 90.0}])},
                  cur=0, pos=10.0)
    assert app._repick_by_position("工程A", 10.0) is True
    assert app.cur is None
    assert app.watch.durs == [0] and app.watch.lefts == [0.0]
    assert any("指针置空" in m for m in app.logs)
    assert not app._repick_by_position("工程A", 10.0)   # 不重复置空
    # 位置进入段内：从空指针恢复（含位置的首个=定位点1）
    assert app._repick_by_position("工程A", 70.0) is True
    assert app.cur == 0
    assert app.watch.durs == [0, 50.0] and app.watch.lefts == [0.0, 50.0]


def test_repick_bare_project_noop():
    """本工程无点条目（未启用定位点/裸条目模式）：位置再怪也不动指针。"""
    app = _pt_app(pl=("A", "B"), pts={}, cur=0, pos=123.0)
    assert not app._repick_by_position("工程A", 123.0)
    assert app.cur == 0 and app.watch.durs == []


def test_repick_foreign_pointer_untouched():
    """指针在他曲条目：对位不动（归 _adopt 按 DAW 标题对位管）。"""
    app = _pt_app(pl=("B", "pt:A#1"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0}])},
                  cur=0, pos=30.0)
    assert not app._repick_by_position("工程A", 30.0)
    assert app.cur == 0 and app.watch.durs == []


def test_repick_goto_in_flight_gated():
    """程序内 GOTO 在途（_goto_ack 1.2s 窗）：位置对位让路（位置即将跳变，
    程序跳转是权威）。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 30.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 90.0}])},
                  cur=1, pos=10.0)            # 位置还在段1（旧位置）
    app._goto_ack = (10.0, time.time(), "x")
    assert not app._repick_by_position("工程A", 10.0)
    assert app.cur == 1 and app.watch.durs == []


def test_repick_unknown_position_noop():
    """位置未知（未收到时码）：不动任何状态。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 30.0,
                                   "end": 90.0}])},
                  cur=0, pos=None)
    assert not app._repick_by_position("工程A", None)
    assert app.cur == 0 and app.watch.durs == []


def test_repick_adjacent_relays_at_boundary():
    """相邻段（段1 [0,60]、段2 [60,120]）：位置过界（>60）自然接力到段2；
    恰在界点上（=60，两段都含）保持现指针。"""
    app = _pt_app(pl=("pt:A#1", "pt:A#2"),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 0.0,
                                   "end": 60.0},
                                  {"id": 2, "name": "定位点2", "start": 60.0,
                                   "end": 120.0}])},
                  cur=0, pos=60.5)
    assert app._repick_by_position("工程A", 60.5) is True
    assert app.cur == 1
    assert app.watch.durs == [60.0] and app.watch.lefts == [60.0]
    assert not app._repick_by_position("工程A", 60.0)   # 界点保持现指针


def test_adopt_pt_entry_defers_coords(monkeypatch):
    """定位点条目的手动采纳：跨工程立即作废旧位置（reset+0），装载提交
    也不写段坐标（首点未必含真实位置，坐标归 _repick_by_position 按时码
    填），durations 不为 pt 键立目。"""
    monkeypatch.setattr(setlist_gui.cpr_meta, "read_locators",
                        lambda p: (0.0, 120.0))
    monkeypatch.setattr(setlist_gui.kbd_auto, "load_slots",
                        lambda p, kind=None, point_id=None: {})
    app = _app(pl=("pt:a#1",))
    app.points = {"a": {"enabled": True, "counter": 1,
                        "points": [{"id": 1, "name": "定位点1",
                                    "start": 0.0, "end": 60.0}]}}
    setlist_gui.App._adopt(app, "X")
    assert app.cur == 0
    assert app.watch.ops[:2] == ["reset", "dur"]        # 采纳即刻作废旧位置
    assert app.watch.durs[0] == 0
    assert _wait(app, lambda: app.cur_song_path == "/p1")
    _drain(app)
    # 提交半程：纪元复位+坐标仍 0（段坐标归位置对位）；粒度标签随装载落位
    assert app.watch.ops == ["reset", "dur", "reset", "dur"]
    assert app.watch.durs == [0, 0]
    assert "pt:a#1" not in app.durations
    assert app.cur_point_id == 1


def test_now_entry_display_matrix():
    """NOW 显示名：段内→条目名（与 NEXT/播放列表同一套）；空档/他曲/无
    指针/无时码→None（回落裸工程名——指针只是按标题猜的，不当事实显）；
    裸条目→工程名。"""
    e_pt = {"display": "工程A · 定位点2", "song_name": "工程A",
            "point": {"start": 60.0, "end": 120.0}}
    e_bare = {"display": "工程A", "song_name": "工程A", "point": None}
    e_other = {"display": "工程B · 定位点1", "song_name": "工程B",
               "point": {"start": 0.0, "end": 10.0}}
    f = setlist_gui.App._now_entry_display
    assert f(e_pt, "工程A", 90.0) == "工程A · 定位点2"   # 段内
    assert f(e_pt, "工程A", 60.0) == "工程A · 定位点2"   # 双端含
    assert f(e_pt, "工程A", 45.0) is None                # 空档→裸名
    assert f(e_pt, "工程A", None) is None                # 无时码→裸名
    assert f(e_bare, "工程A", 1.0) == "工程A"            # 裸条目
    assert f(e_bare, "工程A", None) == "工程A"           # 裸条目无时码同
    assert f(e_other, "工程A", 5.0) is None              # 指针在他曲
    assert f(None, "工程A", 5.0) is None                 # 无指针（置空/游离）


class _FakeCtrl:
    def __init__(self, stop_ok=True):
        self.calls = []
        self.stop_ok = stop_ok

    def transport(self, action, live=None):
        self.calls.append(action)
        return self.stop_ok or action != "stop"


def _play_app(monkeypatch, stop_ok=True, cur_song_path="/pA"):
    """_point_play 行为桩：当前歌=工程A 的定位点段 [30, 90]。"""
    app = _pt_app(pl=("pt:A#1",),
                  pts={"A": _pts([{"id": 1, "name": "定位点1", "start": 30.0,
                                   "end": 90.0}])},
                  cur=0)
    app.cur_song_path = cur_song_path
    app.ctrl = _FakeCtrl(stop_ok)
    app.mmc_out = 7
    app.mtc = None
    app._goto_ack = None
    app._transport_state = lambda: "stopped"
    app._cur_point_entry = lambda: setlist_gui.App._cur_point_entry(app)
    app._goto_send = lambda start, label: \
        setlist_gui.App._goto_send(app, start, label)
    sent = []
    monkeypatch.setattr(setlist_gui.mb, "send_sysex_h",
                        lambda h, data, timeout=1.0: sent.append((h, data))
                        or None)
    monkeypatch.setattr(setlist_gui.time, "sleep", lambda s: None)
    return app, sent


def test_point_play_starts_at_segment(monkeypatch):
    """定位点歌的「开始」：不发回零、GOTO 段起点后原地起播。"""
    app, sent = _play_app(monkeypatch)
    assert setlist_gui.App._point_play(app) is True
    assert sent == [(7, setlist_gui.points.mmc_goto_bytes(30.0))]
    assert app.ctrl.calls == ["resume"]          # 无 stop/无 play=回零路径


def test_point_play_stops_first_when_live(monkeypatch):
    """时码活跃（含盲停态兜底）：先停再 GOTO——播放中 GOTO 被忽略。"""
    app, sent = _play_app(monkeypatch)
    app._transport_state = lambda: "paused"
    assert setlist_gui.App._point_play(app) is True
    assert app.ctrl.calls == ["stop", "resume"]


def test_point_play_stop_fail_no_start(monkeypatch):
    """停止未送达：整单放弃（回 False 交通用路径，起播防半截）。"""
    app, sent = _play_app(monkeypatch, stop_ok=False)
    app._transport_state = lambda: "paused"
    assert setlist_gui.App._point_play(app) is True
    assert sent == [] and app.ctrl.calls == ["stop"]


def test_point_play_falls_back_when_not_point_mode(monkeypatch):
    """整曲歌/工程不对位/无指针：回 False 走通用 play（回零从头播）。"""
    app, sent = _play_app(monkeypatch, cur_song_path="/other")
    assert setlist_gui.App._point_play(app) is False
    app2, _ = _play_app(monkeypatch)
    app2.pl_keys = ["A"]                          # 裸条目
    app2.cur = 0
    assert setlist_gui.App._point_play(app2) is False
    app3, _ = _play_app(monkeypatch)
    app3.cur = None                               # 无指针
    assert setlist_gui.App._point_play(app3) is False
    assert sent == []


def test_point_play_skips_goto_when_already_at_start(monkeypatch):
    """停泊位已是段起点（切换链路刚 GOTO 过/再次按开始）：不再发 GOTO
    ——同位 GOTO 时码不变会触发「工程未响应定位」误报。"""
    app, sent = _play_app(monkeypatch)
    app.mtc = types.SimpleNamespace(position=lambda: 30.04)
    assert setlist_gui.App._point_play(app) is True
    assert sent == [] and app.ctrl.calls == ["resume"]
    # 停在中途：正常 GOTO 回段起点
    app.mtc.position = lambda: 61.0
    assert setlist_gui.App._point_play(app) is True
    assert len(sent) == 1 and app.ctrl.calls == ["resume", "resume"]


def test_point_rewind_goes_to_segment_start(monkeypatch):
    """定位点歌的「回零」：GOTO 回段起点而非工程头（通用 RTZ 落工程头会
    让位置对位判空档→指针置空）。已在段起点时不再定位（免同位误报）。"""
    app, sent = _play_app(monkeypatch)
    assert setlist_gui.App._point_rewind(app) is True
    assert sent == [(7, setlist_gui.points.mmc_goto_bytes(30.0))]
    assert app.ctrl.calls == [] and app.watch.resets == 1
    app.mtc = types.SimpleNamespace(position=lambda: 30.02)
    assert setlist_gui.App._point_rewind(app) is True
    assert len(sent) == 1 and app.watch.resets == 2  # 已在起点：不重发
    app2, _ = _play_app(monkeypatch, cur_song_path="/other")
    assert setlist_gui.App._point_rewind(app2) is False   # 他曲：走通用回零


def test_sync_watch_span_reanchors_after_edit(monkeypatch):
    """定位点定义变更后段坐标重锚：指针在编辑工程=按新段重设；停用=还原
    整曲时长（left 归 0，未知时长=0 推进暂闭）；他曲不动。"""
    app, _ = _play_app(monkeypatch)
    app.durations = {"A": 500.0}
    setlist_gui.App._sync_watch_span(app)
    assert app.watch.durs == [60.0] and app.watch.lefts == [30.0]
    app.pl_keys = ["A"]                     # 停用后的裸条目
    app.cur = 0
    setlist_gui.App._sync_watch_span(app)
    assert app.watch.durs == [60.0, 500.0]
    assert app.watch.lefts == [30.0, 0.0]   # 旧段 left 不许带进整曲
    app.cur_song_path = "/other"            # 指针在他曲：不再动
    app.pl_keys = ["A"]
    setlist_gui.App._sync_watch_span(app)
    assert len(app.watch.durs) == 2
