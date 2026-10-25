# -*- coding: utf-8 -*-
"""键盘音色自动化：监听 loopMIDI「Keyboard Automation」端口，C4(=60)起十个
音符按当前工程映射向 JUNO-DS、C5(=72)起六个音符向 AX-09 Lucina 发
Bank Select+Program Change 切音色。
映射来自「录制」：JUNO 按面板 Favorite（V2 固件会 TX 出 BS/PC）、AX-09 在
面板选中音色（需先把 MIDI 设置 Bn 开为 ON，否则只发 PC）时软件从各自 MIDI
输入捕获，记到工程文件夹 keyboard_automation.json（"slots"=JUNO，"ax"=AX-09）。
JUNO 发回的模式问题（官方 MIDI Implementation 实测）：BS/PC 只在当前声音模式
语境生效，且 Performance 组只认 Performance Control 通道、其余组只认
Patch Rx/Tx 通道——所以按 MSB 推断模式（85→PERFORM、86/87/92/93→PATCH、
GM→GM1）先发模式 SysEx（DT1 写 Setup:Sound Mode，地址 01 00 00 00，
设备ID默认 17=0x10），再按组路由通道发 BS/PC，消息间留间隔。
AX-09 只能经 USB COMPUTER 口接收（DIN 口 OUT-only），无模式概念：
BS(MSB 恒 87)+PC 直接寻址 144 个常规音色（1-128 号 LSB=0，129-144 号
LSB=1）；Favorite/Special Tone 不在 PC 映射表里，MIDI 不可直达。
另有 JUNO 全局移调触发键（C2=36 起：↓半音/↑半音/↓八度/↑八度）：按官方
MIDI Implementation，GM 通用 SysEx Master Coarse Tuning 写 SYSTEM Master
Key Shift（±24 半音，全局、模式无关），连按累加（软件记累计值，反向键
抵消）。
另有 AX-09 全局移调触发键（F2=41 起：同款四个方向）：真机实测（2026-10-18）
AX-09 不收 SysEx，调音 RPN 与 CC64 都进不了本地发声路径，唯弯音进——
发 RPN 0,0 弯音灵敏度=12 后用弯音钉位（灵敏度设 24 被钳回 12，故限
±12）；连按累加，切音色不掉，弯音杆一碰即覆盖。
只捕获短消息（CC0/CC32/PC）；不收 SysEx 输入（长缓冲收包复杂且模式可
推断补齐——ponytail: 若实测 Favorite TX 带模式 SysEx 且推断出错再升级）。"""
import ctypes
import json
import os
import pathlib
import queue
import tempfile
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
from ctypes import wintypes

import dpi
import midi_ble
import midi_bridge as mb

KB_PORT_HINT = "Keyboard Automation"
SLOT_NOTES = list(range(60, 70))            # JUNO：C4 起十个半音
AX_NOTES = list(range(72, 78))              # AX-09：C5 起六个半音
SHIFT_NOTES = {36: -1, 37: 1, 38: -12, 39: 12}   # JUNO 移调：C2↓半音 C#2↑半音 D2↓八度 D#2↑八度
AX_SHIFT_NOTES = {41: -1, 42: 1, 43: -12, 44: 12}  # AX-09 移调：F2↓半音 F#2↑半音 G2↓八度 G#2↑八度
SHIFT_DESC = {36: "降1半音", 37: "升1半音", 38: "降1八度", 39: "升1八度",
              41: "降1半音", 42: "升1半音", 43: "降1八度", 44: "升1八度"}
# ↑↑↓ 箭头字形竖线长箭头头小，暗场远距两行几乎同形（初看像 I1）——
# 升/降改汉字直读，四行同款文案风格
SHIFT_LIMIT = 24                            # JUNO Master Key Shift 硬件范围 ±24 半音
AX_SHIFT_LIMIT = 12                         # AX-09 弯音灵敏度真机钳在 12（设 24 无效）
PAGE_NAMES = ("JUNO DS-88", "AX-09 Lucina")     # 配置窗口的乐器分页
NOTE_NAMES = {36: "C2", 37: "C#2", 38: "D2", 39: "D#2",
              41: "F2", 42: "F#2", 43: "G2", 44: "G#2",
              60: "C4", 61: "C#4", 62: "D4", 63: "D#4", 64: "E4",
              65: "F4", 66: "F#4", 67: "G4", 68: "G#4", 69: "A4",
              72: "C5", 73: "C#5", 74: "D5", 75: "D#5", 76: "E5", 77: "F5"}
MSG_GAP = 0.05                              # BS→PC 间隔
SYSEX_GAP = 0.1                             # 模式 SysEx→BS 间隔
SLOT_FILE = "keyboard_automation.json"      # 存在工程（歌）文件夹里
WHDR_DONE = 0x1
ABSENT_MARK = " · 当前不可用"               # 设备下拉幽灵项：已选名字不在在线清单
                                            # （文案与设置页 _ABSENT 同款；本模块不能
                                            #   反向 import setlist_gui，就地同文）

DEFAULT_JUNO = dict(inHint="JUNO", outHint="JUNO", dev=0,
                    patchCh=1, perfCh=16, deviceId=0x10)
DEFAULT_AX = dict(inHint="AX-09", outHint="AX-09", ch=1, ax=True, dev=0)
# dev=同名端口序号（0 基）：两只同型号无线 MIDI 盒在 winmm 里是两个完全同名
# 的口，名字本身不可区分，靠用户在「键盘自动化」下拉里选的序号落到哪台琴。
# 旧 config 无此键由 DEFAULT 补 0，单设备行为不变。

# AX-09 六组各 24 个常规音色（官方 Tone List：MSB 恒 87，1-128 号 LSB=0、
# 129-144 号 LSB=1；PC 字节=(音色号-1) mod 128）
AX_GROUPS = ("SYNTH/PAD", "PIANO/KEYBOARD", "ORGAN/ACCORDION",
             "STRINGS/CHOIR", "BRASS/WINDS", "GUITAR/BASS")

# MSB → (组名, {LSB: 子库名}, 声音模式 0=PATCH 1=PERFORM 2=GM2 3=GM1)
# 组表来自官方 MIDI Implementation 的 Bank Map（PC 列 1 基，显示时 +1）；
# Sound Mode 取值官方地址表实锤：2=GM2、3=GM1（Roland Clan 帖记反了）
BANK_MAP = {
    85: ("Performance", {0: "用户", 64: "预置"}, 1),
    86: ("鼓组", {0: "用户", 64: "预置", 65: "DS"}, 0),
    87: ("Patch", {0: "用户", 1: "用户", 64: "PRST", 65: "PRST", 66: "PRST",
                   67: "PRST", 68: "PRST", 69: "PRST", 70: "PRST", 71: "PRST",
                   72: "PRST", 73: "DS", 74: "DS"}, 0),
    92: ("EXP 鼓组", {}, 0),
    93: ("EXP Patch", {}, 0),
    0: ("GM", {}, 3), 63: ("GM", {}, 3), 121: ("GM", {}, 3),
    120: ("GM 鼓", {}, 3),
}


def note_name(note):
    return NOTE_NAMES.get(note, str(note))


def mode_for_msb(msb):
    return BANK_MAP[msb][2] if msb in BANK_MAP else None


def mode_sysex(mode, device_id=0x10):
    """DT1 写 Setup:Sound Mode（地址 01 00 00 00）；校验和=128−地址与数据字节和。"""
    ck = (0x80 - ((sum(bytes([0x01, 0x00, 0x00, 0x00])) + mode) & 0x7F)) & 0x7F
    return bytes([0xF0, 0x41, device_id, 0x00, 0x00, 0x3A, 0x12,
                  0x01, 0x00, 0x00, 0x00, mode, ck, 0xF7])


def shift_msgs(total):
    """JUNO 全局移调：GM 通用 SysEx Master Coarse Tuning（官方文档明说写入
    SYSTEM:Master Key Shift），mm=28H-40H-58H=±24 半音，越界钳位。"""
    mm = min(0x58, max(0x28, 0x40 + total))
    return [("long", bytes([0xF0, 0x7F, 0x7F, 0x04, 0x04, 0x00, mm, 0xF7]), 0)]


def ax_shift_msgs(total, ch):
    """AX-09 全局移调：RPN 0,0 弯音灵敏度=12 + 弯音钉位（ch 为 1 基通道）。
    真机实测（2026-10-18）：灵敏度设 24 被钳回 12；弯音值 v=round(8192×
    (12+半音)/12)，±12 边界取满舵 0/16383（+12 实为 11.99，听感等价）。"""
    s = max(-12, min(12, total))
    v = max(0, min(16383, round(8192 * (12 + s) / 12)))
    b = 0xB0 | (ch - 1)
    return [("short", b | 101 << 8, MSG_GAP),                    # RPN 0,0 选参
            ("short", b | 100 << 8, MSG_GAP),
            ("short", b | 6 << 8 | 12 << 16, MSG_GAP),           # 灵敏度 12 半音
            ("short", b | 101 << 8 | 0x7F << 16, MSG_GAP),       # RPN null 收尾
            ("short", b | 100 << 8 | 0x7F << 16, MSG_GAP),
            ("short", 0xE0 | (ch - 1) | (v & 0x7F) << 8
             | (v >> 7 & 0x7F) << 16, 0)]


def switch_msgs(slot, cfg):
    """切换序列 [(kind, payload, gap)]；kind: 'long'=SysEx, 'short'=短消息。
    通道按组路由：MSB 85(Performance)→perfCh，其余→patchCh。"""
    msb, lsb, pc = slot.get("msb"), slot.get("lsb"), slot.get("pc")
    ch = cfg["perfCh"] - 1 if msb == 85 else cfg["patchCh"] - 1
    out = []
    mode = mode_for_msb(msb)
    if mode is not None:
        out.append(("long", mode_sysex(mode, cfg["deviceId"]), SYSEX_GAP))
    if msb is not None:
        out.append(("short", 0xB0 | ch | msb << 16, MSG_GAP))      # CC0 = MSB
    if lsb is not None:
        out.append(("short", 0xB0 | ch | 32 << 8 | lsb << 16, MSG_GAP))  # CC32
    out.append(("short", 0xC0 | ch | pc << 8, 0))                  # PC
    return out


def ax_switch_msgs(slot, cfg):
    """AX-09 切换序列：BS(MSB/LSB)+PC 单通道直发，无模式 SysEx。"""
    msb, lsb, pc = slot.get("msb"), slot.get("lsb"), slot.get("pc")
    ch = cfg["ch"] - 1
    out = []
    if msb is not None:
        out.append(("short", 0xB0 | ch | msb << 16, MSG_GAP))      # CC0 = MSB
    if lsb is not None:
        out.append(("short", 0xB0 | ch | 32 << 8 | lsb << 16, MSG_GAP))  # CC32
    out.append(("short", 0xC0 | ch | pc << 8, 0))                  # PC
    return out


def slot_raw(slot):
    """原始 MIDI 字节串（仅诊断用）：'MSB85 LSB64 PC3'。"""
    return "MSB%s LSB%s PC%d" % (
        slot.get("msb") if slot.get("msb") is not None else "-",
        slot.get("lsb") if slot.get("lsb") is not None else "-",
        slot.get("pc"))


def _juno_patch_desc(lsb, pc):
    """JUNO-DS Patch 的琴显编号（官方 PC Map，MSB=87）：USER 0501-0756、
    PRST 0001-1088、DS 0001-0184，四位零填充与琴面板一致。"""
    if lsb == 0:
        return "USER:%04d" % (501 + pc)
    if lsb == 1:
        return "USER:%04d" % (629 + pc)
    if 64 <= lsb <= 71:
        return "PRST:%04d" % (128 * (lsb - 64) + 1 + pc)
    if lsb == 72:
        return "PRST:%04d" % (1025 + pc)
    if lsb == 73:
        return "DS:%04d" % (1 + pc)
    if lsb == 74:
        return "DS:%04d" % (129 + pc)
    return None


def describe_slot(slot):
    """人话描述：Patch 用琴显编号（如 USER:0521），Performance 用组名；
    未设置→'未设置'。原始字节只在录制结果状态里出现一次，不进映射列。"""
    if not slot:
        return "未设置"
    msb, lsb, pc = slot.get("msb"), slot.get("lsb"), slot.get("pc")
    if msb == 87:
        desc = _juno_patch_desc(lsb, pc)
        if desc:
            return desc
    if msb == 85:
        # Performance：面板显示如「USER 065」（用户 001-128/预置 001-064）
        sub = {0: "USER", 64: "PRST"}.get(lsb)
        if sub:
            return "PERF %s:%03d" % (sub, pc + 1)
    if msb in (0, 63, 121, 120):
        # GM 真机实测（2026-09-25）：MSB 0/121=标准组 0001-0128（PC0→Piano 1），
        # MSB 63=扩展组 0129-0256（PC0→European Pf）、120=鼓组（PC0→GM2
        # STANDARD 套鼓）。已知差异：面板 GM 模式只显示模板 Performance
        # 界面（无 GM 编号），此处编号为程序内区分，与面板对不上属预期
        if msb == 63:
            return "GM:%04d" % (129 + pc)
        if msb == 120:
            return "GM Drum:%04d" % (1 + pc)
        return "GM:%04d" % (1 + pc)
    if msb == 93:
        return "EXP:%04d" % (pc + 1)
    if msb == 92:
        return "EXP Drum:%04d" % (pc + 1)
    if msb == 86:
        # 鼓组编号按 +500 用户/1 基预置推断（未真机验证，捕获后以触发为准）
        sub = {0: ("USER", 501), 64: ("PRST", 1), 65: ("DS", 1)}.get(lsb)
        if sub:
            return "DRUM %s:%04d" % (sub[0], sub[1] + pc)
    if msb in BANK_MAP:
        gname, subs, _ = BANK_MAP[msb]
        sub = subs.get(lsb)
        return "%s%s #%d" % (gname, "·" + sub if sub else "", pc + 1)
    return "PC #%d" % (pc + 1)


def describe_ax(slot):
    """AX-09 人话描述：'SYNTH/PAD #5'，#号=组内第几个（1-24，与面板一致）。
    纯 PC 捕获（未开 Bn）按 LSB=0 理解，仅覆盖 1-128 号音色。"""
    if not slot:
        return "未设置"
    pc = slot.get("pc")
    lsb = slot.get("lsb") or 0
    n = pc + 1 + (128 if lsb == 1 else 0)
    return ("%s #%d" % (AX_GROUPS[(n - 1) // 24], (n - 1) % 24 + 1)
            if 1 <= n <= 144 else "PC #%d" % (pc + 1))


# ---- 持久化：工程文件夹里的 keyboard_automation.json ----

def _slot_path(cpr_path):
    return pathlib.Path(cpr_path).resolve().parent / SLOT_FILE


def load_slots(cpr_path, key="slots"):
    p = _slot_path(cpr_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.resolve().read_text(encoding="utf-8"))
        out = {}
        for k, v in data.get(key, {}).items():
            # 类型全验：手改 JSON 把 pc/msb/lsb 写成 null/字符串时，此前只
            # 验 "pc" in v，describe_slot 做 501+pc 才炸（且会炸死发送线程）
            if not (str(k).isdigit() and isinstance(v, dict)):
                continue
            pc = v.get("pc")
            if not isinstance(pc, int) or isinstance(pc, bool):
                continue
            slot = {"pc": pc}
            for f in ("msb", "lsb"):
                x = v.get(f)
                if x is None:
                    continue
                if not isinstance(x, int) or isinstance(x, bool):
                    slot = None
                    break
                slot[f] = x
            if slot is not None:
                out[int(k)] = slot
        return out
    except (OSError, ValueError):
        return {}


def save_slots(cpr_path, slots, key="slots"):
    p = _slot_path(cpr_path).resolve()
    if p.parent != pathlib.Path(cpr_path).resolve().parent:  # 只准落在工程文件夹
        raise ValueError("音色映射文件路径越界：%s" % p)
    try:                                    # 读改写：另一台琴的段原样保留
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[key] = {str(k): slots[k] for k in sorted(slots)}
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = p.with_name(p.name + ".tmp")      # 先写临时再替换：断电不截断原文件
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(str(tmp), str(p))


# ---- MIDI 收发（winmm；输入只收短消息，输出支持 SysEx 长消息） ----

class PortNotFound(Exception):
    pass


def _norm_dev(v):
    """config 手编敌意值收敛：dev 只认 int（bool 是 int 子类须排除——
    true 会静默等于 1 错绑第 2 个同名口）；字符串/null/浮点一律回 0。
    配置值进门即校验、不在使用点炸（load_slots pc:null 事故同族口径）。"""
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _in_dev(cfg):
    """IN 侧位次：inDev 是用户在「MIDI 输入」下拉里选的同名序（选过才落
    键，不进 DEFAULT——装载处强建会废掉旧配置的 dev 跟随语义）；无 inDev
    时 dev 只在 inHint 与 outHint 同名（同一台盒的一对口）时对 IN 组有
    意义；异名退回首命中——一个序号不跨两组用。"""
    if "inDev" in cfg:
        return _norm_dev(cfg["inDev"])
    return cfg["dev"] if cfg["inHint"] == cfg["outHint"] else 0


def _pick_hit(devs, hint, dev=0):
    """名字命中第 dev 个端口（同名多口消歧）。hint 非字符串或空 → None
    （显式未配置态；"" 子串对一切口恒真，曾会打到第一个口）。全名精确
    命中优先——下拉存的是完整端口名，防止「USB-Midi」子串误配到
    「USB-Midi 2」这类前缀重名口；无精确命中再回退子串（兼容「JUNO」
    这类手写宽 hint）。dev 脏类型经 _norm_dev 收敛，越界收敛到末位——
    选了「第2个」后拔掉一只，剩余选择仍落在真实端口上而不是报错。
    返回 (设备号, 端口名) 或 None。纯函数，发送/打开/状态行/下拉共用。"""
    if not isinstance(hint, str) or not hint:
        return None
    hits = ([(i, n) for i, n in devs if n == hint]
            or [(i, n) for i, n in devs if hint in n])
    if not hits:
        return None
    return hits[min(max(_norm_dev(dev), 0), len(hits) - 1)]


def _name_pos(devs, name, idx):
    """端口在同名端口中的位次与总数（1 基）——状态行与下拉标注「第N个」用。"""
    same = [i for i, n in devs if n == name]
    return same.index(idx) + 1, len(same)


def _device_entries(names):
    """在线端口名清单 → 下拉项 [(显示名, 端口名, 同名序 0 基)]。唯一名字
    显示全名；同名 N 个逐项带 · 第N个。纯函数，UI 与测试共用。"""
    total = {}
    for n in names:
        total[n] = total.get(n, 0) + 1
    seen = {}
    out = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        out.append((n if total[n] == 1 else "%s · 第%d个" % (n, seen[n]),
                    n, seen[n] - 1))
    return out


def _all_in_devices():
    """winmm 输入口 + BLE 端点（合成「名（BLE）」后缀，见 midi_ble.BLE_MARK）。
    winmm 恒在前：手写宽 hint（如「JUNO」）先落 USB 口，行为与旧版一致；
    BLE 追加使同名跨后端口以 dev 序号消歧——与两只同型号无线盒同款语义。
    两向都用缓存读（BLE 侧后台刷新），主线程绝不阻塞。"""
    return mb._in_devices() + midi_ble.in_devices()


def _all_out_devices():
    """winmm 输出口 + BLE 端点，同 _all_in_devices。"""
    return mb._out_devices() + midi_ble.out_devices()


class RawMidiIn:
    """按名字子串打开 MIDI 输入口；on_msg(status, d1, d2) 只收短消息。
    open/close 走 midi_bridge 的 winmm 专线（限时+弃单回收）——主线程
    （热切换/录制/学习期）不再被进程级 close 锁挂死。
    后端分派：命中设备的设备号是 int=winmm 序号（原路径），str=BLE 端点
    id（走 midi_ble，一次性线程限时打开+弃单自回收）。"""

    def __init__(self, hint, on_msg, dev=0):
        """dev：名字命中多个端口时取第 dev 个（见 _pick_hit）——录槽捕获
        从 cfg["dev"] 传入，保证收发落在同一序号的那只盒子上；序号在
        合并清单（winmm 在前、BLE 在后）上统一编号。"""
        devs = _all_in_devices()
        hit = _pick_hit(devs, hint, dev)
        if hit is None:
            raise PortNotFound("未找到含「%s」的 MIDI 输入端口；现有：%s" % (
                hint, "、".join(n for _, n in devs) or "无"))
        tok, self.name = hit
        self._on_msg = on_msg
        self._ble = None
        self._h = None
        if isinstance(tok, str):    # BLE 端点：设备号是端点 id 字符串
            port, err = midi_ble.open_in(tok, on_msg)
            if port is None:
                raise PortNotFound(err)
            self._ble = port
            return
        self._cb = mb._Proc(self._dispatch)   # 持引用防 GC
        h, err = mb.open_in(tok, self._cb)
        if h is None:
            raise PortNotFound(err)
        self._h = h

    def _dispatch(self, h, msg, inst, p1, p2):
        if msg == mb._MIM_DATA:
            self._on_msg(p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF)

    def close(self):
        if self._ble is not None:
            self._ble.close()
            self._ble = None
            return
        h = self._h
        self._h = None
        if h:
            mb.close_in(h)   # 官方收尾序，见 midi_bridge.close_in


class _MIDIHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_char_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_size_t),
                ("dwOffset", ctypes.c_size_t),
                ("dwReserved", ctypes.c_size_t * 4)]


for _fn in ("midiOutPrepareHeader", "midiOutLongMsg", "midiOutUnprepareHeader"):
    # 不留模块级别名（下划线 _winmm 直绑 import 时本体）：别名让测试
    # monkeypatch mb._winmm 打不进来（test_stability 已知坑）；
    # argtypes 在 import 时一次性设到真实 DLL 上即可
    getattr(mb._winmm, _fn).argtypes = (wintypes.HANDLE,
                                        ctypes.POINTER(_MIDIHDR),
                                        wintypes.DWORD)


def _send_long(h, data):
    """SysEx 走 midiOutLongMsg；缓冲在驱动标 DONE 前必须保持有效。"""
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    hdr = _MIDIHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
    hdr.dwBufferLength = len(data)
    if mb._winmm.midiOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr)):
        return True
    mb._winmm.midiOutLongMsg(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    deadline = time.time() + 2
    while not (hdr.dwFlags & WHDR_DONE) and time.time() < deadline:
        time.sleep(0.005)
    mb._winmm.midiOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    return False


def send_slot(slot, cfg, msgs=None):
    """按 cfg（JUNO 或 AX-09）向输出端口发整个切换序列（或预构造 msgs）；
    返回错误文案，None=成功。open/close 走 midi_bridge 输出专线（与输入
    同锁同队列）——Win11 进程级锁挂死时发送线程限时失败不再陪葬。
    后端分派：命中设备的设备号 str=BLE 端点（_send_slot_ble，走 midi_ble
    输出），int=winmm 序号（原路径逐字节不动）。"""
    devs = _all_out_devices()
    hit = _pick_hit(devs, cfg["outHint"], cfg.get("dev", 0))
    if hit is None:
        return "未找到含「%s」的 MIDI 输出端口；现有：%s" % (
            cfg["outHint"], "、".join(n for _, n in devs) or "无")
    if msgs is None:
        msgs = (ax_switch_msgs(slot, cfg) if cfg.get("ax")
                else switch_msgs(slot, cfg))
    if isinstance(hit[0], str):
        return _send_slot_ble(hit[0], msgs)
    h, err = mb.open_out(hit[0])
    if h is None:
        return err
    try:
        for kind, payload, gap in msgs:
            if kind == "long":
                if _send_long(h, payload):
                    return "SysEx 发送失败"
            else:
                mb._winmm.midiOutShortMsg(h, payload)
            if gap:
                time.sleep(gap)
    finally:
        mb.close_out(h)
    return None


def _send_slot_ble(dev_id, msgs):
    """BLE 输出分支：同一 msgs 序列走 BleOut（短/长消息后端分派在句柄上），
    间隔 sleep 复用——调用方是 ToneSwitcher 串行发送线程，不占主线程。"""
    h, err = midi_ble.open_out(dev_id)
    if h is None:
        return err
    try:
        for kind, payload, gap in msgs:
            if kind == "long":
                if h.send_long(payload):
                    return "SysEx 发送失败"
            else:
                e = h.send_short(payload)
                if e:
                    return e
            if gap:
                time.sleep(gap)
    finally:
        h.close()
    return None


class ToneSwitcher:
    """串行发送线程：发送含 sleep（约 0.2s/次），不能在 winmm 回调线程做。
    on_result(描述, 错误文案|None) 在发送线程回调，供监控栏显示最近结果。"""

    def __init__(self, cfg, log, on_result=None):
        self.cfg = cfg
        self._log = log
        self.on_result = on_result
        self._q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, slot, why=""):
        if slot:
            self._q.put((slot, why))

    def submit_msgs(self, msgs, why=""):
        """预构造消息序列（两琴全局移调）走同一串行队列。"""
        self._q.put((msgs, why))

    def submit_shift(self, total, why=""):
        """全局移调走同一串行队列（预构造消息序列）。"""
        self.submit_msgs(shift_msgs(total), why)

    def _loop(self):
        while True:
            item, why = self._q.get()
            try:
                if isinstance(item, list):      # 预构造序列（全局移调）
                    err = send_slot(None, self.cfg, msgs=item)
                    self._log("%s%s" % (why, "失败：%s" % err if err else ""))
                    if self.on_result:
                        self.on_result(why, err)
                    continue
                slot = item
                err = send_slot(slot, self.cfg)
                self._log("音色切换%s → %s%s" % (
                    "（%s）" % why if why else "", describe_slot(slot),
                    "失败：%s" % err if err else ""))
                if self.on_result:
                    self.on_result(describe_slot(slot), err)
            except Exception as e:
                # 发送线程绝不允许静默死亡：死一次=音色切换/移调全场
                # 失效且零日志（load_slots 类型洞曾可经 describe_slot 炸死）
                try:
                    self._log("音色发送异常（已恢复）：%s: %s"
                              % (type(e).__name__, e))
                except Exception:
                    pass
                if self.on_result:
                    try:
                        self.on_result(why or "音色切换",
                                       "内部异常：%s" % e)
                    except Exception:
                        pass
            time.sleep(0.05)


class SlotCapture:
    """录制：持续跟踪最近一次音色选择——每次 Program Change 都覆盖
    （配上最近收到的 CC0/CC32，BS 可能缺失）；停止录制时保存最后一个。"""

    def __init__(self):
        self.msb = self.lsb = None
        self.slot = None

    def feed(self, status, d1, d2):
        st = status & 0xF0
        if st == 0xB0:
            if d1 == 0:
                self.msb = d2
            elif d1 == 32:
                self.lsb = d2
        elif st == 0xC0:
            self.slot = {"pc": d1}
            if self.msb is not None:
                self.slot["msb"] = self.msb
            if self.lsb is not None:
                self.slot["lsb"] = self.lsb


# ---- 配置窗口 ----

class KeyboardAutoWindow(tk.Toplevel):
    """JUNO 十个 + AX-09 六个音符槽位：显示映射 / 录制（琴上捕获）/ 触发 /
    清除。绑定一首歌（选中优先，退回已加载）；主窗口再点「键盘自动化」可换绑定。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.withdraw()     # 幕后建窗：Toplevel 创建即上屏，建完再显形
        self.app = app
        self.song = None
        self.slots = {}             # JUNO 映射（音符 60-69）
        self.ax_slots = {}          # AX-09 映射（音符 72-77）
        self._cap = None            # (note, SlotCapture, RawMidiIn, deadline)
        self.title("键盘自动化")
        # 录制态按钮的警示字色（录制中=红字「停止」；本窗局部注册——
        # TButton 族 style 路径可渲染，map 须钉红防 pressed 态继承主题灰）
        st = ttk.Style(self)
        st.configure("Rec.TButton", foreground=dpi.C_ERR)
        st.map("Rec.TButton", foreground=[("pressed", dpi.C_ERR)])
        pad = dpi.scale(self, 12)   # pack 边距是裸像素，高 DPI 下须换算
        # 乐器分页：下拉切换，窗口只显示一台琴的内容。行构成与页内
        # 「MIDI 输出」行严格同款（宽 15 标签+下拉填充+右侧宽 6 刷新钮）
        # ——三行下拉起点/宽度对齐；刷新=重枚举当前页设备并复核端口状态，
        # 设备插回时不必切页即可一键复核。窗级说明段零保留（规范五）：
        # 录制/触发的用法在各自按钮的悬停提示里
        self._sel = tk.StringVar(value=PAGE_NAMES[0])
        top = ttk.Frame(self)
        top.pack(fill="x", padx=pad, pady=(pad, 4))
        ttk.Label(top, text="乐器", width=15, anchor="w",
                  style="Dim.TLabel").pack(side="left")
        self._menu = ttk.Combobox(top, textvariable=self._sel,
                                  values=list(PAGE_NAMES), state="readonly")
        self._menu.pack(side="left", fill="x", expand=True)
        self._menu.bind("<<ComboboxSelected>>",
                        lambda _e: self._show_page(self._sel.get()))
        dpi.bind_hint(self._menu, self._hint,
                      "切换琴型页：JUNO DS-88 / AX-09")
        ref = ttk.Button(top, text="刷新", width=6,
                         command=self._refresh_page)
        dpi.bind_hint(ref, self._hint, "重新枚举设备并复核端口状态")
        ref.pack(side="left", padx=(6, 0))
        self._slot_lbl = {}
        self._rec_btn = {}
        self._pages = {}
        self._dev_vars = {}         # 每乐器页×每方向的设备下拉（key:(页,方向)）
        self._dev_menus = {}
        self._dev_items = {}        # 与各下拉 values 平行：(端口名, 同名序 0 基)
        for key, groups in (
                ("juno", (("── 音色槽（C4 起 10 键）──", SLOT_NOTES),
                          ("── 全局移调（C2 起 4 键）──", list(SHIFT_NOTES)))),
                ("ax", (("── 音色槽（C5 起 6 键）──", AX_NOTES),
                        ("── 全局移调（F2 起 4 键）──",
                         list(AX_SHIFT_NOTES))))):
            page = ttk.Frame(self)
            # 收发设备两行：输出=触发发送（BS/PC/移调出琴）、输入=录制捕获
            # （琴上选音色进软件）。收发异名盒（WIDI Uhost 的 OUT/IN 后缀
            # 口）两行各选各的口，同名盒输出行选定即跟随输入行
            # （_select_device）。下拉动态枚举当前在线端口——零写死设备名
            # （无线盒今天枚举出 USB-Midi、明天换有线枚举出 JUNO-DS，同一
            # 套机制）；同名多口（两只同型号无线盒）以 · 第N个 区分，序号
            # 输出行存 cfg["dev"]、输入行存 cfg["inDev"]。行构成仿踩钉控制
            # 窗的设备行（宽 15 标签+下拉填充+宽 6 刷新）。
            for direction, label, hint in (
                    ("out", "MIDI 输出", "触发/发送用的 MIDI 口"),
                    ("in", "MIDI 输入", "录制捕获用的 MIDI 口")):
                devrow = ttk.Frame(page)
                devrow.pack(fill="x", pady=(0, 4))
                ttk.Label(devrow, text=label, width=15,
                          anchor="w", style="Dim.TLabel").pack(side="left")
                self._dev_vars[key, direction] = tk.StringVar(value="…")
                opt = ttk.Combobox(
                    devrow, textvariable=self._dev_vars[key, direction],
                    state="readonly")
                opt.pack(side="left", fill="x", expand=True)
                self._dev_menus[key, direction] = opt
                self._dev_items[key, direction] = []
                opt.bind("<<ComboboxSelected>>",
                         lambda _e, k=key, d=direction:
                         self._on_dev_selected(k, d))
                dpi.bind_hint(opt, self._hint,
                              "%s；「当前不可用」即设备未接入" % hint)
                ref = ttk.Button(
                    devrow, text="刷新", width=6,
                    command=lambda k=key: self._rebuild_device_menu(k))
                dpi.bind_hint(ref, self._hint, "重新枚举设备并复核端口状态")
                ref.pack(side="left", padx=(6, 0))
            grid = ttk.Frame(page)
            grid.pack(fill="both", expand=True)
            for c, t in enumerate(("音符", "音色映射", "操作")):
                ttk.Label(grid, text=t, anchor="w", style="Dim.TLabel").grid(
                    row=0, column=c, sticky="w", pady=(0, 2),
                    padx=(8, 0) if c == 1 else (0, 0))  # 对齐数据列左缩进
            row = 1
            for title, notes in groups:
                ttk.Label(grid, text=title, anchor="w",
                          style="Dim.TLabel").grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
                row += 1
                for note in notes:
                    ttk.Label(grid, text="%s（%d）" % (note_name(note), note),
                              anchor="w").grid(row=row, column=0, sticky="w",
                                               pady=2)
                    desc = SHIFT_DESC.get(note)
                    if desc:        # 固定功能行：只显示，无录制/触发
                        ttk.Label(grid, text=desc, anchor="w",
                                  style="Dim.TLabel").grid(
                            row=row, column=1, sticky="we",
                            padx=(8, 8), pady=2)
                        row += 1
                        continue
                    lbl = ttk.Label(grid, text="未设置", anchor="w",
                                    style="Dim.TLabel")
                    lbl.grid(row=row, column=1, sticky="we",
                             padx=(8, 8), pady=2)
                    grid.columnconfigure(1, weight=1)
                    cell = ttk.Frame(grid)
                    cell.grid(row=row, column=2, sticky="w", pady=2)
                    self._rec_btn[note] = ttk.Button(
                        cell, text="录制", width=8,
                        command=lambda n=note: self._toggle_record(n))
                    self._rec_btn[note].pack(side="left", padx=2)
                    dpi.bind_hint(self._rec_btn[note], self._hint,
                                  "按下后到琴上选好音色，回来按「停止」保存")
                    trig = ttk.Button(cell, text="触发", width=8,
                                      command=lambda n=note: self._trigger(n))
                    trig.pack(side="left", padx=2)
                    dpi.bind_hint(trig, self._hint,
                                  "向琴发送该音色的 PC 消息，验证映射生效")
                    clr = ttk.Button(cell, text="清除", width=8,
                                     command=lambda n=note: self._clear(n))
                    clr.pack(side="left", padx=2)
                    dpi.bind_hint(clr, self._hint, "删除该音符的音色映射")
                    self._slot_lbl[note] = lbl
                    row += 1
            for r in range(row):
                grid.rowconfigure(r, uniform="rows")  # 全行等高=按钮行距统一
            self._pages[key] = page
        # 底栏唯一一行：三方优先级（悬停提示>瞬时反馈>端口状态）由
        # _present 统一裁决；端口状态按乐器页存 _port_state（_port_row）
        self.status = ttk.Label(self, text="…", anchor="w", style="Dim.TLabel")
        self._hover = None
        self._transient = ("", dpi.MUT)
        self._transient_until = 0.0
        self._port_state = {}
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.attributes("-topmost", True)   # 与主窗一致保持可见
        dpi.setup_window(self)
        # side=bottom 停靠：页框架 fill=both expand 在 _show_page 才 pack，
        # 普通次序会把本行挤出窗底（四边留白回归抓过）
        self.status.pack(side="bottom", fill="x", padx=pad,
                         pady=(6, dpi.scale(self, 8)))
        dpi.bind_hint(self.status, self._hint,
                      "❌=未找到设备；查 config 当前乐器页的 inHint/outHint")
        self._show_page(PAGE_NAMES[0])
        self.after(300, self._tick)
        # 幕后首绘定稿再显形（同设置/踩钉两窗）：透明状态亮窗完成首绘
        # ——<Map> 触发 DWM 深色标题栏重写、ttk 元素高度定稿——恢复
        # 不透明时一次性以终态出现，构建不再逐块上屏
        self.attributes("-alpha", 0)
        self.deiconify()
        self.update()
        self.attributes("-alpha", 1)

    def _show_page(self, name):
        """乐器分页切换；窗口尺寸随页内容重定，minsize 同步允许缩小。
        页与端口行先全部 pack_forget 再按序落尾——不用 before= 引用：
        目标行可能正被 forget（Tcl 报 isn't packed）。"""
        key = "juno" if name.startswith("JUNO") else "ax"
        self._page_key = key
        self._sel.set(name)
        for p in self._pages.values():
            p.pack_forget()
        self._pages[key].pack(fill="both", expand=True,
                              padx=dpi.scale(self, 12))
        # 切页即重枚举当前页下拉（开窗首次也走这里）：另一页在看不见的
        # 期间设备可能插拔，陈旧菜单误导选——枚举走 io 专线（1s 限时+
        # last-good 缓存）不担心主线程挂死
        self._rebuild_device_menu(key)
        self._present()             # 底栏切显示当前页端口状态
        self.update_idletasks()
        # 先降 minsize 再改尺寸——顺序反了会被上一页的 minsize 钳住缩不回去
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        w = max(dpi.scale(self, 560), self.winfo_reqwidth())
        h = max(dpi.scale(self, 480),
                self.winfo_reqheight() + dpi.scale(self, 12))
        self.geometry("%dx%d" % (w, h))

    def _store(self, note):
        """音符归属的映射表：72 起归 AX-09，其余归 JUNO。"""
        return self.ax_slots if note in AX_NOTES else self.slots

    def _describe(self, note):
        slot = self._store(note).get(note)
        return (describe_ax(slot) if note in AX_NOTES
                else describe_slot(slot))

    def set_song(self, song):
        """绑定歌曲并装载映射。装载 IO（工程文件夹旁挂 JSON）放后台线程
        ——网络盘慢/掉线时主线程（打开窗口/标题热同步路径）不再冻结
        （复审 R3-1：_adopt/_redict 同款手法）；代际守卫防连点换歌回落。"""
        self._cancel_capture()
        self.song = song
        self.slots = {}
        self.ax_slots = {}
        self._song_gen = getattr(self, "_song_gen", 0) + 1
        gen = self._song_gen
        for note in SLOT_NOTES + AX_NOTES:
            self._refresh(note)             # 旧映射即刻失效
        if not song:
            return
        path = song["path"]

        def run():
            slots = load_slots(path)
            ax = load_slots(path, "ax")
            if gen != self._song_gen:
                return                      # 绑定已换：丢弃旧装载
            self.slots = slots
            self.ax_slots = ax
            self.app.calls.put(self._refresh_all)
        threading.Thread(target=run, daemon=True).start()

    def _refresh_all(self):
        """装载完成的全量刷新（经 calls 回主线程；窗口已关则自然蒸发）。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        for note in SLOT_NOTES + AX_NOTES:
            self._refresh(note)

    def _persist_async(self, store, path, key):
        """save_slots 落盘放后台线程（网络盘写冻结主线程，复审 R3-1）；
        失败经 calls 回主线程报状态。落盘目标是捕获时的歌曲路径，换绑后
        照写无碍（写的就是那首歌的旁挂文件）。"""
        snapshot = dict(store)

        def run():
            try:
                save_slots(path, snapshot, key)
            except (OSError, ValueError) as e:
                self.app.calls.put(lambda: self.set_status(
                    "映射保存失败：%s" % e, dpi.C_ERR))

        threading.Thread(target=run, daemon=True).start()

    def _refresh(self, note):
        slot = self._store(note).get(note)
        self._slot_lbl[note].config(
            text=self._describe(note),
            style="TLabel" if slot else "Dim.TLabel",
            foreground=dpi.FG if slot else dpi.MUT)

    def set_status(self, text, color=dpi.MUT):
        """瞬时反馈：展示至下一条消息或 5 秒后回落端口状态（_tick 驱动
        _present）；空文本=清除。悬停提示优先级更高（_hint）。"""
        self._transient = (text, color)
        self._transient_until = time.time() + 5.0
        self._present()

    def _hint(self, text):
        self._hover = text
        self._present()

    def _present(self):
        """底栏三方优先级：悬停提示 > 瞬时反馈 > 当前页端口状态。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self._hover is not None:
            self._write(self._hover, dpi.MUT)
        elif self._transient[0] and time.time() < self._transient_until:
            self._write(*self._transient)
        else:
            text, ok = self._port_state.get(
                getattr(self, "_page_key", "juno"), ("…", None))
            fg = dpi.C_OK if ok else (dpi.C_ERR if ok is not None
                                      else dpi.MUT)
            self._write(text, fg)

    def _write(self, text, color=dpi.MUT):
        # fg 与 style 同写：sv-ttk 下 TLabel 族 style fg 不参与绘制
        # （dpi.paint_tree 注），widget 级 fg 是唯一渲染路径
        self.status.config(text=text, style=dpi.tone(color) + ".TLabel",
                           foreground=color)

    # ---- 录制 ----

    def _toggle_record(self, note):
        if not self.song:
            self.set_status("未绑定歌曲", dpi.C_ERR)
            return
        if self._cap is not None:
            if self._cap["note"] == note:
                self._finish_capture()      # 再按一次=停止并保存最后音色
            else:
                self.set_status("正在录制 %s，请先按其「停止」" %
                                note_name(self._cap["note"]), dpi.C_ERR)
            return
        cap = SlotCapture()
        ax = note in AX_NOTES
        cfg = self.app.axcfg if ax else self.app.jcfg
        try:
            port = RawMidiIn(cfg["inHint"], cap.feed, dev=_in_dev(cfg))
        except PortNotFound as e:
            self.set_status(str(e), dpi.C_ERR)
            return
        self._cap = dict(note=note, cap=cap, port=port)
        self._rec_btn[note].config(text="停止", style="Rec.TButton")
        ask = ("请在 AX-09 上选中该槽位对应的音色（MIDI 设置 Bn 需已开启）"
               if ax else
               "请在 JUNO-DS 上调用该槽位对应的 Favorite")
        self.set_status("录制 %s 中：%s（选好后按「停止」保存当前音色）"
                        % (note_name(note), ask), dpi.C_ERR)

    def _reset_rec_btn(self, note):
        self._rec_btn[note].config(text="录制", style="TButton")

    def _cancel_capture(self):
        if self._cap is None:
            return
        note = self._cap["note"]
        try:
            self._cap["port"].close()
        except OSError:
            pass
        self._cap = None
        self._reset_rec_btn(note)

    def _finish_capture(self):
        note = self._cap["note"]
        cap, port = self._cap["cap"], self._cap["port"]
        try:
            port.close()
        except OSError:
            pass
        self._cap = None
        self._reset_rec_btn(note)
        if cap.slot is None:
            self.set_status("%s 没收到音色信息" % note_name(note), dpi.C_ERR)
            return
        store = self._store(note)
        store[note] = cap.slot
        self._refresh(note)
        self._persist_async(store, self.song["path"],
                            "ax" if note in AX_NOTES else "slots")
        # 热同步判定按「App 当前已装载映射的工程路径」：完整版=切歌完成/
        # 恢复时装载的那首，简化版（Cube Automator）=标题识别的当前工程；
        # 两种 App 都维护 cur_song_path（未装载/切换中=None，不热同步）
        if getattr(self.app, "cur_song_path", None) == self.song["path"]:
            if note in AX_NOTES:            # 已加载的同一首：联动即时生效
                self.app.ax_slots = dict(store)
            else:
                self.app.slots = dict(store)
        self.set_status("%s 已记录：%s（%s）"
                        % (note_name(note), self._describe(note),
                           slot_raw(store[note])), dpi.C_OK)

    # ---- 触发 / 清除 ----

    def _trigger(self, note):
        slot = self._store(note).get(note)
        if not slot:
            self.set_status("%s 未配置，先录制" % note_name(note), dpi.C_ERR)
            return
        switcher = (self.app.ax_switcher if note in AX_NOTES
                    else self.app.switcher)
        switcher.submit(slot, why="手动 %s" % note_name(note))

    def _clear(self, note):
        if not self._store(note).pop(note, None):
            return
        self._persist_async(self._store(note), self.song["path"],
                            "ax" if note in AX_NOTES else "slots")
        self._refresh(note)
        self.set_status("已清除")

    # ---- 轮询：录制进度/超时 + 端口状态 ----

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            self._poll_capture()
            self._poll_ports()
        except tk.TclError:
            return
        except Exception as e:              # 轮询失败不得炸断 after 链
            self.set_status("轮询异常：%s" % e, dpi.C_ERR)
        self.after(300, self._tick)

    def _poll_capture(self):
        if self._cap is None:
            return
        note = self._cap["note"]
        cap = self._cap["cap"]
        if cap.slot is not None:
            desc = (describe_ax(cap.slot) if note in AX_NOTES
                    else describe_slot(cap.slot))
            self.set_status("录制 %s 中（当前 %s，选好后按「停止」）"
                            % (note_name(note), desc), dpi.C_ERR)
        else:
            hint = ("AX-09 上选中音色（Bn 需开启）" if note in AX_NOTES
                    else "JUNO 上调用 Favorite")
            self.set_status("录制 %s 中（等待：%s）" % (note_name(note), hint),
                            dpi.C_ERR)

    def _poll_ports(self):
        ins = _all_in_devices()
        outs = _all_out_devices()
        for key, cfg, cfg_key in (("juno", self.app.jcfg, "juno"),
                                  ("ax", self.app.axcfg, "ax09")):
            ti, oki = self._side_line(ins, cfg["inHint"], _in_dev(cfg),
                                      cfg_key, "inHint")
            to, oko = self._side_line(outs, cfg["outHint"], cfg.get("dev", 0),
                                      cfg_key, "outHint")
            self._port_row(key, "输入 %s ｜ 输出 %s" % (ti, to), oki and oko)

    @staticmethod
    def _side_line(devs, hint, dev, cfg_key, hint_key):
        """单向（输入或输出）端口状态文案：✔（同名多口时标注实际绑定的
        「第N个」）或 ❌（主界面状态格同款极简式；含义见底栏行悬停提示）。"""
        hit = _pick_hit(devs, hint, dev)
        if hit is None:
            return "❌", False
        pos, total = _name_pos(devs, hit[1], hit[0])
        return ("✔ 第%d个" % pos) if total > 1 else "✔", True

    def _port_row(self, key, text, ok):
        self._port_state[key] = (text, ok)
        if key == getattr(self, "_page_key", "juno"):
            self._present()

    def _refresh_page(self):
        """乐器行「刷新」：重枚举当前页设备下拉并复核端口状态——与页内
        设备行刷新同效（设备插回时一键复核，不必切页/滚动）。"""
        self._rebuild_device_menu(self._page_key)
        self._poll_ports()

    def _on_dev_selected(self, key, direction):
        """收/发设备下拉选中：按位次映射回（端口名, 同名序）交给
        _select_device。显示值是组合态（含 · 第N个 /幽灵标注），不在
        values 里时 current()=-1——只消费用户真选中的项。"""
        i = self._dev_menus[key, direction].current()
        if 0 <= i < len(self._dev_items[key, direction]):
            name, k = self._dev_items[key, direction][i]
            self._select_device(key, direction, name, k)

    def _rebuild_device_menu(self, key):
        """重建设备下拉项（开窗/点「刷新」/改选后调用）：输出行列当前在线
        输出端口、输入行列输入端口（收发异名盒各落各的口，见
        _select_device），唯一名显示全名、同名多口逐项「名 · 第N个」；
        已选设备不在在线清单则追加幽灵项保住显示（设备可能只是没上电），
        改选其它项即替换。候选由 Combobox values 承载，选中语义=当前值
        （原 radiobutton 圆点改由当前显示值表达）。"""
        cfg = self.app.jcfg if key == "juno" else self.app.axcfg
        for direction, hint, dev, devs in (
                ("out", cfg["outHint"], cfg.get("dev", 0),
                 _all_out_devices()),
                ("in", cfg["inHint"], _in_dev(cfg), _all_in_devices())):
            hit = _pick_hit(devs, hint, dev)
            entries = _device_entries([n for _, n in devs])
            items = [(name, k) for _label, name, k in entries]
            labels = [label for label, _name, _k in entries]
            if hit is not None:
                pos, total = _name_pos(devs, hit[1], hit[0])
                shown = hit[1] if total == 1 else "%s · 第%d个" % (
                    hit[1], pos)
            else:                   # 幽灵项：已选设备当前不在场，可点回它
                shown = hint + ABSENT_MARK
                labels.append(shown)
                items.append((hint, dev))
            self._dev_items[key, direction] = items
            self._dev_menus[key, direction].config(values=labels)
            self._dev_vars[key, direction].set(shown)

    def _select_device(self, key, direction, name, k):
        """选定收/发设备（两行下拉各选各的）：名字存完整端口名（完整名是
        其自身的子串，恒命中且不误配他口）、同名序号按行写 dev/inDev——
        收发异名盒（WIDI Uhost 的 OUT/IN 后缀口）录制与触发各走各的口。
        输出行选定仅当所选名字在输入组也精确在场（同一台盒的一对同名口）
        才让 inHint 跟随，保持一次选定体验；不再盲改异名 inHint（旧逻辑
        会把 OUT 口名污染进 inHint，录制必撞 PortNotFound）。原地改 cfg
        dict——ToneSwitcher 持有本体、发送路径每次现枚举、录制路径录制时
        现开，选完下个音符即生效，无需重连重启；落盘后两行下拉与状态行
        即时复核。config 按段整替落盘（与 pedal._save 同口径，单实例互斥
        下运行中外部手加的未知键会被内存快照覆盖——已知取舍）。同名序号
        口径见 _in_dev；踩钉学习候选的 kb_ports 解析同此。"""
        cfg = self.app.jcfg if key == "juno" else self.app.axcfg
        if direction == "in":
            cfg["inHint"] = name
            cfg["inDev"] = k
        else:
            cfg["outHint"] = name
            cfg["dev"] = k
            if name in [n for _, n in _all_in_devices()]:
                cfg["inHint"] = name    # 同名盒：输入行免重选
        self.app._persist_config(**({"juno": dict(cfg)} if key == "juno"
                                    else {"ax09": dict(cfg)}))
        self._rebuild_device_menu(key)
        self._poll_ports()

    def _close(self):
        self._cancel_capture()
        self.destroy()


if __name__ == "__main__":
    # 离线自检：AX-09 消息构造 / 描述 / 双段存储往返（无需琴与端口）
    ax = dict(DEFAULT_AX)
    m = ax_switch_msgs({"msb": 87, "lsb": 0, "pc": 0}, ax)
    assert [(k, p & 0xF0) for k, p, _ in m] == [("short", 0xB0),
                                                ("short", 0xB0),
                                                ("short", 0xC0)]
    assert m[1][1] >> 8 & 0xFF == 32 and m[1][1] >> 16 & 0xFF == 0  # CC32 LSB=0
    assert m[0][1] & 0x0F == 0                    # 通道 ch-1
    m2 = ax_switch_msgs({"msb": 87, "lsb": 1, "pc": 15}, dict(ch=3, ax=True))
    assert m2[0][1] & 0x0F == 2 and m2[2][1] >> 8 & 0xFF == 15
    m3 = ax_switch_msgs({"pc": 7}, ax)                         # 纯 PC（未开 Bn）
    assert len(m3) == 1 and m3[0][1] >> 8 & 0xFF == 7
    assert "SYNTH/PAD #1" in describe_ax({"msb": 87, "lsb": 0, "pc": 0})
    # lsb=1 = 129-144 号 = GUITAR/BASS 组第 9-24 个：组内序号口径（2026-09-25
    # 拍板「报目标说第几类第几个」；此处旧期望 #129/#144 是裸编号口径的陈值，
    # 2026-10-01 随自检重启修正）
    assert "GUITAR/BASS #9" in describe_ax({"msb": 87, "lsb": 1, "pc": 0})
    assert "GUITAR/BASS #24" in describe_ax({"msb": 87, "lsb": 1, "pc": 15})
    assert "PC #144" in describe_ax({"msb": 87, "lsb": 1, "pc": 143})  # 越界兜底
    assert "SYNTH/PAD #8" in describe_ax({"pc": 7})   # 纯 PC 按 LSB=0 归组
    assert describe_ax(None) == "未设置"
    s = shift_msgs(0)
    assert s[0][0] == "long" and s[0][1] == bytes(
        [0xF0, 0x7F, 0x7F, 0x04, 0x04, 0x00, 0x40, 0xF7])
    assert shift_msgs(24)[0][1][6] == 0x58 and shift_msgs(-24)[0][1][6] == 0x28
    assert shift_msgs(30)[0][1][6] == 0x58 and shift_msgs(-99)[0][1][6] == 0x28
    p = ax_shift_msgs(0, 1)
    assert p[2][1] >> 8 & 0xFF == 6 and p[2][1] >> 16 == 12  # 灵敏度 12 半音
    assert p[-1][1] == 0xE0 | 0x40 << 16                     # 归零=弯音回中
    assert ax_shift_msgs(3, 1)[-1][1] == 0xE0 | 0x50 << 16   # +3 半音（真机实测）
    assert ax_shift_msgs(-12, 1)[-1][1] == 0xE0              # -12=弯音谷底
    assert ax_shift_msgs(12, 1)[-1][1] == 0xE0 | 0x7F << 8 | 0x7F << 16  # 满舵
    assert ax_shift_msgs(99, 1) == ax_shift_msgs(12, 1)      # 越界钳位
    assert ax_shift_msgs(1, 16)[-1][1] & 0x0F == 15          # ch 1 基换算
    assert note_name(69) == "A4" and note_name(72) == "C5"
    assert note_name(36) == "C2" and note_name(39) == "D#2"
    assert note_name(41) == "F2" and note_name(44) == "G#2"
    # 同名多口消歧（两只同型号无线盒）：dev 序号落位/越界收敛/互不误配
    dv = [(0, "Rubix24"), (1, "USB-Midi"), (2, "USB-Midi"), (3, "JUNO-DS")]
    assert _pick_hit(dv, "USB-Midi") == (1, "USB-Midi")
    assert _pick_hit(dv, "USB-Midi", 1) == (2, "USB-Midi")
    assert _pick_hit(dv, "USB-Midi", 5) == (2, "USB-Midi")      # 越界收敛末位
    assert _pick_hit(dv, "JUNO-DS") == (3, "JUNO-DS")
    assert _pick_hit(dv, "AX-09") is None
    # 前缀重名口（"USB-Midi" 与 "USB-Midi 2" 并存）：全名精确优先不误配
    pv = [(0, "USB-Midi 2"), (1, "USB-Midi")]
    assert _pick_hit(pv, "USB-Midi") == (1, "USB-Midi")
    assert _pick_hit(pv, "USB-Midi 2") == (0, "USB-Midi 2")
    assert _pick_hit([(0, "JUNO-DS")], "JUNO") == (0, "JUNO-DS")  # 宽 hint 回退
    assert _norm_dev("1") == 0 and _norm_dev(True) == 0 and _norm_dev(2) == 2
    assert _pick_hit(dv, "") is None and _pick_hit(dv, None) is None
    assert _name_pos(dv, "USB-Midi", 2) == (2, 2)
    # IN 侧位次：无 inDev 走旧语义（同名跟随 dev、异名退 0），有则独立收敛
    assert _in_dev({"inHint": "A", "outHint": "A", "dev": 1}) == 1
    assert _in_dev({"inHint": "B", "outHint": "A", "dev": 1}) == 0
    assert _in_dev({"inHint": "B", "outHint": "A", "dev": 1, "inDev": 2}) == 2
    assert _in_dev({"inHint": "A", "outHint": "A", "dev": 1,
                    "inDev": True}) == 0
    assert _device_entries(["A", "B", "B"])[1:] == [
        ("B · 第1个", "B", 0), ("B · 第2个", "B", 1)]
    with tempfile.TemporaryDirectory() as td:
        cpr = os.path.join(td, "s.cpr")
        save_slots(cpr, {60: {"msb": 85, "pc": 1}}, "slots")
        save_slots(cpr, {72: {"msb": 87, "lsb": 1, "pc": 0}}, "ax")
        assert load_slots(cpr)[60]["msb"] == 85
        assert load_slots(cpr, "ax")[72]["lsb"] == 1
        save_slots(cpr, {}, "slots")           # 清空 JUNO 段不得动 AX 段
        assert load_slots(cpr) == {} and len(load_slots(cpr, "ax")) == 1
    print("kbd_auto self-check OK")

