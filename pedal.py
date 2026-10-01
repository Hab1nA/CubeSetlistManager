# -*- coding: utf-8 -*-
"""踩钉双通道快捷键：MIDI 踩钉（USB 直连或经声卡 MIDI IN，winmm 输入设备）
发 CC；蓝牙键盘型踩钉（如 M-Vave CUBE TURNER PRO，蓝牙 HID 发多媒体键）
走 Raw Input 设备桥——逐事件识别输入来源，只有所选设备能触发动作，且其按键
可被系统级拦截。触发方式三选一（学习时踩出来自动分类）：单踩=按下沿即触发
（零延迟快路径）；双踩=窗内第二踩落下触发；长踩=按住满阈值即触发（不等松
脚，蓝牙中途掉线不丢）。判别延迟只落在绑了时序手势的键上，仅单踩键零延迟。
绑定来自「学习」：双通道同时监听（MIDI 输入口排除 loopMIDI 虚拟口与两台琴；
按键只录所选设备），先到先得，写 config.json 的 pedal 段（bindings=MIDI CC，
hidBindings=虚拟键码，gestures=动作→手势，longPress/doubleWindow=阈值，
hidDeviceHint=所选设备身份，intercept=拦截开关）。热插拔：MIDI 口未连接时
由 GUI 轮询 try_open() 重连；蓝牙重连后句柄变化无需刷新（raw 侧逐事件解析
设备路径，无缓存）。"""
import ctypes
import re
import threading
import time
import tkinter as tk
import winreg
from ctypes import wintypes

import dpi
import midi_bridge as mb
from kbd_auto import RawMidiIn, PortNotFound

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

ACTIONS = (("play", "开始"), ("stop", "停止"), ("rewind", "回零"),
           ("next", "下一首"), ("panic", "全停"), ("auto", "自动切换"))
RISE = 64               # 上升沿阈值
DEBOUNCE = 0.15         # 两次触发最小间隔（秒）
EXCLUDE = ("JUNO", "AX-09", "Lucina")   # 已知硬件琴的 MIDI 口，学习时不当踩钉候选
GESTURES = ("single", "double", "long")  # 单踩/双踩/长踩
LONG_PRESS = 0.45       # 长踩阈值（秒）：按住满此时长即触发，不等松脚
DOUBLE_WINDOW = 0.35    # 双踩窗（秒）：松脚到此期限内来了第二踩=双踩
BOUNCE_GATE = 0.03      # 触点抖动闸（秒）：闭合弹跳的密集重按下/假松开、
                        #   以及分断弹跳的回弹重压（距上次被受理松开 <30ms）
                        #   ——同一物理脚的爆发整体折算为一次按压
_GNAME = {"single": "", "double": "·双踩", "long": "·长踩"}


def _is_virtual(name):
    """软件虚拟口判定：loopMIDI 注册表名单里的端口（改名/新增自动覆盖）
    或名字含 loopMIDI（名单读不到时的兜底）。虚拟口是软件间通路，上面
    只会有 Cubase 发的触发/时钟，学成踩钉就是误绑。"""
    return name in mb.loopmidi_ports() or "loopMIDI" in name


def learning_candidates():
    """学习阶段的踩钉候选输入口：排除已知硬件琴与全部 loopMIDI 虚拟口。"""
    return [(i, n) for i, n in mb._in_devices()
            if not any(x in n for x in EXCLUDE) and not _is_virtual(n)]


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


# ---- 时序手势引擎（单踩/双踩/长踩） ----

class GestureEngine:
    """每键独立小状态机：把「单击/快踩两下/踩住半秒」归一成动作。
    键 = ("hid", VK) 或 ("midi", CC)；绑定表 {(键, 手势): 动作}——同一键
    绑多个手势正是本引擎的存在意义。状态流转：
      down(按下,等长踩阈值) --松脚--> wait2(等第二踩) --到期--> 触发单踩
      down --按住满阈值--> 触发长踩（不等松脚，蓝牙中途掉线不丢）
      wait2 --第二踩落下--> 触发双踩（此后按住视为已消费，等松开归位）
    判别延迟只落在绑了时序手势的键上；仅单踩的键根本不进引擎（桥内
    按下沿快路径零延迟）。同键+同手势只能属一个动作（载入期去重、
    学习期改绑摘旧保证）。线程模型：HID 事件在桥线程、MIDI 在 winmm
    线程、定时器在 Timer 线程，全部经 _lock 串行，动作在锁外回调；
    token 让被新事件取代的过期定时器静默失效。"""

    def __init__(self, on_action, spawn_timer=None, on_event=None):
        self.on_action = on_action
        self.on_event = on_event   # 可选：诊断上报（动作回调异常等）
        self.binds = {}            # (键, 手势) → 动作
        self.key_gestures = {}     # 键 → {已绑手势}
        self.long_press = LONG_PRESS
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

    def configure(self, binds, long_press=None, double_window=None):
        with self._lock:
            self.binds = dict(binds)
            if long_press:
                self.long_press = float(long_press)
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
        """某通道里走引擎（绑了双踩/长踩）的键集合，桥据此分流。"""
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
                # 爆发折算为一次按压；还原 down 相位的旧方案会在收尾弹开
                # 沿上留僵尸 down=假长踩/单踩丢失，审计实测否决）
                return False
            if not down and st and st[0] == "down" \
                    and now - st[1] < BOUNCE_GATE:
                # 松开沿同龄闸：按下未满 30ms 的假 up 当没松。代价注记：
                # 表达踏板擦阈值（<30ms 越阈即回）会计为一次持续按住，
                # 长踩阈值后误发一次长踩——足部全周期 <30ms 超出生理，接受
                return False             #   此代价（审计 R5a 量化）
            fire = self._feed_locked(key, down, now)
            if down:
                self._last_down[key] = now
            elif st is not None and st[0] in ("down", "held"):
                # 锚只认真正的按压收尾释放沿（down 与 held 两个相位）：
                # wait2/游离期的重复 up 是同一次释放的回声，不设锚；held
                # 释放沿不设锚则长踩/双踩触发后的释放回弹会武装僵尸 down
                # （幽灵长踩二连发+下一真踩被吞，审计实测）
                self._last_up[key] = now
        if fire:
            self._emit(fire)
        return True

    def _feed_locked(self, key, down, now):
        gs = self.key_gestures.get(key) or set()
        st = self._state.get(key)
        if down:
            if st is None:                       # 新序列：等长踩阈值
                self._arm(key, "down", now)
                if "long" in gs:
                    self._timer(key, self.long_press, "long")
            elif st[0] == "wait2":               # 双踩窗内第二踩
                self._stop_timer(key)
                if "double" in gs:
                    self._arm(key, "held", now)  # 已消费，按住到松开归位
                    return (key, "double")
                self._arm(key, "down", now)      # 无双踩绑：当新序列开头
                if "long" in gs:
                    self._timer(key, self.long_press, "long")
            # down/held 期间的再次按下=固件按住重发或已消费：忽略（无长踩
            # 绑定的悬挂 down——爆发吞 up 所致——由下一真踩的 up 保守折算
            # 单踩自愈，动作无净丢失；审计 F4 接受不挂折算定时器）
            return None
        if st is None:
            return None                          # 游离松开（边界残留）：忽略
        if st[0] == "down":
            self._stop_timer(key)                # 长踩定时器一并撤
            if "double" in gs:
                self._arm(key, "wait2", now)
                self._timer(key, self.double_window, "single")
                return None
            self._state.pop(key, None)
            # 单+长组合：松脚即排除长踩，单踩立即触发（不等窗）
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

    def _timer(self, key, delay, kind):
        token = self._state[key][2]
        self._stop_timer(key)
        self._timers[key] = self._spawn(
            delay, lambda: self._on_timer(key, token, kind))

    def _stop_timer(self, key):
        old = self._timers.pop(key, None)
        if old:
            old.cancel()

    def _on_timer(self, key, token, kind):
        with self._lock:
            st = self._state.get(key)
            if not st or st[2] != token:
                return                  # 已被新事件/reset 取代：过期静默
            self._timers.pop(key, None)
            gs = self.key_gestures.get(key) or set()
            if kind == "long" and st[0] == "down" and "long" in gs:
                self._arm(key, "held", time.monotonic())
                fire = (key, "long")
            elif kind == "single" and st[0] == "wait2":
                self._state.pop(key, None)
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
LLKHF_INJECTED = 0x10           # SendInput 等软件注入标记（不是真踩钉）
WM_INPUT = 0x00FF
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


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                              wintypes.WPARAM, ctypes.c_ssize_t)
_HOOK_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
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
u32.SetWindowsHookExW.restype = wintypes.HANDLE
u32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOK_PROC, wintypes.HANDLE,
                                  wintypes.DWORD)
u32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
u32.CallNextHookEx.restype = ctypes.c_ssize_t
u32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM,
                               ctypes.c_ssize_t)
u32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                            wintypes.UINT, wintypes.UINT)
u32.PostThreadMessageW.argtypes = (wintypes.DWORD, ctypes.c_uint,
                                   wintypes.WPARAM, wintypes.LPARAM)


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


class RawInputBridge:
    """专用输入线程：隐藏窗口 + Raw Input(INPUTSINK) + LL 键盘钩子
    + 消息泵，四者同线程免锁。职责：
    ① 逐事件设备归属：WM_INPUT 的 hDevice → 接口路径 → 与所选设备匹配
       （被钩子拦截的键不产生 WM_INPUT，故拦截决策不依赖同事件 raw）；
    ② 仅所选设备的按下沿触发 on_action（来源判定 100% 正确，后台有效）；
    ③ 低级钩子吞掉「所选设备已证实发过的键」，防漏给其他程序；没见过的
       键放行（交给 raw 侧按归属触发——每键首踩漏一次给前台是刻意代价，
       否则空证据下任何来源的绑定键都会被误当踩钉，且被吞键不产生
       WM_INPUT、证据永无积累，误触发是永久性的）；键盘也发过的键放行；
       软件注入（LLKHF_INJECTED）不是踩钉，放行。"""

    def __init__(self, on_action, on_event=None):
        self.on_action = on_action
        self.on_event = on_event   # 可选：内部事件上报（钩子自愈等，桥线程）
        self.binds = {}          # vk → 动作
        self.device_hint = ""    # 所选设备身份子串（空=未选择）
        self.block = False       # 拦截开关
        self.learning = False    # 学习期：不触发不拦截，事件转投 capture
        self.capture = None      # 学习回调 fn((键, down, 时刻))
        self.engine = None       # 手势引擎（监听器注入）；None=无时序绑定
        self.temporal = frozenset()   # 走引擎的 HID 键（绑了双踩/长踩）
        self.pedal_keys = set()  # 踩钉已证实发过的键（未绑定的也拦）
        self._evidence = {}      # vk → [踩钉发过, 其他设备发过]
        self._state = {}         # vk → hid_fire 状态
        self._swallowed = set()  # 钩子已吞 down 的键，其 up 一并吞
        self._stop = threading.Event()
        self._thread = None
        self._tid = None
        self._hook = None
        self._hook_seen = None   # 钩子最近一次见键时刻（运行期自愈探针）
        self._rehook_at = 0.0    # 上次自愈重装时刻（限频 15s）
        self._rehooking = False  # 自愈线程在途标志
        self._rehook_seq = 0     # 自愈尝试代际（挂死看门狗甄别用）
        self._rehook_ok_seq = 0  # 已成功装上钩子的代际（甄别边界误报）
        self._rehook_tids = []   # 在途自愈泵线程 id（stop 逐个收尾）
        self._tids_lock = threading.Lock()
        self.raw_ok = None           # 线程启动后回填：INPUTSINK 是否注册成功
        self._installed = threading.Event()   # LL 钩子安装完成信号
        self._pumping = threading.Event()     # 线程消息队列已建成（WM_QUIT 可投）
        self._live = False       # 钩子允许工作（迟到线程装上的钩子不得读状态）
        self._watchdog = None
        self._attempt = 0
        self._gen = 0            # 每次起线程自增：卡死后迟到的旧线程凭它自检退场

    def configure(self, binds=None, device_hint=None, block=None,
                  engine=None, temporal=None):
        if binds is not None:
            if binds != self.binds:
                self._swallowed.clear()  # 换绑：旧按住状态作废，防误吃 up
            self.binds = dict(binds)
        if device_hint is not None and device_hint != self.device_hint:
            self._evidence.clear()      # 换设备：来源证据全部作废
            self._state.clear()
            self.pedal_keys.clear()     # 旧设备证实过的键不再拦
            self._swallowed.clear()
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

    def reset_hold_state(self):
        """学习/拦截开关等边界切换后调用：吞单与按下态全作废——边界窗口期
        里漏掉的 up 没人处理，残留状态会把切换后的第一脚吃成「按住重复」
        静默吞掉（审计两轮各实测复现一次：切拦截、学习期各一脚无反应）。"""
        self._swallowed.clear()
        self._state.clear()

    def begin_capture(self, cb):
        self.learning = True
        self.capture = cb

    def end_capture(self):
        self.learning = False
        self.capture = None

    def start(self):
        """启动设备桥。LL 钩子安装存在非确定性挂死（实测本机偶发，
        SetWindowsHookExW 不返回），由看门狗线程换新线程重试最多 3 次。"""
        if self._watchdog is not None or self._thread is not None:
            return
        self._installed.clear()
        self._stop.clear()
        self._watchdog = threading.Thread(target=self._start_watchdog,
                                          name="hid-bridge-wd", daemon=True)
        self._watchdog.start()

    def _start_watchdog(self):
        for self._attempt in range(3):
            if self._installed.is_set() or self._stop.is_set():
                return
            self._gen += 1              # 旧线程若只是卡而未死，迟到后凭代际退场
            self._pumping.clear()
            self._thread = threading.Thread(target=self._run,
                                            name="hid-bridge", daemon=True)
            self._thread.start()
            if self._installed.wait(8):
                self._watchdog = None   # 成功即放行 start()：泵线程日后自行
                return                  #   死亡（GetMessageW -1）必须可重拉
            self._thread = None        # 卡死的旧线程随进程退出（daemon）
            self._tid = None
        self._watchdog = None

    def _retire(self):
        """早退线程若仍是登记在册的那个，清掉登记。stop() 的零等待早退与
        看门狗「_stop 检查后、_thread 赋值前」的字节码级交错，会让 running
        永久 True、start 永久被拒（故障注入实测可达，自然窗口极窄）。"""
        if self._thread is threading.current_thread():
            self._thread = None
            self._tid = None

    def stop(self):
        self._stop.set()
        self._installed.set()          # 唤醒看门狗等待
        self._live = False             # 钩子即刻失效（放行，不做半截决策）
        if self._thread is None and self._tid is None:
            self._watchdog = None
            return                     # 从未起线程：零等待（否则白等 1.2 秒，
                                       #   退出确认后主线程冻结，纯 MIDI 用户实测）
        for _ in range(100):           # 等线程报到再投 WM_QUIT
            if self._tid is not None:
                break
            time.sleep(0.002)
        # 消息队列建成前 PostThreadMessageW 必失败且消息永久丢失（实测确定性
        # 复现：WM_QUIT 丢掉→泵不退→join 超时→running 谎报 False 而钩子还活着）
        if self._pumping.wait(1.0) and self._tid is not None:
            for _ in range(20):
                if u32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0):
                    break
                time.sleep(0.01)
        with self._tids_lock:               # 在途自愈泵逐个收尾（复审 R3-5：
            rehook_tids, self._rehook_tids = self._rehook_tids, []  # 多代
        for tid in rehook_tids:             #   并存时不再只通知最后一个）
            u32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        th = self._thread     # 先摘登记再 join：对已赋值未起跑的交错态，
        self._thread = None   #   join 会抛 RuntimeError 且残留登记谎报 running
        self._tid = None
        self._watchdog = None
        if th is not None:
            try:
                th.join(timeout=1.0)
            except RuntimeError:
                pass

    @property
    def running(self):
        return self._thread is not None

    def _feed(self, vk, down, is_pedal):
        """raw 事件 → 证据积累；所选设备的按下沿触发动作/学习捕获（含
        松开沿，手势学习要完整踩法）；时序手势键转投引擎，仅单踩键走
        按下沿快路径。"""
        ev = self._evidence.setdefault(vk, [False, False])
        ev[0 if is_pedal else 1] = True
        if not is_pedal:
            return
        if down:
            self.pedal_keys.add(vk)
        if self.learning and self.capture:
            self.capture((vk, down, time.monotonic()))
        if self.learning:
            return
        if vk in self.temporal and self.engine is not None:
            self.engine.feed(("hid", vk), down, time.monotonic())
            return
        if hid_fire(self._state, vk, down, time.monotonic()) and down:
            action = self.binds.get(vk)
            if action:
                self.on_action(action)

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
        is_pedal = bool(self.device_hint) and self.device_hint in name.upper()
        self._feed(kb.VKey, kb.Message in _LL_KEYDOWN, is_pedal)
        self._probe(time.monotonic())

    def _probe(self, now):
        """钩子自愈探测（raw 事件时点调用，桥线程同线程无锁）：物理键必先
        过 LL 钩子链再到 raw——被吞键不产生 WM_INPUT，钩子活着则每条 raw
        键事件前几毫秒必有钩子事件（_hook_seen 刚更新）。raw 见键而探针
        落后 2 秒=钩子已被系统静默摘除（LowLevelHooksTimeout 超时规则，
        官方文档明言无任何通知）。动作由 raw 侧兜底不中断，这里只恢复
        「拦截」能力；探针为 None=钩子从未见过任何键（装完即死同理）。"""
        if self._live and (self._hook_seen is None
                           or now - self._hook_seen > 2.0):
            self._rehook(now)

    def _rehook(self, now):
        """钩子运行期自愈：换独立常驻线程重装。LL 钩子回调在安装线程的
        消息泵上执行——一次性线程装完即死=钩子变僵尸，故重装线程自带消息
        泵（stop 时收 WM_QUIT 拆钩退场）。SetWindowsHookExW 已知偶发挂死，
        绝不在桥线程重装（桥死=raw 通道死=动作也断，比死拦截更糟）。
        旧钩子若其实还活着：重叠窗口里 _hook_event 双见同键，动作由
        hid_fire 按住态去重、吞键幂等，无副作用。限频 15 秒。"""
        if self._rehooking or now - self._rehook_at < 15:
            return
        self._rehook_at = now
        self._rehooking = True
        self._rehook_seq += 1
        seq = self._rehook_seq

        def job():
            tid = k32.GetCurrentThreadId()
            with self._tids_lock:
                self._rehook_tids.append(tid)
            h = u32.SetWindowsHookExW(13, self._href, None, 0)
            if not h:
                self._rehooking = False
                self._report("低级钩子疑似被系统摘除，重装失败"
                             "（15 秒后自动重试）")
                return
            self._rehook_ok_seq = seq
            old = self._hook
            self._hook = h
            if old:
                u32.UnhookWindowsHookEx(old)
            self._report("低级钩子疑似被系统摘除，已自动重装")
            msg = wintypes.MSG()
            while not self._stop.is_set() \
                    and u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
            u32.UnhookWindowsHookEx(h)
            self._rehooking = False

        threading.Thread(target=job, daemon=True,
                         name="hid-rehook").start()

        def unstick():
            # 重装挂死看门狗：SetWindowsHookExW 已知偶发不返回（启动路径
            # 同款），挂死则 _rehooking 永久 True=自愈单点失效——到点放行
            # 限频门再试（挂死线程为 daemon，随进程回收）。装成于边界时
            # 静默放行（_rehooking 在泵退出前一直为 True，这里兼任「成功
            # 后放行未来再自愈」职责，不打误导日志，复审 R3-5）
            if self._rehooking and self._rehook_seq == seq:
                if self._rehook_ok_seq != seq:
                    self._report("钩子重装超时未返回，稍后自动重试")
                self._rehooking = False

        t = threading.Timer(REHOOK_UNSTICK_SEC, unstick)
        t.daemon = True
        t.start()

    def _report(self, msg):
        try:
            if self.on_event:
                self.on_event(msg)
        except Exception:
            pass

    def _hook_cb(self, nCode, wp, lp):
        """LL 钩子回调（签名=nCode/wp/lp）；决策转投 _hook_event。
        任何异常都不得外抛，否则钩子链行为不可预期。"""
        try:
            # 迟到线程解挂后到自检退场之间，它装的钩子短暂是活的——
            # _live 未置位期间只放行，不得读共享状态做拦截/触发
            if nCode == 0 and self._live:
                down = wp in _LL_KEYDOWN
                up = wp in _LL_KEYUP
                if down or up:
                    st = ctypes.cast(lp, ctypes.POINTER(
                        _KBDLLHOOKSTRUCT)).contents
                    self._hook_seen = time.monotonic()   # 自愈探针证据
                    if self._hook_event(st.vkCode, down,
                                        bool(st.flags & LLKHF_INJECTED)):
                        return 1                 # 系统级拦截
        except Exception:
            pass
        return u32.CallNextHookEx(None, nCode, wp, lp)

    def _hook_event(self, vk, down, injected=False):
        """钩子事件决策：返回是否吞键。只拦「所选设备已证实发过的键」——
        空证据的键放行（交给 raw 侧按归属决定是否触发，每键首踩漏一次
        给前台是刻意代价；否则空证据下任何来源的绑定键都会被误当踩钉，
        且被吞键不产生 WM_INPUT、证据永无积累，误触发是永久性的）。
        键盘也发过的键放行；软件注入（LLKHF_INJECTED，AutoHotkey/遥控
        等）不是踩钉，放行。吞下的按下沿触发动作；学习期不拦不触发。"""
        if injected or self.learning or not self.block or not self.device_hint:
            return False
        ev = self._evidence.get(vk)
        if not (ev and ev[0]):          # 所选设备从未发过此键：不拦不触发
            return False
        if vk not in self.binds and vk not in self.pedal_keys:
            return False
        if not down and vk not in self._swallowed:
            return False
        if ev[1]:                    # 键盘也发过 → 放行
            self._swallowed.discard(vk)
            return False
        if down:
            repeat = vk in self._swallowed
            self._swallowed.add(vk)
            if vk in self.temporal and self.engine is not None:
                # 时序手势键：吞下但不即时触发，转投引擎判别（未决延迟
                # 是双踩/长踩判别的物理必然，仅单踩键不受影响）
                if not repeat:
                    self.engine.feed(("hid", vk), True, time.monotonic())
            elif not repeat and hid_fire(self._state, vk, True,
                                         time.monotonic()):
                action = self.binds.get(vk)
                if action:
                    self.on_action(action)
            return True
        self._swallowed.discard(vk)
        if vk in self.temporal and self.engine is not None:
            self.engine.feed(("hid", vk), False, time.monotonic())
        else:
            hid_fire(self._state, vk, False, time.monotonic())
        return True

    def _wndproc(self, h, m, w, l):
        if m == WM_INPUT:
            # RAWINPUT 句柄在 lParam；wParam 只是 RIM_INPUT(SINK) 标志
            # （传错参数读出全零数据，raw 通道整体失聪——端到端实测抓出）
            self._on_raw(l)
        return u32.DefWindowProcW(h, m, w, l)

    def _run(self):
        try:
            self._run_inner()
        except BaseException:
            # 未捕异常死亡也必须清登记：残留 _thread 会让 running 恒 True、
            # 10s 重拉门控（not bridge.running）失去触发条件=HID 死到重启
            self._retire()

    def _run_inner(self):
        gen = self._gen                  # 看门狗换线程后旧值失效，迟到即退场
        if gen == self._gen:             # 迟到线程不得写 _tid：stop 已清过后
            self._tid = k32.GetCurrentThreadId()   #   再写入会拖慢下次 stop
        ref = _WNDPROC(self._wndproc)
        href = _HOOK_PROC(self._hook_cb)
        # 回调 trampoline 必须活过整个窗口生命周期：挂 self 上防 GC——
        # 局部变量随线程退出被回收后，窗口再收到消息就是原生崩溃
        self._ref = ref
        self._href = href
        # 窗口类名进程级唯一：同进程第二个桥/看门狗重试线程沿用旧类名时
        # RegisterClassW 静默失败，窗口的 wndproc 会落到旧实例的回调上
        #（实测=失聪；旧实例退出回收 trampoline 后=原生崩溃），必须逐次唯一
        cls = "CSMRawInputBridge-%x-%d" % (id(self), gen)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = ref
        wc.lpszClassName = cls
        wc.hInstance = k32.GetModuleHandleW(None)
        if not u32.RegisterClassW(ctypes.byref(wc)):
            self.raw_ok = False
            self._installed.set()        # 让看门狗按时换线程重试
            self._pumping.set()          # 线程即将退出，别让 stop 白等队列
            self._retire()
            return
        hwnd = u32.CreateWindowExW(0, cls, "x", 0, 0, 0, 0, 0,
                                   None, None, wc.hInstance, None)
        self._pumping.set()   # 窗口建成=消息队列存在，WM_QUIT 从此可投递
        if gen != self._gen or self._stop.is_set():
            # 还没装钩子就已过时/被叫停：不装钩子直接退场
            u32.DestroyWindow(hwnd)
            u32.UnregisterClassW(cls, wc.hInstance)
            self._retire()
            return
        hook = u32.SetWindowsHookExW(13, href, None, 0)   # 已知会偶发挂死
        if gen != self._gen or self._installed.is_set() or self._stop.is_set():
            # 后继线程已接管/已被叫停：只拆自己装过的钩子，绝不碰 raw 注册
            # （RIDEV_REMOVE 是进程级的，会拆掉接管线程的注册）
            if hook:
                u32.UnhookWindowsHookEx(hook)
            u32.DestroyWindow(hwnd)
            u32.UnregisterClassW(cls, wc.hInstance)
            self._retire()
            return
        self._hook = hook
        rid = _RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, hwnd)
        self.raw_ok = bool(u32.RegisterRawInputDevices(
            ctypes.byref(rid), 1, ctypes.sizeof(_RAWINPUTDEVICE)))
        self._installed.set()             # 安装返回（挂死则看门狗换线程重试）
        self._live = True                 # 钩子自此刻起允许做决策
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        self._live = False
        if self._hook:
            u32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        ridrm = _RAWINPUTDEVICE(1, 6, RIDEV_REMOVE, None)
        u32.RegisterRawInputDevices(ctypes.byref(ridrm), 1,
                                    ctypes.sizeof(_RAWINPUTDEVICE))
        u32.DestroyWindow(hwnd)
        u32.UnregisterClassW(cls, wc.hInstance)
        self._retire()   # 循环在 stop() 之外死亡（GetMessageW 返 -1）时
                         #   不清登记=running 永久 True、start 永久被拒


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


def load_binding(cfg):
    """完整 config dict → (设备名提示, {动作: cc 号})。JSON 的 true 是
    int 子类，须一并挡掉（否则绑定成 CC 1）；pedal 段/子键被手改成非
    dict 时按空段处理（启动路径，类型错不能炸开机）。同码去重按
    (码, 手势) 身份——同键不同手势是手势引擎的特性，不算冲突。"""
    p = _pedal_dict(cfg)
    gest = _gestures_of(p)
    binds = {}
    seen = set()
    for k, v in _pedal_dict(cfg, "bindings").items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool):
            identity = (v, gest.get(k, "single"))
            if identity in seen:    # 同码同手势：反转时后者静默覆盖前者，
                continue            #   载入期保首个，杜绝绑定静默失效
            seen.add(identity)
            binds[k] = v
    return str(p.get("deviceHint") or ""), binds


def load_hid(cfg):
    """完整 config dict → {动作: VK 码}（pedal.hidBindings 段）。去重
    身份同 load_binding：(码, 手势)。"""
    p = _pedal_dict(cfg)
    gest = _gestures_of(p)
    out = {}
    seen = set()
    for k, v in _pedal_dict(cfg, "hidBindings").items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool):
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
    """完整 config dict → (手势表, 长踩阈值, 双踩窗)。阈值只认真数字并
    夹在合理区间（手改配置不能炸、不能设出负窗）。"""
    p = _pedal_dict(cfg)

    def num(key, dflt, lo, hi):
        v = p.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and lo <= v <= hi:
            return float(v)
        return dflt

    return _gestures_of(p), num("longPress", LONG_PRESS, 0.15, 2.0), \
        num("doubleWindow", DOUBLE_WINDOW, 0.12, 1.0)


class PedalListener:
    """MIDI 口按 deviceHint 常驻监听（CC 上升沿）＋设备桥按所选设备监听
    （HID 按下沿，来源判定+定向拦截）→ on_action(动作名)。仅单踩绑定走
    各自通道的按下沿快路径；双踩/长踩绑定交给 GestureEngine 判别。MIDI
    回调在 winmm 线程、按键在设备桥线程、手势定时器在 Timer 线程，GUI
    侧经 calls 队列转投主线程。"""

    def __init__(self, on_action, on_event=None):
        self.on_action = on_action
        self.hint = ""
        self.binds = {}
        self.hid_binds = {}        # 动作 → VK
        self.gestures = {}         # 动作 → 手势
        self.timing = (LONG_PRESS, DOUBLE_WINDOW)
        self.device_hint = ""      # 所选设备身份子串
        self.intercept = True      # 拦截开关
        self.port = None
        self.name = ""
        self._state = {}
        self._midi_on = {}         # 手势键迟滞：CC 当前是否在按下态
        self._lock = threading.Lock()
        self._muted = False        # 学习期静音：停触发、让出 MIDI 口
        self.engine = GestureEngine(on_action, on_event=on_event)
        self.bridge = RawInputBridge(on_action, on_event)

    def apply(self, hint, binds, hid_binds=None, device_hint=None,
              intercept=None, gestures=None, timing=None):
        timing = timing or (LONG_PRESS, DOUBLE_WINDOW)
        if not (isinstance(timing, tuple) and len(timing) == 2):
            timing = (LONG_PRESS, DOUBLE_WINDOW)    # 形状防御（锁内别炸）
        with self._lock:
            self.hint = hint
            self.gestures = dict(gestures or {})
            self.timing = timing
            # 双通道归一分流：纯单踩键走按下沿快路径；绑了双踩/长踩的键，
            # 其全部手势（含 single 兄弟绑定）都进引擎——否则单踩留在桥
            # 快路径而桥把 temporal 键整体转投引擎，单踩即死绑（审计实测）
            temporal_midi = {cc for a, cc in (binds or {}).items()
                             if self.gestures.get(a, "single") != "single"}
            eng = {}
            midi_single = {}
            for a, cc in (binds or {}).items():
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
            if device_hint is not None:
                self.device_hint = device_hint
            if intercept is not None:
                self.intercept = bool(intercept)
            self.engine.configure(eng, *self.timing)   # 未决手势一并作废
            self._midi_on.clear()                      # 迟滞态随重配归零
            temporal = self.engine.temporal_keys("hid")
        self.bridge.configure(binds=hid_single,
                              device_hint=self.device_hint,
                              block=self.intercept,
                              engine=self.engine, temporal=temporal)
        self.close()

    def sync_bridge(self):
        # 镜像 apply 的分流（只同步桥视图；引擎配置以最近一次 apply 为准）
        temporal_hid = {vk for a, vk in self.hid_binds.items()
                        if self.gestures.get(a, "single") != "single"}
        singles = {vk: a for a, vk in self.hid_binds.items()
                   if self.gestures.get(a, "single") == "single"
                   and vk not in temporal_hid}
        self.bridge.configure(binds=singles,
                              device_hint=self.device_hint,
                              block=self.intercept,
                              engine=self.engine,
                              temporal=self.engine.temporal_keys("hid"))

    @property
    def muted(self):
        return self._muted

    def mute(self):
        """学习期静音：桥停触发/停拦截（事件转投学习捕获），MIDI 口让出——
        winmm 输入口独占，不松口学习器打不开同一口。"""
        with self._lock:
            self._muted = True
        self.bridge.learning = True
        self.bridge.reset_hold_state()   # 进出学习的边界同 mid-hold：不清则
        self.engine.reset()              #   学后第一脚被残留吞单/手势吃掉
        self.close()

    def unmute(self):
        with self._lock:
            self._muted = False
        self.bridge.learning = False
        self.bridge.reset_hold_state()
        self.engine.reset()
        self._midi_on.clear()   # 学习期前的迟滞态不得吞掉恢复后第一脚
        self.engine.reset()

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
    快踩两下/踩住约半秒），_tick 轮询 result() 时按同一套阈值懒分类。
    result() → ("midi", 设备名, CC, 手势) 或 ("hid", 键名, VK, 手势)。
    未选设备时只学 MIDI。"""

    _cc_lock = threading.RLock()   # winmm 线程与桥线程并发写（RLock：迟滞
                                   #   读改写持锁后再调 _note 重入；类级：测试
                                   # 用 object.__new__ 绕过 __init__ 也得有锁）
    _clock = staticmethod(time.monotonic)   # 同理：可注入假钟的类级默认

    def __init__(self, hint="", device_hint="", bridge=None,
                 timing=None, clock=None):
        self.events = []               # (键, down, 时刻) 示范序列（已滤抖动、
                                       #   按压交替的干净事件流）
        self.cc = None                 # (通道, 来源名, 码, 手势)
        self.long_press, self.double_window = timing or (LONG_PRESS,
                                                         DOUBLE_WINDOW)
        self._clock = clock or time.monotonic   # 可注入（测试假钟）
        self._midi_name = None
        self.ports = []
        if hint:
            cands = [(i, n) for i, n in mb._in_devices() if hint in n]
        else:
            cands = learning_candidates()
        for _idx, name in cands:
            try:
                self.ports.append(RawMidiIn(name, self._make(name)))
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
                # 时不产生假边沿（否则长踩演示被学成双踩）。迟滞读改写与
                # _note 入账同一把锁（多 MIDI 口 winmm 线程并发防丢更新）
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
            arm = getattr(self, "_arm", None) or {}
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
                arm = {**arm, key: t}     # up 闸锚=按压武装时刻（引擎同语义）
            else:
                if not held.get(key):
                    return                # 游离松开（点学习时脚已在板上）：不学
                a = arm.get(key)
                if a is not None and t - a < BOUNCE_GATE:
                    return                # 按下未满 30ms 的假 up：当没松
                held = {**held, key: False}
                up_t = {**up_t, key: t}
            if down:
                down_t = {**down_t, key: t}
            self._held, self._arm, self._down_t, self._up_t = \
                held, arm, down_t, up_t
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
        距首踩松开 ≤double_window；长踩=唯一按压持续满阈值（可能未松）；
        单踩=松脚后 double_window 平静过期。"""
        ev = self.events
        key = ev[0][0]
        downs = [t for k, d, t in ev if d and k == key]
        ups = [t for k, d, t in ev if not d and k == key]
        if not downs:
            return None
        if len(downs) >= 2:
            # 干净交替流：ups[0] 必为首踩松开、downs[1] 必为第二踩。
            # 长踩满阈即发、抢先一切（引擎同序）：首按压已满长踩阈值时
            # 补踩折算为新序列，不得判双踩——否则学到的双踩运行期不可达
            if ups[0] - downs[0] >= self.long_press:
                return (key, "long")
            gap = downs[1] - ups[0]
            return (key, "double" if gap <= self.double_window else "single")
        # 单按压：长踩判定用「按压持续时长」——已松开也成立（_tick 300ms
        # 轮询可能整个错过「按住中」判定窗，松脚后时长不灭，审计实测
        # 「踩住约半秒」提示在 hold=0.5~0.6s 段误学 single 达 50-83%）
        dur = (now if len(ups) < len(downs) else ups[0]) - downs[0]
        if dur >= self.long_press:
            return (key, "long")
        if len(ups) < len(downs):
            return None                       # 还按着、未满阈值：继续等
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
REHOOK_UNSTICK_SEC = 10.0  # 运行期重装挂死看门狗（SetWindowsHookExW 已知
                           #   偶发不返回；常量化供测试缩时）


class PedalWindow(tk.Toplevel):
    """设备选择 + 六行功能 × [学习][清除]，底部监听状态。绑定存 config.json
    的 pedal 段。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.learner = None
        self.title("踩钉控制")
        self.geometry(dpi.scale(self, 560, 400))
        pad = dpi.scale(self, 12)   # pack 边距是裸像素，高 DPI 下须换算
        tk.Label(self, text="选择输入设备后，只有该设备的按键能触发动作；"
                   "勾选拦截可防止其按键漏给其他软件（每键首踩放行一次以"
                   "确认来源）。").pack(
            anchor="w", padx=pad, pady=(pad, 4))
        # 设备行与设置页严格同款（对照 SettingsWindow 的 menu_row 与
        # row(browse=True)：宽 15 标签 + 下拉填充 + 右侧 width=6 小按钮）；
        # 拦截勾选独立成行，同设置页复选框行（anchor="w", pady=1）
        devrow = tk.Frame(self)
        devrow.pack(fill="x", padx=pad, pady=2)
        tk.Label(devrow, text="输入设备", width=15,
                 anchor="w").pack(side="left")
        self.dev_var = tk.StringVar(value="（未选择）")
        self.dev_opt = tk.OptionMenu(devrow, self.dev_var, "（未选择）")
        self.dev_opt.config(anchor="w", direction="below")
        self.dev_opt.pack(side="left", fill="x", expand=True)
        tk.Button(devrow, text="刷新", width=6,
                  command=self._update_device_menu).pack(
            side="left", padx=(6, 0))
        self.intercept_var = tk.BooleanVar(value=self.app.pedal_intercept)
        tk.Checkbutton(self, text="拦截踏板按键",
                       variable=self.intercept_var,
                       command=self._toggle_intercept).pack(
            anchor="w", padx=pad, pady=1)
        grid = tk.Frame(self)
        grid.pack(fill="both", expand=True, padx=pad)
        for c, t in enumerate(("功能", "绑定", "操作")):
            tk.Label(grid, text=t, anchor="w", fg=dpi.MUT).grid(
                row=0, column=c, sticky="w", pady=(0, 2),
                padx=(8, 0) if c == 1 else (0, 0))  # 对齐数据列左缩进
        self._bind_lbl = {}
        for r, (action, name) in enumerate(ACTIONS, start=1):
            tk.Label(grid, text=name, anchor="w").grid(
                row=r, column=0, sticky="w", pady=2)
            lbl = tk.Label(grid, text="未设置", anchor="w", fg=dpi.MUT)
            lbl.grid(row=r, column=1, sticky="we", padx=(8, 8), pady=2)
            grid.columnconfigure(1, weight=1)
            cell = tk.Frame(grid)
            cell.grid(row=r, column=2, sticky="w", pady=2)
            tk.Button(cell, text="学习", width=8,
                      command=lambda a=action: self._learn(a)).pack(
                side="left", padx=2)
            tk.Button(cell, text="清除", width=8,
                      command=lambda a=action: self._clear(a)).pack(
                side="left", padx=2)
            self._bind_lbl[action] = lbl
        self.status = tk.Label(self, text="…", anchor="w", fg=dpi.MUT)
        self.status.pack(fill="x", padx=pad, pady=(6, dpi.scale(self, 8)))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.attributes("-topmost", True)   # 与主窗一致保持可见
        dpi.darkify(self)
        dpi.flatten(self)       # 表单页文字直接坐窗口底色，去掉面板色斑
        # 下拉不在 darkify 覆盖范围——设置页同款套色（SettingsWindow 尾部
        # 对 _menus 的处理逐字一致，darkify 之后套才不会被盖掉）
        self.dev_opt.config(
            bg=dpi.PANEL, fg=dpi.FG, activebackground="#33363d",
            activeforeground=dpi.FG, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=dpi.BORDER,
            highlightcolor=dpi.C_OK, padx=8, pady=3)
        self.dev_opt["menu"].config(
            bg=dpi.FIELD, fg=dpi.FG, activebackground=dpi.SELECT,
            activeforeground=dpi.FG)
        # 尺寸适配：最小=内容自然需求；初始不低于规划值与需求值
        self.update_idletasks()
        w = max(dpi.scale(self, 560), self.winfo_reqwidth())
        h = max(dpi.scale(self, 400), self.winfo_reqheight())
        self.geometry("%dx%d" % (w, h))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.after(300, self._tick)
        self._refresh()
        self._update_device_menu()

    def _update_device_menu(self):
        """重建下拉项并刷新显示（打开窗口/点刷新/换设备后调用）。项用
        radiobutton（绑定 dev_var）——选中项带圆点标记，与设置页下拉同款。"""
        menu = self.dev_opt["menu"]
        menu.delete(0, "end")
        menu.add_radiobutton(label="（不区分来源，仅 MIDI 踩钉）",
                             variable=self.dev_var,
                             command=lambda: self._set_device(""))
        for hint, disp in pedal_list_devices_safe():
            menu.add_radiobutton(label=disp, variable=self.dev_var,
                                 command=lambda h=hint: self._set_device(h))
        hint = self.app.pedal_device_hint
        if hint:
            online = self.app.pedal is not None and self.app.pedal.device_online()
            self.dev_var.set("%s（%s）" % (device_display(hint),
                                          "在线" if online else "离线"))
            # 唯一保留的状态色：设备离线=警示（设置页下拉无此状态概念）
            self.dev_opt.config(fg=dpi.FG if online else dpi.C_WARN)
        else:
            self.dev_var.set("（未选择）")
            self.dev_opt.config(fg=dpi.FG)

    def _set_device(self, hint):
        self.app.pedal_device_hint = hint
        self._save()
        p = self.app.pedal
        if p is not None:
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_timing)
            p.try_open()
        self._update_device_menu()
        self._set_status("已选输入设备：%s" % (device_display(hint)
                                          if hint else "未选择"))

    def _toggle_intercept(self):
        self.app.pedal_intercept = bool(self.intercept_var.get())
        self._save()
        p = self.app.pedal
        if p is not None:
            # 必须走 apply：只改 bridge.block 会被重连 tick 的 sync_bridge
            # 用陈旧 listener.intercept 悄悄改回去（实测回滚）
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_timing)
            p.try_open()

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
                self._bind_lbl[action].config(text="＋".join(texts), fg=dpi.FG)
            else:
                self._bind_lbl[action].config(text="未设置", fg=dpi.MUT)

    def _set_status(self, text, color=dpi.MUT):
        self.status.config(text=text, fg=color)

    def _save(self):
        import setlist_gui as sg       # 延迟导入避免循环
        cfg = sg._load_config()
        cfg["pedal"] = {"deviceHint": self.app.pedal_hint,
                        "bindings": self.app.pedal_binds,
                        "hidBindings": self.app.pedal_hid,
                        "hidDeviceHint": self.app.pedal_device_hint,
                        "intercept": self.app.pedal_intercept,
                        "gestures": self.app.pedal_gestures,
                        "longPress": self.app.pedal_timing[0],
                        "doubleWindow": self.app.pedal_timing[1]}
        sg._save_config(cfg)

    def _learn(self, action):
        self._cancel_learn("已取消上一次学习")
        p = self.app.pedal
        if p is not None:
            p.mute()                    # 学习期静音：不误触发旧绑定，让出 MIDI 口
        self.learner = (action,
                        Learner(self.app.pedal_hint,
                                self.app.pedal_device_hint,
                                p.bridge if p is not None else None,
                                timing=self.app.pedal_timing),
                        time.time() + LEARN_TIMEOUT)
        where = ("设备「%s」的按键或 MIDI"
                 % device_display(self.app.pedal_device_hint)
                 if self.app.pedal_device_hint else "MIDI CC")
        self._set_status("学习「%s」：单击 / 快踩两下（双踩）/ 踩住约半秒"
                         "（长踩）——%d 秒内，等待%s"
                         % (dict(ACTIONS)[action], LEARN_TIMEOUT, where),
                         dpi.C_ERR)

    def _cancel_learn(self, msg):
        if self.learner is None:
            return
        self.learner[1].close()
        self.learner = None
        p = self.app.pedal
        if p is not None:
            p.unmute()
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept, self.app.pedal_gestures,
                    self.app.pedal_timing)
            p.try_open()
        if msg:
            self._set_status(msg)

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
                    self.app.pedal_timing)
            p.try_open()
        self._refresh()
        self._set_status("已清除「%s」" % dict(ACTIONS)[action])

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
                    p.unmute()
                    p.apply(self.app.pedal_hint, self.app.pedal_binds,
                            self.app.pedal_hid, self.app.pedal_device_hint,
                            self.app.pedal_intercept,
                            self.app.pedal_gestures, self.app.pedal_timing)
                    p.try_open()
                self._refresh()
                self._set_status("「%s」已绑定 %s"
                                 % (dict(ACTIONS)[action], label), dpi.C_OK)
            elif time.time() > deadline:
                if self.app.pedal_device_hint:
                    self._cancel_learn("学习超时：所选设备没发按键（踏板连接/模式请检查）")
                else:
                    self._cancel_learn("学习超时：没等到 MIDI CC（未选择输入设备）")
            else:
                self._set_status("学习「%s」中（剩 %.0f 秒），请踩一下踩钉"
                                 % (dict(ACTIONS)[action],
                                    deadline - time.time()), dpi.C_ERR)
        else:
            p = self.app.pedal
            if p is None:
                self._set_status("服务启动中…")
            elif not self.app.pedal_binds and not self.app.pedal_hid:
                self._set_status("还没有任何绑定：点任一「学习」开始", dpi.MUT)
            else:
                parts = ([p.name] if p.connected else []) \
                    + (["键盘按键"] if p.hid_active else [])
                conf = []
                if p.device_hint:
                    online = p.device_online()
                    parts.append("%s（%s，拦截%s）" % (
                        device_display(p.device_hint),
                        "在线" if online else "离线",
                        "开" if self.app.pedal_intercept else "关"))
                    if self.app.pedal_intercept:
                        # 键盘也发过的键物理上拦不住（LL 钩子无设备字段），
                        # 拦截对这些键静默失效——如实示警，不装没事
                        for vk in set(self.app.pedal_hid.values()):
                            ev = p.bridge._evidence.get(vk)
                            if ev and ev[1]:
                                conf.append(hid_name(vk))
                if conf:
                    parts.append("注意：键盘也发过 %s，这些键拦截不生效"
                                 % "、".join(sorted(set(conf))))
                if parts:
                    self._set_status("监听中：%s" % "，".join(parts),
                                     dpi.C_WARN if conf else dpi.C_OK)
                elif p.hint:
                    self._set_status("未找到踩钉口「%s」，每 10 秒自动重试"
                                     % p.hint, dpi.C_WARN)
                else:
                    self._set_status("还没有任何绑定：点任一「学习」开始",
                                     dpi.MUT)

    def _close(self):
        self._cancel_learn("")
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
    assert learning_candidates() == [(3, "Rubix USB"), (4, "踩钉")]
    # HID 按键通道
    assert load_hid({}) == {}
    assert load_hid(
        {"pedal": {"hidBindings": {"next": 0xB0, "bad": 9}}}) == {"next": 0xB0}
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
    # 设备桥：来源判定 + 学习捕获 + 拦截决策
    hits = []
    br = RawInputBridge(hits.append)
    br.configure(binds={0x0D: "play"}, device_hint="9DF17DA3C702", block=True)
    br._feed(0x0D, True, True)                     # 踩钉回车按下 → 触发
    br._feed(0x0D, True, False)                    # 键盘回车 → 只记证据
    assert hits == ["play"] and br._evidence[0x0D] == [True, True]
    assert 0x0D in br.pedal_keys
    assert not br._hook_event(0x0D, True)          # 键盘也发过 → 放行不拦
    br2 = RawInputBridge(hits.append)
    br2.configure(binds={0xB0: "next"}, device_hint="9DF17DA3C702", block=True)
    assert not br2._hook_event(0xB0, True)         # 空证据：来源不明，放行不触发
    assert not br2._hook_event(0xB0, True, True)   # 软件注入：证据再足也放行
    br2._evidence[0xB0] = [True, False]            # 踩钉证实发过（raw 侧积累）
    assert br2._hook_event(0xB0, True)             # 证实过的独占键 → 吞+触发
    assert hits == ["play", "next"]
    assert br2._hook_event(0xB0, True)             # 按住重复：吞不触发
    assert br2._hook_event(0xB0, False)            # up 一并吞
    assert hits == ["play", "next"]
    br2.learning = True
    assert not br2._hook_event(0xB0, True)         # 学习期不拦截
    br2.learning = False
    br2.configure(device_hint="OTHER")
    assert br2._evidence == {} and not br2.pedal_keys   # 换设备：证据全清
    assert not br2._hook_event(0xB0, True)         # 旧设备的键不再拦
    br2.stop()                                     # 未 start 时 stop 安全
    cap = []
    br3 = RawInputBridge(None)
    br3.configure(device_hint="9DF17DA3C702")
    br3.begin_capture(cap.append)
    assert br3.learning and br3.capture is not None
    br3._feed(0x0D, True, True)
    br3._feed(0x0D, False, True)                   # 手势学习要完整踩法
    assert [(v, d) for v, d, _t in cap] == [(0x0D, True), (0x0D, False)]
    br3.end_capture()
    assert br3.capture is None
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
    assert fired2 == []                            # 时序键不再按下即发
    eng.feed(("hid", 0xB0), False, 100.1)
    assert fired2 == []                            # 等双踩窗判定
    eng.feed(("hid", 0xB0), True, 100.3)           # 窗内第二踩 → 双踩
    assert fired2 == ["next"]
    eng.feed(("hid", 0xB0), False, 100.4)          # 已消费，松开归位
    eng.feed(("hid", 0xB0), True, 106.0)           # 新序列
    eng.feed(("hid", 0xB0), False, 106.1)          # 松脚，等单踩窗到期
    assert fired2 == ["next"] and len(spawned) == 2
    assert abs(spawned[0].delay - DOUBLE_WINDOW) < 1e-9
    assert spawned[0].dead                    # 消费双踩时窗定时器已显式取消
    spawned[0].cb()                           # 即便误触发：token 过期静默
    assert fired2 == ["next"]
    assert not spawned[1].dead
    spawned[1].cb()                           # 第二序列窗到期 → 单踩
    assert fired2 == ["next", "play"]
    # 单+长组合：松脚即单踩（无双踩窗可等）
    fired3 = []
    eng2 = GestureEngine(fired3.append, spawn_timer=lambda d, cb: None)
    eng2.configure({(("midi", 4), "single"): "play",
                    (("midi", 4), "long"): "panic"})
    eng2.feed(("midi", 4), True, 200.0)
    eng2.feed(("midi", 4), False, 200.12)
    assert fired3 == ["play"]                      # 松脚即触发，不等窗
    eng2.feed(("midi", 4), True, 201.0)
    eng2._on_timer(("midi", 4), eng2._state[("midi", 4)][2], "long")
    assert fired3 == ["play", "panic"]             # 按住满阈值即长踩
    eng2.feed(("midi", 4), False, 201.5)           # 消费完毕归位
    eng2.feed(("midi", 4), True, 202.0)            # 新序列不受影响
    assert fired3 == ["play", "panic"]
    # 示范式学习分类
    ln = object.__new__(Learner)                   # 绕过 __init__ 不开真端口
    ln.cc = None
    ln.events = []
    ln.long_press, ln.double_window = LONG_PRESS, DOUBLE_WINDOW
    ln._clock = lambda: 300.2                      # 假钟
    ln._raw_capture(0xB0, True, 300.0)
    assert ln.result() is None                     # 还按着、未满长踩阈值
    ln._raw_capture(0xB0, False, 300.12)
    assert ln.result() is None                     # 等双踩窗平静过期
    ln._raw_capture(0xB0, True, 300.3)             # 窗内第二踩 → 双踩
    assert ln.result() == ("hid", "下一曲", 0xB0, "double")
    ln.cc = None
    ln.events = [(("hid", 0x0D), True, 400.0)]      # 只按住未松
    ln._clock = lambda: 400.55
    assert ln.result() == ("hid", "回车", 0x0D, "long")   # 按住 0.5s=长踩
    # 设备桥真机冒烟：线程内注册成功、停止干净（仅本机诊断运行）
    brl = RawInputBridge(lambda a: None)
    brl.configure(device_hint="9DF17DA3C702", block=True)
    brl.start()
    time.sleep(0.6)
    print("设备桥冒烟：running=%s raw_ok=%s" % (brl.running, brl.raw_ok))
    assert brl.running and brl.raw_ok
    brl.stop()
    assert not brl.running
    print("pedal self-check OK")
