# -*- coding: utf-8 -*-
"""Studio One .song 工程时长解析（S1 版 cpr_meta 对应物）。

.song 是 ZIP 容器，Song/song.xml 为明文 XML：事件带 start/length（四分音
符拍），TempoMap 分段给 tempo（秒/拍）。工程时长 = 可发声事件绝对终点
（start+length 拍）的最大值换算成秒——S1 播放到最后一轨事件结束才断时钟，
与自动推进「时钟断流且活跃≥时长」的判定口径一致（cpr_meta 的定位条同理
是「内容终点」语义）。

解析规则：
- 事件 = 带 start+length 属性的发声元素（AudioEvent/AudioPartEvent/
  InstrumentEvent 等）；无 start 的是 Part 内部相对事件，跳过（其容器
  Part 自带 start+length，已按容器终点计入）；
- MarkerEvent 等非发声元素不计入；
- Root/Attributes 的 length 属性是占位默认值（新建工程恒 300），不采信；
- 拍→秒按 TempoMap 分段 tempo 线性换算，兼容 TempoMapSegment 与
  AudioTempoMapSegment 两代标签、start/offset 两种属性名；段缺失时退回
  metainfo.xml 的 Media:Tempo（BPM）；都拿不到→None（手填兜底）。
"""
import re
import zipfile

import cpr_meta

# S1 内置节拍器/用户自建 Click 采样轨的默认轨名；是否排除由真机对表定案
# （S1 停表语义=全部轨播完，默认不排除；对表若证实 Click 远端残段不参与
# 停表，再把该名单交给 read_duration 启用）
CLICK_TRACKS = frozenset(("Click",))

_TRACK = re.compile(r'(?=<MediaTrack\b)')       # lookahead 分块：开标签（含轨名）留在块内
_ELEM = re.compile(r'<([A-Za-z][\w]*)\b([^>]*?)/?>', re.S)
_TEMPO_SEG = re.compile(
    r'<(?:TempoMapSegment|AudioTempoMapSegment)\b([^>]*)>', re.S)
_BPM = re.compile(r'id="Media:Tempo"[^>]*value="([0-9.eE+-]+)"')
_DBL_MAX = 1.7976931348623157e+308
_AUDIBLE = ("AudioEvent", "AudioPartEvent", "InstrumentEvent")


def _attr(attrs, name):
    m = re.search(r'\b%s="([^"]*)"' % name, attrs)
    return m.group(1) if m else None


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _tempo_secs_per_beat(zf):
    """节拍图分段 [(start_beat, spb)]；空表=拍速未知。"""
    xml = zf.read("Song/song.xml").decode("utf-8", "replace")
    segs = []
    for attrs in _TEMPO_SEG.findall(xml):
        start = _f(_attr(attrs, "start"))
        if start is None:
            start = _f(_attr(attrs, "offset"))
        tempo = _f(_attr(attrs, "tempo"))       # 秒/拍（120BPM → 0.5）
        if start is not None and tempo and tempo > 0:
            segs.append((start, tempo))
    if segs:
        segs.sort()
        return segs
    try:                                        # 兜底：metainfo 的 BPM
        meta = zf.read("metainfo.xml").decode("utf-8", "replace")
        bpm = _f(_BPM.search(meta).group(1))
        return [(0.0, 60.0 / bpm)] if bpm and bpm > 0 else []
    except (KeyError, AttributeError, ValueError):
        return []


def _beats_to_sec(beats, segs):
    sec = 0.0
    for i, (start, spb) in enumerate(segs):
        seg_end = segs[i + 1][0] if i + 1 < len(segs) else _DBL_MAX
        if beats <= start:
            break
        sec += (min(beats, seg_end) - start) * spb
    return sec


def _track_ends(xml):
    """按轨聚合事件终点（拍）：{轨名: maxEnd}；轨名缺省记 ''。"""
    ends = {}
    for ch in _TRACK.split(xml)[1:]:            # 每块以 <MediaTrack 开标签起
        name = (_attr(ch[:ch.find(">")], "name") or "").strip()
        for tag, attrs in _ELEM.findall(ch):
            if tag not in _AUDIBLE:
                continue
            start, length = _f(_attr(attrs, "start")), _f(_attr(attrs, "length"))
            if start is None or length is None:
                continue                        # Part 内部相对事件：跳过
            end = start + length
            if 0 <= end < _DBL_MAX and end > ends.get(name, -1):
                ends[name] = end
    return ends


def read_duration(path):
    """返回工程时长秒数；解析不出（坏包/缺节拍/无事件）返回 None。"""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("Song/song.xml").decode("utf-8", "replace")
            segs = _tempo_secs_per_beat(zf)
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    if not segs:
        return None
    ends = _track_ends(xml).values()
    if not ends:
        return None
    return _beats_to_sec(max(ends), segs)


# fmt_mmss / parse_mmss 与 .cpr 共用同一套显示换算
fmt_mmss = cpr_meta.fmt_mmss
parse_mmss = cpr_meta.parse_mmss
