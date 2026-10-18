# -*- coding: utf-8 -*-
"""MTC 解码器单测：QF 顺序装配/乱序弃组/中途加入/速率换算/纪元复位、
全帧扫描（杂前缀/多条取末/越界丢弃/非 MTC 忽略）、计数与回调分发。
纯逻辑零 winmm；全部合成字节，无需任何设备在场。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import mtc


def _dec(on_qf=None, on_position=None, on_full=None):
    return mtc.Decoder(on_qf=on_qf or (lambda: None),
                       on_position=on_position or (lambda s: None),
                       on_full=on_full)


def _feed_cycle(dec, h=0, m=0, s=0, f=0, rate=3):
    for b in mtc.qf_bytes(h, m, s, f, rate):
        dec.feed(b)


def test_qf_assembly_and_rates():
    pos, qfs = [], []
    d = _dec(on_qf=lambda: qfs.append(1), on_position=pos.append)
    _feed_cycle(d, 1, 2, 3, 15, rate=3)             # 30fps：1:02:03.5
    assert pos == [3600 + 120 + 3 + 0.5]
    _feed_cycle(d, 0, 0, 0, 12, rate=0)             # 24fps：帧/24
    assert abs(pos[1] - 0.5) < 1e-9
    _feed_cycle(d, 0, 1, 1, 24, rate=1)             # 25fps：61+24/25
    assert abs(pos[2] - (61 + 0.96)) < 1e-9
    assert len(qfs) == 24 and d.qf_seen == 24       # 每 QF 一次活跃回调
    assert d.position() == pos[-1]


def test_qf_join_midcycle_and_disorder():
    pos = []
    d = _dec(on_position=pos.append)
    for b in mtc.qf_bytes(0, 0, 10, 0)[3:]:         # 中途加入：先吃半组
        d.feed(b)
    assert pos == []                                # 没有完整组不出位置
    _feed_cycle(d, 0, 0, 10, 0)
    assert pos == [10.0]
    d.feed(mtc.qf_bytes(0, 0, 20, 0)[5])            # 类型 5 孤条：弃组
    d.feed(mtc.qf_bytes(0, 0, 20, 0)[5])            # 重复同样弃
    _feed_cycle(d, 0, 0, 20, 0)
    assert pos == [10.0, 20.0]                      # 弃组后等 0 重起，不产伪值


def test_qf_out_of_range_discards():
    pos = []
    d = _dec(on_position=pos.append)
    _feed_cycle(d, 0, 77, 3, 0)                     # 分钟 77 越界 → 整组作废
    assert pos == [] and d.position() is None


def test_reset_epoch():
    pos = []
    d = _dec(on_position=pos.append)
    _feed_cycle(d, 0, 0, 5, 0)
    assert d.position() == 5.0
    d.reset()
    assert d.position() is None                     # 位置回未知
    for b in mtc.qf_bytes(0, 0, 9, 0)[:4]:          # 复位清装配组：
        d.feed(b)                                   #   旧半组不得接新组
    _feed_cycle(d, 0, 0, 9, 0)
    assert pos == [5.0, 9.0]                        # 新组完整才恢复


def test_full_frame_scan():
    pos, fulls = [], []
    d = _dec(on_position=pos.append, on_full=fulls.append)
    frame = b"\xf0\x7f\x7f\x01\x01" + bytes([0x61, 2, 3, 15]) + b"\xf7"
    d.full_frame(frame)                             # hh=0x61：1 时+30fps 速率位
    assert fulls == [3600 + 120 + 3 + 0.5] and pos == []   # 全帧走 on_full
    d.full_frame(b"\xaa\xbb" + frame + frame + b"\x00")     # 杂前缀+多条
    assert fulls[-1] == fulls[0] and len(fulls) == 2        # 一缓冲只发最新一条
    d.full_frame(b"\xf0\x7e\x00\x00\xf7")                   # 非 MTC SysEx
    d.full_frame(b"\xf0\x7f\x7f\x01\x01" + bytes([1, 77, 0, 0]))  # 分越界
    assert len(fulls) == 2
    assert d.position() == fulls[-1]


def test_full_frame_default_routes_to_position():
    pos = []
    d = mtc.Decoder(on_qf=lambda: None, on_position=pos.append)
    d.full_frame(b"\xf0\x7f\x7f\x01\x01" + bytes([0, 0, 7, 0]) + b"\xf7")
    assert pos == [7.0]                             # 未给 on_full 时并入主路


def test_f8_counter_and_epoch_not_clearing():
    d = _dec()
    for _ in range(10):
        d.note_f8()
    assert d.f8_seen == 10 and d.qf_seen == 0
    _feed_cycle(d, 0, 0, 1, 0)
    d.reset()
    assert d.f8_seen == 10 and d.qf_seen == 8       # 计数跟口不跟歌
