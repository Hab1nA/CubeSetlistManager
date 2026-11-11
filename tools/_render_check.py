# -*- coding: utf-8 -*-
"""报告三/四整改的几何验证（离线渲染，无真服务）：
1) 同组按钮等宽、文字不裁剪；2) 列表间距>0；3) 截图五窗 PNG 供人工核对。"""
import pathlib
import struct
import sys
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
import tkinter.ttk as ttk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import setlist_gui as sg
sg.dpi.enable()          # 与真机一致：DPI 感知后字体/像素按真实缩放渲染

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
sg.dpi.apply_theme(root)   # sv-ttk dark+命名 style（与真机 main() 同入口）
root.geometry("+80+60")
root.attributes("-topmost", True)   # 独占屏幕：防控制台/光标污染像素采样

WINDOWS = []          # 尾部统一销毁


def shot(win, name):
    from PIL import ImageGrab
    win.deiconify()
    wh = win.geometry().split("+", 1)[0]
    if "x" not in wh:
        wh = "%dx%d" % (win.winfo_reqwidth(), win.winfo_reqheight())
    win.geometry("%s+80+60" % wh)
    root.update_idletasks()
    root.update()
    time.sleep(0.15)
    img = ImageGrab.grab()
    img.save(pathlib.Path("_render", "%s.png" % name))
    print("SHOT _render/%s.png" % name)




def classic_strays(win):
    """classic 控件残留清点（按 ttk.Widget 排除——py3.14 里 ttk.Combobox
    仍是 tk.Entry 子类，须挡住误报）。"""
    bad = []
    stack = [win]
    while stack:
        w3 = stack.pop()
        for c in w3.winfo_children():
            stack.append(c)
            if isinstance(c, (tk.Frame, tk.Label, tk.Button, tk.LabelFrame,
                              tk.Entry, tk.Checkbutton, tk.OptionMenu))                     and not isinstance(c, ttk.Widget):
                bad.append(c)
    return bad

fails = []

def check(name, ok_):
    print("%-34s %s" % (name, "PASS" if ok_ else "FAIL"))
    if not ok_:
        fails.append(name)


# --- automator（ttk + sv-ttk 迁移窗）：主题已在前文统一挂载（对本解释器
# 全局生效），此处直接建窗 ---
import json
import automator_gui as am
am._HERE = d
am.CONFIG_PATH = d / "config.json"
am.App._startup = lambda self: None      # 离线：不起 OBS/网页/工程库扫描
aw = tk.Toplevel(root)                   # automator 主窗（Toplevel 同根主题）
aapp = am.App(aw)
root.update_idletasks(); root.update()
aset = am.SettingsWindow(aapp)
WINDOWS += [aw, aset]
root.update_idletasks(); root.update()

# 迁移完整性：不得残留任何 classic 控件（py3.14 起 ttk 组件不再是
# classic 类的子类，唯 Combobox 仍是 tk.Entry 子类——classic_strays 内
# 已按 ttk.Widget 排除）


check("automator主窗无 classic 控件残留",
      not classic_strays(aw)
      and aapp.btn_black.winfo_class() == "TButton"
      and aapp.state_lbl.winfo_class() == "TLabel")
check("automator设置页无 classic 控件残留", not classic_strays(aset))
check("保存钮=Start.TButton 熄屏钮=Stop.TButton",
      aset.save_btn.cget("style") == "Start.TButton"
      and aapp.btn_black.cget("style") == "Stop.TButton")

# 下拉：全部 readonly Combobox（6 个：显示位置+三联动口+翻谱口+键盘口；
# py3.14 cget 返回 Tcl 对象，须 str() 归一）
cbs = []
stack = [aset]
while stack:
    w3 = stack.pop()
    for c in w3.winfo_children():
        stack.append(c)
        if c.winfo_class() == "TCombobox":
            cbs.append(c)
check("设置页下拉 %d 个全 readonly" % len(cbs),
      len(cbs) == 6 and all(str(c.cget("state")) == "readonly" for c in cbs))
check("tone 状态色→词干",
      sg.dpi.tone(sg.dpi.C_OK) == "Ok" and sg.dpi.tone(sg.dpi.C_WARN) == "Warn"
      and sg.dpi.tone(sg.dpi.C_ERR) == "Err" and sg.dpi.tone(sg.dpi.MUT) == "Dim")
aapp._set(("vj", "OBS 状态"), "未连接", sg.dpi.C_ERR)
aapp._set(("vj", "端口名称"), "—")
check("状态格改色=改 style",
      aapp.rows[("vj", "OBS 状态")].cget("style") == "Err.TLabel"
      and aapp.rows[("vj", "端口名称")].cget("style") == "Dim.TLabel")

# 跑马灯：ttk Entry + 专用无边框 style；状态色切换 style 而非 fg
check("跑马灯=ttk Entry 专用 style 只读",
      isinstance(aapp.m_now, am.Marquee)
      and aapp.m_now.winfo_class() == "TEntry"
      and str(aapp.m_now.cget("style")).startswith("Marquee")
      and str(aapp.m_now.cget("state")) == "disabled")
aapp.m_now.set("SongA", sg.dpi.C_OK)
check("跑马灯状态色切 style",
      aapp.m_now.cget("style") == "MarqueeOk.TEntry"
      and not hasattr(aapp.m_now, "NO_RING"))

# 日志 Listbox 保留 classic，显式配色对齐新主题
check("日志仍是 classic Listbox 显式配色",
      isinstance(aapp.log, tk.Listbox)
      and str(aapp.log.cget("bg")).lower() == sg.dpi.FIELD
      and str(aapp.log.cget("fg")).lower() == sg.dpi.LOG_FG
      and int(aapp.log.cget("highlightthickness")) == 0)

# _tick_body 冒烟：q→日志排空路径确定性地走一遍（时序无关）
akw = am.KeyboardAutoWindow(aapp)
WINDOWS.append(akw)
akw.set_song({"name": "SongA", "path": str(
    lib / "TeamA" / "SongA" / "SongA.cpr"), "team": "TeamA", "key":
    "TeamA/SongA"})
root.update_idletasks(); root.update()
check("键盘自动化窗可建（迁移后归宿）",
      akw.winfo_exists() and akw.title() == "键盘自动化")
aapp.q.put("离线冒烟探针")
aapp._tick_body()
root.update_idletasks(); root.update()
check("automator _tick_body 冒烟（日志实刷）",
      any("离线冒烟探针" in aapp.log.get(i) for i in range(aapp.log.size())))
# 音色映射格无标题识别时须有占位（该格是 Marquee：曾误走 _set 的
# config(text=)，对 ttk.Entry 静默无效、格内恒空）
check("音色映射空态占位（Marquee.set 路径）",
      aapp.m_map.get() == "（未加载工程）")

# 迁移窗源码不得再引用 classic 主题 token（五文件终态门禁）
for _fname in ("setlist_gui.py", "dpi.py", "automator_gui.py",
               "pedal.py", "kbd_auto.py"):
    _src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        _fname).read_text(encoding="utf-8")
    check("%s 源码无 classic 主题残留" % _fname,
          not any(t in _src for t in (
              "darkify", "flatten(", "NO_RING", "tk.OptionMenu",
              "activebackground", "selectcolor", "insertbackground",
              "disabledforeground", "readonlybackground")))

try:
    shot(aw, "auto_main")
    shot(aset, "auto_settings")
except Exception as e:
    print("截图失败：%r" % e)
    fails.append("automator 截图失败")

# 截图清单：文件名 → 窗口中文名（人工核对索引）
manifest = {"main": "setlist主窗",
            "auto_main": "automator主窗", "auto_settings": "automator设置页"}
pathlib.Path("_render", "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8")
print("SHOT _render/manifest.json")

print("FAILS:", fails if fails else "无")
root.destroy()
sys.exit(1 if fails else 0)
