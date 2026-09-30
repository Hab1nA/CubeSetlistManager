# -*- coding: utf-8 -*-
"""踩钉设备桥端到端模拟输入 harness（用户离线时的真机验证替代）。

    python tests/pedal_sim.py

设计：SendInput 注入媒体键（下一曲 0xB0 / 上一曲 0xB1 / 停止 0xB2——无媒体
程序在场时系统级惰性，不碰字母/空格/回车，零前台风险），观测三路真实 OS
管道，三条证据链互相独立：
  ① 观察者 WH_KEYBOARD_LL（先装，位于钩子链尾）：被测桥若拦截成功，
     观察者收不到该事件；放行则收得到（含 LLKHF_INJECTED 注入标记）。
  ② 独立 Raw Input 探针（不复用生产代码）：判定本机 INPUTSINK 管道死活，
     环境死了的用例标 SKIP-ENV，不冒充通过也不冒充失败。
  ③ 生产 RawInputBridge 真线程真窗口真注册：注入事件 hDevice=NULL，归属
     判定天然 False——「未选设备/非踏板不触发」被真机负验证；归属匹配段
     （hDevice→路径→比对）需要真实设备句柄，由 SimBridge 桩把指定 VK 的
     is_pedal 伪造成 True 模拟，其余全真。

只注入媒体键；preflight 发现 CSM/媒体程序在场即中止（拦截失效也不误伤）。
输出逐用例 PASS/FAIL/SKIP-ENV 与环境判定 JSON，退出码 0=全过。
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pedal                                        # noqa: E402

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

VK_NEXT = 0xB0          # 下一曲
VK_PREV = 0xB1          # 上一曲
VK_STOP = 0xB2          # 媒体停止（观察者标定用，无绑定不拦截）
LLKHF_INJECTED = 0x10
WM_QUIT = 0x0012
_HOOK_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                wintypes.WPARAM, ctypes.c_ssize_t)
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
k32.GetCurrentThreadId.restype = wintypes.DWORD

_CSB_TITLES = ("Cube Setlist Manager", "Cube Automator", "踩钉控制")
_MEDIA_BANNED = ("cubase", "studioone", "Cubase", "Studio One",
                 "spotify", "Spotify", "QQMusic", "Netease")


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]   # mi 定 40 字节


class _INPUT(ctypes.Structure):
    class U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]
    _fields_ = [("type", wintypes.DWORD), ("U", U)]


class Observer:
    """独立观察者 LL 钩子：全系统键盘事件流水（vk, down, injected）。"""

    def __init__(self):
        self.events = []                 # (vk, down, injected, monotonic)
        self.events_lock = threading.Lock()
        self.installed = threading.Event()
        self.tid = None
        self._proc = _HOOK_PROC(self._cb)
        self._hook = None
        self._thread = threading.Thread(target=self._run,
                                        name="sim-observer", daemon=True)
        self._thread.start()
        self.installed.wait(5)

    def _cb(self, ncode, wp, lp):
        try:
            if ncode == 0:
                down = wp in (0x0100, 0x0104)
                up = wp in (0x0101, 0x0105)
                if down or up:
                    st = ctypes.cast(lp, ctypes.POINTER(
                        _KBDLLHOOKSTRUCT)).contents
                    with self.events_lock:
                        self.events.append((st.vkCode, down,
                                            bool(st.flags & LLKHF_INJECTED),
                                            time.monotonic()))
        except Exception:
            pass
        return u32.CallNextHookEx(None, ncode, wp, lp)

    def _run(self):
        self.tid = k32.GetCurrentThreadId()
        self._hook = u32.SetWindowsHookExW(13, self._proc, None, 0)
        self.installed.set()
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        if self._hook:
            u32.UnhookWindowsHookEx(self._hook)

    def seen(self, vk):
        with self.events_lock:
            return [e for e in self.events if e[0] == vk]

    def clear(self):
        with self.events_lock:
            self.events = []

    def stop(self):
        if self.tid:
            u32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
        self._thread.join(timeout=2)


def send_key(vk, up=False):
    """前台注入单个键（媒体键；无媒体程序时系统级惰性）。"""
    inp = _INPUT()
    inp.type = 1                                    # INPUT_KEYBOARD
    inp.U.ki.wVk = vk
    inp.U.ki.dwFlags = 2 if up else 0               # KEYEVENTF_KEYUP
    n = u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    if n != 1:
        raise OSError("SendInput 失败 err=%d" % k32.GetLastError())


def press(vk, gap=0.03):
    send_key(vk)
    time.sleep(gap)
    send_key(vk, up=True)


def _csm_windows():
    """按窗口标题找在跑的 CSM 程序：程序必有主窗，比进程名可靠
    （开发态跑在 python.exe 下，tasklist 看不出是它）。"""
    titles = []
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)
    proc = _ENUMPROC(lambda h, l: (titles.append(_title(h)) or True))
    u32.EnumWindows(proc, 0)
    return [t for t in titles
            if any(b.lower() in t.lower() for b in _CSB_TITLES)]


def _title(hwnd):
    n = u32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def preflight():
    """CSM/媒体程序在场即拒绝注入：拦截一旦失效也不误伤真程序。"""
    out = subprocess.run(["tasklist"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    bad = [ln.split()[0] for ln in out.splitlines()
           if any(b.lower() in ln.lower() for b in _MEDIA_BANNED)]
    bad += ["窗口《%s》" % t for t in _csm_windows()]
    if bad:
        print("PREFLIGHT-ABORT: 相关程序在场 %s，拒绝注入" % bad)
        return False
    return True


class _MiniSink:
    """独立 Raw Input 探针（不用 pedal.py 任何代码）：INPUTSINK 注册 + 消息泵，
    统计收到的键盘 WM_INPUT 数——本机管道死活的独立证人。"""

    def __init__(self):
        self.n = 0
        self.ready = threading.Event()
        self.tid = None
        self._thread = threading.Thread(target=self._run,
                                        name="sim-rawsink", daemon=True)
        self._thread.start()
        self.ready.wait(5)

    def _run(self):
        from ctypes import wintypes as _w
        class HDR(ctypes.Structure):
            _fields_ = [("dwType", _w.DWORD), ("dwSize", _w.DWORD),
                        ("hDevice", _w.HANDLE), ("wParam", _w.WPARAM)]
        class KB(ctypes.Structure):
            _fields_ = [("MakeCode", _w.USHORT), ("Flags", _w.USHORT),
                        ("Reserved", _w.USHORT), ("VKey", _w.USHORT),
                        ("Message", _w.UINT), ("ExtraInformation", _w.ULONG)]
        class DEV(ctypes.Structure):
            _fields_ = [("usUsagePage", _w.USHORT), ("usUsage", _w.USHORT),
                        ("dwFlags", _w.DWORD), ("hwndTarget", _w.HWND)]
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, _w.HWND, ctypes.c_uint,
                                     _w.WPARAM, ctypes.c_ssize_t)
        class WC(ctypes.Structure):
            _fields_ = [("style", _w.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", _w.HINSTANCE), ("hIcon", _w.HANDLE),
                        ("hCursor", _w.HANDLE), ("hbrBackground", _w.HANDLE),
                        ("lpszMenuName", _w.LPCWSTR),
                        ("lpszClassName", _w.LPCWSTR)]
        self.tid = k32.GetCurrentThreadId()
        ref = WNDPROC(self._wnd)
        wc = WC()
        wc.lpfnWndProc = ref
        wc.lpszClassName = "SimRawSink"
        wc.hInstance = k32.GetModuleHandleW(None)
        u32.RegisterClassW(ctypes.byref(wc))
        hwnd = u32.CreateWindowExW(0, "SimRawSink", "x", 0, 0, 0, 0, 0,
                                   None, None, wc.hInstance, None)
        dev = DEV(1, 6, 0x00000100, hwnd)                # INPUTSINK
        self.ok = bool(u32.RegisterRawInputDevices(
            ctypes.byref(dev), 1, ctypes.sizeof(DEV)))
        self.ready.set()
        msg = _w.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        u32.DestroyWindow(hwnd)

    def _wnd(self, h, m, w, l):
        if m == 0x00FF:                                 # WM_INPUT
            self.n += 1
        return u32.DefWindowProcW(h, m, w, l)

    def stop(self):
        if self.tid:
            u32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
        self._thread.join(timeout=2)


class SimBridge(pedal.RawInputBridge):
    """生产桥 + 模拟桩：
    - raw 侧 spoof：指定 VK 伪造成所选设备发来的（模拟 hDevice→路径→
      匹配段；那段需要真实蓝牙设备句柄，驱动级注入不做）；
    - hook 侧 sim_hw：把注入当硬件事件（测拦截机制本身）。生产语义里
      软件注入永远放行——该语义本身由 T4a/T4b 用纯生产桥单独验证。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.spoof = frozenset()
        self.sim_hw = False
        self.raw_events = []

    def _feed(self, vk, down, is_pedal):
        self.raw_events.append((vk, down, is_pedal, time.monotonic()))
        if vk in self.spoof and not is_pedal:
            is_pedal = True
        return pedal.RawInputBridge._feed(self, vk, down, is_pedal)

    def _hook_event(self, vk, down, injected=False):
        if injected and self.sim_hw:
            injected = False
        return pedal.RawInputBridge._hook_event(self, vk, down, injected)


class Case:
    def __init__(self):
        self.results = []

    def run(self, name, ok, detail=""):
        verdict = "PASS" if ok else "FAIL"
        self.results.append((name, verdict, detail))
        print("%-8s %-38s %s" % (verdict, name, detail))
        return ok

    def skip(self, name, why):
        self.results.append((name, "SKIP-ENV", why))
        print("%-8s %-38s %s" % ("SKIP-ENV", name, why))


def wait_running(br, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if br.running and br.raw_ok:
            return True
        time.sleep(0.05)
    return br.running and bool(br.raw_ok)


def main():
    if not preflight():
        return 2
    case = Case()
    obs = Observer()
    if not case.run("观察者钩子安装", obs.installed.is_set() and obs._hook):
        return 2

    # ---- 环境自分类（独立证人） ----
    env_hook = env_raw = None
    sink = _MiniSink()
    obs.clear()
    press(VK_STOP)
    time.sleep(0.4)
    got = obs.seen(VK_STOP)
    env_hook = any(e[2] for e in got)               # 注入标记必须在场
    case.run("ENV 注入→LL钩子", env_hook,
             "%d 个事件" % len(got) if env_hook else "钩子管道死（历史已知降级）")
    time.sleep(0.3)
    base = sink.n
    press(VK_NEXT)
    time.sleep(0.5)
    env_raw = sink.n > base
    case.run("ENV 注入→RawInput", env_raw,
             "WM_INPUT %d→%d" % (base, sink.n) if env_raw else "raw 管道死")
    sink.stop()

    # ---- T1 生产桥 raw 收到注入键；设备过滤负验证（hDevice=NULL≠踏板） ----
    if env_raw:
        br = SimBridge(lambda a: fired.append(a))
        fired = []
        br.configure(binds={VK_NEXT: "next"}, device_hint="SIMFACE", block=False)
        br.start()
        ok = wait_running(br)
        case.run("T1 桥线程启动+注册", ok,
                 "running=%s raw_ok=%s" % (br.running, br.raw_ok))
        obs.clear()
        press(VK_NEXT)
        time.sleep(0.4)
        raws = [e for e in br.raw_events if e[0] == VK_NEXT]
        case.run("T1 raw 收到注入键", len(raws) >= 2,
                 "down/up=%d" % len(raws))
        case.run("T1 归属过滤：注入不冒充踏板",
                 bool(raws) and not any(e[2] for e in raws)
                 and fired == [],
                 "动作触发=%d（应为 0）" % len(fired))
        case.run("T1 钩子放行（block=off）", len(obs.seen(VK_NEXT)) >= 2)
        br.stop()
        time.sleep(0.2)
        case.run("T1 stop 干净", not br.running)
    else:
        case.skip("T1 raw 收到注入键", "raw 管道死")
        case.skip("T1 归属过滤", "raw 管道死")

    # ---- T2 归属桩 + 触发全链（真 raw 管道 + 桩归属 + 去抖） ----
    if env_raw:
        fired = []
        br = SimBridge(lambda a: fired.append(a))
        br.configure(binds={VK_NEXT: "next", VK_PREV: "prev"},
                     device_hint="SIMFACE", block=False)
        br.spoof = frozenset((VK_NEXT, VK_PREV))
        br.start()
        wait_running(br)
        press(VK_NEXT)
        time.sleep(0.4)
        case.run("T2 单踩单触发", fired == ["next"], str(fired))
        press(VK_NEXT, gap=0.02)
        send_key(VK_NEXT)                           # up 后 80ms 内再踩：去抖窗内
        time.sleep(0.08)
        send_key(VK_NEXT, up=True)
        time.sleep(0.4)
        case.run("T2 去抖窗内连踩只触发一次", fired == ["next", "next"],
                 str(fired))
        time.sleep(0.2)
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T2 独立键独立计数", fired == ["next", "next", "prev"],
                 str(fired))
        br.stop()
    else:
        case.skip("T2 归属+触发全链", "raw 管道死")

    # ---- T3 键盘证据：注入键记为「他源」→ 开拦截后钩子仍放行 ----
    #（block 必须先关：被钩子吞掉的键不产生 WM_INPUT，证据无从积累）
    if env_raw:
        br = SimBridge(lambda a: fired.append(a))
        fired = []
        br.configure(binds={VK_NEXT: "next"}, device_hint="SIMFACE",
                     block=False)
        br.start()
        wait_running(br)
        press(VK_NEXT)                              # 无 spoof：等于键盘发的
        time.sleep(0.4)
        ev = br._evidence.get(VK_NEXT)
        case.run("T3 他源证据入账", bool(ev) and ev[1] is True, str(ev))
        case.run("T3 不触发动作", fired == [], str(fired))
        br.configure(block=True)                    # 开拦截后再验放行决策
        case.run("T3 钩子放行他源键", br._hook_event(VK_NEXT, True) is False)
        case.run("T3 放行不触发", fired == [], str(fired))
        br.stop()
    else:
        case.skip("T3 键盘证据放行", "raw 管道死")

    # ---- T4 拦截：生产行为（空证据/注入放行）+ 机制（模拟硬件事件） ----
    if env_hook:
        # T4a 空证据：来源不明 → 放行不触发。防键盘媒体键/耳机 AVRCP 误触发；
        # 被吞键不产生 WM_INPUT、证据永无积累，所以误触发必须是「无」而非「一次」
        fired = []
        br = pedal.RawInputBridge(fired.append)
        br.configure(binds={VK_PREV: "prev"}, device_hint="SIMFACE", block=True)
        br.start()
        wait_running(br)
        obs.clear()
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T4a 空证据放行不误触发",
                 len(obs.seen(VK_PREV)) >= 2 and fired == [],
                 "放行 %d 触发 %d" % (len(obs.seen(VK_PREV)), len(fired)))
        br.stop()
        time.sleep(0.2)

        # T4b 有证据但事件是软件注入：不是踩钉 → 放行
        br = pedal.RawInputBridge(fired.append)
        br.configure(binds={VK_PREV: "prev"}, device_hint="SIMFACE", block=True)
        br._evidence[VK_PREV] = [True, False]       # 桩：踩钉已证实发过
        br.pedal_keys.add(VK_PREV)
        br.start()
        wait_running(br)
        obs.clear()
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T4b 注入键放行（非踩钉来源）",
                 len(obs.seen(VK_PREV)) >= 2 and fired == [],
                 "放行 %d 触发 %d" % (len(obs.seen(VK_PREV)), len(fired)))
        br.stop()
        time.sleep(0.2)

        # T4c 机制：同状态、事件视作硬件 → 系统级拦截+触发+不牵连无关键
        fired = []
        brs = SimBridge(fired.append)
        brs.configure(binds={VK_PREV: "prev"}, device_hint="SIMFACE", block=True)
        brs._evidence[VK_PREV] = [True, False]
        brs.pedal_keys.add(VK_PREV)
        brs.sim_hw = True
        brs.start()
        wait_running(brs)
        obs.clear()
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T4c 拦截：观察者收不到被吞键",
                 len(obs.seen(VK_PREV)) == 0,
                 "%d 个泄漏事件" % len(obs.seen(VK_PREV)))
        case.run("T4c 拦截路径触发动作", fired == ["prev"], str(fired))
        obs.clear()
        press(VK_STOP)
        time.sleep(0.3)
        case.run("T4c 无关键不牵连", len(obs.seen(VK_STOP)) >= 2)
        brs.configure(block=False)
        obs.clear()
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T4c 关拦截恢复放行", len(obs.seen(VK_PREV)) >= 2,
                 "%d 个事件" % len(obs.seen(VK_PREV)))
        brs.stop()
        time.sleep(0.2)

        # T4f 首踩契约：空证据的踩钉键首踩放行一次但动作照触发（raw 侧），
        # 第二踩起系统级拦截、动作照触发——只漏一次按键、不漏任何动作
        fired = []
        brf = SimBridge(fired.append)
        brf.configure(binds={VK_NEXT: "next"}, device_hint="SIMFACE", block=True)
        brf.spoof = frozenset((VK_NEXT,))
        brf.sim_hw = True
        brf.start()
        wait_running(brf)
        obs.clear()
        press(VK_NEXT)
        time.sleep(0.4)
        ok1 = fired == ["next"] and len(obs.seen(VK_NEXT)) == 2
        press(VK_NEXT)
        time.sleep(0.4)
        case.run("T4f 首踩漏一次动作不丢、再踩拦截照常",
                 ok1 and fired == ["next", "next"]
                 and len(obs.seen(VK_NEXT)) == 2,
                 "触发 %s，首踩放行 %d 事件" % (fired, len(obs.seen(VK_NEXT))))
        brf.stop()
    else:
        case.skip("T4 拦截语义与机制", "钩子管道死（历史降级）")

    # ---- T5 stop 后钩子确实摘除（不再吞键） ----
    if env_hook:
        fired = []
        br = SimBridge(fired.append)
        br.configure(binds={VK_PREV: "prev"}, device_hint="SIMFACE", block=True)
        br._evidence[VK_PREV] = [True, False]
        br.pedal_keys.add(VK_PREV)
        br.sim_hw = True
        br.start()
        wait_running(br)
        br.stop()
        time.sleep(0.2)
        obs.clear()
        press(VK_PREV)
        time.sleep(0.4)
        case.run("T5 stop 后不再拦截",
                 len(obs.seen(VK_PREV)) >= 2 and fired == [],
                 "%d 个事件" % len(obs.seen(VK_PREV)))
    else:
        case.skip("T5 stop 后摘钩", "钩子管道死")

    # ---- T6 PedalListener 装配层（不注入，纯生命周期） ----
    fired = []
    pl = pedal.PedalListener(fired.append)
    pl.apply("无此口", {"next": 4}, {"next": VK_NEXT}, "SIMFACE", True)
    case.run("T6 apply 契约（VK 反转）",
             pl.bridge.binds == {VK_NEXT: "next"}
             and pl.bridge.device_hint == "SIMFACE"
             and pl.bridge.block is True)
    pl.try_open()
    ok = wait_running(pl.bridge)
    case.run("T6 try_open 拉起设备桥", ok and pl.hid_active,
             "running=%s" % pl.bridge.running)
    pl.mute()
    case.run("T6 静音=学习让路",
             pl.muted and pl.bridge.learning and not pl.try_open())
    pl.unmute()
    pl.try_open()
    wait_running(pl.bridge)
    case.run("T6 解除静音恢复", pl.hid_active)
    pl.shutdown()
    time.sleep(0.2)
    case.run("T6 shutdown 停桥", not pl.bridge.running)

    # ---- T7 连踩压力：20 次交替触发零丢失零重复 ----
    if env_raw:
        fired = []
        br = SimBridge(lambda a: fired.append(a))
        br.configure(binds={VK_NEXT: "next", VK_PREV: "prev"},
                     device_hint="SIMFACE", block=False)
        br.spoof = frozenset((VK_NEXT, VK_PREV))
        br.start()
        wait_running(br)
        seq = [VK_NEXT, VK_PREV] * 10
        expect = ["next", "prev"] * 10
        for vk in seq:
            press(vk, gap=0.02)
            time.sleep(0.16)                        # > 去抖 0.15s
        time.sleep(0.6)
        case.run("T7 连踩 20 次精确触发", fired == expect,
                 "触发 %d 次（应 20）" % len(fired))
        case.run("T7 顺序正确", fired == expect,
                 str(fired[:6]) + ("…" if len(fired) > 6 else ""))
        br.stop()
    else:
        case.skip("T7 连踩压力", "raw 管道死")

    obs.stop()
    fails = [r for r in case.results if r[1] == "FAIL"]
    skips = [r for r in case.results if r[1] == "SKIP-ENV"]
    summary = {
        "verdict": "FAIL" if fails else "PASS",
        "env": {"hook": env_hook, "raw": env_raw},
        "pass": sum(1 for r in case.results if r[1] == "PASS"),
        "fail": len(fails), "skip_env": len(skips),
        "details": [{"case": n, "verdict": v, "detail": d}
                    for n, v, d in case.results],
    }
    print("SIM-SUMMARY " + json.dumps(summary, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
