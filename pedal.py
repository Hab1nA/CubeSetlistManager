# -*- coding: utf-8 -*-
"""踩钉双通道快捷键：MIDI 踩钉（USB 直连或经声卡 MIDI IN，winmm 输入设备）
发 CC；蓝牙键盘型踩钉（如 M-Vave CUBE TURNER PRO，蓝牙 HID 发多媒体键）
走 Raw Input 设备桥。触发方式二选一（学习时踩出来自动分类）：单踩=按下沿
即触发（零延迟快路径）；双踩=窗内第二踩落下触发。判别延迟只落在绑了双踩
的键上，仅单踩键零延迟。（长踩手势已移除：TurnerPro 实测长按时硬件不发
任何键——GAKS 全键扫描零边沿，属固件设计，无信号可判。）

输入核心用两条正交的标准机制，互不纠缠：
  触发/学习 = Raw Input（INPUTSINK）：每个硬件按键都携带设备句柄，逐事件
    解析接口路径并与所选设备匹配——只有所选设备的按键进学习捕获/手势
    引擎/快路径（来源判定 100% 正确，后台有效）；
  拦截 = RegisterHotKey：把已绑定的按键注册成系统热键，win32k 在 legacy
    路径上直接消费，按键永远不会到达其它程序；Raw Input 在更底层并行
    投递不受影响（微软键盘输入文档），所以拦截与归属触发天然解耦——
    无需低级钩子、无需吞键证据、无需注入识别。WM_HOTKEY 本身忽略
    （动作只由 raw 触发）。

绑定来自「学习」：双通道同时监听（MIDI 输入口排除 loopMIDI 虚拟口与两台
琴；按键只录所选设备），先到先得，写 config.json 的 pedal 段（bindings=
MIDI CC，hidBindings=虚拟键码，gestures=动作→手势，doubleWindow=
双踩窗阈值，hidDeviceHint=所选设备身份，intercept=拦截开关）。热插拔：MIDI 口
未连接时由 GUI 轮询 try_open() 重连；蓝牙重连后句柄变化无需刷新（raw 侧
逐事件解析设备路径，无缓存）。"""
import ctypes
import re
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
import winreg
from ctypes import wintypes

import dpi
import midi_bridge as mb
from kbd_auto import (RawMidiIn, PortNotFound, _device_entries, _in_dev,
                      _pick_hit)

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

ACTIONS = (("pause", "暂停/继续"), ("play", "开始"), ("rewind", "回零"),
           ("panic", "全停"), ("prev", "上一首"), ("next", "下一首"))
RISE = 64               # 上升沿阈值
DEBOUNCE = 0.15         # 两次触发最小间隔（秒）
EXCLUDE = ("JUNO", "AX-09", "Lucina")   # 已知硬件琴的 MIDI 口，学习时不当踩钉候选
GESTURES = ("single", "double")  # 单踩/双踩
DOUBLE_WINDOW = 0.35    # 双踩窗（秒）：松脚到此期限内来了第二踩=双踩
BOUNCE_GATE = 0.03      # 触点抖动闸（秒）：闭合弹跳的密集重按下/假松开、
                        #   以及分断弹跳的回弹重压（距上次被受理松开 <30ms）
                        #   ——同一物理脚的爆发整体折算为一次按压
_GNAME = {"single": "", "double": "·双踩"}


def _is_virtual(name):
    """软件虚拟口判定：loopMIDI 注册表名单里的端口（改名/新增自动覆盖）
    或名字含 loopMIDI（名单读不到时的兜底）。虚拟口是软件间通路，上面
    只会有 Cubase 发的触发/时钟，学成踩钉就是误绑。"""
    return name in mb.loopmidi_ports() or "loopMIDI" in name


def learning_candidates(kb_ports=()):
    """学习阶段的踩钉候选输入口：排除各琴正在占用的 MIDI 口与全部
    loopMIDI 虚拟口。返回 **[(设备号, 端口名, 同名内位次 0 基)]**——位次
    供重开 RawMidiIn(dev=…) 时原位落位（只按名重开+默认 0 会把位次排除
    原样打回，同名盒场景学习监听落回被排除的琴口）。kb_ports=(inHint,
    dev) 对：在同一次枚举内经 kbd_auto._pick_hit 解析出琴口设备号**按
    位次精确排除**——同名盒世界里按名字排除会把同名的真踩钉口一起误伤；
    解析不到（琴没上电/没接）自然无口可排。EXCLUDE 兜底名单继续按名滤
    JUNO/AX-09 系，覆盖 config 还是出厂缺省的旧接法。"""
    devs = mb._in_devices()
    bound = set()
    for hint, d in kb_ports:
        hit = _pick_hit(devs, hint, d)
        if hit:
            bound.add(hit[0])
    entries = _device_entries([n for _, n in devs])   # (显示名, 名, 同名位次)
    return [(i, n, k) for (i, n), (_lbl, _nm, k) in zip(devs, entries)
            if i not in bound and not _is_virtual(n)
            and not any(x in n for x in EXCLUDE)]


def fire(state, cc, val, now):
    """上升沿+去抖判定；state: {cc: (上个值, 上次触发时刻)}，原地更新。"""
    prev, last = state.get(cc, (0, 0.0))
    state[cc] = (val, last)
    if val >= RISE and prev < RISE and now - last >= DEBOUNCE:
        state[cc] = (val, now)
        return True
    return False


# ---- 蓝牙键盘型踩钉（HID 按键通道） ----

HID_NAMES = {0x08: "退格", 0x0D: "回车", 0x20: "空格", 0x21: "上翻页",
             0x22: "下翻页", 0x23: "End", 0x24: "Home", 0x25: "左",
             0x26: "上", 0x27: "右", 0x28: "下", 0x2E: "Delete",
             0xAD: "静音", 0xAE: "音量−", 0xAF: "音量＋", 0xB0: "下一曲",
             0xB1: "上一曲", 0xB2: "停止", 0xB3: "播放/暂停"}


def hid_name(vk):
    """虚拟键码 → 展示名（绑定列表/学习结果用）。"""
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    return HID_NAMES.get(vk, "VK 0x%02X" % vk)


def hid_fire(state, vk, pressed, now):
    """按下沿+去抖判定（与 fire() 同构）；
    state: {vk: (上个按下态, 上次触发时刻)}，原地更新。"""
    prev, last = state.get(vk, (False, 0.0))
    state[vk] = (pressed, last)
    if pressed and not prev and now - last >= DEBOUNCE:
        state[vk] = (pressed, now)
        return True
    return False


# ---- 远程转发（平板 USB 有线踩钉 → APP 捕获 → HTTP → 注入桥） ----

# Android 的 KeyEvent.getScanCode() 实际上报的是 Linux input.h 键码（LKC），
# 不是原始 HID usage——内核 hid-input 层把 usage 折算成 LKC 再进 evdev，
# usage 本身不传给应用（AOSP《Keyboard devices》码表：Play/Pause 0xCD→164、
# Scan Next 0xB5→163、PageUp 0x4B→104、F13 0x68→183）。本表按 LKC 建键，
# 与 Windows 原生映射逐键对齐——蓝牙路径学的 VK 绑定对转发路自动生效。
# 音量键也在此表（PC 侧无害），但 Android 白名单不放行（保平板音量控制）。
_REMOTE_LKC = {
    1: 0x1B, 14: 0x08, 15: 0x09, 28: 0x0D, 57: 0x20,   # Esc/退格/Tab/回车/空格
    102: 0x24, 104: 0x21, 107: 0x23, 109: 0x22,        # Home/上翻页/End/下翻页
    110: 0x2D, 111: 0x2E,                              # Insert/Delete
    103: 0x26, 105: 0x25, 106: 0x27, 108: 0x28,        # 上/左/右/下
    113: 0xAD, 114: 0xAE, 115: 0xAF,                   # 静音/音量−/音量＋
    119: 0x13,                                         # KEY_PAUSE（consumer
                                                       #   Pause 的实际落点）
    163: 0xB0, 164: 0xB3, 165: 0xB1, 166: 0xB2,        # 下一曲/播放暂停/上一曲/停止
    16: 0x51, 17: 0x57, 18: 0x45, 19: 0x52, 20: 0x54,  # Q W E R T（LKC 字母区非线性）
    21: 0x59, 22: 0x55, 23: 0x49, 24: 0x4F, 25: 0x50,
    30: 0x41, 31: 0x53, 32: 0x44, 33: 0x46, 34: 0x47,
    35: 0x48, 36: 0x4A, 37: 0x4B, 38: 0x4C,
    44: 0x5A, 45: 0x58, 46: 0x43, 47: 0x56, 48: 0x42,
    49: 0x4E, 50: 0x4D,
    2: 0x31, 3: 0x32, 4: 0x33, 5: 0x34, 6: 0x35,       # 1..9,0（LKC 2..11）
    7: 0x36, 8: 0x37, 9: 0x38, 10: 0x39, 11: 0x30,
}
# Android keycode 兜底表（个别设备/栈 scanCode 报 0 时按 getKeyCode 映射；
# 与 LKC 表对同一物理键映射一致——如 PageUp LKC104/AKC92 均→VK_PRIOR）。
_REMOTE_AKC = {
    85: 0xB3, 86: 0xB2, 87: 0xB0, 88: 0xB1,            # 播放暂停/停止/下一曲/上一曲
    121: 0x13,                                         # BREAK（consumer Pause 的
                                                       #   Generic.kl 实际落点）
    126: 0xB3, 127: 0xB3,                              # PLAY/PAUSE → 播放暂停
    164: 0xAD, 24: 0xAF, 25: 0xAE,                     # 静音/音量＋/音量−
    92: 0x21, 93: 0x22, 122: 0x24, 123: 0x23,          # PageUp/Down/Home/End
    19: 0x26, 20: 0x28, 21: 0x25, 22: 0x27,            # 上/下/左/右
    66: 0x0D, 111: 0x1B, 62: 0x20, 61: 0x09, 67: 0x08,  # 回车/Esc/空格/Tab/退格
}


def remote_key_to_vk(sc, kc):
    """scanCode(LKC) 主查 + Android keycode 兜底 → Windows VK；
    None=两个命名空间都不认识（调用方丢弃并记日志）。F 键/字母/数字的
    区间映射按表作用域区分——两命名空间数值有交叉（LKC 87=F11 vs
    AKC 87=下一曲），绝不可混用。"""
    for u, tab in ((sc, _REMOTE_LKC), (kc, _REMOTE_AKC)):
        if isinstance(u, bool) or not isinstance(u, int):
            continue
        if u in tab:
            return tab[u]
        if tab is _REMOTE_LKC:
            if 59 <= u <= 68:            # LKC F1-F10
                return 0x70 + (u - 59)
            if u in (87, 88):            # LKC F11/F12
                return 0x7A + (u - 87)
            if 183 <= u <= 194:          # F13-F24
                return 0x7C + (u - 183)
        else:
            if 131 <= u <= 142:          # AKC F1-F12
                return 0x70 + (u - 131)
            if 326 <= u <= 337:          # AKC F13-F24（API 30/Android 11 起有
                                         #   键码；143-154 是 NumLock/小键盘，
                                         #   绝不混入）
                return 0x7C + (u - 326)
            if 29 <= u <= 54:            # AKC A-Z 线性
                return 0x41 + (u - 29)
            if 7 <= u <= 16:             # AKC 0-9 线性
                return 0x30 + (u - 7)
    return None


class RemoteClock:
    """跨包时间重定基准：以 APP 端单调时戳（事件 uptimeMillis，boot 毫秒）
    的差值外推本包事件时刻——窗内间距不被到达抖动拉伸、跨包事件序/弹跳
    闸/去抖窗按真实间距判定。能力边界：包到达本身晚于手势窗剩余时间时，
    窗内定时器已触发结算，任何接收端无法回溯补救（蓝牙直连同款 RF 延迟
    同样如此）——锚定不拯救超窗迟到包，只保证不比直连路径更差。
    APP 重启（时戳回跳）自动重锚。"""

    def __init__(self):
        self.et = None               # 锚：APP 端时戳（ms）
        self.t = None                # 锚：PC 端 monotonic 时刻

    def anchor(self, et_ms, now):
        """本包最后事件在 PC 时间轴上的时刻；et 缺失（旧客户端/心跳）退回
        到达时刻。向前外推但不超过 now（trim 掉到达抖动的膨胀）。"""
        if (isinstance(et_ms, bool) or not isinstance(et_ms, int)
                or et_ms < 0):
            self.et, self.t = None, now
            return now
        if self.et is None or et_ms < self.et:       # 首包 / APP 重启
            self.et, self.t = et_ms, now
            return now
        self.t = min(self.t + (et_ms - self.et) / 1000.0, now)
        self.et = et_ms
        return self.t


# 学习阶段的按键候选：排除鼠标键（0x01-0x06）、修饰键及其左右变体
# （Shift/Ctrl/Alt 0x10-0x12 与 0xA0-0xA5）、Pause/CapsLock（0x13/0x14）、
# Win/Menu（0x5B-0x5D）、NumLock/ScrollLock（0x90/0x91）——这些只会
# 来自误触，不该学成踩钉。Enter/Tab/Backspace/Esc（<0x20）是踏板真实会发
# 的键，必须可学（首版 range(0x20,...) 起点一刀切漏掉 Enter，实测踩坑）
LEARN_VKS = tuple(vk for vk in range(0x08, 0x100)
                  if vk not in range(0x10, 0x15)
                  and vk not in range(0x5B, 0x5E)
                  and vk not in range(0x90, 0x92)
                  and vk not in range(0xA0, 0xA6))


# ---- 时序手势引擎（单踩/双踩） ----

class GestureEngine:
    """每键独立小状态机：把「单击/快踩两下」归一成动作。
    键 = ("hid", VK) 或 ("midi", CC)；绑定表 {(键, 手势): 动作}——同一键
    绑多个手势正是本引擎的存在意义。状态流转：
      down(按下) --松脚--> wait2(等第二踩) --到期--> 触发单踩
      wait2 --第二踩落下--> 触发双踩（此后按住视为已消费，等松开归位）
    判别延迟只落在绑了双踩的键上；仅单踩的键根本不进引擎（桥内
    按下沿快路径零延迟）。同键+同手势只能属一个动作（载入期去重、
    学习期改绑摘旧保证）。线程模型：HID 事件在桥线程、MIDI 在 winmm
    线程、定时器在 Timer 线程，全部经 _lock 串行，动作在锁外回调；
    token 让被新事件取代的过期定时器静默失效。"""

    def __init__(self, on_action, spawn_timer=None, on_event=None):
        self.on_action = on_action
        self.on_event = on_event   # 可选：诊断上报（动作回调异常等）
        self.binds = {}            # (键, 手势) → 动作
        self.key_gestures = {}     # 键 → {已绑手势}
        self.double_window = DOUBLE_WINDOW
        self._spawn = spawn_timer or self._spawn_real
        self._lock = threading.Lock()
        self._state = {}           # 键 → [相位, 起始时刻, token]
        self._timers = {}          # 键 → 在途定时器句柄
        self._last_down = {}       # 键 → 上次按下的引擎时刻（触点抖动闸）
        self._last_up = {}         # 键 → 上次被受理松开的时刻（分断弹跳闸）
        self._seq = 0              # token 发生器

    @staticmethod
    def _spawn_real(delay, cb):
        t = threading.Timer(delay, cb)
        t.daemon = True
        t.start()
        return t

    def configure(self, binds, double_window=None):
        with self._lock:
            self.binds = dict(binds)
            if double_window:
                self.double_window = float(double_window)
            self.key_gestures = {}
            for key, g in self.binds:
                self.key_gestures.setdefault(key, set()).add(g)
            self._cancel_all()     # 换绑：未决手势全作废（边界纪律）

    def reset(self):
        with self._lock:
            self._cancel_all()

    def _cancel_all(self):
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
        self._state.clear()

    def is_temporal(self, key):
        with self._lock:
            return key in self.key_gestures

    def temporal_keys(self, channel):
        """某通道里走引擎（绑了双踩）的键集合，桥据此分流。"""
        with self._lock:
            return {k[1] for k in self.key_gestures if k[0] == channel}

    def feed(self, key, down, now):
        """喂入边沿。返回 False=被抖动闸拒收（调用方有镜像状态的——如
        MIDI 迟滞层——须维持原态，不得跟随翻转）。"""
        with self._lock:
            st = self._state.get(key)
            if down and (now - self._last_down.get(key, -1e9) < BOUNCE_GATE
                         or now - self._last_up.get(key, -1e9) < BOUNCE_GATE):
                # 触点抖动闸：距上次按下或上次被受理松开 <30ms 的按下沿
                # =分断弹跳的回弹重压，整体拒收——wait2 窗保持武装（弹跳
                # 爆发折算为一次按压）。刻意不设「松开沿同龄闸」：实测
                # TurnerPro 真松开沿距按下仅 7-16ms（脉冲式固件，踩下即
                # 发键+释放），同龄闸会把真松开当噪声吞掉——单踩误判、
                # 双踩折叠成单踩（校准探针毫秒级实测）
                return False
            fire = self._feed_locked(key, down, now)
            if down:
                self._last_down[key] = now
            elif st is not None and st[0] in ("down", "held"):
                # 锚只认真正的按压收尾释放沿（down 与 held 两个相位）：
                # wait2/游离期的重复 up 是同一次释放的回声，不设锚；held
                # 释放沿不设锚则双踩触发后的释放回弹会武装僵尸 down
                # （幽灵二连发+下一真踩被吞，审计实测）
                self._last_up[key] = now
        if fire:
            self._emit(fire)
        return True

    def _feed_locked(self, key, down, now):
        gs = self.key_gestures.get(key) or set()
        st = self._state.get(key)
        if down:
            if st is None:                       # 新序列
                self._arm(key, "down", now)
            elif st[0] == "wait2":               # 双踩窗内第二踩
                self._stop_timer(key)
                if "double" in gs:
                    self._arm(key, "held", now)  # 已消费，按住到松开归位
                    return (key, "double")
                self._arm(key, "down", now)      # 无双踩绑：当新序列开头
            # down/held 期间的再次按下=固件按住重发或已消费：忽略（无双踩
            # 绑定的悬挂 down——爆发吞 up 所致——由下一真踩的 up 保守折算
            # 单踩自愈，动作无净丢失；审计 F4 接受不挂折算定时器）
            return None
        if st is None:
            return None                          # 游离松开（边界残留）：忽略
        if st[0] == "down":
            if "double" in gs:
                self._arm(key, "wait2", now)
                self._timer(key, self.double_window)
                return None
            self._state.pop(key, None)
            # 纯单踩进引擎的形态（仅直接构造 binds 可达；载入路径里进引擎
            # 必有双踩绑）：松脚即触发，没有窗可等
            return (key, "single") if "single" in gs else None
        if st[0] == "held":
            # 消费完毕归位。held 释放沿无同龄闸（学习器按武装时刻拒收、
            # 引擎受理——双踩第二踩 <30ms 即松的触点抖动区才可达，示范
            # 分类与运行期在该病态输入下允许分叉，审计 P3-1 文档化接受）
            self._state.pop(key, None)
        return None                              # wait2 不该有 up：容错忽略

    def _arm(self, key, phase, now):
        self._seq += 1
        self._state[key] = [phase, now, self._seq]

    def _timer(self, key, delay):
        token = self._state[key][2]
        self._stop_timer(key)
        self._timers[key] = self._spawn(
            delay, lambda: self._on_timer(key, token))

    def _stop_timer(self, key):
        old = self._timers.pop(key, None)
        if old:
            old.cancel()

    def _on_timer(self, key, token):
        with self._lock:
            st = self._state.get(key)
            if not st or st[2] != token:
                return                  # 已被新事件/reset 取代：过期静默
            self._timers.pop(key, None)
            if st[0] == "wait2":        # 双踩窗平静过期 → 单踩
                self._state.pop(key, None)
                gs = self.key_gestures.get(key) or set()
                fire = (key, "single") if "single" in gs else None
            else:
                return
        if fire:
            self._emit(fire)

    def _emit(self, kg):
        action = self.binds.get(kg)
        if action:
            try:
                self.on_action(action)
            except Exception as e:
                # 动作链异常不得杀定时器/引擎线程，但也不能无声
                self._report("手势动作回调异常：%s" % e)

    def _report(self, msg):
        try:
            if self.on_event:
                self.on_event(msg)
        except Exception:
            pass


# ---- Raw Input 设备桥 ----

RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001       # 摘除只认 0x1+NULL 句柄（0x2000 实为 DEVNOTIFY，
                                # 写反后摘除实测变成重新注册 flags=0）
WM_INPUT = 0x00FF
WM_HOTKEY = 0x0312
WM_APP_SYNC = 0x8000            # WM_APP：configure 线程请桥线程重注册热键
MOD_NOREPEAT = 0x4000           # 热键按住不重复（反正 WM_HOTKEY 也被忽略）
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
_LL_KEYDOWN = (0x0100, 0x0104)      # WM_KEYDOWN / WM_SYSKEYDOWN
_LL_KEYUP = (0x0101, 0x0105)
WM_QUIT = 0x0012


class _RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT),
                ("Reserved", wintypes.USHORT), ("VKey", wintypes.USHORT),
                ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class _RAWINPUT(ctypes.Structure):
    class U(ctypes.Union):
        _fields_ = [("keyboard", _RAWKEYBOARD), ("raw", ctypes.c_byte * 40)]
    _fields_ = [("header", _RAWINPUTHEADER), ("data", U)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                              wintypes.WPARAM, ctypes.c_ssize_t)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


u32.RegisterRawInputDevices.argtypes = (ctypes.c_void_p, wintypes.UINT, wintypes.UINT)
# restype 用 c_int：错误时实际返回 (UINT)-1，按无符号读会变成巨大正数，
# 「<=0」守卫失效照常解析（缓冲区不足实测返回 4294967295）
u32.GetRawInputData.restype = ctypes.c_int
u32.GetRawInputData.argtypes = (wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p,
                                ctypes.POINTER(ctypes.c_uint), ctypes.c_uint)
u32.GetRawInputDeviceInfoW.restype = wintypes.UINT
u32.GetRawInputDeviceInfoW.argtypes = (wintypes.HANDLE, ctypes.c_uint, wintypes.LPVOID,
                                       ctypes.POINTER(wintypes.UINT))
u32.GetRawInputDeviceList.restype = wintypes.UINT
u32.GetRawInputDeviceList.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                      wintypes.UINT)
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.DefWindowProcW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                               ctypes.c_ssize_t)
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID)
u32.RegisterClassW.argtypes = (ctypes.c_void_p,)
u32.DestroyWindow.argtypes = (wintypes.HWND,)
u32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
k32.GetModuleHandleW.restype = wintypes.HINSTANCE
k32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
u32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                            wintypes.UINT, wintypes.UINT)
u32.PostThreadMessageW.argtypes = (wintypes.DWORD, ctypes.c_uint,
                                   wintypes.WPARAM, wintypes.LPARAM)
u32.RegisterHotKey.restype = wintypes.BOOL
u32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT,
                               wintypes.UINT)
u32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
u32.PostMessageW.argtypes = (wintypes.HWND, ctypes.c_uint, wintypes.WPARAM,
                             wintypes.LPARAM)


def device_identity(path):
    """设备接口路径 → 稳定身份子串（BLE 取 12 位 MAC，USB 取 VID&PID+接口）。
    同一设备重连后句柄/实例号会变，身份段不变。"""
    up = path.upper()
    if "HID#" not in up:
        return ""
    seg = up.split("HID#", 1)[1].split("#", 1)[0]
    seg = re.sub(r"&COL\d+$", "", seg)
    m = re.search(r"([0-9A-F]{12})$", seg)              # BLE MAC
    if m:
        return m.group(1)
    m = re.search(r"(VID_[0-9A-F]{4}&PID_[0-9A-F]{4}(?:&MI_\d+)?)", seg)
    if m:
        return m.group(1)
    return seg


def device_display(hint):
    """身份子串 → 展示名（BLE 查蓝牙注册表配对名，USB 查枚举 FriendlyName）。"""
    if re.fullmatch(r"[0-9A-F]{12}", hint):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Services\BTHPORT"
                                r"\Parameters\Devices\%s" % hint) as k:
                raw = winreg.QueryValueEx(k, "Name")[0]
            name = bytes(raw).decode("utf-8", "replace").split("\x00")[0].strip()
            if name:
                return name
        except OSError:
            pass
    if hint.startswith("VID_"):
        try:
            base = r"SYSTEM\CurrentControlSet\Enum\USB\%s" % hint
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                inst = winreg.EnumKey(k, 0)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                base + "\\" + inst) as k:
                for v in ("FriendlyName", "DeviceDesc"):
                    try:
                        s = winreg.QueryValueEx(k, v)[0]
                        return s.split(";")[-1]
                    except OSError:
                        continue
        except OSError:
            pass
    return hint


def _hint_tail(hint):
    """身份子串 → 短尾段（下拉菜单同名设备消歧用）。"""
    if re.fullmatch(r"[0-9A-F]{12}", hint):
        return hint[-4:]                     # BLE MAC 尾 4 位
    m = re.match(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", hint)
    return "%s:%s" % (m.group(1), m.group(2)) if m else hint[-6:]


def list_keyboard_devices():
    """当前在线的键盘类设备 [(身份子串, 展示名)]，按身份去重。
    首次调用返回所需数量是协议（探测数量）；(UINT)-1 是错误（两次调用
    之间设备热插拔会触发），不能当数量用。
    已知限制：两台同 VID/PID 同接口号的 USB 设备身份相同会被折叠成一条
    （Raw Input 路径层面本就不可区分），选中后两台都能触发。"""
    n = wintypes.UINT(0)
    u32.GetRawInputDeviceList(None, ctypes.byref(n),
                              ctypes.sizeof(_RAWINPUTDEVICELIST))
    if n.value in (0, 0xFFFFFFFF):
        return []
    buf = (_RAWINPUTDEVICELIST * n.value)()
    if u32.GetRawInputDeviceList(buf, ctypes.byref(n),
                                 ctypes.sizeof(_RAWINPUTDEVICELIST)) \
            in (0, 0xFFFFFFFF):
        return []
    out = set()
    for d in buf:
        if d.dwType != RIM_TYPEKEYBOARD:
            continue
        size = wintypes.UINT(0)
        u32.GetRawInputDeviceInfoW(d.hDevice, RIDI_DEVICENAME, None, ctypes.byref(size))
        if size.value in (0, 0xFFFFFFFF):   # 热拔竞态：失败按无此设备处理
            continue
        b = ctypes.create_unicode_buffer(size.value + 1)
        u32.GetRawInputDeviceInfoW(d.hDevice, RIDI_DEVICENAME, b, ctypes.byref(size))
        hint = device_identity(b.value)
        if hint:
            out.add(hint)
    named = [(h, device_display(h)) for h in sorted(out)]
    shown = [n for _, n in named]
    # 同名歧义消解：两个 USB 设备都叫「USB Input Device」，不加尾段
    # 用户会选错（选成自己的键盘=全键触发+全局拦截）
    return [(h, "%s [%s]" % (n, _hint_tail(h)) if shown.count(n) > 1 else n)
            for h, n in named]


def device_online(hint):
    """所选设备当前是否在线。"""
    return bool(hint) and any(h == hint for h, _ in list_keyboard_devices())


class DeviceBridge:
    """专用输入线程：隐藏窗口 + Raw Input(INPUTSINK) + RegisterHotKey
    拦截 + 消息泵，四者同线程免锁。两条正交的标准机制：
    ① 触发/学习：WM_INPUT 逐事件携带设备句柄 → 接口路径 → 与所选设备
       匹配（逐事件解析不缓存——句柄会被系统回收复用）；只有所选设备
       的按键进学习捕获/手势引擎/单踩快路径；
    ② 拦截：已绑定的按键注册成系统热键，win32k 在 legacy 路径直接消费、
       永不送达其它程序；Raw Input 在更底层并行投递不受热键影响（微软
       键盘输入文档），故 WM_HOTKEY 一律忽略——动作只由归属明确的
       WM_INPUT 触发，键盘同名键不会误触发。"""

    def __init__(self, on_action, on_event=None):
        self.on_action = on_action
        self.on_event = on_event   # 可选：内部事件上报（注册失败等）
        self.binds = {}          # vk → 动作（纯单踩快路径）
        self.device_hint = ""    # 所选设备身份子串（空=未选择）
        self.block = False       # 拦截开关（注册/注销系统热键）
        self.learning = False    # 学习期：事件转投 capture（学习器专用通道）
        self.capture = None      # 学习回调 fn(vk, down, 时刻) 三参直传
        self.silent = False      # 页面静音：踩钉控制页存活期丢弃一切事件
                                 #   （与 learning 正交——学习器 end_capture
                                 #   只动 learning，不得复活触发）
        self.engine = None       # 手势引擎（监听器注入）；None=无时序绑定
        self.temporal = frozenset()   # 走引擎的 HID 键（绑了双踩）
        self._state = {}         # vk → hid_fire 状态（单踩去抖）
        self._pending = {}       # vk → 热键回执时刻（拦截开启时的按下沿）
        self._io_lock = threading.RLock()  # 串行化桥线程 raw 路径与 HTTP 注入
                                           #   （_state/_pending 原依赖单线程亲和；
                                           #   RLock 因 inject→_feed 同线程嵌套）
        self._last_hid_down = (None, 0.0)  # 最近本地按下沿 (vk, t)：跨源去重
        self.remote_event_t = 0.0          # 最近远程事件到达时刻（健康显示）
        self.remote_hb_t = 0.0             # 最近远程心跳到达时刻
        self.remote_unknown = 0            # 累计未识别键丢弃数（健康显示：
                                           #   心跳只证链路活，此数证键路通）
        self._rclock = RemoteClock()       # 跨包时间重定基准锚
        self.raw_ok = None           # 线程启动后回填：INPUTSINK 是否注册成功
        self._hotkeys = {}       # 热键 id → vk（桥线程私有）
        self._stop = threading.Event()
        self._ready = threading.Event()   # 窗口建成=消息队列可投 WM_QUIT
        self._tid = None
        self._thread = None
        self._hwnd = None
        self._gen = 0            # 每次起线程自增：窗口类名逐次唯一

    def configure(self, binds=None, device_hint=None, block=None,
                  engine=None, temporal=None):
        if binds is not None:
            if binds != self.binds:
                self._state.clear()  # 换绑：旧按住状态作废，防误吃 up
            self.binds = dict(binds)
        if device_hint is not None and device_hint != self.device_hint:
            self._state.clear()      # 换设备：按下态作废
        if device_hint is not None:
            self.device_hint = device_hint
        if block is not None:
            new = bool(block)
            if new != self.block:
                self.reset_hold_state()    # mid-hold 切换：漏掉的 up 没人处理
            self.block = new
        if engine is not None:
            self.engine = engine
        if temporal is not None:
            self.temporal = frozenset(temporal)
        self._sync_hotkeys()     # 跨线程投递；窗口未建时启动流程会主动同步

    def reset_hold_state(self):
        """学习/拦截开关等边界切换后调用：按下态全作废——边界窗口期里
        漏掉的 up 没人处理，残留状态会把切换后的第一脚吃成「按住重复」
        静默吞掉（审计两轮各实测复现一次）。"""
        self._state.clear()
        self._pending.clear()

    def begin_capture(self, cb):
        self.learning = True
        self.capture = cb

    def end_capture(self):
        self.learning = False
        self.capture = None

    def _hotkey_vks(self):
        """拦截生效的键集合=全部绑定键（单踩+时序）。未选设备时绑定本就
        不触发，热键也不注册（把键留给系统）。"""
        if not (self.block and self.device_hint):
            return []
        return sorted(set(self.binds) | set(self.temporal))

    def _sync_hotkeys(self):
        if self._hwnd:
            u32.PostMessageW(self._hwnd, WM_APP_SYNC, 0, 0)

    def _apply_hotkeys(self, hwnd):
        for id_ in list(self._hotkeys):
            u32.UnregisterHotKey(hwnd, id_)
        self._hotkeys.clear()
        for i, vk in enumerate(self._hotkey_vks()):
            id_ = 0xB000 + i
            if u32.RegisterHotKey(hwnd, id_, MOD_NOREPEAT, vk):
                self._hotkeys[id_] = vk
            else:
                # 多为媒体键被其它程序（播放器等）抢占：拦截缺席但触发
                # 不受影响（触发走 raw）
                self._report("热键注册失败（VK %s 被其它程序占用），"
                             "该键无法拦截" % hid_name(vk))

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._gen += 1           # 类名逐次唯一（同进程重复注册会静默失败）
        self._thread = threading.Thread(target=self._run,
                                        name="hid-bridge", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        # 窗口建成=消息队列存在；WM_QUIT 是泵的唯一出口（漏投=线程僵尸，
        # 其热键继续消费系统按键——T6 实测 0xB0 全灭）
        if self._ready.wait(1.0) and self._tid is not None:
            u32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        th, self._thread = self._thread, None
        if th:
            th.join(timeout=1.0)

    @property
    def running(self):
        return self._thread is not None

    def _report(self, msg):
        try:
            if self.on_event:
                self.on_event(msg)
        except Exception:
            pass

    def _run(self):
        try:
            self._run_inner()
        finally:
            self._thread = None   # 自然死亡也清登记：GUI tick 可重拉

    def _run_inner(self):
        ref = _WNDPROC(self._wndproc)
        self._ref = ref           # trampoline 挂 self 防 GC（回收=原生崩溃）
        cls = "CSMDeviceBridge-%x-%d" % (id(self), self._gen)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = ref
        wc.lpszClassName = cls
        wc.hInstance = k32.GetModuleHandleW(None)
        if not u32.RegisterClassW(ctypes.byref(wc)):
            self.raw_ok = False
            return
        hwnd = u32.CreateWindowExW(0, cls, "x", 0, 0, 0, 0, 0,
                                   None, None, wc.hInstance, None)
        self._hwnd = hwnd
        self._tid = k32.GetCurrentThreadId()
        self._ready.set()         # 窗口建成=消息队列存在，WM_QUIT 可投
        rid = _RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, hwnd)
        self.raw_ok = bool(u32.RegisterRawInputDevices(
            ctypes.byref(rid), 1, ctypes.sizeof(_RAWINPUTDEVICE)))
        self._apply_hotkeys(hwnd)
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        for id_ in list(self._hotkeys):
            u32.UnregisterHotKey(hwnd, id_)
        self._hotkeys.clear()
        ridrm = _RAWINPUTDEVICE(1, 6, RIDEV_REMOVE, None)
        u32.RegisterRawInputDevices(ctypes.byref(ridrm), 1,
                                    ctypes.sizeof(_RAWINPUTDEVICE))
        u32.DestroyWindow(hwnd)
        u32.UnregisterClassW(cls, wc.hInstance)
        self._hwnd = None
        self._tid = None
        self._ready.clear()

    def _wndproc(self, h, m, w, l):
        if m == WM_INPUT:
            # RAWINPUT 句柄在 lParam；wParam 只是 RIM_INPUT(SINK) 标志
            # （传错参数读出全零数据，raw 通道整体失聪——端到端实测抓出）
            self._on_raw(l)
        elif m == WM_HOTKEY:
            # 拦截回执：按下沿被系统热键消费（不产生 WM_INPUT），记下
            # 时刻等松开沿带归属后合成按压对；键盘同名键的松开沿无归属、
            # 会在 _feed 被丢弃（不误触发）
            vk = self._hotkeys.get(w)
            if vk is not None:
                self._pending[vk] = time.monotonic()
        elif m == WM_APP_SYNC:
            self._apply_hotkeys(h)
        return u32.DefWindowProcW(h, m, w, l)

    def _on_raw(self, hraw):
        size = ctypes.c_uint(256)
        buf = ctypes.create_string_buffer(size.value)
        if u32.GetRawInputData(hraw, RID_INPUT, buf, ctypes.byref(size),
                               ctypes.sizeof(_RAWINPUTHEADER)) <= 0:
            return
        ri = ctypes.cast(buf, ctypes.POINTER(_RAWINPUT)).contents
        if ri.header.dwType != RIM_TYPEKEYBOARD:
            return
        kb = ri.data.keyboard
        if kb.Message not in _LL_KEYDOWN + _LL_KEYUP:
            return
        dev = ri.header.hDevice
        name = ""
        if dev:
            # 逐事件解析设备接口路径（两次 syscall，微秒级）：不缓存
            # hDevice→路径——句柄会被系统回收复用，缓存可能把新设备的
            # 事件误判成旧设备（审计实测句柄是小数值可复用）
            n = wintypes.UINT(0)
            u32.GetRawInputDeviceInfoW(dev, RIDI_DEVICENAME, None, ctypes.byref(n))
            if n.value in (0, 0xFFFFFFFF):   # 设备热拔竞态：失败/越界按无路径处理
                return
            b = ctypes.create_unicode_buffer(n.value + 1)
            u32.GetRawInputDeviceInfoW(dev, RIDI_DEVICENAME, b, ctypes.byref(n))
            name = b.value
        is_pedal = self._attr(name)
        if is_pedal:
            self._last_hid_down = (kb.VKey, time.monotonic())
            self._feed(kb.VKey, kb.Message in _LL_KEYDOWN)

    def _attr(self, name):
        """设备归属：接口路径含所选设备身份子串。"""
        return bool(self.device_hint) and self.device_hint in name.upper()

    def note_remote_hb(self):
        """远程心跳到达（平板转发路的健康显示数据源）。"""
        self.remote_hb_t = time.monotonic()

    def note_remote_unknown(self, sc, kc):
        """未识别键丢弃计数：心跳只证链路活，此计数暴露「键路不通」
        （如固件换了键位/映射表缺键），防健康显示被心跳买活。"""
        with self._io_lock:
            self.remote_unknown += 1
        self._report("远程按键未识别（scanCode=%r keyCode=%r），已丢弃"
                     % (sc, kc))

    def remote_anchor(self, et_ms, now):
        """跨包时间锚（RemoteClock 委托）：本包最后事件在 PC 时间轴上的
        时刻。见 RemoteClock——网络抖动/重试延迟不进跨包手势窗。"""
        return self._rclock.anchor(et_ms, now)

    def inject(self, vk, down, t=None):
        """远程注入（平板 USB 有线踩钉经 HTTP 转发）：走 _feed 同层，
        学习捕获/页面静音/手势分流全部继承。t 为重定基准后的本机
        monotonic 时刻（包内边沿间距由转发端 dt 偏移保留，跨包间距经
        RemoteClock 按 APP 端单调时戳外推，网络抖动进不了弹跳闸/双踩窗）。
        remote_event_t 只记**通过去重闸**的事件（P3-5：被去重的重试包
        不算新事件）。跨源去重：TurnerPro 有线时蓝牙断开（实测二选一），
        此闸仅为固件双输出场景的保险——同键 down 沿与本地蓝牙沿相距
        <0.15s 时丢弃远程份，防双触发。"""
        rcv = time.monotonic()
        if t is None:
            t = rcv
        with self._io_lock:
            lvk, lt = self._last_hid_down
            if down and vk == lvk and 0.0 <= t - lt < 0.15:
                self._report("远程按键与本地蓝牙重复（%s），已去重"
                             % hid_name(vk))
                return False
            self.remote_event_t = rcv
            self._feed(vk, down, t, src="remote")
        return True

    def _feed(self, vk, down, now=None, src="hid"):
        """所选设备的按键 → 学习捕获/手势引擎/单踩快路径。通道优先级：
        学习捕获（learning+capture 在场）> 页面静音（silent=丢弃一切，
        与 learning 正交——学习器 end_capture 只动 learning，页面静音
        不因此失效）> 正常触发。拦截开启时按下沿被系统热键消费
        （WM_HOTKEY 回执记 _pending），raw 只送达松开沿——松开沿带设备
        归属，凭它补合成按压对（按下=热键回执时刻），学习与手势语义
        不变；非所选设备的按键在归属门即被丢弃、进不了 _feed，pending
        只会等真设备的松开沿（或边界 reset 作废）——键盘同名键不误
        触发。capture 约定：三参数直传 fn(vk, down, 时刻)。now 供远程
        注入重定基准（None=当下）；src 区分来源——_pending 是本地热键
        拦截的回执配对，只允许 hid 源消费（远程 down/up 自成对注入、
        不经热键，若任由远程松开沿消费本地 pending 会合成从未发生的
        按压对）；全程持 _io_lock——桥线程 raw 路径与 HTTP 注入路径
        共用 _state/_pending，必须串行。"""
        if now is None:
            now = time.monotonic()
        with self._io_lock:
            if self.learning and self.capture:
                if (not down and src == "hid" and vk in self._pending):
                    t0 = self._pending.pop(vk)
                    if now - t0 > 2.0:
                        t0 = now    # 失联保护：回执已陈旧（2 秒前的按下），
                                    # 按压时长不采信，折算成当下的一次新按压
                    self.capture(vk, True, t0)
                    self.capture(vk, False, now)
                    return
                self.capture(vk, down, now)
                return
            if self.silent:
                self._pending.clear()   # 静音期回执/松开沿全弃，不留陈旧配对
                return
            if self.learning:
                return                  # 学习切换的过渡瞬间（capture 缺席）：丢弃
            if not down and src == "hid" and vk in self._pending:
                t0 = self._pending.pop(vk)
                if now - t0 > 2.0:
                    t0 = now        # 失联保护：回执已陈旧（2 秒前的按下），按
                                    # 压时长不采信，折算成当下的一次新按压
                if vk in self.temporal and self.engine is not None:
                    self.engine.feed(("hid", vk), True, t0)    # 合成按压对
                    self.engine.feed(("hid", vk), False, now)
                    return
                hid_fire(self._state, vk, True, t0)            # 单踩：松开沿触发
                hid_fire(self._state, vk, False, now)
                action = self.binds.get(vk)
                if action:
                    self.on_action(action)
                return
            if vk in self.temporal and self.engine is not None:
                self.engine.feed(("hid", vk), down, now)
                return
            if hid_fire(self._state, vk, down, now) and down:
                action = self.binds.get(vk)
                if action:
                    self.on_action(action)


def load_binding(cfg):
    """完整 config dict → (设备名提示, {动作: cc 号})。JSON 的 true 是
    int 子类，须一并挡掉（否则绑定成 CC 1）；pedal 段/子键被手改成非
    dict 时按空段处理（启动路径，类型错不能炸开机）。同码去重按
    (码, 手势) 身份——同键不同手势是手势引擎的特性，不算冲突。
    手势为已废除的「长踩」的旧绑定整体作废（降级成单踩会在演出时
    误触发，宁可让用户重新学习）。"""
    p = _pedal_dict(cfg)
    gest = _gestures_of(p)
    stale = _legacy_long(p)
    binds = {}
    seen = set()
    for k, v in _pedal_dict(cfg, "bindings").items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool) and k not in stale:
            identity = (v, gest.get(k, "single"))
            if identity in seen:    # 同码同手势：反转时后者静默覆盖前者，
                continue            #   载入期保首个，杜绝绑定静默失效
            seen.add(identity)
            binds[k] = v
    return str(p.get("deviceHint") or ""), binds


def load_hid(cfg):
    """完整 config dict → {动作: VK 码}（pedal.hidBindings 段）。去重
    身份同 load_binding：(码, 手势)；废除手势的旧绑定同样作废。"""
    p = _pedal_dict(cfg)
    gest = _gestures_of(p)
    stale = _legacy_long(p)
    out = {}
    seen = set()
    for k, v in _pedal_dict(cfg, "hidBindings").items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool) and k not in stale:
            identity = (v, gest.get(k, "single"))
            if identity in seen:
                continue
            seen.add(identity)
            out[k] = v
    return out


def load_device_cfg(cfg):
    """完整 config dict → (所选设备身份子串, 拦截开关)。intercept 只认真
    bool：JSON 字符串 "false" 按 bool() 会变 True（手改配置实测踩过）。"""
    p = _pedal_dict(cfg)
    intercept = p.get("intercept", True)
    return str(p.get("hidDeviceHint") or ""), \
        intercept if isinstance(intercept, bool) else True


def load_gestures(cfg):
    """完整 config dict → (手势表, 双踩窗)。阈值只认真数字并夹在合理
    区间（手改配置不能炸、不能设出负窗）。"""
    p = _pedal_dict(cfg)

    def num(key, dflt, lo, hi):
        v = p.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and lo <= v <= hi:
            return float(v)
        return dflt

    return _gestures_of(p), num("doubleWindow", DOUBLE_WINDOW, 0.12, 1.0)


def _pedal_dict(cfg, key=None):
    """cfg → pedal 段（非 dict 按空段）；key 给定时再取子键（非 dict 按空）。"""
    p = cfg.get("pedal")
    if not isinstance(p, dict):
        p = {}
    if key is None:
        return p
    sub = p.get(key)
    return sub if isinstance(sub, dict) else {}


def _gestures_of(p):
    """pedal 段 → {动作: 手势}（只认合法动作名与手势名）。"""
    g = p.get("gestures")
    return {k: v for k, v in (g if isinstance(g, dict) else {}).items()
            if k in dict(ACTIONS) and v in GESTURES}


def _legacy_long(p):
    """pedal 段 → 手势仍写着已废除「长踩」的动作名单（其绑定随手势
    一并作废，见 load_binding）。"""
    g = p.get("gestures")
    return {k for k, v in (g if isinstance(g, dict) else {}).items()
            if v == "long"}


class PedalListener:
    """MIDI 口按 deviceHint 常驻监听（CC 上升沿）＋设备桥按所选设备监听
    （raw 逐事件归属）→ on_action(动作名)。仅单踩绑定走各自通道的按下沿
    快路径；双踩绑定交给 GestureEngine 判别。MIDI 回调在 winmm 线程、
    按键在设备桥线程、手势定时器在 Timer 线程，GUI 侧经 calls 队列转投
    主线程。"""

    def __init__(self, on_action, on_event=None):
        self.on_action = on_action
        self.hint = ""
        self.binds = {}
        self.hid_binds = {}        # 动作 → VK
        self.gestures = {}         # 动作 → 手势
        self.double_window = DOUBLE_WINDOW
        self.device_hint = ""      # 所选设备身份子串
        self.intercept = True      # 拦截开关
        self.port = None
        self.name = ""
        self._state = {}
        self._midi_on = {}         # 手势键迟滞：CC 当前是否在按下态
        self._lock = threading.Lock()
        self._muted = False        # 踩钉控制页存活期静音：停触发、让出 MIDI 口
        self.engine = GestureEngine(on_action, on_event=on_event)
        self.bridge = DeviceBridge(on_action, on_event)

    def apply(self, hint, binds, hid_binds=None, device_hint=None,
              intercept=None, gestures=None, double_window=None):
        if not isinstance(double_window, (int, float)) \
                or isinstance(double_window, bool) or double_window <= 0:
            double_window = DOUBLE_WINDOW       # 形状防御（锁内别炸）
        with self._lock:
            self.hint = hint
            self.gestures = dict(gestures or {})
            self.double_window = float(double_window)
            self._bind_split(binds or {},
                             hid_binds if hid_binds is not None else None)
            if device_hint is not None:
                self.device_hint = device_hint
            if intercept is not None:
                self.intercept = bool(intercept)
            self.engine.configure(self._eng, self.double_window)
            self._midi_on.clear()                      # 迟滞态随重配归零
            temporal = self.engine.temporal_keys("hid")
        self.bridge.configure(binds=self._hid_single,
                              device_hint=self.device_hint,
                              block=self.intercept,
                              engine=self.engine, temporal=temporal)
        self.close()

    def _bind_split(self, binds, hid_binds):
        """双通道归一分流（锁内）：纯单踩键走按下沿快路径；绑了双踩
        的键，其全部手势（含 single 兄弟绑定）都进引擎——否则单踩留在桥
        快路径而桥把 temporal 键整体转投引擎，单踩即死绑（审计实测）。
        hid_binds=None 表示「不更新 HID 绑定」：HID 侧沿用现值重分流。"""
        temporal_midi = {cc for a, cc in binds.items()
                         if self.gestures.get(a, "single") != "single"}
        eng = {}
        midi_single = {}
        for a, cc in binds.items():
            g = self.gestures.get(a, "single")
            if g == "single" and cc not in temporal_midi:
                midi_single[cc] = a
            else:
                eng[("midi", cc), g] = a
        self.binds = midi_single
        if hid_binds is not None:
            self.hid_binds = dict(hid_binds)
        # temporal_hid 必须与下方单踩循环同源（现值 self.hid_binds）：
        # hid_binds=None 表示沿用现值，此时旧实参推导会让单踩错落
        # 快路径而 temporal 键整体转投引擎=死绑（审计实测）
        temporal_hid = {vk for a, vk in self.hid_binds.items()
                        if self.gestures.get(a, "single") != "single"}
        hid_single = {}
        for a, vk in self.hid_binds.items():
            g = self.gestures.get(a, "single")
            if g == "single" and vk not in temporal_hid:
                hid_single[vk] = a
            else:
                eng[("hid", vk), g] = a
        self._eng = eng
        self._hid_single = hid_single

    def sync_bridge(self):
        # 镜像 apply 的 HID 分流（只同步桥视图；引擎配置以最近一次 apply
        # 为准）。try_open 持 _lock 调入，此处不得再取锁（非重入锁）；
        # 字段读取 GIL 原子、值由 apply 锁内定稿
        self.bridge.configure(binds=self._hid_single,
                              device_hint=self.device_hint,
                              block=self.intercept, engine=self.engine,
                              temporal=self.engine.temporal_keys("hid"))

    @property
    def muted(self):
        return self._muted

    def mute(self):
        """踩钉控制页存活期静音：桥丢弃一切按键事件（silent 独立于学习
        通道——学习器 end_capture 只动 learning，页面静音不因此失效）；
        MIDI 口让出（winmm 输入口独占，学习器打不开同一口）。关页 unmute
        恢复。"""
        with self._lock:
            self._muted = True
        self.bridge.silent = True
        self.bridge.reset_hold_state()   # 静音边界的在途按住同 mid-hold：
        self.engine.reset()              #   不清则恢复后第一脚被残留吃掉
        self._midi_on.clear()
        self.close()

    def unmute(self):
        with self._lock:
            self._muted = False
        self.bridge.learning = False     # 学习通道兜底复位（正常由
        self.bridge.reset_hold_state()   #   end_capture 收口，此处防异常残留）
        self.bridge.silent = False       # 压制撤除放 reset 之后（照 configure
        self.engine.reset()              #   顺序）：清态期间桥线程仍被静音拦住
        self._midi_on.clear()

    def try_open(self):
        with self._lock:
            if self._muted:
                return False
            if self.hid_binds or self.device_hint:
                self.sync_bridge()
                self.bridge.start()
            if self.port:
                return True
            if not self.hint:
                return False
            try:
                self.port = RawMidiIn(self.hint, self._msg)
                self.name = self.port.name
                return True
            except PortNotFound:
                self.port = None
                return False

    @property
    def connected(self):
        return self.port is not None

    @property
    def hid_active(self):
        return bool(self.hid_binds) and self.bridge.running

    def device_online(self):
        try:
            return device_online(self.device_hint)
        except Exception:
            return False                # 枚举异常按离线处理，不炸重连 tick

    def _msg(self, status, d1, d2):
        if status & 0xF0 != 0xB0:
            return
        if self._muted:
            return                  # mute 瞬间在途回调：不得武装手势/触发
        # 单调钟：墙钟回拨会让去抖窗变负数，踏板整体冻结
        now = time.monotonic()
        if self.engine.is_temporal(("midi", d1)):
            # 迟滞边沿：≥64 压下、<44 释放，44-63 悬停区间维持原态——
            # 表达踏板/开关在阈值附近振荡时不再产生假按下/松开边沿。
            # 被引擎抖动闸拒收的边沿：迟滞层维持原态（与引擎不失步）
            on = self._midi_on.get(d1, False)
            if not on and d2 >= RISE:
                if self.engine.feed(("midi", d1), True, now):
                    self._midi_on[d1] = True
            elif on and d2 < RISE - 20:
                if self.engine.feed(("midi", d1), False, now):
                    self._midi_on[d1] = False
            return
        if fire(self._state, d1, d2, now):
            action = self.binds.get(d1)
            if action:
                self.on_action(action)

    def close(self):
        if self.port:
            try:
                self.port.close()
            except OSError:
                pass
            self.port = None

    def shutdown(self):
        """程序退出：MIDI 口与设备桥一并停。"""
        self.close()
        self.engine.reset()
        self.bridge.stop()


class Learner:
    """学习：双通道示范式捕获——点「学习」后用户踩出触发方式（单击/
    快踩两下），_tick 轮询 result() 时按同一套阈值懒分类。
    result() → ("midi", 设备名, CC, 手势) 或 ("hid", 键名, VK, 手势)。
    未选设备时只学 MIDI。"""

    _cc_lock = threading.RLock()   # winmm 线程与桥线程并发写（RLock：迟滞
                                   #   读改写持锁后再调 _note 重入；类级：测试
                                   # 用 object.__new__ 绕过 __init__ 也得有锁）
    _clock = staticmethod(time.monotonic)   # 同理：可注入假钟的类级默认

    def __init__(self, hint="", device_hint="", bridge=None,
                 double_window=None, clock=None, kb_ports=()):
        self.events = []               # (键, down, 时刻) 示范序列（已滤抖动、
                                       #   按压交替的干净事件流）
        self.cc = None                 # (通道, 来源名, 码, 手势)
        self.double_window = double_window or DOUBLE_WINDOW
        self._clock = clock or time.monotonic   # 可注入（测试假钟）
        self._midi_name = None
        self.ports = []
        if hint:
            devs = mb._in_devices()
            entries = _device_entries([n for _, n in devs])
            cands = [(i, n, k) for (i, n), (_l, _nm, k) in zip(devs, entries)
                     if hint in n]      # 与 kb_ports 分支同款三元组：下游统一
                                        #   解包，位次语义（同名多口）也一致
        else:
            cands = learning_candidates(kb_ports)
        for _idx, name, dev in cands:
            try:
                # dev=同名内位次（learning_candidates 随候选返回）：按名
                # 重开若丢位次（默认 0），同名盒场景下排除会被原样打回——
                # 候选解析与实际开口必须同一位次
                self.ports.append(RawMidiIn(name, self._make(name), dev=dev))
            except PortNotFound:
                pass                   # 被其他程序占用的口跳过
        self._bridge = None
        if bridge is not None and device_hint:
            self._bridge = bridge
            bridge.begin_capture(self._raw_capture)

    def _make(self, name):
        def feed(status, d1, d2):
            if status & 0xF0 == 0xB0:
                # 与引擎同款迟滞（≥64 压下、<44 释放）：CC 在阈值附近振荡
                # 时不产生假边沿（否则一次按压被拆成多段、分类全乱）。
                # 迟滞读改写与 _note 入账同一把锁（多 MIDI 口 winmm 线程
                # 并发防丢更新）
                with self._cc_lock:
                    on = getattr(self, "_on", None) or {}
                    if not on.get(d1, False) and d2 >= RISE:
                        self._on = {**on, d1: True}
                        self._note(("midi", d1), True,
                                   time.monotonic(), name)
                    elif on.get(d1, False) and d2 < RISE - 20:
                        self._on = {**on, d1: False}
                        self._note(("midi", d1), False,
                                   time.monotonic(), name)
        return feed

    def _raw_capture(self, vk, down, t):
        self._note(("hid", vk), down, t, None)

    def _note(self, key, down, t, name):
        with self._cc_lock:
            if self.cc is not None:
                return
            # 镜像引擎边沿受理的小状态机：示范序列只留干净交替的按压
            # 边界——与运行期分类永不分叉（模糊测试实测独立分类器必分叉）
            held = getattr(self, "_held", None) or {}
            down_t = getattr(self, "_down_t", None) or {}
            up_t = getattr(self, "_up_t", None) or {}
            if down:
                ld, lu = down_t.get(key), up_t.get(key)
                if (ld is not None and t - ld < BOUNCE_GATE) \
                        or (lu is not None and t - lu < BOUNCE_GATE):
                    return                # 触点抖动/分断回弹：同一脚延续
                if held.get(key):
                    return                # 按住重发：同一脚，不另记不后移锚
                held = {**held, key: True}
            else:
                if not held.get(key):
                    return                # 游离松开（点学习时脚已在板上）：不学
                # 无 up 同龄闸（与引擎同步删除）：TurnerPro 真松开沿距
                # 按下仅 7-16ms，任何同龄闸都会把真松开当噪声吞掉
                held = {**held, key: False}
                up_t = {**up_t, key: t}
            if down:
                down_t = {**down_t, key: t}
            self._held, self._down_t, self._up_t = held, down_t, up_t
            self.events.append((key, down, t))
            if name:
                self._midi_name = name

    def result(self):
        with self._cc_lock:
            if self.cc is None and self.events:
                r = self._classify(self._clock())
                if r is not None:
                    key, gesture = r
                    self.cc = ("hid" if key[0] == "hid" else "midi",
                               hid_name(key[1]) if key[0] == "hid"
                               else (self._midi_name or ""),
                               key[1], gesture)
            return self.cc

    def _classify(self, now):
        """示范序列（干净交替边沿）→ (键, 手势) 或 None。双踩=第二按下
        距首踩松开 ≤double_window；单踩=松脚后 double_window 平静过期
        （还按着就等松脚——踩住不放不再产生任何手势）。"""
        ev = self.events
        key = ev[0][0]
        downs = [t for k, d, t in ev if d and k == key]
        ups = [t for k, d, t in ev if not d and k == key]
        if not downs:
            return None
        if len(downs) >= 2:
            # 干净交替流：ups[0] 必为首踩松开、downs[1] 必为第二踩
            gap = downs[1] - ups[0]
            return (key, "double" if gap <= self.double_window else "single")
        if len(ups) < len(downs):
            return None                       # 还按着：等松脚再判
        return (key, "single") if now - ups[0] >= self.double_window else None

    def close(self):
        if getattr(self, "_bridge", None) is not None:
            self._bridge.end_capture()
            self._bridge = None
        for p in getattr(self, "ports", []):
            try:
                p.close()
            except OSError:
                pass
        self.ports = []


LEARN_TIMEOUT = 8.0        # 学习等待踩踏的时限（秒）


class PedalWindow(tk.Toplevel):
    """设备选择 + 六行功能 × [学习][清除]，底部监听状态。绑定存 config.json
    的 pedal 段。**存活期整体静音**：本页打开期间主程序不响应踩钉（调整/
    学习绑定不误触发演出动作），关闭本页恢复响应。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.learner = None
        self._transient_until = 0.0  # 结果类反馈展示窗（_set_transient）
        self._transient = ("", dpi.MUT)
        self._hover = None           # 悬停提示（bind_hint→_hint）
        self.title("踩钉控制")
        self.geometry(dpi.scale(self, 560, 400))
        # 设备下拉离线警示的字色变体（随 apply_theme 全局注册，此页只此
        # 一处用；下阶段收编进 dpi）
        ttk.Style(self).configure("Warn.TCombobox", foreground=dpi.C_WARN)
        pad = dpi.scale(self, 12)   # pack 边距是裸像素，高 DPI 下须换算
        ttk.Label(self, text="只有所选设备的按键会触发动作").pack(
            anchor="w", padx=pad, pady=(pad, 4))
        # 设备行与设置页严格同款（对照 SettingsWindow 的 menu_row 与
        # row(browse=True)：宽 15 标签 + 下拉填充 + 右侧 width=6 小按钮）；
        # 拦截勾选独立成行，同设置页复选框行（anchor="w", pady=1）
        devrow = ttk.Frame(self)
        devrow.pack(fill="x", padx=pad, pady=2)
        ttk.Label(devrow, text="输入设备", width=15,
                  anchor="w", style="Dim.TLabel").pack(side="left")
        self.dev_var = tk.StringVar(value="（未选择）")
        self.dev_opt = ttk.Combobox(devrow, textvariable=self.dev_var,
                                    state="readonly")
        self.dev_opt.pack(side="left", fill="x", expand=True)
        self._dev_hints = [""]     # 与 values 平行：候选→身份子串（0=仅 MIDI）
        self.dev_opt.bind("<<ComboboxSelected>>", self._on_dev_selected)
        dpi.bind_hint(self.dev_opt, self._hint,
                      "只有所选设备的按键会触发动作；未选则按键绑定不生效")
        ref_btn = ttk.Button(devrow, text="刷新", width=6,
                             command=self._update_device_menu)
        dpi.bind_hint(ref_btn, self._hint, "重新枚举设备并复核在线状态")
        ref_btn.pack(side="left", padx=(6, 0))
        self.intercept_var = tk.BooleanVar(value=self.app.pedal_intercept)
        cb = ttk.Checkbutton(self, text="拦截踏板按键",
                             variable=self.intercept_var,
                             command=self._toggle_intercept)
        cb.pack(anchor="w", padx=pad, pady=1)
        dpi.bind_hint(cb, self._hint,
                      "勾选后被绑定按键经系统热键截留，不送达其它软件；"
                      "学习不受影响")
        self.remote_var = tk.BooleanVar(
            value=getattr(self.app, "pedal_remote_enabled", False))
        cb = ttk.Checkbutton(self, text="允许平板转发踩钉",
                             variable=self.remote_var,
                             command=self._toggle_remote)
        cb.pack(anchor="w", padx=pad, pady=1)
        dpi.bind_hint(cb, self._hint,
                      "踏板 USB-C 连平板经 APP 转发；平板端 APP 设置里也要开")
        grid = ttk.Frame(self)
        grid.pack(fill="both", expand=True, padx=pad)
        for c, t in enumerate(("功能", "绑定", "操作")):
            ttk.Label(grid, text=t, anchor="w", style="Dim.TLabel").grid(
                row=0, column=c, sticky="w", pady=(0, 2),
                padx=(8, 0) if c == 1 else (0, 0))  # 对齐数据列左缩进
        self._bind_lbl = {}
        for r, (action, name) in enumerate(ACTIONS, start=1):
            ttk.Label(grid, text=name, anchor="w").grid(
                row=r, column=0, sticky="w", pady=2)
            lbl = ttk.Label(grid, text="未设置", anchor="w",
                            style="Dim.TLabel")
            lbl.grid(row=r, column=1, sticky="we", padx=(8, 8), pady=2)
            grid.columnconfigure(1, weight=1)
            cell = ttk.Frame(grid)
            cell.grid(row=r, column=2, sticky="w", pady=2)
            learn = ttk.Button(cell, text="学习", width=8,
                               command=lambda a=action: self._learn(a))
            learn.pack(side="left", padx=2)
            dpi.bind_hint(learn, self._hint,
                          "单踩为单踩手势，快踩两下为双踩；超时自动取消")
            clr = ttk.Button(cell, text="清除", width=8,
                             command=lambda a=action: self._clear(a))
            clr.pack(side="left", padx=2)
            dpi.bind_hint(clr, self._hint, "删除该动作的绑定")
            self._bind_lbl[action] = lbl
        # 底栏唯一一行：三方优先级（悬停提示>瞬时反馈>常驻状态）由
        # _present 统一裁决；平板转发短态并入常驻文本（_remote_state）；
        # side=bottom 停靠，后续 pack 次序变化不再挤动底栏
        self.status = ttk.Label(self, text="…", anchor="w",
                                style="Dim.TLabel")
        self.status.pack(side="bottom", fill="x", padx=pad,
                         pady=(6, dpi.scale(self, 8)))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.attributes("-topmost", True)   # 与主窗一致保持可见
        dpi.setup_window(self)
        # 尺寸适配：最小=内容自然需求；初始不低于规划值与需求值
        # 收敛窗口尺寸到内容需求：update_idletasks 不做 map，ttk 元素
        # 高度在首绘后才定稿（sv-ttk Entry 行 map 后 +2px/行），只有
        # 全量 update 让窗口完成首绘，req 才是真实值；minsize 与
        # geometry 同源，防窗口停在偏小值裁掉底行
        for _ in range(2):
            self.update()
            w = max(dpi.scale(self, 560), self.winfo_reqwidth())
            h = max(dpi.scale(self, 400), self.winfo_reqheight())
            self.minsize(w, h)
            self.geometry("%dx%d" % (w, h))
        # Map 后首绘让 ttk 图片元素高度定稿再抬 req（Entry 行
        # +2px/行）——照 dark_title 的 <Map> 先例再收敛一轮，
        # 防窗口停在 map 前的偏小值裁掉底部
        def _refit(_e=None):
            for _ in range(2):
                self.update_idletasks()
                w = max(dpi.scale(self, 560), self.winfo_reqwidth())
                h = max(dpi.scale(self, 400), self.winfo_reqheight())
                self.minsize(w, h)
                if self.winfo_height() < h:
                    self.geometry("%dx%d" % (w, h))
        self.bind("<Map>", _refit, add="+")
        # <Map> 在构建期 update 就触发，而首绘后的元素重算更晚：
        # 再挂两次延迟校准兜底（_refit 幂等，只抬不缩）
        for _ms in (120, 400):
            self.after(_ms, _refit)
        p = self.app.pedal
        if p is not None:
            p.mute()        # 存活期静音：本页开着踩钉不触发任何动作
        self.after(300, self._tick)
        self._refresh()
        self._update_device_menu()

    def _on_dev_selected(self, _e=None):
        """下拉选中：按位次映射回设备身份（0=仅 MIDI）。显示值是组合态
        （「名（在线/离线）」），不在 values 里时 current()=-1——只消费
        用户真选中的项。"""
        i = self.dev_opt.current()
        if 0 <= i < len(self._dev_hints):
            self._set_device(self._dev_hints[i])

    def _update_device_menu(self):
        """重建下拉项并刷新显示（打开窗口/点刷新/换设备后调用）。候选
        清单由 Combobox values 承载，选中语义=当前值（原 radiobutton
        圆点改由当前显示值表达）。显示值带在线/离线标注、不在候选里
        ——readonly Combobox 允许显示 values 之外的值。"""
        devs = pedal_list_devices_safe()
        self._dev_hints = [""] + [h for h, _disp in devs]
        self.dev_opt.config(values=["（不区分来源，仅 MIDI 踩钉）"]
                            + [disp for _h, disp in devs])
        hint = self.app.pedal_device_hint
        if hint:
            online = self.app.pedal is not None and self.app.pedal.device_online()
            self.dev_var.set("%s（%s）" % (device_display(hint),
                                          "在线" if online else "离线"))
            # 唯一保留的状态色：设备离线=警示（设置页下拉无此状态概念）。
            # fg 与 style 同写：sv-ttk 下 Combobox 的 style fg 不参与绘制
            # （dpi.paint_tree 注），widget 级 fg 是唯一渲染路径
            self.dev_opt.config(style="TCombobox" if online
                                else "Warn.TCombobox",
                                foreground=dpi.FG if online else dpi.C_WARN)
        else:
            self.dev_var.set("（未选择）")
            self.dev_opt.config(style="TCombobox", foreground=dpi.FG)

    def _set_device(self, hint):
        self.app.pedal_device_hint = hint
        self._save()
        p = self.app.pedal
        if p is not None:
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_double)
            p.try_open()
        self._update_device_menu()
        self._set_transient("已选输入设备：%s" % (device_display(hint)
                                              if hint else "未选择"))

    def _toggle_intercept(self):
        self.app.pedal_intercept = bool(self.intercept_var.get())
        self._save()
        p = self.app.pedal
        if p is not None:
            # 必须走 apply：只改桥属性会被重连 tick 的 sync_bridge
            # 用陈旧 listener 状态悄悄改回去（实测回滚）
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_double)
            p.try_open()

    def _toggle_remote(self):
        self.app.pedal_remote_enabled = bool(self.remote_var.get())
        self._save()
        self._present()

    def _refresh(self):
        for action, _name in ACTIONS:
            cc = self.app.pedal_binds.get(action)
            vk = self.app.pedal_hid.get(action)
            g = _GNAME.get(self.app.pedal_gestures.get(action, "single"), "")
            texts = []
            if cc is not None:
                texts.append("CC %d%s" % (cc, g))
            if vk is not None:
                texts.append("按键 %s%s" % (hid_name(vk), g))
            if texts:
                # 两通道都绑时同显（清除按钮会一起清，不展示会误导）
                self._bind_lbl[action].config(text="＋".join(texts),
                                              style="TLabel",
                                              foreground=dpi.FG)
            else:
                self._bind_lbl[action].config(text="未设置",
                                              style="Dim.TLabel",
                                              foreground=dpi.MUT)

    def _set_status(self, text, color=dpi.MUT):
        # fg 与 style 同写：sv-ttk 下 TLabel 族 style fg 不参与绘制
        # （dpi.paint_tree 注），widget 级 fg 是唯一渲染路径
        self.status.config(text=text, style=dpi.tone(color) + ".TLabel",
                           foreground=color)

    def _set_transient(self, text, color=dpi.MUT, hold=5.0):
        # 结果类反馈展示 hold 秒后回落常驻状态（_tick 驱动 _present）
        self._transient = (text, color)
        self._transient_until = time.time() + hold
        self._present()

    def _hint(self, text):
        self._hover = text
        self._present()

    def _present(self):
        """底栏三方优先级：悬停提示 > 学习倒计时/瞬时反馈 > 常驻状态。"""
        if self._hover is not None:
            self._set_status(self._hover, dpi.MUT)
        elif self.learner is not None:
            action, learner, deadline = self.learner
            extra = ("：未选输入设备，踏板按键不会被抓取"
                     if not self.app.pedal_device_hint
                     and self.app.pedal_hid else "")
            self._set_status("学习「%s」中（剩 %.0f 秒），请踩一下踩钉%s"
                             % (dict(ACTIONS)[action],
                                deadline - time.time(), extra), dpi.C_ERR)
        elif time.time() < self._transient_until:
            self._set_status(*self._transient)
        else:
            self._set_status(*self._state_text())

    def _state_text(self):
        """常驻状态：监听/静音态＋平板转发短态（未启用则不拼）。"""
        p = self.app.pedal
        if p is None:
            return "服务启动中…", dpi.MUT
        if not self.app.pedal_binds and not self.app.pedal_hid:
            base = ("还没有任何绑定：点任一「学习」开始", dpi.MUT)
        else:
            # 页面存活期整体静音——「监听中」在此是说谎，如实标注
            parts = ([p.name] if p.connected else []) \
                + (["键盘按键"] if p.hid_active else [])
            dead_hid = False
            if p.device_hint:
                online = p.device_online()
                parts.append("%s %s·拦截%s" % (
                    device_display(p.device_hint),
                    "在线" if online else "离线",
                    "开" if self.app.pedal_intercept else "关"))
            elif self.app.pedal_hid:
                # 未选设备时 HID 踏板被整体忽略（学习与触发都不抓），
                # 但桥仍在跑——状态栏若只写「键盘按键」=绑定死了看不见
                dead_hid = True
                parts.append("按键绑定未生效：未选择输入设备"
                             "（点「输入设备」选踏板）")
            if parts:
                base = ("已暂停响应（关闭本页恢复）：%s" % "、".join(parts),
                        dpi.C_WARN if dead_hid else dpi.MUT)
            elif p.hint:
                base = ("未找到踩钉口「%s」（关闭本页后自动重试）" % p.hint,
                        dpi.C_WARN)
            else:
                base = ("还没有任何绑定：点任一「学习」开始", dpi.MUT)
        remote = self._remote_state()
        if remote is None:
            return base
        rank = {dpi.MUT: 0, dpi.C_OK: 0, dpi.C_WARN: 1, dpi.C_ERR: 2}
        worse = rank.get(remote[1], 0) > rank.get(base[1], 0)
        return "%s · %s" % (base[0], remote[0]), \
            (remote[1] if worse else base[1])

    def _remote_state(self):
        """平板转发短态（未启用=None，不占常驻文本）。link 判据与 /state
        快照的 pedalRemote 一致（15 秒无心跳=失联）；未识别键>0=链路活
        但键路不通，转警示。远程路不受「输入设备」选择约束（那是本地
        raw HID 的归属门），故不随设备选择变化。"""
        if not getattr(self.app, "pedal_remote_enabled", False):
            return None
        p = self.app.pedal
        if p is None:
            return "平板转发 启动中", dpi.MUT
        now = time.monotonic()
        br = p.bridge
        link_t = max(br.remote_event_t, br.remote_hb_t)
        if link_t <= 0.0:
            return "平板转发 待平板", dpi.MUT
        if now - link_t > 15.0:
            return "平板转发 失联%.0f秒" % (now - link_t), dpi.C_WARN
        if br.remote_unknown:
            return "平板转发 未识别键%d个" % br.remote_unknown, dpi.C_WARN
        if br.remote_event_t > 0.0:
            return ("平板转发 正常·%.0fs前" % (now - br.remote_event_t),
                    dpi.C_OK)
        return "平板转发 正常·无事件", dpi.C_OK

    def _save(self):
        import setlist_gui as sg       # 延迟导入避免循环
        cfg = sg._load_config()
        cfg["pedal"] = {"deviceHint": self.app.pedal_hint,
                        "bindings": self.app.pedal_binds,
                        "hidBindings": self.app.pedal_hid,
                        "hidDeviceHint": self.app.pedal_device_hint,
                        "intercept": self.app.pedal_intercept,
                        "gestures": self.app.pedal_gestures,
                        "doubleWindow": self.app.pedal_double,
                        "remoteEnabled": bool(
                            getattr(self.app, "pedal_remote_enabled", False))}
        sg._save_config(cfg)

    def _learn(self, action):
        self._cancel_learn("已取消上一次学习")
        # 页面级静音已让出 MIDI 口（__init__ mute）；学习器直接开候选口
        try:
            learner = Learner(self.app.pedal_hint,
                              self.app.pedal_device_hint,
                              self.app.pedal.bridge,
                              double_window=self.app.pedal_double,
                              kb_ports=((self.app.jcfg["inHint"],
                                         _in_dev(self.app.jcfg)),
                                        (self.app.axcfg["inHint"],
                                         _in_dev(self.app.axcfg))))
        except BaseException:
            self._set_transient("学习启动失败（口被占用？）", dpi.C_WARN)
            return
        self.learner = (action, learner, time.time() + LEARN_TIMEOUT)
        self._present()

    def _cancel_learn(self, msg):
        if self.learner is None:
            return
        self.learner[1].close()
        self.learner = None
        p = self.app.pedal
        if p is not None:
            # 保持页面级静音（不 unmute）；apply 更新桥配置（try_open 被
            # muted 拦下是期望行为——关页 _close 统一恢复）
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_double)
        if msg:
            self._set_transient(msg)

    def _clear(self, action):
        cc = self.app.pedal_binds.pop(action, None)
        vk = self.app.pedal_hid.pop(action, None)
        if cc is None and vk is None:
            return
        self.app.pedal_gestures.pop(action, None)
        self._save()
        p = self.app.pedal
        if p is not None:
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_double)
            p.try_open()
        self._refresh()
        self._set_transient("已清除「%s」" % dict(ACTIONS)[action])

    def _tick(self):
        """轮询：学习进度与监听状态。单轮故障不得终结轮询（否则状态栏
        冻结、学习超时失效），异常吞掉下一轮自愈。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self._tick_once()
        except Exception:
            pass
        try:
            self.after(300, self._tick)
        except tk.TclError:
            pass                    # 窗口销毁竞态：轮询自然终结

    def _tick_once(self):
        if self.learner is not None:
            action, learner, deadline = self.learner
            res = learner.result()
            if res is not None:
                kind, dev, code, gesture = res
                learner.close()
                self.learner = None
                # 改绑身份=(码, 手势)：同键同手势只能属一个动作（摘旧），
                # 同键不同手势共存正是手势引擎的特性
                if kind == "hid":
                    moved = [a for a, v in self.app.pedal_hid.items()
                             if v == code and a != action
                             and self.app.pedal_gestures.get(a, "single")
                             == gesture]
                    for a in moved:
                        del self.app.pedal_hid[a]
                        # 该动作另一通道还有绑定时保留手势条目——跨通道
                        # 偷绑不得把残留绑定的手势静默翻回单踩
                        if self.app.pedal_binds.get(a) is None:
                            self.app.pedal_gestures.pop(a, None)
                    self.app.pedal_hid[action] = code
                    self.app.pedal_gestures[action] = gesture
                    label = "按键「%s」%s" % (dev, _GNAME.get(gesture, ""))
                else:
                    self.app.pedal_hint = dev
                    moved = [a for a, v in self.app.pedal_binds.items()
                             if v == code and a != action
                             and self.app.pedal_gestures.get(a, "single")
                             == gesture]
                    for a in moved:
                        del self.app.pedal_binds[a]
                        if self.app.pedal_hid.get(a) is None:
                            self.app.pedal_gestures.pop(a, None)  # 同上
                    self.app.pedal_binds[action] = code
                    self.app.pedal_gestures[action] = gesture
                    label = "%s 的 CC %d%s" % (dev, code,
                                               _GNAME.get(gesture, ""))
                if moved:
                    label += "（自「%s」改绑）" % "、".join(
                        dict(ACTIONS)[a] for a in moved)
                self._save()
                p = self.app.pedal
                if p is not None:
                    # 保持页面级静音（学习完成不恢复响应，关页才恢复）
                    p.apply(self.app.pedal_hint, self.app.pedal_binds,
                            self.app.pedal_hid, self.app.pedal_device_hint,
                            self.app.pedal_intercept,
                            self.app.pedal_gestures, self.app.pedal_double)
                self._refresh()
                self._set_transient("「%s」已绑定 %s"
                                    % (dict(ACTIONS)[action], label), dpi.C_OK)
            elif time.time() > deadline:
                if self.app.pedal_device_hint:
                    self._cancel_learn("学习超时：所选设备没发按键（踏板连接/模式请检查）")
                else:
                    self._cancel_learn(
                        "学习超时：未选输入设备，没等到 MIDI CC")
        self._present()

    def _close(self):
        self._cancel_learn("")
        p = self.app.pedal
        if p is not None:
            p.unmute()      # 关页恢复响应；此处主动拉起监听，重连 tick 兜底
            p.try_open()
        self.destroy()


def pedal_list_devices_safe():
    """list_keyboard_devices 的兜底包装：枚举失败不炸 UI。"""
    try:
        return list_keyboard_devices()
    except Exception:
        return []


if __name__ == "__main__":
    # 本机诊断：学习阶段实际会监听哪些口/键（演出日排查用）
    print("本机 loopMIDI 端口:", sorted(mb.loopmidi_ports()))
    print("本机学习候选口:", learning_candidates())
    print("学习候选按键数:", len(LEARN_VKS))
    print("本机键盘类设备:", pedal_list_devices_safe())
    # 纯逻辑自检（假数据，不依赖本机设备在不在场）
    mb.loopmidi_ports = lambda: {"Keyboard Automation", "loopMIDI Port"}
    assert _is_virtual("Keyboard Automation")      # 注册表名单命中（改名口）
    assert _is_virtual("loopMIDI Port 2")          # 名字兜底命中
    assert not _is_virtual("Rubix USB")            # 硬件口不误伤
    mb._in_devices = lambda: [(0, "Keyboard Automation"), (1, "loopMIDI Port"),
                              (2, "JUNO-DS88"), (3, "Rubix USB"), (4, "踩钉")]
    assert learning_candidates() == [(3, "Rubix USB", 0), (4, "踩钉", 0)]
    # HID 按键通道
    assert load_hid({}) == {}
    assert load_hid(
        {"pedal": {"hidBindings": {"next": 0xB0, "bad": 9}}}) == {"next": 0xB0}
    assert load_hid(
        {"pedal": {"hidBindings": {"next": 0xB0},
                   "gestures": {"next": "long"}}}) == {}   # 废除手势：作废
    assert load_device_cfg({}) == ("", True)
    assert load_device_cfg({"pedal": {"hidDeviceHint": None,
                                      "intercept": False}}) == ("", False)
    st = {}
    assert hid_fire(st, 0xB0, True, 200.0)         # 首按
    assert not hid_fire(st, 0xB0, True, 200.05)    # 按住不重复
    assert not hid_fire(st, 0xB0, False, 200.1)    # 松开不触发
    assert not hid_fire(st, 0xB0, True, 200.12)    # 去抖窗内再按被挡
    assert not hid_fire(st, 0xB0, False, 200.13)   # 松开（去抖后需释放再触发）
    assert hid_fire(st, 0xB0, True, 200.3)         # 释放后重踩可再触发
    assert hid_fire(st, 0xB1, True, 200.31)        # 其他键独立计数
    assert hid_name(0xB0) == "下一曲" and hid_name(0x70) == "F1"
    assert 0xB0 in LEARN_VKS and 0x0D in LEARN_VKS  # 多媒体键与回车可学
    assert 0x01 not in LEARN_VKS and 0x11 not in LEARN_VKS \
        and 0x14 not in LEARN_VKS and 0xA0 not in LEARN_VKS
    # 设备身份解析（本机真实路径形态）
    assert device_identity(
        r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}"
        r"_9df17da3c702&Col02#9&7bdb7a&0&0001#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}"
    ) == "9DF17DA3C702"
    assert device_identity(
        r"\\?\HID#VID_32D7&PID_0001&MI_00&Col02#7&42fb74b&0&0001"
        r"#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}"
    ) == "VID_32D7&PID_0001&MI_00"
    # 手势引擎（假定时器：手动推进，不真等时钟）
    fired2 = []
    spawned = []

    class _FT:
        def __init__(s, delay, cb):
            s.delay, s.cb, s.dead = delay, cb, False

        def cancel(s):
            s.dead = True

    def fake_spawn(delay, cb):
        h = _FT(delay, cb)
        spawned.append(h)
        return h

    eng = GestureEngine(fired2.append, spawn_timer=fake_spawn)
    eng.configure({(("hid", 0xB0), "double"): "next",
                   (("hid", 0xB0), "single"): "play"})
    eng.feed(("hid", 0xB0), True, 100.0)
    eng.feed(("hid", 0xB0), False, 100.1)
    assert fired2 == []                            # 等双踩窗判定
    eng.feed(("hid", 0xB0), True, 100.3)           # 窗内第二踩 → 双踩
    assert fired2 == ["next"]
    eng.feed(("hid", 0xB0), False, 100.4)          # 已消费，松开归位
    eng.feed(("hid", 0xB0), True, 106.0)           # 新序列
    eng.feed(("hid", 0xB0), False, 106.1)          # 松脚，等单踩窗到期
    assert fired2 == ["next"] and len(spawned) == 2
    assert abs(spawned[0].delay - DOUBLE_WINDOW) < 1e-9
    assert spawned[0].dead                         # 消费双踩时窗定时器已取消
    spawned[1].cb()                                # 第二序列窗到期 → 单踩
    assert fired2 == ["next", "play"]
    # 按住不放：松脚前零动作、不挂长踩定时器（长踩已废除）
    fired4 = []
    spawned.clear()                                # 与上一引擎的定时器分开数
    eng3 = GestureEngine(fired4.append, spawn_timer=fake_spawn)
    eng3.configure({(("hid", 0x22), "double"): "next",
                    (("hid", 0x22), "single"): "play"})
    eng3.feed(("hid", 0x22), True, 300.0)
    assert fired4 == []                            # 按住中：无动作无定时器
    eng3.feed(("hid", 0x22), False, 301.0)         # 按住 1 秒松开 → 等窗
    assert fired4 == [] and len(spawned) == 1      # 唯一定时器=双踩窗
    spawned[-1].cb()                               # 窗平静过期 → 单踩
    assert fired4 == ["play"]
    # 设备桥：归属判定 + 学习捕获 + 单踩快路径
    hits = []
    br = DeviceBridge(hits.append)
    br.configure(binds={0x0D: "play"}, device_hint="9DF17DA3C702")
    br._feed(0x0D, True)                           # 踩钉回车按下 → 触发
    assert hits == ["play"]
    br.learning = True
    cap = []
    br.capture = lambda vk, d, t: cap.append((vk, d, t))
    br._feed(0x0D, True)                           # 学习期：转捕获不触发
    br._feed(0x0D, False)
    assert [(v, d) for v, d, _t in cap] == [(0x0D, True), (0x0D, False)]
    assert hits == ["play"]
    br.learning = False
    br2 = DeviceBridge(hits.append)
    br2.configure(binds={}, device_hint="X", engine=eng3,
                  temporal={0xB0})                 # temporal 键热键一并注册
    assert not br2._hotkey_vks()                   # 关拦截=无热键
    br2.configure(binds={0xB0: "next"}, block=True)
    assert br2._hotkey_vks() == [0xB0]             # 绑定键注册热键
    br2.configure(device_hint="")                  # 未选设备：键留给系统
    assert br2._hotkey_vks() == []
    # 示范式学习分类
    ln = object.__new__(Learner)
    ln.cc = None
    ln.events = []
    ln.double_window = DOUBLE_WINDOW
    ln._clock = lambda: 300.2                      # 假钟
    ln._raw_capture(0xB0, True, 300.0)
    assert ln.result() is None                     # 还按着：等松脚
    ln._raw_capture(0xB0, False, 300.12)
    assert ln.result() is None                     # 等双踩窗平静过期
    ln._raw_capture(0xB0, True, 300.3)             # 窗内第二踩 → 双踩
    assert ln.result() == ("hid", "下一曲", 0xB0, "double")
    # 设备桥真机冒烟：线程内注册成功、停止干净（仅本机诊断运行）
    brl = DeviceBridge(lambda a: None)
    brl.configure(device_hint="9DF17DA3C702", block=True)
    brl.start()
    time.sleep(0.6)
    print("设备桥冒烟：running=%s raw_ok=%s" % (brl.running, brl.raw_ok))
    assert brl.running and brl.raw_ok
    brl.stop()
    assert not brl.running
    print("pedal self-check OK")
