# -*- coding: utf-8 -*-
import ctypes
import os
import sys
import sv_ttk
import tkinter as tk
import tkinter.ttk as ttk

"""Windows UI 环境适配：DPI 感知 + 深色演出主题。
enable() 必须先于 tk.Tk() 调用；感知后 tkinter 按真实 DPI 自动放大
点数字体，写死的像素尺寸用 scale() 换算。非 Windows 或声明失败时
安全降级（维持旧的不缩放行为）。

主题单路径：main() 建 root 后调 apply_theme(root)——sv-ttk dark
（Win11 观感）+ 集中命名 style；动态状态色 config(style=tone(色)+族名)
同时写 widget 级 foreground（实测见 paint_tree）。sv_ttk 缺失/初始化
失败直接抛异常，无任何回退。窗口级深色标题栏走 setup_window()。"""

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

# 强调按钮前景（Start=绿底深字 / Stop=红底白字）；底色在 assets/ 官方
# 精灵图色相变体里（make_accent_assets.py 生成），不在代码中配
START_FG = "#101418"


def tone(color):
    """状态语义色 → 命名 style 词干（apply_theme 注册的
    Ok/Warn/Err/Dim/Log）。标签族用法 config(style=tone(色)+".TLabel",
    foreground=色)（fg 必写，见 paint_tree），跑马灯族用法
    style="Marquee%s.TEntry" % tone(色)——Marquee 族未注册 Log 档，
    勿传 LOG_FG。"""
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


def paint_tree(win):
    """把命名 style 的前景色落到 widget 级（构建尾部一次性兜静态控件；
    动态改色的 _set/Marquee.set 等在各自上色点直接写 foreground）。
    根因（tools 探针实测，Tk 8.6.15 + sv-ttk 2.6.1，clam 下无此问题）：
    TLabel/TEntry/TCombobox 族 style.configure(-foreground) 只进 lookup、
    不参与绘制，文字恒为主题白；唯一实证可渲染路径是 widget 级
    foreground。命名 style 仍同步保留——语义族名、lookup 与配置级断言
    不受影响；TButton 族 style 路径实测正常（且 Button 拒收 widget 级
    fg，TclError 静默跳过）。"""
    s = ttk.Style(win)
    stack = [win]
    while stack:
        w = stack.pop()
        for c in w.winfo_children():
            stack.append(c)
        try:
            style = str(w.cget("style"))
            if not style:
                continue
            fg = s.lookup(style, "foreground")
            if fg:
                w.configure(foreground=fg)
        except tk.TclError:
            pass


def setup_window(win):
    """已迁移窗口的窗口级设置：深色标题栏 + Win11 圆角 + 状态色落
    widget 级（paint_tree）。各 Toplevel 构建尾部调用（主窗在 _build
    尾，设置页等子窗在各自尾部）。"""
    dark_title(win)
    win.update_idletasks()          # 先让构建期最后一次动态上色生效
    paint_tree(win)


def bind_hint(widget, on_hint, text):
    """悬停提示接线：鼠标进入控件→on_hint(text)，离开→on_hint(None)。
    on_hint 由窗口实现（底栏三方优先级：悬停提示>瞬时反馈>常驻状态）。
    add=\"+\" 不覆盖控件既有绑定。"""
    widget.bind("<Enter>", lambda _e: on_hint(text), add="+")
    widget.bind("<Leave>", lambda _e: on_hint(None), add="+")


def apply_theme(root):
    """已迁移窗口的整主题入口：sv-ttk dark + 集中命名 style。在
    tk.Tk() 建立后、控件构建前调用一次（各程序 main() 与离线渲染
    同一入口）。sv_ttk 缺失/初始化失败直接抛异常——单路径迁移。"""
    sv_ttk.set_theme("dark", root)
    s = ttk.Style(root)
    # sv-ttk 的命名字体全是 Segoe UI Variable 系：西文窄字形、中文无字
    # 形回退宋体，且负像素字号不随 DPI 缩放；TEntry/TCombobox/TSpinbox
    # 还在 <<ThemeChanged>> 时把 widget font 强制回 SunValleyBodyFont、
    # style "." 也挂着它——style/widget 级配置都会被盖。断根处只有一
    # 个：reconfigure 这批命名字体对象本身（点数字号随 tk scaling 缩放）。
    for fname, size in (
            ("SunValleyCaptionFont", 9),
            ("SunValleyBodyFont", 10),
            ("SunValleyBodyStrongFont", 10),
            ("SunValleyBodyLargeFont", 13),
            ("SunValleySubtitleFont", 14),
            ("SunValleyTitleFont", 20),
            ("SunValleyTitleLargeFont", 28),
            ("SunValleyDisplayFont", 36),
    ):
        try:
            root.tk.call("font", "configure", fname,
                         "-family", "Microsoft YaHei UI", "-size", size)
        except tk.TclError:
            pass            # 版本演进字体名缺失：略过，style 已显式钉雅黑
    # 全族默认字体：演出暗场/远距/余光可读，正文 10pt（9pt 实机观感
    # 偏小）。ttk 主体经 "." 继承，ttk 默认西文字体（Segoe UI Variable
    # 系）比雅黑窄小，统一钉回雅黑族防混族；Combobox 弹出列表是 classic
    # Listbox，不吃 ttk style，走 option_add 同步。
    base_font = ("Microsoft YaHei UI", 10)
    s.configure(".", font=base_font)
    root.option_add("*TCombobox*Listbox.font", base_font)
    # Labelframe 标题与正文同族同档弱化色（sv-ttk 默认 SunValleyCaptionFont
    # 为西文窄字体，与全窗 YaHei 混族且偏小）
    s.configure("TLabelframe.Label",
                font=base_font, foreground=MUT)
    # 状态文字：动态状态色的命名 style（语义族名/lookup 承担；实际绘制
    # 色由 widget 级 foreground 承担——TLabel 族 style fg 在 sv-ttk 下
    # 不参与绘制，见 paint_tree）
    for name, color in (("Ok", C_OK), ("Warn", C_WARN), ("Err", C_ERR),
                        ("Dim", MUT), ("Log", LOG_FG)):
        s.configure("%s.TLabel" % name, foreground=color)
    # 强调按钮：sv-ttk 的按钮外观烘焙在精灵图里（image element 运行时
    # 不可换色，官方 Accent.TButton 只有固定蓝）——assets/ 下的绿/红
    # 按钮图由 tools/make_accent_assets.py 从官方精灵图切片做色相重映射
    # 一次性生成（圆角/抗锯齿/九宫格全保真），tk 原生读 PNG 零自绘；
    # 悬停/按压/禁用 = 图片态切换（照官方 dark.tcl 的状态表），文字色走
    # style configure/map。frozen 时资产在 _MEIPASS。
    # 非冻结锚模块目录（仓库根），不随 CWD 漂移
    asset_dir = getattr(sys, "_MEIPASS", "") or os.path.dirname(
        os.path.abspath(__file__))
    imgs = []
    for name, txt, dkey in (("Start", START_FG, "start"),
                            ("Stop", "#ffffff", "stop")):
        ph = {st: tk.PhotoImage(file=os.path.join(
            asset_dir, "assets", "btn_%s_%s.png" % (dkey, st)), master=root)
            for st in ("rest", "hover", "pressed", "focus", "focus-hover",
                       "dis")}
        imgs.extend(ph.values())
        s.element_create(name + ".round", "image", ph["rest"],
                         ("selected disabled", ph["dis"]),
                         ("disabled", ph["dis"]),
                         ("selected", ph["rest"]),
                         ("pressed", ph["pressed"]),
                         ("active focus", ph["focus-hover"]),
                         ("active", ph["hover"]),
                         ("focus", ph["focus"]),
                         border=4, sticky="nsew")
        s.layout(name + ".TButton", [(name + ".round",
                 {"sticky": "nsew",
                  "children": [("Button.padding",
                                {"sticky": "nsew",
                                 "children": [("Button.label",
                                               {"sticky": "nswe"})]})]})])
        s.configure(name + ".TButton", foreground=txt, anchor="center",
                    padding=(8, 2, 8, 3))
        s.map(name + ".TButton", foreground=[("disabled", MUT)])
    root._round_btn_imgs = imgs      # PhotoImage 保活（Tcl 侧不防 GC）
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
