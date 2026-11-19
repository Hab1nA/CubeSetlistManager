# -*- coding: utf-8 -*-
"""定位点粒度音色映射：pointSlots 段读写/懒继承/回落语义/删段。"""
import json

import kbd_auto
import points


def _cpr(tmp_path):
    return str(tmp_path / "song.cpr")


def test_point_section_roundtrip(tmp_path):
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {72: {"msb": 85, "pc": 1}})
    kbd_auto.save_slots(cpr, {72: {"msb": 1, "pc": 2}}, point_id=3)
    # 工程级与点级互不干扰
    assert kbd_auto.load_slots(cpr)[72]["msb"] == 85
    assert kbd_auto.load_slots(cpr, point_id=3)[72]["msb"] == 1


def test_mixed_anchor_migration_no_clobber(tmp_path):
    """旧新锚号混存：+12 会撞上已有新号的旧号原地保留（死区不触发），
    不盲迁移静默顶掉新号。"""
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {60: {"pc": 1}, 72: {"pc": 2}})  # 60=旧号（文件原样）
    # 文件里手工混入旧号 60 与新号 72（save 原样写）——load 时 60+12 撞 72
    s = kbd_auto.load_slots(cpr)
    assert s[72] == {"pc": 2}          # 新号保留
    assert s[60] == {"pc": 1}          # 旧号原地保留（60 不在触发区）
    # 纯旧号段照旧整段迁移
    kbd_auto.save_slots(cpr, {61: {"pc": 3}})
    assert kbd_auto.load_slots(cpr) == {73: {"pc": 3}}


def test_lazy_inherit_fallback(tmp_path):
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {72: {"pc": 5}, 84: {"pc": 6}})
    kbd_auto.save_slots(cpr, {84: {"pc": 7}}, "ax", point_id=1)
    # 无段的琴回落工程级；有段的琴读点段
    assert kbd_auto.load_slots(cpr, point_id=1)[72]["pc"] == 5
    assert kbd_auto.load_slots(cpr, "ax", point_id=1)[84]["pc"] == 7
    # 完全没写过段的点：整体回落
    assert kbd_auto.load_slots(cpr, point_id=99)[72]["pc"] == 5


def test_empty_section_means_cleared(tmp_path):
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {72: {"pc": 5}})
    kbd_auto.save_slots(cpr, {72: {"pc": 8}}, point_id=2)
    kbd_auto.save_slots(cpr, {}, point_id=2)      # 显式清空 ≠ 回落
    assert kbd_auto.load_slots(cpr, point_id=2) == {}
    assert kbd_auto.load_slots(cpr) == {72: {"pc": 5}}  # 工程级不动


def test_empty_sections_persist_no_fallback(tmp_path):
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {72: {"pc": 1}}, point_id=4)
    kbd_auto.save_slots(cpr, {}, point_id=4)
    kbd_auto.save_slots(cpr, {}, "ax", point_id=4)
    # 两琴段都显式清空：段保留、读入为空，不回落工程级
    assert kbd_auto.load_slots(cpr, point_id=4) == {}
    assert kbd_auto.load_slots(cpr, "ax", point_id=4) == {}


def test_delete_point_slots(tmp_path):
    cpr = _cpr(tmp_path)
    kbd_auto.save_slots(cpr, {72: {"pc": 1}}, point_id=5)
    kbd_auto.save_slots(cpr, {72: {"pc": 2}}, point_id=6)
    kbd_auto.delete_point_slots(cpr, 5)
    assert kbd_auto.load_slots(cpr, point_id=5) == {}
    assert kbd_auto.load_slots(cpr, point_id=6)[72]["pc"] == 2
    data = json.loads(kbd_auto._slot_path(cpr).read_text(encoding="utf-8"))
    assert set(data["pointSlots"]) == {"6"}


def test_old_anchor_migration_applies_to_point_section(tmp_path):
    cpr = _cpr(tmp_path)
    p = kbd_auto._slot_path(cpr)
    p.write_text(json.dumps({
        "slots": {72: {"pc": 1}},
        "pointSlots": {"1": {"slots": {60: {"msb": 85, "pc": 1}}}},
    }, ensure_ascii=False), encoding="utf-8")
    assert kbd_auto.load_slots(cpr, point_id=1) == {72: {"msb": 85, "pc": 1}}


def test_dirty_point_id_is_ignored(tmp_path):
    cpr = _cpr(tmp_path)
    p = kbd_auto._slot_path(cpr)
    p.write_text(json.dumps({
        "slots": {72: {"pc": 1}},
        "pointSlots": {"__proto__": {"slots": {72: {"pc": 9}}}},
    }, ensure_ascii=False), encoding="utf-8")
    # 非数字 id 段不会被读入（load 侧 point_id 始终来自内部 int）
    assert kbd_auto.load_slots(cpr, point_id=1)[72]["pc"] == 1


def test_dirty_leaf_section_falls_back(tmp_path):
    cpr = _cpr(tmp_path)
    p = kbd_auto._slot_path(cpr)
    for bad in ('"abc"', "[1, 2]"):
        p.write_text(json.dumps({
            "slots": {72: {"pc": 1}},
            "pointSlots": {"1": {"slots": json.loads(bad)}},
        }, ensure_ascii=False), encoding="utf-8")
        # 叶子段真值非 dict：回落工程级而非 AttributeError
        assert kbd_auto.load_slots(cpr, point_id=1)[72]["pc"] == 1


def test_goto_worker_reloads_point_slots(tmp_path):
    """同工程定位点切换（踩钉下一首/跳到这点/段间推进共用路径）必须按点
    粒度重装载：11-13 真机翻案=该路径只挪指针不动槽位=琴上仍发旧点音色。"""
    import queue
    import threading
    import types
    import setlist_gui
    cpr = tmp_path / "song.cpr"
    cpr.write_text("", encoding="utf-8")
    kbd_auto.save_slots(str(cpr), {72: {"pc": 1}})          # 工程级
    kbd_auto.save_slots(str(cpr), {72: {"pc": 51}}, point_id=1)
    kbd_auto.save_slots(str(cpr), {72: {"pc": 52}}, point_id=2)
    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 2, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0}]}},
        pl_keys=["pt:L#1", "pt:L#2"], end_beh=["", ""], cur=0,
        cur_song_path=str(cpr), cur_point_id=1,
        slots={72: {"pc": 51}}, ax_slots={},
        _load_lock=threading.Lock(), calls=queue.Queue(), watch=None,
        ctrl=types.SimpleNamespace(busy=False, transport=lambda a: None),
        q=_Q())
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    app._goto_send = lambda start, label: None
    setlist_gui.App._goto_worker(app, 1,
                                 app._resolve_entry("pt:L#2"), False, False)
    # finish 已入 calls 队列：排干执行（主线程语义）
    while True:
        try:
            app.calls.get_nowait()()
        except queue.Empty:
            break
    assert app.cur == 1 and app.cur_point_id == 2
    assert app.slots == {72: {"pc": 52}}    # 切点后 live 槽位=新点段


class _Q:
    def put(self, v):
        pass


def test_sync_list_reloads_after_deleting_current_point(tmp_path):
    """删掉正在播放的定位点：粒度标签跟随落点条目重装载（原实现滞留死点
    段且对位规则①抑制自愈）；节目单=用户裁剪——定义编辑只下架点已删的
    条目，不补新点、不复活已删段（旧全量镜像语义废弃）。"""
    import threading
    import types
    import setlist_gui
    cpr = tmp_path / "song.cpr"
    cpr.write_text("", encoding="utf-8")
    kbd_auto.save_slots(str(cpr), {72: {"pc": 1}})
    kbd_auto.save_slots(str(cpr), {72: {"pc": 2}}, point_id=1)
    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)},
                "X": {"key": "X", "name": "他歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 2, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0}]}},
        pl_keys=["pt:L#1", "pt:L#2"], end_beh=["", ""], cur=0,
        cur_song_path=str(cpr), cur_point_id=1, watch=None,
        slots={72: {"pc": 2}}, ax_slots={},
        _load_lock=threading.Lock(), _persist=lambda: None)
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    app._repick_cur_by_position = lambda k: \
        setlist_gui.App._repick_cur_by_position(app, k)
    app._sync_watch_span = lambda: setlist_gui.App._sync_watch_span(app)
    app.points["L"]["points"] = app.points["L"]["points"][1:]  # 删点1
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.cur_point_id == 2            # 粒度跟随落点（点2 无段→回落）
    assert app.slots == {72: {"pc": 1}}     # 点2 回落工程级映射
    assert app.pl_keys == ["pt:L#2"]        # 裁剪同步：只下架被删的段
    assert app.end_beh == [""]              # 行为表逐位对齐未漂移（2 点块缩 1 位）
    # 编辑器加点不自动上节目单（加段走素材库＋按钮）；增删他点不动装载
    app.points["L"]["points"].append(
        {"id": 3, "name": "P3", "start": 60.0, "end": 90.0})
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.pl_keys == ["pt:L#2"]        # 新点 P3 不复活/不追加
    assert app.cur_point_id == 2 and app.slots == {72: {"pc": 1}}
    assert app.end_beh == [""]
    # 出口段被删（块内还有幸存段）：行为迁到幸存出口（同 remove 律）；
    # 出口非列表末位，推进行为保留
    app.pl_keys = ["pt:L#2", "pt:L#3", "X"]
    app.end_beh = ["", "delay:5", ""]
    app.points["L"]["points"] = app.points["L"]["points"][:-1]  # 删 P3
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.pl_keys == ["pt:L#2", "X"]
    assert app.end_beh == ["delay:5", ""]   # 出口行为迁到幸存出口


def test_sync_list_keeps_curation(tmp_path):
    """节目单裁剪与离散块不受定义编辑侵扰：改时码/改名不触发任何下架，
    离散块（同歌两块中间隔着别歌）不会被合并回首块（旧镜像会）。"""
    import threading
    import types
    import setlist_gui
    cpr = tmp_path / "song.cpr"
    cpr.write_text("", encoding="utf-8")
    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)},
                "X": {"key": "X", "name": "他歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 3, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0},
            {"id": 3, "name": "P3", "start": 60.0, "end": 90.0}]}},
        pl_keys=["pt:L#2", "X", "pt:L#3"],   # 离散块：P3 排在别歌后（返场）
        end_beh=["next", "", ""], cur=None, watch=None,
        cur_song_path=None, cur_point_id=None,
        slots={}, ax_slots={}, durations={},
        _load_lock=threading.Lock(), _persist=lambda: None)
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    app._repick_cur_by_position = lambda k: \
        setlist_gui.App._repick_cur_by_position(app, k)
    app._sync_watch_span = lambda: setlist_gui.App._sync_watch_span(app)
    # 改时码/改点名：节目单原样
    app.points["L"]["points"][0]["end"] = 25.0
    app.points["L"]["points"][0]["name"] = "P2 改名"
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.pl_keys == ["pt:L#2", "X", "pt:L#3"]
    assert app.end_beh == ["next", "", ""]
    # 删一个不在节目单里的点（P1 本就没上列表）：无影响
    app.points["L"]["points"] = [
        p for p in app.points["L"]["points"] if p["id"] != 1]
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.pl_keys == ["pt:L#2", "X", "pt:L#3"]


def test_points_sync_reanchors_watch(tmp_path):
    """定义变更（改在播段终点）后段坐标重锚+按位置选段：旧时长继续用会
    缩短晚切、放长早切（4710c2a 改槽位时失落的调用）；位置在后续段时
    盲锚首段会在 100ms poll 误判播完（早停+跳回段头重播）。"""
    import threading
    import types
    import setlist_gui
    cpr = tmp_path / "song.cpr"
    cpr.write_text("", encoding="utf-8")
    spans = []

    class W:
        def reset(self):
            pass

        def set_duration(self, sec, left=None):
            spans.append((sec, left))

        def position(self):
            return 35.0                    # 在播：位置落在 P2 段内

    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 2, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0}]}},
        pl_keys=["pt:L#1", "pt:L#2"], end_beh=["", ""], cur=0,
        cur_song_path=str(cpr), cur_point_id=2, watch=W(),
        slots={}, ax_slots={}, durations={},
        _load_lock=threading.Lock(), _persist=lambda: None)
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app._reload_entry_slots = lambda e: \
        setlist_gui.App._reload_entry_slots(app, e)
    app._repick_cur_by_position = lambda k: \
        setlist_gui.App._repick_cur_by_position(app, k)
    app._sync_watch_span = lambda: setlist_gui.App._sync_watch_span(app)
    # 在播段 P2 终点 60→90（放长）：重锚取新段长，不再沿用装载时旧值
    app.points["L"]["points"][1]["end"] = 90.0
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.cur == 1                    # 位置选段：35 落 P2
    assert spans[-1] == (60.0, 30.0)       # 新段长 90-30、左偏移 30
