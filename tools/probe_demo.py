# -*- coding: utf-8 -*-
# StallNativeProbe 端到端实弹验证：Tk 窗口 + 50ms 心跳 after 循环，
# 主线程人为冻结 6s（模拟 C 级阻塞），探针应产出：前台窗口行 + py-spy
# 原生栈（栈里可见 time.sleep）+ WM_NULL 探针行 + 恢复行。
import pathlib
import sys
import time
import tkinter as tk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import stallguard

LOG = pathlib.Path(__file__).with_name("_probe_demo.log")
if LOG.exists():
    LOG.unlink()

root = tk.Tk()
root.geometry("240x80")
tk.Label(root, text="probe-demo").pack()
hwnd = ctypes_hwnd = None
root.update_idletasks()
hwnd = root.winfo_id()

hb = {"t": time.monotonic()}


def tick():
    hb["t"] = time.monotonic()
    root.after(50, tick)


root.after(50, tick)
probe = stallguard.StallNativeProbe(
    heartbeat=lambda: hb["t"], main_hwnd=lambda: hwnd, path=LOG,
    threshold=1.0, poke=0.3, period=0.25).start()


def freeze():
    time.sleep(6.0)         # 模拟主线程 C 级阻塞；WM_NULL 应无法唤醒
    hb["t"] = time.monotonic()


root.after(500, freeze)
root.after(9000, root.destroy)
root.mainloop()
probe.stop()
print(LOG.read_text(encoding="utf-8"))
