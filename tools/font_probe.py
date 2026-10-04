# -*- coding: utf-8 -*-
"""字体探针（只 print,不写文件）：对比开发环境与 PyInstaller exe 中
各 ttk style / 系统字体的实际解析,排查 dist 值控件/按钮变宋体的根因。
"""
import tkinter as tk
from tkinter import ttk

import sv_ttk

root = tk.Tk()
for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
    print("%s: %s" % (name, root.tk.call("font", "actual", name)))
print("tk scaling: %s" % root.tk.call("tk", "scaling"))
print("encoding system: %s" % root.tk.call("encoding", "system"))
print("patchLevel: %s" % root.tk.eval("info patchlevel"))
sv_ttk.set_theme("dark", root)
s = ttk.Style(root)
print("theme: %s" % s.theme_use())
print('style "." font: %r' % s.lookup(".", "font"))
for st in ("TButton", "TLabel", "TEntry", "TCombobox",
           "TCheckbutton", "TLabelframe.Label"):
    print("style %s font: %r" % (st, s.lookup(st, "font")))
btn = ttk.Button(root, text="测")
ent = ttk.Entry(root)
cb = ttk.Combobox(root, values=["a"])
chk = ttk.Checkbutton(root, text="测")
for x in (btn, ent, cb, chk):
    x.pack()
root.update_idletasks()
for name, wid in (("TButton", btn), ("TEntry", ent),
                  ("TCombobox", cb), ("TCheckbutton", chk)):
    try:
        f = wid.cget("font")
    except Exception as e:
        f = "ERR:%s" % e
    try:
        actual = (root.tk.call("font", "actual", f)
                  if f else "(inherit)")
    except Exception as e:
        actual = "ERR:%s" % e
    print("widget %s cget(font)=%r actual=%s" % (name, f, actual))
root.destroy()
print("PROBE_DONE")
