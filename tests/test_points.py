# -*- coding: utf-8 -*-
"""points 纯逻辑测试：sidecar 读写/容错/防穿越、区间校验、key 合成解析、
播放列表替换变换、MMC GOTO 字节。全部零设备（tmp_path 落盘、合成字节）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import points


def _data(*spans, enabled=True, counter=None):
    return {"enabled": enabled,
            "counter": counter if counter is not None else len(spans),
            "points": [{"id": i + 1, "name": "定位点%d" % (i + 1),
                        "start": s, "end": e}
                       for i, (s, e) in enumerate(spans)]}


# ---- sidecar 读写 ----

def test_sidecar_roundtrip(tmp_path):
    d = _data((0.0, 10.0), (15.0, 40.5))
    points.save_points(str(tmp_path), d)
    back, err = points.load_points(str(tmp_path))
    assert err is None
    assert back == d


def test_sidecar_missing_is_empty_enabled_off(tmp_path):
    back, err = points.load_points(str(tmp_path))
    assert err is None
    assert back == points.empty_data()
    assert back["enabled"] is False


def test_sidecar_corrupt_degrades_with_error(tmp_path):
    (tmp_path / points.SIDECAR).write_text("{not json", encoding="utf-8")
    back, err = points.load_points(str(tmp_path))
    assert back == points.empty_data()
    assert err and "读取失败" in err


def test_sidecar_dirty_row_skipped(tmp_path):
    (tmp_path / points.SIDECAR).write_text(
        '{"enabled": true, "counter": 3, "points": ['
        '{"id": 1, "name": "a", "start": 0, "end": 5},'
        '{"id": "x"},'
        '{"id": 2, "name": 7, "start": "8", "end": 9}]}', encoding="utf-8")
    back, err = points.load_points(str(tmp_path))
    assert err is None
    assert [p["id"] for p in back["points"]] == [1, 2]
    assert back["points"][1]["name"] == "7" and back["points"][1]["end"] == 9.0


def test_sidecar_traversal_normalizes_inside_target(tmp_path):
    # ".." 先被规范化：落点必须在解析后的目标目录之内，不得越过它
    root = tmp_path / "root"
    target = tmp_path / "sub"
    target.mkdir()                       # 目录不代建：在盘才可写（同工程目录）
    points.save_points(str(root / ".." / "sub"), points.empty_data())
    assert (target / points.SIDECAR).exists()
    assert not (root / points.SIDECAR).exists()
    back, err = points.load_points(str(target))
    assert err is None and back == points.empty_data()


def test_sidecar_broken_dir_degrades_without_raising(tmp_path):
    # 目录位是文件：Windows 归入 FileNotFoundError，等同缺失静默降级
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    back, err = points.load_points(str(blocker))
    assert back == points.empty_data()


# ---- 区间校验 ----

def test_validate_rejects_nonpositive_span():
    assert points.validate_point(10.0, 10.0)[0]
    assert points.validate_point(10.0, 5.0)[0]
    assert points.validate_point(10.0, 10.5)[0]        # <1 秒硬拒


def test_validate_short_span_soft_warn_only():
    err, warn = points.validate_point(0.0, 2.0)
    assert err is None and warn


def test_validate_ok_has_no_messages():
    assert points.validate_point(0.0, 60.0) == (None, None)


# ---- key 合成与解析 ----

def test_pt_key_roundtrip_with_slash_and_hash():
    key = points.pt_key("队伍/歌#名", 3)
    assert key == "pt:队伍/歌#名#3"
    assert points.parse_pt_key(key) == ("队伍/歌#名", 3)


def test_parse_rejects_non_pt_and_malformed():
    assert points.parse_pt_key("队伍/歌") is None
    assert points.parse_pt_key("pt:队伍/歌") is None
    assert points.parse_pt_key("pt:队伍/歌#x") is None


# ---- 播放列表替换变换 ----

def test_expand_replace_in_place():
    old = ["A", "B", "C"]
    new = points.expand_replace(old, "B", ["pt:B#1", "pt:B#2"])
    assert new == ["A", "pt:B#1", "pt:B#2", "C"]
    assert old == ["A", "B", "C"]                      # 入参不被改


def test_expand_noop_when_bare_absent_or_empty():
    assert points.expand_replace(["A"], "B", ["pt:B#1"]) == ["A"]
    assert points.expand_replace(["A", "B"], "B", []) == ["A", "B"]


def test_collapse_restore_at_first_point_position():
    old = ["A", "pt:B#1", "pt:B#2", "C"]
    assert points.collapse_restore(old, "B") == ["A", "B", "C"]


def test_collapse_noop_cases():
    assert points.collapse_restore(["A", "B", "pt:B#1"], "B") == ["A", "B"]
    assert points.collapse_restore(["A", "C"], "B") == ["A", "C"]


def test_remap_cur_follows_surviving_entry():
    old = ["A", "B", "C"]
    assert points.remap_cur(old, ["A", "C"], 2, "B") == 1


def test_remap_cur_expand_bare_to_first_point():
    old = ["A", "B", "C"]
    new = ["A", "pt:B#1", "pt:B#2", "C"]
    assert points.remap_cur(old, new, 1, "B") == 1
    assert points.remap_cur(old, new, 2, "B") == 3     # 后续条目跟随位移


def test_remap_cur_collapse_pt_to_restored_bare():
    old = ["A", "pt:B#2", "C"]
    new = ["A", "B", "C"]
    assert points.remap_cur(old, new, 1, "B") == 1


def test_remap_cur_none_and_out_of_range():
    assert points.remap_cur(["A"], ["A"], None, "A") is None
    assert points.remap_cur(["A"], ["A"], 5, "A") is None


# ---- MMC GOTO 字节 ----

def test_mmc_goto_zero_is_absolute_head():
    assert points.mmc_goto_bytes(0.0) == (
        b"\xf0\x7f\x7f\x06\x44\x06\x01\x60\x00\x00\x00\x00\xf7")


def test_mmc_goto_30fps_exact_bytes():
    # 1:01:01.5 @30fps → hr=0x60|1（帧率码 3 左移 5 位）
    assert points.mmc_goto_bytes(3661.5) == (
        b"\xf0\x7f\x7f\x06\x44\x06\x01\x61\x01\x01\x0f\x00\xf7")


def test_mmc_goto_frame_clamp_and_dev_mask():
    b = points.mmc_goto_bytes(59.999, dev=200)         # 29.97 帧→钳到 29
    assert b[7] == 0x60 and b[10] == 29 and b[2] == (200 & 0x7F)


# ---- 显示与命名 ----

def test_fmt_tc_hour_and_minute_forms():
    assert points.fmt_tc(0) == "0:00"
    assert points.fmt_tc(754) == "12:34"
    assert points.fmt_tc(3661) == "1:01:01"


def test_next_point_counter_never_reuses(tmp_path):
    d = points.initial_data(120.0)
    assert d["points"][0]["name"] == "定位点1"
    points.next_point(d, 30.0, 60.0)
    del d["points"][1]                                 # 删掉定位点2
    pt, d = points.next_point(d, 0.0, 1.0)
    assert pt["id"] == 3 and pt["name"] == "定位点3"   # 编号不回补
