# -*- coding: utf-8 -*-
"""模态框探针：实证 tkinter 模态对话框（messagebox/filedialog）打开期间，
主线程 after 循环是否继续运转。此前审计对此有两派说法（原生 MessageBox
自泵消息循环=冻结 vs 模态泵继续派发定时器=存活），直接测。

方法：主线程开对话框，after 循环持续打时间戳；后台线程用 SendInput 把
真实鼠标点击反复送到对话框默认按钮上（最接近真人的输入路径），对话框
关掉后向宿主窗投 WM_CLOSE 收尾；全程带硬超时防挂死。

运行：python tools/probe_modal_after.py   （全自动，约 20 秒；会短暂占用
鼠标真实点击，屏幕上闪现两个对话框，均被自动点掉）
"""
import ctypes
import ctypes.wintypes
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class _KBINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KBINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
WM_CLOSE = 0x0010


def send_click(x, y):
    """SendInput 绝对坐标移动+左键点击（0..65535 归一化到主屏）。"""
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    ax = int(x * 65535 / (sw - 1))
    ay = int(y * 65535 / (sh - 1))

    def one(member, fields):
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        setattr(inp, member, fields)
        u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    one("mi", _MOUSEINPUT(ax, ay, 0,
                          MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE, 0, None))
    time.sleep(0.05)
    one("mi", _MOUSEINPUT(0, 0, 0,
                          MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN,
                          0, None))
    time.sleep(0.05)
    one("mi", _MOUSEINPUT(0, 0, 0,
                          MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP,
                          0, None))


def send_esc():
    """SendInput 真实 ESC 键（兜底解锁）。"""
    for flags in (0, KEYEVENTF_KEYUP):
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = _KBINPUT(0x1B, 0, flags, 0, None)
        u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.03)
# 注：键盘注入用成员名直赋（匿名联合体构造器初始化在 py3.14 不认）


_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                  ctypes.c_void_p)


def _windows():
    out = []

    @_WNDENUMPROC
    def cb(h, _l):
        if u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1) if n else None
            if buf:
                u32.GetWindowTextW(h, buf, n + 1)
            cls = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, cls, 64)
            out.append((h, buf.value if buf else "", cls.value))
        return True

    u32.EnumWindows(cb, 0)
    return out


def _children(h):
    out = []

    @_WNDENUMPROC
    def cb(ch, _l):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(ch, cls, 64)
        out.append((ch, cls.value))
        return True

    u32.EnumChildWindows(h, cb, 0)
    return out


def _click_default(h):
    """找 BS_DEFPUSHBUTTON(0x1) 的 Button 子窗，SendInput 点它中心。"""
    for ch, cls in _children(h):
        if cls == "Button" \
                and u32.GetWindowLongW(ch, -16) & 0xFFFFFFFF & 0x0001:
            rect = ctypes.wintypes.RECT()
            if u32.GetWindowRect(ch, ctypes.byref(rect)):
                send_click((rect.left + rect.right) // 2,
                           (rect.top + rect.bottom) // 2)
                return True
    return False


def probe(name, open_fn, title, click_default=True, wait=10.0):
    """开对话框 → 后台线程定位并反复真实点击/WM_CLOSE 关闭 → 报告存续期内
    after 心跳最大间隔。"""
    root = tk.Tk()
    root.title("probe-host")
    root.geometry("280x120")
    stamps = []
    state = {"t_open": None, "t_close": None, "cls": None}
    root_hwnd = []

    def tick():
        try:
            stamps.append(time.monotonic())
            root.after(100, tick)
        except tk.TclError:
            pass

    root.after(100, tick)

    def opener():
        open_fn()
        state["t_close"] = time.monotonic()
        for h, t, _c in _windows():
            if t == "probe-host":
                u32.PostMessageW(h, WM_CLOSE, 0, 0)   # 宿主收尾
                return

    def closer():
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait and state["t_close"] is None:
            for h, t, cls in _windows():
                if t == title:
                    if state["t_open"] is None:
                        state["t_open"] = time.monotonic()
                        state["cls"] = cls
                    if click_default and _click_default(h):
                        time.sleep(0.3)      # 点击生效窗口，未关则再点
                        continue
            time.sleep(0.15)
        if state["t_close"] is None:         # 点击无效：WM_CLOSE 兜底
            for h, t, _cls in _windows():
                if t == title:
                    u32.PostMessageW(h, WM_CLOSE, 0, 0)

    def hard_guard():
        """硬超时防挂死：15s 未收场→ESC→再 5s→强杀进程（探针绝不悬置）。"""
        time.sleep(15)
        if state["t_close"] is None:
            send_esc()
        time.sleep(5)
        if state["t_close"] is None:
            print("[%s] 硬超时未收场（探针异常，非软件结论）" % name)
            k32.TerminateProcess(k32.GetCurrentProcess(), 2)

    root.after(600, lambda: threading.Thread(target=opener,
                                             daemon=True).start())
    threading.Thread(target=closer, daemon=True).start()
    threading.Thread(target=hard_guard, daemon=True).start()
    try:
        root.mainloop()
    except tk.TclError:
        pass

    if state["t_open"] is None or state["t_close"] is None:
        print("[%s] 未捕捉到对话框完整存续（open=%s close=%s）——探针失败"
              % (name, state["t_open"], state["t_close"]))
        return
    window = [t for t in stamps if state["t_open"] <= t <= state["t_close"]]
    gaps = [b - a for a, b in zip(window, window[1:])]
    max_gap = max(gaps) if gaps else 0.0
    verdict = ("after 循环存活（未冻结）" if max_gap <= 0.5
               else "after 循环冻结！模态即停摆源")
    print("[%s] 对话框类=%s 存续 %.1fs 心跳最大间隔=%.2fs → %s"
          % (name, state["cls"], state["t_close"] - state["t_open"],
             max_gap, verdict))


def main():
    probe("messagebox.askyesno",
          lambda: messagebox.askyesno("模态探针A", "自动点击测试（是/否）"),
          "模态探针A")
    time.sleep(1)
    probe("filedialog.askdirectory",
          lambda: filedialog.askdirectory(title="模态探针B"),
          "模态探针B", click_default=False, wait=6.0)
    print("结论口径：间隔≤0.5s=after 在模态期间持续被服务（模态不是停摆源）；"
          "间隔≈对话框存续时长=冻结（模态=停摆源）。")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
