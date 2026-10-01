# -*- coding: utf-8 -*-
"""踩钉设备桥端到端模拟输入 harness（用户离线时的真机验证替代）。

    python tests/pedal_sim.py

设计：SendInput 注入媒体键（下一曲 0xB0 / 上一曲 0xB1 / 停止 0xB2——无媒体
程序在场时系统级惰性，不碰字母/空格/回车，零前台风险），观测真实 OS 管道：

  ① Raw Input 通道：SimBridge（生产 DeviceBridge + 注入归属桩）真线程真
     窗口真注册，验证归属过滤/触发/学习捕获/手势判别；
  ② 热键拦截：桥注册系统热键后注入按键——断言 WM_INPUT 仍并行送达
     （触发不丢）且前台探针窗收不到 WM_KEYDOWN（按键被 win32k 消费，
     不漏给其它程序）；解除注册后反转。

只注入媒体键；preflight 发现媒体程序在场即中止。输出逐用例 PASS/FAIL 与
环境判定 JSON，退出码 0=全过。"""
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
VK_STOP = 0xB2          # 媒体停止（无绑定对照键）
WM_KEYDOWN = 0x0100
WM_QUIT = 0x0012

_MEDIA_BANNED = ("cubase", "studioone", "Cubase", "Studio One",
                 "spotify", "Spotify", "QQMusic", "Netease")
_CSB_TITLES = ("Cube Setlist Manager", "Cube Automator", "踩钉控制")


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]
    _fields_ = [("type", wintypes.DWORD), ("U", U)]


u32.SendInput.restype = wintypes.UINT
u32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)


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


def pump(sec):
    """主线程泵消息（探针窗收键用）。"""
    t0 = time.monotonic()
    msg = wintypes.MSG()
    while time.monotonic() - t0 < sec:
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def _csm_windows():
    """按窗口标题找在跑的 CSM 程序：程序必有主窗，比进程名可靠
    （开发态跑在 python.exe 下，tasklist 看不出是它）。"""
    titles = []
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                   wintypes.LPARAM)

    def _title(hwnd):
        n = u32.GetWindowTextLengthW(hwnd)
        if not n:
            return ""
        b = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, b, n + 1)
        return b.value

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def proc(h, l):
        titles.append(_title(h))
        return True

    u32.EnumWindows(proc, 0)
    # 排除资源管理器窗口（文件夹名含产品名会被误判为程序在跑）
    return [t for t in titles
            if any(b.lower() in t.lower() for b in _CSB_TITLES)
            and "文件资源管理器" not in t]


def preflight():
    """媒体/DAW 程序与在跑的 CSM 主窗在场即拒绝注入：拦截一旦失效也不
    误伤真程序。"""
    out = subprocess.run(["tasklist"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    bad = [ln.split()[0] for ln in out.splitlines()
           if any(b.lower() in ln.lower() for b in _MEDIA_BANNED)]
    bad += ["窗口《%s》" % t for t in _csm_windows()]
    if bad:
        print("PREFLIGHT-ABORT: 相关程序在场 %s，拒绝注入" % bad)
        return False
    return True


class SimBridge(pedal.DeviceBridge):
    """生产桥 + 注入归属桩：spoof 非空时注入事件（无设备句柄）视为所选
    设备（模拟 hDevice→路径→匹配段；那段需要真实设备，驱动级注入不做）。
    raw_events 记录经归属过滤后到达 _feed 的全部事件。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.spoof = frozenset()
        self.raw_events = []

    def _attr(self, name):
        return bool(self.spoof) or pedal.DeviceBridge._attr(self, name)

    def _feed(self, vk, down):
        self.raw_events.append((vk, down, time.monotonic()))
        pedal.DeviceBridge._feed(self, vk, down)


class LegacyProbe:
    """前台探针窗：记录收到的 WM_KEYDOWN——热键注册后注入键应被 win32k
    消费、永不到达本窗口（「不漏给其它程序」的离线证明）。须在主线程
    创建并随测试 pump。"""

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                  ctypes.c_uint, wintypes.WPARAM,
                                  ctypes.c_ssize_t)

    def __init__(self):
        self.keys = []
        self.hwnd = None
        self._proc = self._WNDPROC(self._wnd)
        self._cls = "SimLegacyProbe-%d" % time.monotonic_ns()

        class WC(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT),
                        ("lpfnWndProc", self._WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HANDLE),
                        ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HANDLE),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]
        wc = WC()
        wc.lpfnWndProc = self._proc
        wc.lpszClassName = self._cls
        wc.hInstance = k32.GetModuleHandleW(None)
        assert u32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u32.CreateWindowExW(0, self._cls, "sim probe", 0xC00000,
                                        10, 10, 200, 100, None, None,
                                        wc.hInstance, None)
        u32.ShowWindow(self.hwnd, 5)                # SW_SHOW

    def _wnd(self, h, m, w, l):
        if m == WM_KEYDOWN:
            self.keys.append(w)
        return u32.DefWindowProcW(h, m, w, l)

    def foreground(self):
        u32.SetForegroundWindow(self.hwnd)
        pump(0.1)
        return u32.GetForegroundWindow() == self.hwnd

    def close(self):
        if self.hwnd:
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None


def wait_running(br, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if br.running and br.raw_ok:
            return True
        time.sleep(0.05)
    return br.running and bool(br.raw_ok)


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


def main():
    if not preflight():
        return 2
    case = Case()

    # ---- 环境自检：注入事件能进 Raw Input 管道 ----
    env_raw = False
    br0 = SimBridge(lambda a: None)
    br0.configure(binds={}, device_hint="SIMFACE")
    br0.start()
    if wait_running(br0):
        br0.spoof = frozenset((VK_STOP,))
        press(VK_STOP)
        time.sleep(0.3)
        env_raw = any(e[0] == VK_STOP for e in br0.raw_events)
    br0.stop()
    case.run("ENV 注入→RawInput", env_raw,
             "管道活" if env_raw else "raw 管道死（历史降级）")

    # ---- T1 归属过滤：spoof=所选设备触发；无 spoof=注入不冒充踏板 ----
    if env_raw:
        fired = []
        br = SimBridge(fired.append)
        br.configure(binds={VK_NEXT: "next"}, device_hint="SIMFACE")
        br.start()
        wait_running(br)
        press(VK_NEXT)
        time.sleep(0.3)
        case.run("T1 归属过滤：注入不冒充踏板", fired == [], str(fired))
        br.spoof = frozenset((VK_NEXT,))
        press(VK_NEXT)
        time.sleep(0.3)
        case.run("T1 所选设备（桩归属）触发", fired == ["next"], str(fired))
        br.stop()
    else:
        case.skip("T1 归属过滤", "raw 管道死")

    # ---- T2 手势端到端（真注入时序） ----
    if env_raw:
        fired = []
        eng = pedal.GestureEngine(fired.append)
        br = SimBridge(fired.append)
        br.configure(binds={}, device_hint="SIMFACE", engine=eng,
                     temporal={VK_NEXT})
        eng.configure({(("hid", VK_NEXT), "double"): "next",
                       (("hid", VK_NEXT), "single"): "play"})
        br.spoof = frozenset((VK_NEXT,))
        br.start()
        wait_running(br)
        send_key(VK_NEXT)                           # 双踩
        time.sleep(0.06)
        send_key(VK_NEXT, up=True)
        time.sleep(0.15)
        case.run("T2 双踩第一踩不即发", fired == [], str(fired))
        send_key(VK_NEXT)
        time.sleep(0.06)
        send_key(VK_NEXT, up=True)
        time.sleep(0.4)
        case.run("T2 双踩触发", fired == ["next"], str(fired))
        send_key(VK_NEXT)                           # 按住：长踩已废除
        time.sleep(0.6)
        case.run("T2 按住中零动作（无长踩手势）", fired == ["next"], str(fired))
        send_key(VK_NEXT, up=True)
        time.sleep(0.6)                             # 窗过期：按住松脚=单踩
        case.run("T2 按住松脚窗后结算单踩", fired == ["next", "play"],
                 str(fired))
        press(VK_NEXT, gap=0.08)                    # 再来一次干净单踩
        time.sleep(0.6)
        case.run("T2 单踩窗后触发", fired == ["next", "play", "play"],
                 str(fired))
        br.stop()
    else:
        case.skip("T2 手势端到端", "raw 管道死")

    # ---- T3 学习捕获（mute 态走捕获不触发） ----
    if env_raw:
        cap = []
        br = SimBridge(None)
        br.configure(binds={}, device_hint="SIMFACE")
        br.spoof = frozenset((VK_NEXT,))
        br.begin_capture(lambda vk, d, t: cap.append((vk, d)))
        br.start()
        wait_running(br)
        press(VK_NEXT, gap=0.08)
        time.sleep(0.3)
        case.run("T3 事件到达 raw 管道", len(br.raw_events) == 2,
                 "raw=%s" % br.raw_events)
        case.run("T3 学习捕获完整踩法",
                 [(v, d) for v, d in cap] == [(VK_NEXT, True), (VK_NEXT, False)],
                 str(cap))
        br.end_capture()
        br.stop()
    else:
        case.skip("T3 学习捕获", "raw 管道死")

    # ---- T4 热键拦截：注册后键被 win32k 消费（前台窗收不到）且
    #      WM_INPUT 仍并行送达（触发不丢）；解除后反转 ----
    if env_raw:
        fired = []
        br = SimBridge(fired.append)
        br.configure(binds={VK_NEXT: "next"}, device_hint="SIMFACE",
                     block=True)
        br.spoof = frozenset((VK_NEXT,))            # 注入视为所选设备
        br.start()
        wait_running(br)
        time.sleep(0.2)                             # 热键注册落地
        probe = LegacyProbe()
        fg = probe.foreground()
        if fg:
            press(VK_NEXT)
            pump(0.4)
            case.run("T4 拦截：前台窗收不到被消费键", probe.keys == [],
                     "legacy=%s" % probe.keys)
            case.run("T4 拦截：热键回执已入 pending",
                     VK_NEXT in br._pending or fired == ["next"],
                     "pending=%s 触发=%s" % (br._pending, fired))
            case.run("T4 拦截：松开沿归属合成对仍触发", fired == ["next"],
                     "触发=%s" % fired)
            # 拦截开启 + 时序手势：合成按压对（按下=热键回执，松开=归属）
            eng = pedal.GestureEngine(fired.append)
            br.configure(binds={}, engine=eng, temporal={VK_NEXT},
                         block=True)
            eng.configure({(("hid", VK_NEXT), "double"): "next"})
            time.sleep(0.25)                        # 热键重注册落地
            send_key(VK_NEXT)
            time.sleep(0.06)
            send_key(VK_NEXT, up=True)
            time.sleep(0.1)
            send_key(VK_NEXT)
            time.sleep(0.06)
            send_key(VK_NEXT, up=True)
            time.sleep(0.3)
            case.run("T4 拦截开启：双踩合成对正常判别", fired[-1] == "next",
                     str(fired))
            br.configure(block=False)               # 解除注册
            time.sleep(0.3)
            press(VK_NEXT)
            pump(0.4)
            case.run("T4 解除后按键恢复送达前台",
                     VK_NEXT in probe.keys, "legacy=%s" % probe.keys)
        else:
            case.skip("T4 热键拦截（前台失败）", "SetForegroundWindow 被拒")
        probe.close()
        br.stop()
    else:
        case.skip("T4 热键拦截", "raw 管道死")

    # ---- T5 监听器装配层（纯生命周期） ----
    fired = []
    pl = pedal.PedalListener(fired.append)
    pl.apply("无此口", {"next": 4}, {"next": VK_NEXT}, "SIMFACE", True)
    case.run("T5 apply 契约（VK 反转）",
             pl.bridge.binds == {VK_NEXT: "next"}
             and pl.bridge.device_hint == "SIMFACE"
             and pl.bridge.block is True)
    pl.try_open()
    case.run("T5 try_open 拉起设备桥", wait_running(pl.bridge)
             and pl.hid_active, "running=%s" % pl.bridge.running)
    pl.mute()
    case.run("T5 静音=学习让路",
             pl.muted and pl.bridge.silent and not pl.try_open())
    pl.unmute()
    pl.try_open()
    case.run("T5 解除静音恢复", pl.hid_active)
    pl.shutdown()
    time.sleep(0.2)
    case.run("T5 shutdown 停桥", not pl.bridge.running)

    # ---- T6 连踩压力：20 次交替触发零丢失零重复 ----
    if env_raw:
        fired = []
        br = SimBridge(fired.append)
        br.configure(binds={VK_NEXT: "next", VK_PREV: "prev"},
                     device_hint="SIMFACE")
        br.spoof = frozenset((VK_NEXT, VK_PREV))
        br.start()
        wait_running(br)
        expect = ["next", "prev"] * 10
        for vk in [VK_NEXT, VK_PREV] * 10:
            press(vk, gap=0.02)
            time.sleep(0.16)                        # > 去抖 0.15s
        time.sleep(0.6)
        case.run("T6 连踩 20 次精确触发", fired == expect,
                 "触发 %d 次（应 20）%s" % (len(fired), fired))
        br.stop()
    else:
        case.skip("T6 连踩压力", "raw 管道死")

    fails = [r for r in case.results if r[1] == "FAIL"]
    skips = [r for r in case.results if r[1] == "SKIP-ENV"]
    summary = {
        "verdict": "FAIL" if fails else "PASS",
        "env": {"raw": env_raw},
        "pass": sum(1 for r in case.results if r[1] == "PASS"),
        "fail": len(fails), "skip_env": len(skips),
        "details": [{"case": n, "verdict": v, "detail": d}
                    for n, v, d in case.results],
    }
    print("SIM-SUMMARY " + json.dumps(summary, ensure_ascii=False))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
