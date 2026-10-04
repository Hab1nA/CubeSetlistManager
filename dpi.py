# -*- coding: utf-8 -*-
import ctypes
import sys
import sv_ttk
import tkinter as tk
import tkinter.ttk as ttk

"""Windows UI 环境适配：DPI 感知 + 深色演出主题。
enable() 必须先于 tk.Tk() 调用；感知后 tkinter 按真实 DPI 自动放大
点数字体，写死的像素尺寸用 scale() 换算。非 Windows 或声明失败时
安全降级（维持旧的不缩放行为）。

主题两代并存（过渡期，全部迁完后删旧代）：
- 已迁移窗口：main() 建 root 后调 apply_theme(root)——sv-ttk dark
  （Win11 观感）+ 集中命名 style；动态状态色不再 config(fg=)，改
  config(style=tone(色)+族名)。sv_ttk 缺失/初始化失败直接抛异常，
  单路径迁移，无任何回退。窗口级深色标题栏走 setup_window()。
- 未迁移窗口：darkify()/flatten() 在窗口构建完成后递归套色（引用
  同一调色板值），darkify 顺带做 setup_window 的事。"""

# 深色主题调色板（各界面文件共用）。底色系=sv-ttk dark 原生值（取自
# theme/dark.tcl 与 spritesheet_dark 实测采样），状态语义色保留原值。
BG = "#1c1c1c"        # 窗口底（sv-ttk -bg；卡片同色=扁平设计）
PANEL = "#1c1c1c"     # 面板/控件底（sv-ttk card 实测即 #1c1c1c）
FIELD = "#292929"     # 输入框/列表底（sv-ttk textbox-rest 采样）
FG = "#fafafa"        # 主文字（sv-ttk -fg）
MUT = "#9e9e9e"       # 弱化文字（暗场远距仍可读的弱化档）
C_OK = "#5ad469"      # 绿：正常/自动
C_WARN = "#f5b944"    # 黄：警告/手动
C_ERR = "#ff5c5c"     # 红：错误/未知
SELECT = "#2f60d8"    # 列表选中底（sv-ttk -selbg 原生）
BORDER = "#4d4d4d"    # 面板边线：与列表框边线同档的中灰
LOG_FG = "#b8b8b8"    # 日志正文：比主文字暗一档（黑匣子不该抢视觉权重）
CUR_BG = "#28496e"    # 列表当前曲行底色：蓝系、暗于选中色，未选中可定位

# 强调按钮配色（Start=绿底深字 / Stop=红底白字；hover 提亮一档）
START_FG = "#101418"
START_HOVER = "#7fe896"
STOP_BG = "#a03030"
STOP_HOVER = "#c04444"


def tone(color):
    """状态语义色 → 命名 style 词干（apply_theme 注册的
    Ok/Warn/Err/Dim/Log）。标签族用法 config(style=tone(色)+".TLabel")，
    跑马灯族用法 style="Marquee%s.TEntry" % tone(色)。"""
    return {C_OK: "Ok", C_WARN: "Warn", C_ERR: "Err",
            LOG_FG: "Log"}.get(color, "Dim")


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


def setup_window(win):
    """已迁移窗口的窗口级设置：深色标题栏 + Win11 圆角。各 Toplevel
    构建尾部调用（主窗在 _build 尾，设置页等子窗在各自尾部）。"""
    dark_title(win)


def apply_theme(root):
    """已迁移窗口的整主题入口：sv-ttk dark + 集中命名 style。在
    tk.Tk() 建立后、控件构建前调用一次（各程序 main() 与离线渲染
    同一入口）。sv_ttk 缺失/初始化失败直接抛异常——单路径迁移。"""
    sv_ttk.set_theme("dark", root)
    s = ttk.Style(root)
    # Labelframe 标题保持正文同族字号与弱化档（主题默认 Segoe 会跟
    # 全窗 YaHei 混族）
    s.configure("TLabelframe.Label",
                font=("Microsoft YaHei UI", 9), foreground=MUT)
    # 状态文字：动态状态色的命名 style（替代 config(fg=)）
    for name, color in (("Ok", C_OK), ("Warn", C_WARN), ("Err", C_ERR),
                        ("Dim", MUT), ("Log", LOG_FG)):
        s.configure("%s.TLabel" % name, foreground=color)
    # 强调按钮：sv-ttk 的按钮是图片 element（-background 不生效），须换
    # default 主题的素色 border element 才能上绿/红底；hover/按压提亮走
    # style.map（取代 Enter/Leave 手工 bind），禁用态压灰保「不可点」语义
    s.element_create("Flat.button", "from", "default", "Button.border")
    flat_btn = [("Flat.button",
                 {"sticky": "nsew",
                  "children": [("Button.focus",
                                {"sticky": "nsew",
                                 "children": [("Button.padding",
                                               {"sticky": "nsew",
                                                "children": [("Button.label",
                                                              {"sticky":
                                                               "nswe"})]})]})]})]
    for name, base, hover, txt in (("Start", C_OK, START_HOVER, START_FG),
                                   ("Stop", STOP_BG, STOP_HOVER, "#ffffff")):
        style = name + ".TButton"
        s.layout(style, flat_btn)
        s.configure(style, background=base, foreground=txt,
                    borderwidth=0, relief="flat", anchor="center",
                    padding=(8, 2, 8, 3))
        s.map(style,
              background=[("disabled", FIELD), ("pressed", hover),
                          ("active", hover)],
              foreground=[("disabled", MUT)])
    # 跑马灯：平地 element（default 主题的素 field）换掉 sv-ttk 的
    # 图片 field——展示型 Entry 不是输入区，无边框、底色随所在面板；
    # 状态色变体族供 Marquee.set 动态切换（layout 须逐个注册：ttk 的
    # 布局回退只剥前缀词，MarqueeOk.TEntry 不会命中 Marquee.TEntry）
    s.element_create("Marquee.field", "from", "default", "Entry.field")
    for name, color in (("", FG), ("Ok", C_OK), ("Warn", C_WARN),
                        ("Err", C_ERR), ("Dim", MUT)):
        style = "Marquee%s.TEntry" % name
        s.layout(style, [("Marquee.field",
                          {"sticky": "nsew",
                           "children": [("Entry.textarea",
                                         {"sticky": "nsew"})]})])
        s.configure(style, fieldbackground=BG, foreground=color,
                    padding=0)
        s.map(style,
              foreground=[("disabled", color)],
              fieldbackground=[("disabled", BG)])


def scale(root, w, h=None):
    """像素值按屏幕缩放率换算（96dpi = 100%）。h 为 None 时返回整数。"""
    s = root.winfo_fpixels("1i") / 96.0
    if h is None:
        return int(w * s)
    return "%dx%d" % (int(w * s), int(h * s))


def flatten(w):
    """无框线表单窗（设置/键盘自动化/踩钉）的内容底色统一为窗口底色：
    darkify 给 Frame/Label/Checkbutton 套 PANEL 面板色，在主窗里是有框
    面板的底，在这些纯表单页里却呈现为一块块色斑——文字应直接坐在
    窗口底色上。只改容器/文字类底色，控件（按钮/输入框/下拉）的底色
    不动。递归套色之后调用。"""
    if isinstance(w, (tk.Frame, tk.Label, tk.Checkbutton)):
        w.config(bg=BG)
    for c in w.winfo_children():
        flatten(c)


def darkify(w):
    """递归套深色主题（旧代：未迁移窗口专用，全部迁完后整体删除）。
    在窗口构建完成后调用一次；此后的动态 fg（状态色）覆盖不受影响。
    Label 默认弱色，动态更新的由各自逻辑覆写；Entry 必须显式
    insertbackground，否则深底下光标不可见。"""
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
            # 悬停提亮一档（取 activebackground：递归套色后改色的强调
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
