# -*- coding: utf-8 -*-
import ctypes
import sys
import tkinter as tk

"""Windows UI 环境适配：DPI 感知 + 深色演出主题。
enable() 必须先于 tk.Tk() 调用；感知后 tkinter 按真实 DPI 自动放大
点数字体，写死的像素尺寸用 scale() 换算。非 Windows 或声明失败时
安全降级（维持旧的不缩放行为）。darkify() 在窗口构建完成后递归套
深色主题（演出软件惯例：暗场、远距、余光可读），并对每个顶层窗口
顺带启用深色标题栏与 Win11 圆角；之后再设置的动态
状态色不受影响。"""

# 深色主题调色板（各界面文件共用；状态色用亮化变体保深底对比度）
BG = "#141518"        # 窗口底
PANEL = "#1e2024"     # 面板/控件底
FIELD = "#191b1f"     # 输入框/列表底
FG = "#e6e6e6"        # 主文字
MUT = "#9aa0a6"       # 弱化文字
C_OK = "#5ad469"      # 绿：正常/自动
C_WARN = "#f5b944"    # 黄：警告/手动
C_ERR = "#ff5c5c"     # 红：错误/未知
SELECT = "#2d5d7a"    # 列表选中底
BORDER = "#525457"    # 面板边线：与列表框 sunken 1px 边线同色（Tk 对 FIELD 的着色）
LOG_FG = "#8a9096"    # 日志正文：比主文字暗一档（黑匣子不该抢视觉权重）
CUR_BG = "#223648"    # 列表当前曲行底色：未选中也能一眼定位


def enable():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Win8.1 以前
        except Exception:
            pass


def _colorref(hexstr):
    """#RRGGBB → Win32 COLORREF (0x00BBGGRR)。"""
    return (int(hexstr[5:7], 16) << 16 | int(hexstr[3:5], 16) << 8
            | int(hexstr[1:3], 16))


def dark_title(win):
    """深色标题栏（Win10 20H1+ 暗色标志 + Win11 直接指定标题栏配色）+
    Win11 圆角。实测坑：窗口显示前写 DWM 属性会静默不生效（ret=0 但
    读回 0），必须在 <Map> 后重写并 SWP_FRAMECHANGED 强制重绘非客户
    区——故立即写一次、映射时再写一次。attr35/36 直接上色最可靠，
    attr20 负责关闭按钮的亮色字形。失败静默：老系统只是没效果。"""
    if sys.platform != "win32":
        return

    def apply(_e=None):
        try:
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            if not hwnd:
                return
            dwm = ctypes.windll.dwmapi
            v = ctypes.c_int(1)                 # TRUE = 暗色标题栏标志
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
            cap = ctypes.c_uint(_colorref(BG))  # 标题栏底=窗口底色
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), 4)
            txt = ctypes.c_uint(_colorref(FG))  # 标题文字=主文字色
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), 4)
            v2 = ctypes.c_int(2)                # DWMWCP_ROUND = 圆角
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(v2), 4)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, 0x27)      # +FRAMECHANGED 强制重绘
        except Exception:
            pass

    apply()
    win.bind("<Map>", apply, add="+")


def scale(root, w, h=None):
    """像素值按屏幕缩放率换算（96dpi = 100%）。h 为 None 时返回整数。"""
    s = root.winfo_fpixels("1i") / 96.0
    if h is None:
        return int(w * s)
    return "%dx%d" % (int(w * s), int(h * s))


def flatten(w):
    """无框线表单窗（设置/键盘自动化/踩钉）的内容底色统一为窗口底色：
    darkify 给 Frame/Label/Checkbutton 套 PANEL 面板色，在主窗里是有框
    面板的底，在这些纯表单页里却呈现为一块块比窗口浅的色斑，看着像
    误加的高亮——文字应直接坐在窗口底色上。只改容器/文字类底色，
    控件（按钮/输入框/下拉）的底色不动。darkify 之后调用。"""
    if isinstance(w, (tk.Frame, tk.Label, tk.Checkbutton)):
        w.config(bg=BG)
    for c in w.winfo_children():
        flatten(c)


def darkify(w):
    """递归套深色主题。在窗口构建完成后调用一次；此后的动态 fg（状态
    色）覆盖不受影响。Label 默认弱色，动态更新的由各自逻辑覆写；
    Entry 必须显式 insertbackground，否则深底下光标不可见。"""
    if isinstance(w, (tk.Tk, tk.Toplevel)):
        dark_title(w)
        w.config(bg=BG)
    elif isinstance(w, tk.LabelFrame):
        # flat + 1px 高亮环：边线颜色精确指定（solid/groove 的线色由 Tk
        # 从底色派生——solid 近黑、groove@1px 顶边残缺），与列表框边线同色同粗
        w.config(bg=PANEL, fg=MUT, bd=0, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=BORDER)
        # 标题与左边框留出与内部文字相当的间距（classic labelframe 的
        # padX 不作用于标题，前导空格实测有效；防重复加前缀）
        t = str(w.cget("text"))
        if t and not t.startswith(" "):
            w.config(text=" " + t)
    elif isinstance(w, tk.Frame):
        w.config(bg=PANEL)
    elif isinstance(w, tk.Label):
        w.config(bg=PANEL, fg=MUT)
    elif isinstance(w, tk.Button):
        w.config(bg=PANEL, fg=FG, activebackground="#33363d",
                 activeforeground=FG, disabledforeground="#6a6f76")

        def _in(_e, b=w):
            # 悬停提亮一档（取 activebackground：darkify 后改色的强调
            # 按钮——绿开始/红全停——自动用各自的亮化变体）；存原色须在
            # Enter 时取，Leave 才能还原到改色后的底
            if str(b["state"]) == "normal":
                b._bg0 = b.cget("bg")
                b.config(bg=b.cget("activebackground"))

        def _out(_e, b=w):
            if getattr(b, "_bg0", None):
                b.config(bg=b._bg0)

        w.bind("<Enter>", _in)
        w.bind("<Leave>", _out)
    elif isinstance(w, tk.Listbox):
        w.config(bg=FIELD, fg=FG, selectbackground=SELECT,
                 selectforeground=FG, highlightthickness=0)
    elif isinstance(w, tk.Entry):
        if getattr(w, "NO_RING", False):
            # 展示型 Entry（跑马灯）：不是输入区，底色随所在面板而非
            # 输入框色，否则会带一圈比面板暗的底带
            w.config(bg=PANEL, fg=FG, insertbackground=FG,
                     readonlybackground=PANEL, disabledbackground=PANEL)
        else:
            # 焦点环：平时 1px 边框标出可交互区，聚焦变绿
            w.config(bg=FIELD, fg=FG, insertbackground=FG,
                     readonlybackground=FIELD, disabledbackground=FIELD,
                     highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=C_OK)
    elif isinstance(w, tk.Checkbutton):
        w.config(bg=PANEL, fg=FG, selectcolor=FIELD,
                 activebackground=PANEL, activeforeground=FG)
    for c in w.winfo_children():
        darkify(c)
