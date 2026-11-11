# -*- coding: utf-8 -*-
"""键盘音色自动化：监听 loopMIDI「Keyboard Automation」端口，C4(=72)起十个
音符按当前工程映射向 JUNO-DS、C5(=84)起六个音符向 AX-09 Lucina 发
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
另有 JUNO 全局移调触发键（C2=48 起：↓半音/↑半音/↓八度/↑八度）：按官方
MIDI Implementation，GM 通用 SysEx Master Coarse Tuning 写 SYSTEM Master
Key Shift（±24 半音，全局、模式无关），连按累加（软件记累计值，反向键
抵消）。
另有 AX-09 全局移调触发键（F2=53 起：同款四个方向）：真机实测（2026-10-18）
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

import dpi
import midi_ble
import midi_bridge as mb

KB_PORT_HINT = "Keyboard Automation"
SLOT_NOTES = list(range(72, 82))            # JUNO：C4 起十个半音
AX_NOTES = list(range(84, 90))              # AX-09：C5 起六个半音
SHIFT_NOTES = {48: -1, 49: 1, 50: -12, 51: 12}   # JUNO 移调：C2↓半音 C#2↑半音 D2↓八度 D#2↑八度
AX_SHIFT_NOTES = {53: -1, 54: 1, 55: -12, 56: 12}  # AX-09 移调：F2↓半音 F#2↑半音 G2↓八度 G#2↑八度
SHIFT_DESC = {48: "降1半音", 49: "升1半音", 50: "降1八度", 51: "升1八度",
              53: "降1半音", 54: "升1半音", 55: "降1八度", 56: "升1八度"}
# ↑↑↓ 箭头字形竖线长箭头头小，暗场远距两行几乎同形（初看像 I1）——
# 升/降改汉字直读，四行同款文案风格
SHIFT_LIMIT = 24                            # JUNO Master Key Shift 硬件范围 ±24 半音
AX_SHIFT_LIMIT = 12                         # AX-09 弯音灵敏度真机钳在 12（设 24 无效）
PAGE_NAMES = ("JUNO DS-88", "AX-09 Lucina")     # 配置窗口的乐器分页
_NOTE_CHR = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MSG_GAP = 0.05                              # BS→PC 间隔
_OLD_ANCHORS = {"slots": frozenset(range(60, 70)),
                "ax": frozenset(range(72, 78))}
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
    """音名与 DAW 底座界面同口径：60=C3、72=C4（Cubase/Studio One 显示约定）。"""
    return "%s%d" % (_NOTE_CHR[note % 12], note // 12 - 2)


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
        # 2026-11-09 触发音锚点整体升八度（对齐 Cubase 显示口径）；旧工程
        # 槽位文件读入自动迁移，段落独立判别（旧 JUNO 60-69/旧 AX 72-77 与
        # 新锚点区间不相交，检测到旧号即整段 +12）
        old = _OLD_ANCHORS.get(key, frozenset())
        if old and any(k in old for k in out):
            out = {(k + 12 if k in old else k): v for k, v in out.items()}
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
    # 先写临时再替换：断电不截断原文件。临时名带进程+线程标识——Tk 线程
    # 与后台保存线程（及双开进程）并发写同一固定 .tmp 会交错损坏
    tmp = p.with_name("%s.%d-%d.tmp"
                      % (p.name, os.getpid(), threading.get_ident()))
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
        self._closing = False        # 本方 close 中：MIM_CLOSE 分支不算死亡
        self._dead = False           # MIM_CLOSE（非本方 close）=口已掉线
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
        elif msg == mb._MIM_CLOSE and not self._closing:
            self._dead = True        # 设备移除/端口被删：自愈循环据此重开

    @property
    def dead(self):
        """口已掉线（MIM_CLOSE 送达且非本方 close）。自愈/try_open 据此重开。"""
        return self._dead

    def close(self):
        if self._ble is not None:
            self._ble.close()
            self._ble = None
            return
        h = self._h
        self._h = None
        self._closing = True   # 自己的 close 也会收到 MIM_CLOSE 回调，别误标死亡
        if h:
            mb.close_in(h)   # 官方收尾序，见 midi_bridge.close_in


def _send_long(h, data):
    """SysEx 走 midiOutLongMsg；缓冲在驱动标 DONE 前必须保持有效。
    头结构复用 mb._MIDIHDR：曾在此另立同名类并覆写 winmm argtypes——
    两类互斥使后导入方生效，send_sysex_h 传 mb 侧头即 TypeError。"""
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    hdr = mb._MIDIHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
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
                    "：%s" % why if why else "", describe_slot(slot),
                    "失败：%s" % err if err else ""))
                if self.on_result:
                    self.on_result(describe_slot(slot), err)
            except Exception as e:
                # 发送线程绝不允许静默死亡：死一次=音色切换/移调全场
                # 失效且零日志（load_slots 类型洞曾可经 describe_slot 炸死）
                try:
                    self._log("音色发送异常，已恢复：%s: %s"
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
    assert note_name(69) == "A3" and note_name(72) == "C4"
    assert note_name(60) == "C3" and note_name(84) == "C5"
    assert note_name(48) == "C2" and note_name(51) == "D#2"
    assert note_name(53) == "F2" and note_name(56) == "G#2"
    assert note_name(102) == "F#6"
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
        save_slots(cpr, {72: {"msb": 85, "pc": 1}}, "slots")
        save_slots(cpr, {84: {"msb": 87, "lsb": 1, "pc": 0}}, "ax")
        assert load_slots(cpr)[72]["msb"] == 85
        assert load_slots(cpr, "ax")[84]["lsb"] == 1
        save_slots(cpr, {}, "slots")           # 清空 JUNO 段不得动 AX 段
        assert load_slots(cpr) == {} and len(load_slots(cpr, "ax")) == 1
        save_slots(cpr, {60: {"msb": 85, "pc": 1}}, "slots")
        assert load_slots(cpr) == {72: {"msb": 85, "pc": 1}}  # 旧号读入升八度
    print("kbd_auto self-check OK")

