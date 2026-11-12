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


def test_sync_list_reloads_after_deleting_current_point(tmp_path):
    """删掉正在播放的定位点：粒度标签跟随落点条目重装载（原实现滞留死点
    段且对位规则①抑制自愈）。"""
    import threading
    import types
    import setlist_gui
    cpr = tmp_path / "song.cpr"
    cpr.write_text("", encoding="utf-8")
    kbd_auto.save_slots(str(cpr), {72: {"pc": 1}})
    kbd_auto.save_slots(str(cpr), {72: {"pc": 2}}, point_id=1)
    app = types.SimpleNamespace(
        by_key={"L": {"key": "L", "name": "歌", "path": str(cpr)}},
        points={"L": {"enabled": True, "counter": 2, "points": [
            {"id": 1, "name": "P1", "start": 0.0, "end": 30.0},
            {"id": 2, "name": "P2", "start": 30.0, "end": 60.0}]}},
        pl_keys=["pt:L#1", "pt:L#2"], cur=0,
        cur_song_path=str(cpr), cur_point_id=1,
        slots={72: {"pc": 2}}, ax_slots={},
        _load_lock=threading.Lock(), _persist=lambda: None)
    app._resolve_entry = lambda k: setlist_gui.App._resolve_entry(app, k)
    app.points["L"]["points"] = app.points["L"]["points"][1:]  # 删点1
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.cur_point_id == 2            # 粒度跟随落点（点2 无段→回落）
    assert app.slots == {72: {"pc": 1}}     # 点2 回落工程级映射
    # 增删他点不动当前装载：零动作
    app.points["L"]["points"].append(
        {"id": 3, "name": "P3", "start": 60.0, "end": 90.0})
    assert setlist_gui.App._points_sync_list(app, "L") is None
    assert app.cur_point_id == 2 and app.slots == {72: {"pc": 1}}
