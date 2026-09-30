# -*- coding: utf-8 -*-
"""踩钉双通道快捷键：MIDI 踩钉（USB 直连或经声卡 MIDI IN，winmm 输入设备）
发 CC；蓝牙键盘型踩钉（如 M-Vave CUBE TURNER PRO，蓝牙 HID 发多媒体键）
走 Raw Input 设备桥——逐事件识别输入来源，只有所选设备能触发动作，且其按键
可被系统级拦截。都是按下沿+去抖触发，踩钉无需改任何设置。绑定来自「学习」：
双通道同时监听（MIDI 输入口排除 loopMIDI 虚拟口与两台琴；按键只录所选设备），
先到先得，写 config.json 的 pedal 段（bindings=MIDI CC，hidBindings=虚拟键码，
hidDeviceHint=所选设备身份，intercept=拦截开关）。热插拔：MIDI 口未连接时由
GUI 轮询 try_open() 重连；蓝牙重连后句柄变化无需刷新（raw 侧逐事件解析
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
        self.capture = None      # 学习回调 fn(vk)
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
        self._rehook_tid = None  # 自愈线程 id（stop 时投 WM_QUIT 收尾）
        self.raw_ok = None           # 线程启动后回填：INPUTSINK 是否注册成功
        self._installed = threading.Event()   # LL 钩子安装完成信号
        self._pumping = threading.Event()     # 线程消息队列已建成（WM_QUIT 可投）
        self._live = False       # 钩子允许工作（迟到线程装上的钩子不得读状态）
        self._watchdog = None
        self._attempt = 0
        self._gen = 0            # 每次起线程自增：卡死后迟到的旧线程凭它自检退场

    def configure(self, binds=None, device_hint=None, block=None):
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
        if self._rehook_tid is not None:    # 自愈线程的泵同步退场拆钩
            u32.PostThreadMessageW(self._rehook_tid, WM_QUIT, 0, 0)
            self._rehook_tid = None         # 一次投递：防 tid 复用后误投
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
        """raw 事件 → 证据积累；所选设备的按下沿触发动作/学习捕获。"""
        ev = self._evidence.setdefault(vk, [False, False])
        ev[0 if is_pedal else 1] = True
        if not is_pedal:
            return
        if down:
            self.pedal_keys.add(vk)
            if self.learning and self.capture:
                self.capture(vk)
        if hid_fire(self._state, vk, down, time.monotonic()) and down \
                and not self.learning:
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
            self._rehook_tid = k32.GetCurrentThreadId()
            h = u32.SetWindowsHookExW(13, self._href, None, 0)
            if not h:
                self._rehooking = False
                self._report("低级钩子疑似被系统摘除，重装失败"
                             "（15 秒后自动重试）")
                return
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
            # 同款），挂死则 _rehooking 永久 True=自愈单点失效——10 秒无果
            # 放行限频门再试（挂死线程为 daemon，随进程回收）
            if self._rehooking and self._rehook_seq == seq:
                self._rehooking = False
                self._report("钩子重装超时未返回，稍后自动重试")

        t = threading.Timer(10.0, unstick)
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
            if not repeat and hid_fire(self._state, vk, True, time.monotonic()):
                action = self.binds.get(vk)
                if action:
                    self.on_action(action)
            return True
        self._swallowed.discard(vk)
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


def load_binding(cfg):
    """完整 config dict → (设备名提示, {动作: cc 号})。JSON 的 true 是
    int 子类，须一并挡掉（否则绑定成 CC 1）；pedal 段被手改成非 dict 时
    按空段处理（启动路径，类型错不能炸开机）。"""
    p = cfg.get("pedal")
    if not isinstance(p, dict):
        p = {}
    binds = {}
    seen = set()
    b = p.get("bindings")       # 子键同样只认 dict（手改 "junk" 不得炸启动）
    for k, v in (b if isinstance(b, dict) else {}).items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool):
            if v in seen:       # 同码双绑：反转时后者静默覆盖前者，载入期
                continue        #   保首个，杜绝一条绑定静默失效
            seen.add(v)
            binds[k] = v
    return str(p.get("deviceHint") or ""), binds


def load_hid(cfg):
    """完整 config dict → {动作: VK 码}（pedal.hidBindings 段）。"""
    p = cfg.get("pedal")
    hid = p.get("hidBindings") if isinstance(p, dict) else None
    out = {}
    seen = set()
    for k, v in (hid if isinstance(hid, dict) else {}).items():
        if k in dict(ACTIONS) and isinstance(v, int) \
                and not isinstance(v, bool):
            if v in seen:       # 同码双绑去重（同 load_binding）
                continue
            seen.add(v)
            out[k] = v
    return out


def load_device_cfg(cfg):
    """完整 config dict → (所选设备身份子串, 拦截开关)。intercept 只认真
    bool：JSON 字符串 "false" 按 bool() 会变 True（手改配置实测踩过）。"""
    p = cfg.get("pedal")
    if not isinstance(p, dict):
        p = {}
    intercept = p.get("intercept", True)
    return str(p.get("hidDeviceHint") or ""), \
        intercept if isinstance(intercept, bool) else True


class PedalListener:
    """MIDI 口按 deviceHint 常驻监听（CC 上升沿）＋设备桥按所选设备监听
    （HID 按下沿，来源判定+定向拦截）→ on_action(动作名)。MIDI 回调在
    winmm 线程、按键在设备桥线程触发，GUI 侧自行转投主线程。"""

    def __init__(self, on_action, on_event=None):
        self.on_action = on_action
        self.hint = ""
        self.binds = {}
        self.hid_binds = {}        # 动作 → VK
        self.device_hint = ""      # 所选设备身份子串
        self.intercept = True      # 拦截开关
        self.port = None
        self.name = ""
        self._state = {}
        self._lock = threading.Lock()
        self._muted = False        # 学习期静音：停触发、让出 MIDI 口
        self.bridge = RawInputBridge(on_action, on_event)

    def apply(self, hint, binds, hid_binds=None, device_hint=None,
              intercept=None):
        with self._lock:
            self.hint = hint
            # binds 是 {动作: CC}；运行期按 CC 号查动作，须反转
            self.binds = {cc: a for a, cc in binds.items()}
            if hid_binds is not None:
                self.hid_binds = dict(hid_binds)
            if device_hint is not None:
                self.device_hint = device_hint
            if intercept is not None:
                self.intercept = bool(intercept)
        self.sync_bridge()
        self.close()

    def sync_bridge(self):
        # hid_binds 是 {动作: VK}；桥内按 VK 触发，须反转
        self.bridge.configure(binds={vk: a for a, vk in self.hid_binds.items()},
                              device_hint=self.device_hint,
                              block=self.intercept)

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
        self.close()                     #   学后第一脚被残留吞单吃掉

    def unmute(self):
        with self._lock:
            self._muted = False
        self.bridge.learning = False
        self.bridge.reset_hold_state()

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
        # 单调钟：墙钟回拨会让去抖窗变负数，踏板整体冻结
        if fire(self._state, d1, d2, time.monotonic()):
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
        self.bridge.stop()


class Learner:
    """学习：MIDI 输入口与所选设备 raw 流双通道监听，先到先得。result() →
    ("midi", 设备名, CC 号) 或 ("hid", 键名, VK 码)。未选设备时只学 MIDI。"""

    _cc_lock = threading.Lock()    # winmm 线程与桥线程并发写 cc（类级：测试
                                   # 用 object.__new__ 绕过 __init__ 也得有锁）

    def __init__(self, hint="", device_hint="", bridge=None):
        self.cc = None                 # (通道, 来源名, 码)
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
                with self._cc_lock:
                    if self.cc is None:
                        self.cc = ("midi", name, d1)
        return feed

    def _raw_capture(self, vk):
        with self._cc_lock:
            if self.cc is None:
                self.cc = ("hid", hid_name(vk), vk)

    def result(self):
        return self.cc

    def close(self):
        if self._bridge is not None:
            self._bridge.end_capture()
            self._bridge = None
        for p in self.ports:
            try:
                p.close()
            except OSError:
                pass
        self.ports = []


LEARN_TIMEOUT = 8.0        # 学习等待踩踏的时限（秒）


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
                    self.app.pedal_intercept)
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
                    self.app.pedal_intercept)
            p.try_open()

    def _refresh(self):
        for action, _name in ACTIONS:
            cc = self.app.pedal_binds.get(action)
            vk = self.app.pedal_hid.get(action)
            texts = []
            if cc is not None:
                texts.append("CC %d" % cc)
            if vk is not None:
                texts.append("按键 %s" % hid_name(vk))
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
                        "intercept": self.app.pedal_intercept}
        sg._save_config(cfg)

    def _learn(self, action):
        self._cancel_learn("已取消上一次学习")
        p = self.app.pedal
        if p is not None:
            p.mute()                    # 学习期静音：不误触发旧绑定，让出 MIDI 口
        self.learner = (action,
                        Learner(self.app.pedal_hint,
                                self.app.pedal_device_hint,
                                p.bridge if p is not None else None),
                        time.time() + LEARN_TIMEOUT)
        where = ("设备「%s」的按键或 MIDI"
                 % device_display(self.app.pedal_device_hint)
                 if self.app.pedal_device_hint else "MIDI CC")
        self._set_status("学习「%s」：踩/按一下（%d 秒内，等待%s）"
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
                    self.app.pedal_intercept)
            p.try_open()
        if msg:
            self._set_status(msg)

    def _clear(self, action):
        cc = self.app.pedal_binds.pop(action, None)
        vk = self.app.pedal_hid.pop(action, None)
        if cc is None and vk is None:
            return
        self._save()
        p = self.app.pedal
        if p is not None:
            p.apply(self.app.pedal_hint, self.app.pedal_binds,
                    self.app.pedal_hid, self.app.pedal_device_hint,
                    self.app.pedal_intercept)
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
                kind, dev, code = res
                learner.close()
                self.learner = None
                if kind == "hid":
                    moved = [a for a, v in self.app.pedal_hid.items()
                             if v == code and a != action]
                    for a in moved:          # 同码改绑：从旧动作摘下，防
                        del self.app.pedal_hid[a]   #   一条绑定静默失效
                    self.app.pedal_hid[action] = code
                    label = "按键「%s」" % dev
                else:
                    self.app.pedal_hint = dev
                    moved = [a for a, v in self.app.pedal_binds.items()
                             if v == code and a != action]
                    for a in moved:
                        del self.app.pedal_binds[a]
                    self.app.pedal_binds[action] = code
                    label = "%s 的 CC %d" % (dev, code)
                if moved:
                    label += "（自「%s」改绑）" % "、".join(
                        dict(ACTIONS)[a] for a in moved)
                self._save()
                p = self.app.pedal
                if p is not None:
                    p.unmute()
                    p.apply(self.app.pedal_hint, self.app.pedal_binds,
                            self.app.pedal_hid, self.app.pedal_device_hint,
                            self.app.pedal_intercept)
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
    assert cap == [0x0D]                           # 学习捕获
    br3.end_capture()
    assert br3.capture is None
    ln = object.__new__(Learner)                   # 绕过 __init__ 不开真端口
    ln.cc = None
    ln._raw_capture(0xB0)
    assert ln.result() == ("hid", "下一曲", 0xB0)
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
