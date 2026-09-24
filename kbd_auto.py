# -*- coding: utf-8 -*-
"""键盘音色自动化：监听 loopMIDI「Keyboard Automation」端口，C3(=60)起十个
音符按当前工程映射向 JUNO-DS、C4(=72)起六个音符向 AX-09 Lucina 发
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
抵消）；AX-09 不参与（SysEx 接收=X）。
另有延音踏板键：E2(40)→JUNO、A2(45)→AX-09（41-44 留给以后 AX-09 音高
移动）。按住=向琴发 CC64(Hold 1) 127、松开=发 0——两台琴官方文档都接收
CC64；注意 JUNO 的 CC64 只作用于 MIDI IN 音符（不影响手弹），AX-09 仅
USB 接收（真机行为待实测）。
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
from ctypes import wintypes

import dpi
import midi_bridge as mb

KB_PORT_HINT = "Keyboard Automation"
SLOT_NOTES = list(range(60, 70))            # JUNO：C3 起十个半音（Cubase 音名 C3=60）
AX_NOTES = list(range(72, 78))              # AX-09：C4 起六个半音（Cubase 音名 C4=72）
SHIFT_NOTES = {36: -1, 37: 1, 38: -12, 39: 12}   # JUNO 移调：C2↓半音 C#2↑半音 D2↓八度 D#2↑八度
SHIFT_DESC = {36: "↓1 半音", 37: "↑1 半音", 38: "↓1 八度", 39: "↑1 八度"}
SHIFT_LIMIT = 24                            # JUNO Master Key Shift 硬件范围 ±24 半音
PEDAL_NOTES = {40: "juno", 45: "ax"}        # 延音踏板键：E2→JUNO、A2→AX-09
                                            # （41-44 留给以后 AX-09 音高移动）
PEDAL_DESC = {40: "按住踩下延音，松开抬起", 45: "按住踩下延音，松开抬起"}
PAGE_NAMES = ("JUNO DS-88", "AX-09 Lucina")     # 配置窗口的乐器分页
NOTE_NAMES = {36: "C2", 37: "C#2", 38: "D2", 39: "D#2", 40: "E2",
              45: "A2",
              60: "C3", 61: "C#3", 62: "D3", 63: "D#3", 64: "E3",
              65: "F3", 66: "F#3", 67: "G3", 68: "G#3", 69: "A3",
              72: "C4", 73: "C#4", 74: "D4", 75: "D#4", 76: "E4", 77: "F4"}
CAPTURE_TIMEOUT = 10.0                      # 录制等待上限（秒）
MSG_GAP = 0.05                              # BS→PC 间隔
SYSEX_GAP = 0.1                             # 模式 SysEx→BS 间隔
SLOT_FILE = "keyboard_automation.json"      # 存在工程（歌）文件夹里
WHDR_DONE = 0x1

DEFAULT_JUNO = dict(inHint="JUNO", outHint="JUNO",
                    patchCh=1, perfCh=16, deviceId=0x10)
DEFAULT_AX = dict(inHint="AX-09", outHint="AX-09", ch=1, ax=True)

# AX-09 六组各 24 个常规音色（官方 Tone List：MSB 恒 87，1-128 号 LSB=0、
# 129-144 号 LSB=1；PC 字节=(音色号-1) mod 128）
AX_GROUPS = ("SYNTH/PAD", "PIANO/KEYBOARD", "ORGAN/ACCORDION",
             "STRINGS/CHOIR", "BRASS/WINDS", "GUITAR/BASS")

# MSB → (组名, {LSB: 子库名}, 声音模式 0=PATCH 1=PERFORM 2=GM1)
# 组表来自官方 MIDI Implementation 的 Bank Map（PC 列 1 基，显示时 +1）
BANK_MAP = {
    85: ("Performance", {0: "用户", 64: "预置"}, 1),
    86: ("鼓组", {0: "用户", 64: "预置", 65: "DS"}, 0),
    87: ("Patch", {0: "用户", 1: "用户", 64: "PRST", 65: "PRST", 66: "PRST",
                   67: "PRST", 68: "PRST", 69: "PRST", 70: "PRST", 71: "PRST",
                   72: "PRST", 73: "DS", 74: "DS"}, 0),
    92: ("EXP 鼓组", {}, 0),
    93: ("EXP Patch", {}, 0),
    0: ("GM", {}, 2), 63: ("GM", {}, 2), 121: ("GM", {}, 2),
    120: ("GM 鼓", {}, 2),
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


def pedal_msgs(on, ch):
    """延音 CC64（Hold 1）：on=True→127 踩下、False→0 松开；ch 为 1 基通道。
    JUNO 用 patchCh、AX-09 用 ch，与各自音色切换通道一致。"""
    return [("short",
             0xB0 | (ch - 1) | 64 << 8 | (0x7F if on else 0) << 16, 0)]


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


def describe_slot(slot):
    """人话描述：'Performance·预置 #4'；未设置→'未设置'。
    原始字节只在录制结果状态里出现一次，不进映射列。"""
    if not slot:
        return "未设置"
    msb, lsb, pc = slot.get("msb"), slot.get("lsb"), slot.get("pc")
    if msb in BANK_MAP:
        gname, subs, _ = BANK_MAP[msb]
        sub = subs.get(lsb)
        return "%s%s #%d" % (gname, "·" + sub if sub else "", pc + 1)
    return "PC #%d" % (pc + 1)


def describe_ax(slot):
    """AX-09 人话描述：'SYNTH/PAD #5'，#号=面板音色号。
    纯 PC 捕获（未开 Bn）按 LSB=0 理解，仅覆盖 1-128 号音色。"""
    if not slot:
        return "未设置"
    pc = slot.get("pc")
    lsb = slot.get("lsb") or 0
    n = pc + 1 + (128 if lsb == 1 else 0)
    return ("%s #%d" % (AX_GROUPS[(n - 1) // 24], n)
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
        return {int(k): v for k, v in data.get(key, {}).items()
                if str(k).isdigit() and isinstance(v, dict) and "pc" in v}
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


class RawMidiIn:
    """按名字子串打开 MIDI 输入口；on_msg(status, d1, d2) 只收短消息。"""

    def __init__(self, hint, on_msg):
        devs = mb._in_devices()
        hits = [(i, n) for i, n in devs if hint in n]
        if not hits:
            raise PortNotFound("未找到含「%s」的 MIDI 输入端口；现有：%s" % (
                hint, "、".join(n for _, n in devs) or "无"))
        idx, self.name = hits[0]
        self._on_msg = on_msg
        self._cb = mb._Proc(self._dispatch)   # 持引用防 GC
        self._h = wintypes.HANDLE()
        r = mb._winmm.midiInOpen(ctypes.byref(self._h), idx, self._cb, 0,
                                 mb._CALLBACK_FUNC)
        if r:
            raise PortNotFound("midiInOpen 失败（code %d，端口被占用？）" % r)
        mb._winmm.midiInStart(self._h)

    def _dispatch(self, h, msg, inst, p1, p2):
        if msg == mb._MIM_DATA:
            self._on_msg(p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF)

    def close(self):
        mb._winmm.midiInClose(self._h)


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


_winmm = mb._winmm
for _fn in ("midiOutPrepareHeader", "midiOutLongMsg", "midiOutUnprepareHeader"):
    getattr(_winmm, _fn).argtypes = (wintypes.HANDLE,
                                     ctypes.POINTER(_MIDIHDR), wintypes.DWORD)


def _send_long(h, data):
    """SysEx 走 midiOutLongMsg；缓冲在驱动标 DONE 前必须保持有效。"""
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    hdr = _MIDIHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
    hdr.dwBufferLength = len(data)
    if _winmm.midiOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr)):
        return True
    _winmm.midiOutLongMsg(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    deadline = time.time() + 2
    while not (hdr.dwFlags & WHDR_DONE) and time.time() < deadline:
        time.sleep(0.005)
    _winmm.midiOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    return False


def send_slot(slot, cfg, msgs=None):
    """按 cfg（JUNO 或 AX-09）向输出端口发整个切换序列（或预构造 msgs）；
    返回错误文案，None=成功。"""
    devs = mb._out_devices()
    hits = [(i, n) for i, n in devs if cfg["outHint"] in n]
    if not hits:
        return "未找到含「%s」的 MIDI 输出端口；现有：%s" % (
            cfg["outHint"], "、".join(n for _, n in devs) or "无")
    h = wintypes.HANDLE()
    r = _winmm.midiOutOpen(ctypes.byref(h), hits[0][0], mb._Proc(), 0, 0)
    if r:
        return "midiOutOpen 失败（code %d）" % r
    try:
        if msgs is None:
            msgs = (ax_switch_msgs(slot, cfg) if cfg.get("ax")
                    else switch_msgs(slot, cfg))
        for kind, payload, gap in msgs:
            if kind == "long":
                if _send_long(h, payload):
                    return "SysEx 发送失败"
            else:
                _winmm.midiOutShortMsg(h, payload)
            if gap:
                time.sleep(gap)
    finally:
        _winmm.midiOutClose(h)
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
        """预构造消息序列（全局移调/延音踏板）走同一串行队列。"""
        self._q.put((msgs, why))

    def submit_shift(self, total, why=""):
        """全局移调走同一串行队列（预构造消息序列）。"""
        self.submit_msgs(shift_msgs(total), why)

    def _loop(self):
        while True:
            item, why = self._q.get()
            if isinstance(item, list):          # 预构造序列（移调/延音）
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
            time.sleep(0.05)


class SlotCapture:
    """录制：收集 CC0/CC32，首个 Program Change 完成配对（BS 可能缺失）。"""

    def __init__(self):
        self.msb = self.lsb = None
        self.slot = None

    def feed(self, status, d1, d2):
        if self.slot is not None:
            return
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

    @property
    def done(self):
        return self.slot is not None


# ---- 配置窗口 ----

class KeyboardAutoWindow(tk.Toplevel):
    """JUNO 十个 + AX-09 六个音符槽位：显示映射 / 录制（琴上捕获）/ 触发 /
    清除。绑定一首歌（选中优先，退回已加载）；主窗口再点「键盘自动化」可换绑定。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.song = None
        self.slots = {}             # JUNO 映射（音符 60-69）
        self.ax_slots = {}          # AX-09 映射（音符 72-77）
        self._cap = None            # (note, SlotCapture, RawMidiIn, deadline)
        self.title("键盘自动化")
        pad = dpi.scale(self, 12)   # pack 边距是裸像素，高 DPI 下须换算
        tk.Label(self, text="录制：在琴上选好该音色；"
                            "触发：发送到琴上验证。").pack(
            anchor="w", padx=pad, pady=(pad, 4))
        # 乐器分页：下拉切换，窗口只显示一台琴的内容
        self._sel = tk.StringVar(value=PAGE_NAMES[0])
        top = tk.Frame(self)
        top.pack(fill="x", padx=pad, pady=(0, 4))
        tk.Label(top, text="乐器", anchor="w").pack(side="left")
        self._menu = tk.OptionMenu(top, self._sel, *PAGE_NAMES,
                                   command=self._show_page)
        self._menu.config(anchor="w")
        self._menu.pack(side="left", padx=8)
        self._slot_lbl = {}
        self._rec_btn = {}
        self._pages = {}
        for key, groups in (
                ("juno", (("── 音色槽（C3 起 10 键）──", SLOT_NOTES),
                          ("── 全局移调（C2 起 4 键）──", list(SHIFT_NOTES)),
                          ("── 延音踏板 ──", [40]))),
                ("ax", (("── 音色槽（C4 起 6 键）──", AX_NOTES),
                        ("── 延音踏板 ──", [45])))):
            page = tk.Frame(self)
            grid = tk.Frame(page)
            grid.pack(fill="both", expand=True)
            for c, t in enumerate(("音符", "音色映射", "操作")):
                tk.Label(grid, text=t, anchor="w", fg=dpi.MUT).grid(
                    row=0, column=c, sticky="w", pady=(0, 2),
                    padx=(8, 0) if c == 1 else (0, 0))  # 对齐数据列左缩进
            row = 1
            for title, notes in groups:
                tk.Label(grid, text=title, anchor="w", fg=dpi.MUT).grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
                row += 1
                for note in notes:
                    tk.Label(grid, text="%s（%d）" % (note_name(note), note),
                             anchor="w").grid(row=row, column=0, sticky="w",
                                              pady=2)
                    desc = SHIFT_DESC.get(note) or PEDAL_DESC.get(note)
                    if desc:        # 固定功能行：只显示，无录制/触发
                        tk.Label(grid, text=desc, anchor="w",
                                 fg=dpi.MUT).grid(row=row, column=1,
                                                  sticky="we",
                                                  padx=(8, 8), pady=2)
                        row += 1
                        continue
                    lbl = tk.Label(grid, text="未设置", anchor="w", fg=dpi.MUT)
                    lbl.grid(row=row, column=1, sticky="we",
                             padx=(8, 8), pady=2)
                    grid.columnconfigure(1, weight=1)
                    cell = tk.Frame(grid)
                    cell.grid(row=row, column=2, sticky="w", pady=2)
                    self._rec_btn[note] = tk.Button(
                        cell, text="录制", width=8,
                        command=lambda n=note: self._toggle_record(n))
                    self._rec_btn[note].pack(side="left", padx=2)
                    tk.Button(cell, text="触发", width=8,
                              command=lambda n=note: self._trigger(n)).pack(
                        side="left", padx=2)
                    tk.Button(cell, text="清除", width=8,
                              command=lambda n=note: self._clear(n)).pack(
                        side="left", padx=2)
                    self._slot_lbl[note] = lbl
                    row += 1
            self._pages[key] = page
        # 动作状态行：无消息时整行隐藏，不占位
        self.status = tk.Label(self, text="", anchor="w", fg=dpi.MUT)
        # 端口状态行：每琴一行，只显示当前乐器页那行（_show_page 接管）；
        # 文案不带设备前缀——页已经表意
        self._port_lbl = {}
        for key, text in (("juno", "…"), ("ax", "…")):
            lbl = tk.Label(self, text=text, anchor="w", fg=dpi.MUT)
            self._port_lbl[key] = lbl
        self._port_lbl["juno"].pack(fill="x", padx=pad,
                                    pady=(0, dpi.scale(self, 8)))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.attributes("-topmost", True)   # 与主窗一致保持可见
        dpi.darkify(self)
        dpi.flatten(self)       # 表单页文字直接坐窗口底色，去掉面板色斑
        m = self._menu          # 下拉不在 darkify 覆盖范围，仿设置页套同族色
        m.config(bg=dpi.PANEL, fg=dpi.FG, activebackground="#33363d",
                 activeforeground=dpi.FG, relief="flat", bd=0,
                 highlightthickness=1, highlightbackground=dpi.BORDER,
                 highlightcolor=dpi.C_OK, padx=8, pady=3)
        m["menu"].config(bg=dpi.FIELD, fg=dpi.FG,
                         activebackground=dpi.SELECT, activeforeground=dpi.FG)
        # 尺寸适配：按当前页内容定高（状态行动态出现也不会裁掉底部端口行）
        self.status.pack(fill="x", padx=pad, pady=(6, 0),
                         before=self._port_lbl["juno"])
        self._show_page(PAGE_NAMES[0])
        self.status.pack_forget()
        self.after(300, self._tick)

    def _show_page(self, name):
        """乐器分页切换；窗口尺寸随页内容重定，minsize 同步允许缩小。
        页与端口行先全部 pack_forget 再按序落尾——不用 before= 引用：
        目标行可能正被 forget（Tcl 报 isn't packed）。"""
        key = "juno" if name.startswith("JUNO") else "ax"
        self._page_key = key
        self._sel.set(name)
        for p in self._pages.values():
            p.pack_forget()
        for lbl in self._port_lbl.values():
            lbl.pack_forget()
        self._pages[key].pack(fill="both", expand=True,
                              padx=dpi.scale(self, 12))
        self._port_lbl[key].pack(fill="x", padx=dpi.scale(self, 12),
                                 pady=(0, dpi.scale(self, 8)))
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
        self._cancel_capture()
        self.song = song
        self.slots = load_slots(song["path"]) if song else {}
        self.ax_slots = load_slots(song["path"], "ax") if song else {}
        for note in SLOT_NOTES + AX_NOTES:
            self._refresh(note)

    def _refresh(self, note):
        slot = self._store(note).get(note)
        self._slot_lbl[note].config(text=self._describe(note),
                                    fg=dpi.FG if slot else dpi.MUT)

    def set_status(self, text, color=dpi.MUT):
        if not text:
            self.status.pack_forget()
            return
        self.status.config(text=text, fg=color)
        self.status.pack(fill="x", padx=dpi.scale(self, 12), pady=(6, 0),
                         before=self._port_lbl[getattr(self, "_page_key",
                                                       "juno")])

    # ---- 录制 ----

    def _toggle_record(self, note):
        if not self.song:
            self.set_status("未绑定歌曲", dpi.C_ERR)
            return
        if self._cap is not None:
            if self._cap["note"] == note:
                self._cancel_capture()
                self.set_status("录制已取消")
            else:
                self.set_status("正在录制 %s，请先完成或再按一次取消" %
                                note_name(self._cap["note"]), dpi.C_ERR)
            return
        cap = SlotCapture()
        ax = note in AX_NOTES
        try:
            port = RawMidiIn(self.app.axcfg["inHint"] if ax
                             else self.app.jcfg["inHint"], cap.feed)
        except PortNotFound as e:
            self.set_status(str(e), dpi.C_ERR)
            return
        self._cap = dict(note=note, cap=cap, port=port,
                         deadline=time.time() + CAPTURE_TIMEOUT)
        self._rec_btn[note].config(text="录制中…", fg=dpi.C_ERR)
        ask = ("请在 AX-09 上选中该槽位对应的音色（MIDI 设置 Bn 需已开启）"
               if ax else
               "请在 JUNO-DS 上调用该槽位对应的 Favorite")
        self.set_status("录制 %s：%s（%d 秒内）"
                        % (note_name(note), ask, CAPTURE_TIMEOUT), dpi.C_ERR)

    def _reset_rec_btn(self, note):
        self._rec_btn[note].config(text="录制", fg=dpi.FG)

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
        note, cap, port, _ = self._cap
        try:
            port.close()
        except OSError:
            pass
        self._cap = None
        self._reset_rec_btn(note)
        store = self._store(note)
        store[note] = cap.slot
        try:
            save_slots(self.song["path"], store,
                       "ax" if note in AX_NOTES else "slots")
        except (OSError, ValueError) as e:
            self.set_status("保存失败：%s" % e, dpi.C_ERR)
            return
        self._refresh(note)
        loaded_key = (self.app.pl_keys[self.app.cur]
                      if self.app.cur is not None
                      and self.app.cur < len(self.app.pl_keys) else None)
        if loaded_key == self.song["key"]:
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
        try:
            save_slots(self.song["path"], self._store(note),
                       "ax" if note in AX_NOTES else "slots")
            self._refresh(note)
            self.set_status("%s 已清除" % note_name(note))
        except (OSError, ValueError) as e:
            self.set_status("保存失败：%s" % e, dpi.C_ERR)

    # ---- 轮询：录制进度/超时 + 端口状态 ----

    def _tick(self):
        try:
            if self.winfo_exists():
                self._poll_capture()
                self._poll_ports()
                self.after(300, self._tick)
        except tk.TclError:
            pass

    def _poll_capture(self):
        if self._cap is None:
            return
        left = self._cap["deadline"] - time.time()
        if self._cap["cap"].done:
            self._finish_capture()
        elif left <= 0:
            note = self._cap["note"]
            self._cancel_capture()
            if note in AX_NOTES:
                why = ("AX-09 上没收到音色信息：请在琴上开启 MIDI 设置 Bn"
                       "（SHIFT+V-LINK 连按 5 次后 SHIFT+WRITE 保存）再重选音色")
            else:
                why = ("琴上没收到音色信息。请确认 V2 固件的"
                       " Favorite TX 已开启（System→MIDI→Tx 设置）")
            self.set_status("%s 录制超时：%s" % (note_name(note), why),
                            dpi.C_ERR)
        else:
            self.set_status("录制 %s 中（剩 %.0f 秒）"
                            % (note_name(self._cap["note"]), left), dpi.C_ERR)

    def _poll_ports(self):
        ins = [n for _, n in mb._in_devices()]
        outs = [n for _, n in mb._out_devices()]

        def io_line(hint, cfg_key):
            ok_in = any(hint in n for n in ins)
            ok_out = any(hint in n for n in outs)
            return "输入 %s ｜ 输出 %s" % (
                "✓" if ok_in else "✗ 未找到（查 config %s.inHint）" % cfg_key,
                "✓" if ok_out else "✗ 未找到（查 config %s.outHint）" % cfg_key
            ), ok_in and ok_out

        text, ok = io_line(self.app.jcfg["inHint"], "juno")
        self._port_row("juno", text, ok)
        text, ok = io_line(self.app.axcfg["inHint"], "ax09")
        self._port_row("ax", text, ok)

    def _port_row(self, key, text, ok):
        self._port_lbl[key].config(text=text,
                                   fg=dpi.C_OK if ok else dpi.C_ERR)

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
    assert "GUITAR/BASS #129" in describe_ax({"msb": 87, "lsb": 1, "pc": 0})
    assert "GUITAR/BASS #144" in describe_ax({"msb": 87, "lsb": 1, "pc": 15})
    assert "PC #144" in describe_ax({"msb": 87, "lsb": 1, "pc": 143})  # 越界兜底
    assert "SYNTH/PAD #8" in describe_ax({"pc": 7})   # 纯 PC 按 LSB=0 归组
    assert describe_ax(None) == "未设置"
    s = shift_msgs(0)
    assert s[0][0] == "long" and s[0][1] == bytes(
        [0xF0, 0x7F, 0x7F, 0x04, 0x04, 0x00, 0x40, 0xF7])
    assert shift_msgs(24)[0][1][6] == 0x58 and shift_msgs(-24)[0][1][6] == 0x28
    assert shift_msgs(30)[0][1][6] == 0x58 and shift_msgs(-99)[0][1][6] == 0x28
    p = pedal_msgs(True, 1)
    assert p[0][1] == 0xB0 | 0 | 64 << 8 | 0x7F << 16      # B0 40 7F 踩下
    q = pedal_msgs(False, 16)
    assert q[0][1] >> 16 & 0xFF == 0 and q[0][1] & 0x0F == 15   # BF 40 00 抬起
    assert note_name(69) == "A3" and note_name(72) == "C4"
    assert note_name(36) == "C2" and note_name(39) == "D#2"
    assert note_name(40) == "E2" and note_name(45) == "A2"
    assert PEDAL_NOTES == {40: "juno", 45: "ax"}
    with tempfile.TemporaryDirectory() as td:
        cpr = os.path.join(td, "s.cpr")
        save_slots(cpr, {60: {"msb": 85, "pc": 1}}, "slots")
        save_slots(cpr, {72: {"msb": 87, "lsb": 1, "pc": 0}}, "ax")
        assert load_slots(cpr)[60]["msb"] == 85
        assert load_slots(cpr, "ax")[72]["lsb"] == 1
        save_slots(cpr, {}, "slots")           # 清空 JUNO 段不得动 AX 段
        assert load_slots(cpr) == {} and len(load_slots(cpr, "ax")) == 1
    print("kbd_auto self-check OK")

