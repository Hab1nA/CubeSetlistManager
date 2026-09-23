# -*- coding: utf-8 -*-
"""Cubase 工程文件(.cpr)时长解析：不开 Cubase 直接读「工程时长」。
本机 70 个工程实测：.cpr 是 RIFF/RIF2 二进制容器（不是 XML），
工程时长 = 走带循环定位条 Cycle Left/Right 两个 Time（秒，8 字节 BE double）
之差（时长近似值，可手填覆盖；实测 Cubase 播到头并不会自动停，到点停走带
由 advance.AdvanceWatch 程序接管）。库内 65/70 可直接读出，
其余未设定位条（左右均为 0）→ 返回 None，由调用方手填兜底。
记录编码：[BE32 长度=strlen+1][字段名+\0][类型标记][8 字节 BE 值]，
Cycle Left/Right 与其 Time 字段在文件内连续出现且位置唯一（实测全库唯一命中）。"""
import re
import struct

# 首次命中即取值（与全库实测脚本一致）；(Left|Right) 捕获组区分左右定位条
_RE_CYCLE = re.compile(
    rb"Cycle (Left|Right)\x00\x00\x02\x00\x06\x00\x00\x00\x02"
    rb"\x00\x00\x00\x05Time\x00\x00\x04(.{8})", re.S)


def read_duration(path):
    """工程时长秒数（右定位条-左定位条）；未设定位条/解析失败返回 None。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] not in (b"RIFF", b"RIF2"):
        return None
    pos = {}
    for m in _RE_CYCLE.finditer(data):
        side = m.group(1).decode()          # 捕获组是 bytes，统一成 str 键
        if side not in pos:                 # 首次命中为准
            pos[side] = struct.unpack(">d", m.group(2))[0]
    if "Left" not in pos or "Right" not in pos:
        return None
    dur = pos["Right"] - pos["Left"]
    return dur if dur > 0 else None


def fmt_mmss(sec):
    """213.4 → '3:33'；None/0 → '未知'。"""
    if not sec or sec <= 0:
        return "未知"
    return "%d:%02d" % (int(sec) // 60, int(sec) % 60)


def parse_mmss(text):
    """'3:33' 或 '213' → 秒（float）；解析失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            return None
        m, s = (int(p) for p in parts)
        return m * 60 + s
    try:
        return float(text)
    except ValueError:
        return None
