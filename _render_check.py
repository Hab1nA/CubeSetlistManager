# -*- coding: utf-8 -*-
"""报告三/四整改的几何验证（离线渲染，无真服务）：
1) 同组按钮等宽、文字不裁剪；2) 列表间距>0；3) 截图五窗 PNG 供人工核对。"""
import pathlib
import struct
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont

import setlist_gui as sg
sg.dpi.enable()          # 与真机一致：DPI 感染后按钮 bg 才按主题渲染
import pedal
import kbd_auto

d = pathlib.Path(tempfile.mkdtemp(prefix="render_"))
sg._HERE = d
sg.CONFIG_PATH = d / "config.json"
sg.PLAYLIST_PATH = d / "playlist.json"
lib = d / "projects"


def mkcpr(team, song, dur):
    p = lib / team / song
    p.mkdir(parents=True)
    rec = lambda side, sec: ("Cycle %s" % side).encode() + b"\x00" \
        + b"\x00\x02\x00\x06\x00\x00\x00\x02" + struct.pack(">I", 5) \
        + b"Time\x00" + b"\x00\x04" + struct.pack(">d", sec)
    (p / (song + ".cpr")).write_bytes(
        b"RIF2" + b"\x00" * 8 + rec("Left", 10.0) + rec("Right", 10.0 + dur))


for t, s, du in (("TeamA", "SongA", 134.4), ("TeamA", "SongB", 240.0),
                 ("TeamB", "SongC", 0.0), ("TeamB", "SameName", 90.0),
                 ("TeamD", "Drown out the noise", 200.0)):
    mkcpr(t, s, du)

sg.App._startup = lambda self: None
root = tk.Tk()
root.geometry(sg.dpi.scale(root, 980, 840))
root.attributes("-topmost", True)   # 独占屏幕：防控制台/光标污染像素采样
app = sg.App(root)
app.ccfg["projectsRoot"] = str(lib)
app.pl_keys = ["TeamA/SongA", "TeamA/SongB", "TeamB/SongC",
               "TeamD/Drown out the noise"]
app.cur = 0
app._load_songs()
app._refresh()
# 固定到主屏 + 置顶后再做像素采样：默认位置可能落副屏/被控制台遮挡，
# ImageGrab 采到的就是别的窗口（边线色断言曾因此逐次漂移）
root.geometry("+80+60")
root.update_idletasks()
root.update()

fails = []


def check(name, ok):
    print("%s %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        fails.append(name)


def warn(name, ok):
    """环境敏感的逐像素采样：结果只提示，不进失败清单（受缩放混合
    相位与截屏坐标偏差影响，时好时坏；设计意图由配置级断言守护）。"""
    print("%s %s" % ("PASS" if ok else "WARN", name))


# --- 长歌名不截断：两列表行内不出现省略号，完整歌名在列 ---
lib_texts = [app.lib.get(i) for i in range(app.lib.size())]
pl_texts = [app.pl.get(i) for i in range(app.pl.size())]
check("素材库无省略号", all("…" not in t for t in lib_texts))
check("播放列表无省略号", all("…" not in t for t in pl_texts))
check("长歌名完整在列",
      any("Drown out the noise" in t for t in lib_texts)
      and any("Drown out the noise" in t for t in pl_texts))


def fits(btn):
    f = tkfont.Font(font=btn["font"])
    return f.measure(btn["text"]) <= btn.winfo_width() - 4


# --- 编排组：四按钮等宽且文字不裁剪 ---
ws = [app.btn_add.winfo_width(), app.btn_remove.winfo_width(),
      app.btn_up.winfo_width(), app.btn_down.winfo_width()]
check("编排组等宽 %s" % ws, len(set(ws)) == 1)
check("编排组文字不裁剪", all(fits(b) for b in
                           (app.btn_add, app.btn_remove, app.btn_up,
                            app.btn_down, app.btn_clear)))
# --- 播放组 ---
check("播放组文字不裁剪", all(fits(b) for b in app.tbtns.values())
      and fits(app.btn_panic))
# --- 绿色开始与同级按钮等大（强调只改色不改尺寸）---
check("开始与同级等高",
      app.tbtns["开始"].winfo_height() == app.tbtns["暂停"].winfo_height()
      and app.tbtns["开始"].winfo_height() == app.btn_panic.winfo_height())
# --- 自动化组 / 播放配置行：找按钮 ---
auto_btns = [w for w in root.winfo_children()]
g3_btns = [c for c in app.root.winfo_children()]


def find_buttons(win, texts):
    out = {}
    stack = [win]
    while stack:
        w = stack.pop()
        for c in w.winfo_children():
            stack.append(c)
            if isinstance(c, tk.Button) and c["text"] in texts:
                out[c["text"]] = c
    return out


btns = find_buttons(root, {"键盘自动化", "踩钉控制", "写入时长", "重新识别",
                           "设置", "退出", "清空日志"})
check("自动化组等宽",
      btns["键盘自动化"].winfo_width() == btns["踩钉控制"].winfo_width())
check("自动化/配置行文字不裁剪",
      all(fits(b) for b in btns.values()))
check("配置行三按钮等宽",
      len({btns["写入时长"].winfo_width(),
           btns["重新识别"].winfo_width(),
           btns["设置"].winfo_width()}) == 1)
# --- 退出按钮：与底栏按钮等大、底边同基线、右边距同「设置」 ---
check("退出与播放组按钮等大",
      btns["退出"].winfo_width() == app.tbtns["开始"].winfo_width()
      and btns["退出"].winfo_height() == app.tbtns["开始"].winfo_height())


def bottom_edge(btn):
    return btn.winfo_rooty() + btn.winfo_height()


check("退出与播放组按钮底边对齐",
      abs(bottom_edge(btns["退出"]) - bottom_edge(app.tbtns["开始"])) <= 2)


def right_gap(btn):
    fr = btn.master
    return (fr.winfo_rootx() + fr.winfo_width()
            - btn.winfo_rootx() - btn.winfo_width())


check("退出右边距与设置按钮一致 %d vs %d"
      % (right_gap(btns["退出"]), right_gap(btns["设置"])),
      abs(right_gap(btns["退出"]) - right_gap(btns["设置"])) <= 2)
# --- 双列表间距 > 0 ---
gap = app.pl.winfo_x() - (app.lib.winfo_x() + app.lib.winfo_width())
check("素材库↔播放列表间距 %dpx" % gap, gap >= 6)
# --- 无横向滚动条 / 框线宽度统一（列表与 LabelFrame 同 1px）---
stack, sbs, bordered = [root], [], []
while stack:
    w = stack.pop()
    for c in w.winfo_children():
        stack.append(c)
        if isinstance(c, tk.Scrollbar):
            sbs.append(c)
        if isinstance(c, (tk.Listbox, tk.LabelFrame)):
            bordered.append(c)
check("主窗无横向滚动条", not sbs)
lfs = [b for b in bordered if isinstance(b, tk.LabelFrame)]
lbs = [b for b in bordered if isinstance(b, tk.Listbox)]
check("面板框线=flat 1px 环同色",
      lfs and all(int(b.cget("bd")) == 0 and b.cget("relief") == "flat"
                  and int(b.cget("highlightthickness")) == 1
                  and b.cget("highlightbackground") == sg.dpi.BORDER
                  for b in lfs) and lbs
      and all(int(b.cget("bd")) == 1 for b in lbs))
# --- 监控栏两框边缘与素材库/播放列表精确对齐 ---
vj_f = app.rows[("vj", "端口名称")].master
kb_f = app.rows[("kb", "端口名称")].master


def span(w):
    return (w.winfo_rootx(), w.winfo_rootx() + w.winfo_width())


check("VJ 框与素材库边缘对齐 %s vs %s" % (span(vj_f), span(app.lib)),
      span(vj_f) == span(app.lib))
check("键盘框与播放列表边缘对齐 %s vs %s" % (span(kb_f), span(app.pl)),
      span(kb_f) == span(app.pl))
# --- 框线颜色像素核对：面板边线与列表框边线同色（浅灰非黑） ---
try:
    from PIL import ImageGrab
    root.update_idletasks()
    root.update()

    def line_color(w):
        """控件左缘向右扫 4px，取第一个非底色像素（边线本身）。"""
        y = w.winfo_rooty() + w.winfo_height() // 2
        img = ImageGrab.grab((w.winfo_rootx(), y,
                              w.winfo_rootx() + 4, y + 1))
        for i in range(4):
            p = img.getpixel((i, 0))
            if p != (20, 21, 24):
                return p
        return None

    pline = line_color(vj_f)
    lline = line_color(app.lib)
    # 逐像素采样只作警告不作硬断言：175% 缩放下 1 逻辑px=1.75 物理px，
    # 边线渲染带混合相位，且 winfo 坐标与 ImageGrab 屏幕坐标有 2~3px
    # 系统偏差（探针实测边线在 rootx 左侧 ~3px），逐次运行漂移——
    # 边框设计的权威断言是上面的配置级检查（flat/1px/同色）
    warn("面板边线采样 帧=%s 列表=%s（应≈%s）" % (pline, lline, sg.dpi.BORDER),
         pline == lline and pline not in (None, (0, 0, 0)))

    def label_indent(frame):
        """标题文字起始像素的横向偏移（顶部 14px 内第一个非背景/非环像素）。"""
        img = ImageGrab.grab((frame.winfo_rootx(), frame.winfo_rooty(),
                              frame.winfo_rootx() + 80,
                              frame.winfo_rooty() + 14))
        for xx in range(80):
            for yy in range(14):
                r, g, b = img.getpixel((xx, yy))[:3]
                if (r, g, b) not in ((30, 32, 36), (82, 84, 87),
                                     (20, 21, 24)):
                    return xx
        return -1

    li = label_indent(vj_f)
    ni = app.rows[("vj", "端口名称")].master.grid_slaves(
        row=0, column=0)[0].winfo_rootx() - vj_f.winfo_rootx()
    # 同上：标题缩进的逐像素采样受混合相位影响（角落环像素被算进文字
    # 起点），只警告不作硬断言
    warn("面板标题缩进 %dpx ≈ 内部文字 %dpx" % (li, ni),
         5 <= li <= 12 and abs(li - ni) <= 4)
except Exception as e:
    print("像素核对跳过：%r" % e)
# --- 无箭头按钮 ---
check("控件无→/←/＝", not any(
    ch in b["text"] for b in btns.values()
    for ch in ("→", "←")) and "→" not in app.btn_add["text"])
# --- 监控行键 ---
check("OBS 状态行键", ("vj", "OBS 状态") in app.rows)
check("FOLLOW 已停止", sg.FOLLOW["stopped"] == "已停止")

# --- 本轮 UI 现代化改造断言（焦点环/悬停/圆点/进度条/当前行/空状态/日志/标题栏） ---
import ctypes

# Entry 焦点环：真实输入框有 1px 边框 + 聚焦绿环
entries = []


def collect_entries(win):
    stack = [win]
    while stack:
        w2 = stack.pop()
        for c in w2.winfo_children():
            stack.append(c)
            if isinstance(c, tk.Entry) and not isinstance(c, sg.Marquee):
                entries.append(c)


collect_entries(root)
check("输入框有焦点环 %d 个" % len(entries),
      entries and all(int(e.cget("highlightthickness")) == 1
                      and str(e.cget("highlightcolor")).lower()
                      == sg.dpi.C_OK for e in entries))
mq = (app.m_now, app.m_next, app.m_tgt, app.m_map, app.m_last)
check("五个跑马灯无焦点环",
      all(int(m.cget("highlightthickness")) == 0 for m in mq))
check("跑马灯底色随面板（无暗底带）",
      all(str(m.cget("bg")).lower() == sg.dpi.PANEL
          and str(m.cget("disabledbackground")).lower() == sg.dpi.PANEL
          for m in mq))

# 按钮悬停：Enter 提亮、Leave 还原；禁用态不响应
bg0 = str(app.btn_panic["bg"]).lower()
app.btn_panic.event_generate("<Enter>")
root.update_idletasks()
hovered = str(app.btn_panic["bg"]).lower()
app.btn_panic.event_generate("<Leave>")
root.update_idletasks()
restored = str(app.btn_panic["bg"]).lower()
check("按钮悬停变色 %s→%s" % (bg0, hovered),
      hovered == str(app.btn_panic["activebackground"]).lower()
      and hovered != bg0)
check("按钮移出还原", restored == bg0)
app.btn_add.event_generate("<Enter>")
root.update_idletasks()
check("禁用按钮悬停不变色",
      str(app.btn_add["bg"]).lower() == sg.dpi.PANEL)

# 状态圆点：状态格有 ● 前缀，名称/内容格与占位符没有
app._tick_body()
check("状态格有圆点",
      app.rows[("vj", "端口状态")].cget("text").startswith("● ")
      and app.rows[("vj", "OBS 状态")].cget("text").startswith("● "))
check("名称/内容/占位格无圆点",
      not app.rows[("vj", "端口名称")].cget("text").startswith("● ")
      and app.rows[("vj", "走带跟随")].cget("text") == "-"
      and not app.m_map.get().startswith("● "))

# 走带跟随：状态色 + 播放中带视频名（假 sync 走真实 _tick_body 渲染路径）
class _FakeSync:
    def __init__(self, state, video, on=True):
        self.video_state, self.current_video, self._on = state, video, on

    def is_following(self):
        return self._on


tl = app.rows[("vj", "走带跟随")]
app.sync = _FakeSync("stopped", None, on=False)
app._tick_body()
check("走带跟随未启用=黄",
      tl.cget("text") == "● 未启用（未收到时钟）"
      and str(tl.cget("fg")).lower() == sg.dpi.C_WARN)
app.sync = _FakeSync("playing", "1 开场.mp4")
app._tick_body()
check("走带跟随播放中=绿+视频名",
      tl.cget("text") == "● 播放中：1 开场.mp4"
      and str(tl.cget("fg")).lower() == sg.dpi.C_OK)
app.sync.video_state = "paused"
app._tick_body()
check("走带跟随暂停=黄+视频名",
      tl.cget("text") == "● 已暂停：1 开场.mp4"
      and str(tl.cget("fg")).lower() == sg.dpi.C_WARN)
app.sync.video_state, app.sync.current_video = "stopped", None
app._tick_body()
check("走带跟随停止=灰无名",
      tl.cget("text") == "● 已停止" and str(tl.cget("fg")).lower() == sg.dpi.MUT)
app.sync = None          # 还原：后续段落沿用「无同步」的原始路径
app._tick_body()

# 进度条：直接驱动 _progress 做单元断言（离线无真实工程窗口，_tick_banner
# 走不到「有当前曲」分支）；首拍挂载时宽度未布局只挂不画，次拍出矩形
app._progress(0.45)
root.update_idletasks()
root.update()
app._progress(0.45)
root.update_idletasks()
root.update()
check("进度条显示且有填充",
      app.prog.winfo_ismapped() and len(app.prog.find_all()) == 1)
check("进度条横贯顶部 %d≈%d" % (app.prog.winfo_width(),
                              app.prog.master.winfo_width()),
      abs(app.prog.winfo_width() - app.prog.master.winfo_width()) <= 2)
bar = app.prog.find_all()
check("进度条填充比例",
      bool(bar) and abs(app.prog.coords(bar[0])[2]
                        - app.prog.winfo_width() * 0.45) <= 2)
app._progress(None)
root.update_idletasks()
check("无进度时隐藏", not app.prog.winfo_ismapped())
# 截图前模拟「工程已打开、走带 60s」：NOW/NEXT/进度条按真实路径渲染
import types
sg.cubase_ctrl.current_project = lambda: (None, "Cubase Pro 工程 - SongA")
app.ctrl = types.SimpleNamespace(busy=False)
app.watch = types.SimpleNamespace(active=lambda: 60.0)
app._tick_body()
root.update_idletasks()
root.update()
app._tick_body()
root.update_idletasks()
root.update()
check("横幅模拟态渲染",
      app.now_lbl.get() == "SongA" and app.prog.winfo_ismapped())

# 当前行底色
check("当前行底色高亮",
      app.pl.itemcget(0, "background") == sg.dpi.CUR_BG
      and app.pl.itemcget(1, "background") != sg.dpi.CUR_BG)

# 空状态提示：清空出现、恢复消失（先存现场）
saved_keys, saved_cur = app.pl_keys, app.cur
app.pl_keys, app.cur = [], None
app._refresh()
root.update_idletasks()
check("空状态提示出现", app.pl_empty.winfo_ismapped())
app.pl_keys, app.cur = saved_keys, saved_cur
app._refresh()
root.update_idletasks()
check("空状态提示消失", not app.pl_empty.winfo_ismapped())

# 日志降噪：日志暗一档，素材库仍主文字色
check("日志文字弱化",
      str(app.log.cget("fg")).lower() == sg.dpi.LOG_FG
      and str(app.lib.cget("fg")).lower() == sg.dpi.FG)

# 深色标题栏：重复调用不抛异常；Win11 可回读圆角偏好
try:
    sg.dpi.dark_title(root)
    check("dark_title 重复调用安全", True)
except Exception:
    check("dark_title 重复调用安全", False)
try:
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    v = ctypes.c_int(-1)
    r = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 33, ctypes.byref(v), 4)
    check("圆角偏好已写入 (ret=%s val=%d)" % (r, v.value),
          r == 0 and v.value == 2)
except Exception as e:
    print("圆角回读跳过：%r" % e)
# --- 断言完，现场已还原，继续截图 ---


# --- 截图 ---
WINDOWS = [root]


def shot(win, name):
    from PIL import ImageGrab
    win.deiconify()
    win.geometry("+80+60")
    for other in WINDOWS:
        if other is not win and other.winfo_exists():
            other.withdraw()
    win.update_idletasks()
    win.update()
    time.sleep(0.25)        # 等 WM 完成 hide/show 与底层重绘，防上一窗残影
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab((x, y, x + w, y + h))
    out = pathlib.Path("_render") / ("%s.png" % name)
    out.parent.mkdir(exist_ok=True)
    img.save(out)
    print("SHOT", out)


shot(root, "main")

# --- 主窗像素级断言（截图内容 = 主窗本身）---
try:
    from PIL import Image

    img = Image.open(pathlib.Path("_render") / "main.png").convert("RGB")
    px = img.load()
    greens = reds = 0
    for yy in range(0, img.height, 2):
        for xx in range(0, img.width, 2):
            r, g, b = px[xx, yy]
            if abs(r - 0x5a) < 12 and abs(g - 0xd4) < 12 \
                    and abs(b - 0x69) < 12:
                greens += 1          # 开始/保存主操作绿
            elif abs(r - 0xa0) < 14 and abs(g - 0x30) < 14 \
                    and abs(b - 0x30) < 14:
                reds += 1            # 全停红
    check("主窗含绿色主操作区（%d px）" % greens, greens > 40)
    check("主窗含红色全停区（%d px）" % reds, reds > 20)
    # 双列表之间的间隔列应为窗口底色（#141518），证明两列表没有贴死
    gy = app.lib.winfo_rooty() - root.winfo_rooty() + app.lib.winfo_height() // 2
    gx = app.pl.winfo_x() - 4        # pl 左缘往左 4px 落在间隔里
    r, g, b = px[gx, gy]
    dark_bg = abs(r - 0x14) < 10 and abs(g - 0x15) < 10 and abs(b - 0x18) < 10
    check("列表间隔列=窗口底色 RGB(%d,%d,%d)" % (r, g, b), dark_bg)
except Exception as e:
    print("像素断言跳过：%r" % e)

# --- 弹窗 ---
sw = sg.SettingsWindow(app)
WINDOWS.append(sw)
root.update_idletasks(); root.update()
pw = pedal.PedalWindow(app)
WINDOWS.append(pw)
root.update_idletasks(); root.update()
kw = kbd_auto.KeyboardAutoWindow(app)
WINDOWS.append(kw)
kw.set_song(app.by_key["TeamA/SongA"])
root.update_idletasks(); root.update()

pbtns = find_buttons(pw, {"学习", "清除"})
check("踩钉窗学习/清除等宽",
      pbtns["学习"].winfo_width() == pbtns["清除"].winfo_width())
kbtns = find_buttons(kw, {"录制", "触发", "清除"})
check("键盘窗三按钮等宽", len({b.winfo_width() for b in kbtns.values()}) == 1)
check("两窗操作按钮同宽",
      pbtns["学习"].winfo_width() == kbtns["录制"].winfo_width())
check("键盘窗文字不裁剪", all(fits(b) for b in kbtns.values()))
check("键盘窗提示语无 BS/PC", "BS/PC" not in kw.winfo_children()[0]["text"])
port_texts = [l["text"] for l in kw._port_lbl.values()]
check("键盘窗端口行无设备前缀",
      len(kw._port_lbl) == 2
      and all(t.startswith("输入") for t in port_texts))
check("键盘窗只显示当前乐器端口行",
      kw._port_lbl["juno"].winfo_ismapped()
      and not kw._port_lbl["ax"].winfo_ismapped())
check("键盘窗绑定后状态行隐藏",
      not kw.status.winfo_ismapped()
      and kw.title() == "键盘自动化")

# --- 两窗第二列表头与数据列文字左缘对齐（数据列有 padx=(8,8) 左缩进） ---
kb_hdr = kw._slot_lbl[kbd_auto.SLOT_NOTES[0]].master.grid_slaves(
    row=0, column=1)[0]
check("键盘窗音色映射表头对齐",
      kb_hdr.winfo_rootx() == kw._slot_lbl[kbd_auto.SLOT_NOTES[0]]
      .winfo_rootx())
pd_first = pw._bind_lbl[pedal.ACTIONS[0][0]]
pd_hdr = pd_first.master.grid_slaves(row=0, column=1)[0]
check("踩钉窗绑定表头对齐", pd_hdr.winfo_rootx() == pd_first.winfo_rootx())

# --- 三子窗口：最小尺寸已设，且缩到最小时内容完整 ---
for name, win in (("设置", sw), ("踩钉", pw), ("键盘", kw)):
    mw, mh = win.wm_minsize()
    check("%s窗最小尺寸 %dx%d" % (name, mw, mh), mw > 0 and mh > 0)
    orig = win.geometry()
    win.geometry("%dx%d" % (mw, mh))
    win.update_idletasks()
    win.update()
    ok, stack = True, [win]
    wx, wy = win.winfo_rootx(), win.winfo_rooty()
    ww, wh = win.winfo_width(), win.winfo_height()
    while stack:
        w2 = stack.pop()
        for c in w2.winfo_children():
            stack.append(c)
            if c.winfo_ismapped() and (
                    c.winfo_rootx() + c.winfo_width() > wx + ww + 2
                    or c.winfo_rooty() + c.winfo_height() > wy + wh + 2):
                ok = False
    check("%s窗最小尺寸下内容完整" % name, ok)
    win.geometry(orig)
    win.update_idletasks()
    win.update()

# --- 三子窗口：四边留白随 DPI 缩放（回归：pack 裸像素边距高分屏下顶满） ---
for name, win in (("设置", sw), ("踩钉", pw), ("键盘", kw)):
    wx, wy = win.winfo_rootx(), win.winfo_rooty()
    l = t = r = b = 10 ** 6
    stack = [win]
    while stack:
        w2 = stack.pop()
        for c in w2.winfo_children():
            stack.append(c)
            if c.winfo_ismapped():
                l = min(l, c.winfo_rootx() - wx)
                t = min(t, c.winfo_rooty() - wy)
                r = min(r, wx + win.winfo_width()
                        - c.winfo_rootx() - c.winfo_width())
                b = min(b, wy + win.winfo_height()
                        - c.winfo_rooty() - c.winfo_height())
    floor = sg.dpi.scale(win, 6)
    check("%s窗四边留白≥%dpx（左%d 上%d 右%d 下%d）"
          % (name, floor, l, t, r, b), min(l, t, r, b) >= floor)

# --- 三子窗：文字/容器底色=窗口底色（回归：darkify 的 PANEL 色斑） ---
def flat_ok(win):
    stack = [win]
    while stack:
        w2 = stack.pop()
        for c in w2.winfo_children():
            stack.append(c)
            if isinstance(c, (tk.Frame, tk.Label, tk.Checkbutton)) \
                    and str(c.cget("bg")).lower() != sg.dpi.BG:
                return c
    return None

for name, win in (("设置", sw), ("踩钉", pw), ("键盘", kw)):
    bad = flat_ok(win)
    check("%s窗无面板色斑" % name if bad is None
          else "%s窗无面板色斑（%s 仍 %s）" % (name, bad, bad.cget("bg")),
          bad is None)

# --- 设置窗：主/次按钮等大、仅颜色区分 ---
sbtns = find_buttons(sw, {"保存并应用", "取消"})
check("设置窗主次按钮等大",
      sbtns["保存并应用"].winfo_width() == sbtns["取消"].winfo_width()
      and sbtns["保存并应用"].winfo_height() == sbtns["取消"].winfo_height())

try:
    shot(sw, "settings")
    shot(pw, "pedal")
    shot(kw, "kbd")
except Exception as e:
    print("截图跳过：%r" % e)

print("FAILS:", fails if fails else "无")
for w in (sw, pw, kw):
    try:
        w.destroy()
    except Exception:
        pass
root.destroy()
