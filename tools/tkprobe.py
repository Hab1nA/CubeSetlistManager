# -*- coding: utf-8 -*-
# 最小 Tk 心跳探针：50ms after 循环；间隙 >0.9s 落盘。
# 目的：判定主线程停摆是 OS/Tcl/Python 层（最小程序复现）还是本应用代码层。
import time
import tkinter as tk

LOG = r"C:\Users\XKZ\Documents\VSCode Projects\CubeSetlistManager\out\tkprobe_hb.log"
root = tk.Tk()
root.title("tkprobe")
root.geometry("200x80")
lbl = tk.Label(root, text="probe", font=("Arial", 12))
lbl.pack()
last = time.monotonic()

def tick():
    global last
    now = time.monotonic()
    gap = now - last
    last = now
    if gap > 0.9:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s gap=%.2fs\n" % (time.strftime("%H:%M:%S"), gap))
    root.after(50, tick)

root.after(50, tick)
root.mainloop()
