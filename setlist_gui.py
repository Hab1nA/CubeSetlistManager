# -*- coding: utf-8 -*-
"""Cube Setlist Manager：素材库选歌编排播放列表 + Cubase 工程"先关后开"切换 +
播完自动推进 + 键盘自动化联动 + VJ 视频联动（桥文件零改动）。
界面分上下两段：上方=素材库/播放列表 + VJ 自动化/键盘自动化监控栏 + 日志（固定高）；
下方控制区两行——行1(上)=播放配置独占整行（配置目标=标蓝选中曲/时长设定/设置）；
行2=编排（加入/移除/上移/下移/清空，随标蓝启停）+播放（开始=回零从头播/暂停/
继续=从当前位置播/回零/上一首/下一首/全停）+自动化（键盘自动化/踩钉控制）居中，
退出靠右。
切歌=CubaseController.switch_to（关闭当前工程[自动保存]→打开下一个），
只认播放列表里的歌；自动推进=AdvanceWatch 累计走带活跃时长≥工程时长×95%
且时钟断流。工程时长：启动时后台全量重探（自动来源；手动设定不覆盖）+
打开工程时重测，持久化在 playlist.json，未设定位条的用「写入时长」手填。
直接 `python setlist_gui.py` 运行，或 PyInstaller 打包 exe（config.json、
playlist.json 与 exe 同目录）。"""
import ctypes
import faulthandler
import json
import os
import pathlib
import socket
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

import advance
import cubase_ctrl
import cpr_meta
import dpi
import hotspot
import kbd_auto
import midi_bridge as mb
import pedal
import web_remote
from obs_ctrl import ObsController, natural_key, find_processes_by_prefix, \
    launch_detached, list_screens, close_obs_app, close_loopmidi

FOLLOW = {"playing": "播放中", "paused": "已暂停", "stopped": "已停止"}
LOG_MAX = 2000       # 日志行数上限：长演出防列表无限增长拖慢刷新


def _find_cubase_exe():
    r"""扫 Program Files\Steinberg 各版本目录，取版本号最高的 Cubase<N>.exe
    （N 取自 exe 文件名，兼容「Cubase Pro 13」式目录名；升级 Cubase 无需改路径）。"""
    base = r"C:\Program Files\Steinberg"
    try:
        dirs = os.listdir(base)
    except OSError:
        return None
    best = None
    for d in dirs:
        if "cubase" not in d.lower():
            continue
        try:
            exes = os.listdir(os.path.join(base, d))
        except OSError:
            continue
        for e in exes:
            m = re.fullmatch(r"Cubase(\d+)\.exe", e)
            if m and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), os.path.join(base, d, e))
    return best[1] if best else None


DEFAULT_CUBASE_EXE = (_find_cubase_exe()
                      or r"C:\Program Files\Steinberg\Cubase 15\Cubase15.exe")
DEFAULT_PROJECTS_ROOT = r"C:\Users\XKZ\Documents\Cubase Projects"

if getattr(sys, "frozen", False):   # PyInstaller exe：数据文件放 exe 同目录
    _HERE = pathlib.Path(sys.executable).resolve().parent
else:
    _HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = _HERE / "config.json"
PLAYLIST_PATH = _HERE / "playlist.json"
_CRASH_LOG = None                  # faulthandler 落盘句柄，防 GC


def _check_in_here(p):
    """写入前校验：规范化后必须仍落在程序目录内（防路径越界）。"""
    p = pathlib.Path(p).resolve()
    if p.parent != _HERE:
        raise ValueError("数据文件路径越界：%s" % p)
    return p


def _load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            return {}        # 文件损坏：降级为默认并让界面能起来
    return {}


def _atomic_write(path, text):
    """先写临时文件再原子替换，避免断电/崩溃截断数据文件。"""
    p = _check_in_here(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(p))


def _save_config(cfg):
    _atomic_write(CONFIG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2))


def _clip(s, n=26):
    """监控栏单元格防溢出：超长截断加省略号。"""
    return s if len(s) <= n else s[:n - 1] + "…"


def _err(e):
    """异常进日志的统一格式：不用 repr（会带 Python 引号转义），
    消息为空时退回异常类名，保证始终有可读信息。"""
    return str(e).strip() or type(e).__name__


def _load_playlist():
    """{"playlist": [key…], "durations": {key: 秒}, "durSrc": {key: manual}}
    durSrc 里登记的是手动设定；不在表里=自动识别；无时长=未知。
    兼容旧 schema 的 order 键。"""
    if PLAYLIST_PATH.exists():
        try:
            with open(PLAYLIST_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            data = {}        # 损坏降级：空播放列表（durations 启动探测会重建）
        keys = data.get("playlist") or data.get("order") or []
        return {"playlist": [str(k) for k in keys],
                "durations": {k: float(v)
                              for k, v in data.get("durations", {}).items()
                              if isinstance(v, (int, float))},
                "durSrc": {k: "manual"
                           for k, v in data.get("durSrc", {}).items()
                           if v == "manual"}}
    return {"playlist": [], "durations": {}, "durSrc": {}}


def save_playlist(keys, durations, dur_src):
    data = json.dumps({"playlist": keys, "durations": durations,
                       "durSrc": dur_src},
                      ensure_ascii=False, indent=2)
    _atomic_write(PLAYLIST_PATH, data)


def scan_library(root):
    """工程库：<root>/<队伍>/<歌>/<歌>.cpr → [{key, team, name, path}]。"""
    base = pathlib.Path(root).resolve()
    out = []
    for team in sorted(os.listdir(base), key=natural_key):
        if not team or team.startswith("."):
            continue
        tdir = base / team
        if not tdir.is_dir():
            continue
        for name in sorted(os.listdir(tdir), key=natural_key):
            if not name or name.startswith("."):
                continue
            path = tdir / name / (name + ".cpr")
            if path.exists():
                out.append({"key": "%s/%s" % (team, name), "team": team,
                            "name": name, "path": str(path)})
    return out


def _reprobe(songs, durations, dur_src):
    """自动来源的工程时长全量重读 .cpr → {key: 新秒}（只收值有变化的）。
    手动设定不参与（「写入时长」受保护，要回工程内原值用「重新识别」）。"""
    out = {}
    for s in songs:
        if dur_src.get(s["key"]) == "manual":
            continue
        d = cpr_meta.read_duration(s["path"]) or 0.0
        if d != durations.get(s["key"]):
            out[s["key"]] = d
    return out


class Marquee(tk.Entry):
    """跑马灯：只读 Entry + xview 像素滚动（Tk 原生裁剪，经典做法）。
    文本超宽才滚动、往返循环；文字未变化时重复 set 不打断滚动。
    Entry 定宽（字符）兜底防撑破布局，实际宽度随布局伸展。
    NO_RING：darkify 的 Entry 豁免标记——展示型组件保持无边框、底色
    随所在面板（非输入框色）。"""

    NO_RING = True
    STEP_MS = 40
    SPEED = 3.0             # 滚动速度：字符/秒（按字体像素精确换算）
    PAUSE_TICKS = 8         # 滚到头/回到起点各停顿 ~0.3 秒

    def __init__(self, master, max_chars=30, **kw):
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("justify", "left")
        kw.setdefault("takefocus", False)
        kw.setdefault("bg", dpi.PANEL)          # 展示型组件：底色随所在面板
        kw.setdefault("fg", dpi.FG)
        kw.setdefault("disabledbackground", dpi.PANEL)
        kw.setdefault("disabledforeground", dpi.FG)
        super().__init__(master, **kw)
        self.max_chars = max_chars
        self._full = ""
        self._frac = 0.0
        self._dir = 1
        self._hold = 0
        self._job = None
        self._font = tkfont.Font(font=self["font"])
        self.config(state="disabled")

    def set(self, text, fg=None):
        if text != self._full:              # 文字没变就不归零偏移（关键）
            self._full = text
            self.config(state="normal")
            self.delete(0, "end")
            self.insert(0, text)
            self.config(state="disabled")
            self.xview_moveto(0)
            self._frac = 0.0
            self._dir = 1
            self._hold = self.PAUSE_TICKS   # 起始停一拍，先看清开头
        if fg:
            self.config(fg=fg, disabledforeground=fg)
        self._schedule()

    def _overflow(self):
        w = self.winfo_width()
        if w <= 1:                          # 未布局：退回字符数判定
            return len(self._full) > self.max_chars
        return self._font.measure(self._full) > w - 8

    def _schedule(self):
        if self._overflow() and self._job is None:
            self._job = self.after(self.STEP_MS, self._step)

    def _step(self):
        if not self._overflow():
            self._cancel()
            self.xview_moveto(0)
            return
        total = max(1, self._font.measure(self._full))
        span = max(0.0, 1.0 - self.winfo_width() / total)   # 可滚动比例
        cpx = total / max(1, len(self._full))               # 平均字符像素
        if self._hold > 0:                  # 到头/回起点停一拍，便于阅读
            self._hold -= 1
        else:
            if self._frac >= span and self._dir == 1:
                self._dir = -1              # 滚到尾：翻向并停一拍
                self._hold = self.PAUSE_TICKS
            elif self._frac <= 0 and self._dir == -1:
                self._dir = 1               # 滚回头：翻向并停一拍
                self._hold = self.PAUSE_TICKS
            else:
                self._frac = min(span, max(
                    0.0, self._frac
                    + self._dir * self.SPEED * cpx * self.STEP_MS / 1000
                    / total))
                self.xview_moveto(self._frac)
        self._job = self.after(self.STEP_MS, self._step)

    def _cancel(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


class App:
    def __init__(self, root):
        self.root = root
        root.title("Cube Setlist Manager")
        root.geometry(dpi.scale(root, 980, 840))
        root.minsize(dpi.scale(root, 940), dpi.scale(root, 760))
        cfg = _load_config()
        self.ccfg = dict(projectsRoot=DEFAULT_PROJECTS_ROOT,
                         cubaseExe=DEFAULT_CUBASE_EXE, autoSave=True)
        self.ccfg.update(cfg.get("cubase") or {})
        if self.ccfg["cubaseExe"] and not os.path.exists(self.ccfg["cubaseExe"]):
            # 配置钉的路径已不存在（升级 Cubase/换机）→ 回退自动探测，防静默失效
            self.ccfg["cubaseExe"] = DEFAULT_CUBASE_EXE
        self.auto_advance = bool(cfg.get("autoAdvance", False))
        self.cont_play = bool(cfg.get("autoPlay", False))  # 连续播放：切完自动起播
        self.top_most = bool(cfg.get("topMost", True))     # 保持软件前台
        self.switch_confirm = bool(cfg.get("switchConfirm", True))  # 切歌需确认
        self.exit_close_apps = bool(cfg.get("exitCloseApps"))  # 退出连带关被控软件
        # 端口提示名：空串=停用该联动（设置页下拉「无」）；缺键才回默认
        self.vj_hint = (mb.PORT_HINT if cfg.get("vjPortHint") is None
                        else str(cfg["vjPortHint"]))
        self.kb_hint = (kbd_auto.KB_PORT_HINT if cfg.get("kbPortHint") is None
                        else str(cfg["kbPortHint"]))
        self.settings_win = None
        self.jcfg = dict(kbd_auto.DEFAULT_JUNO)
        self.jcfg.update(cfg.get("juno") or {})
        self.axcfg = dict(kbd_auto.DEFAULT_AX)
        self.axcfg.update(cfg.get("ax09") or {})
        self.pedal_hint, self.pedal_binds = pedal.load_binding(cfg)
        self._pedal_retry = 0.0
        # 移动端遥控（webRemote 段；web 实例在 _startup 里起）
        self.web_cfg = dict(web_remote.DEFAULT_WEB_REMOTE)
        self.web_cfg.update(cfg.get("webRemote") or {})
        self.web = None
        self._web_snap = {}     # /state 快照（_tick_banner 每 400ms 重建）
        pl = _load_playlist()
        self.pl_keys = pl["playlist"]   # 播放列表（用户编排，持久化）
        self.durations = pl["durations"]
        self.dur_src = pl["durSrc"]     # "manual"=手填；不在表=自动；无时长=未知
        self.songs = []                 # 素材库全量
        self._lib_query = ""            # 素材库搜索词（小写）
        self.by_key = {}
        self.cur = None                 # 播放列表下标：已请求加载的项
        self._play_after_switch = False  # 切换完成后是否自动开始播放
        self._pending_switch = None     # 切换中用户点的下一首（完成后执行）
        self._switch_key = None         # 进行中切换的 key 快照
        self._kb_warned = set()         # 未配置音色音符的告警去重
        self.kb_err = ""                # 键盘自动化停用原因（监控栏显示）
        self._kb_last = ("待机", dpi.MUT)  # 最近一次音色切换结果（监控栏显示）
        self._sel_lock = False          # 双列表互斥选中的防重入标记
        self.slots = {}                 # 已加载工程：JUNO 映射（音符 60-69）
        self.ax_slots = {}              # 已加载工程：AX-09 映射（音符 72-77）
        self.q = queue.Queue()          # 日志/事件
        self.calls = queue.Queue()      # 跨线程 GUI 调用
        self._persist_lock = threading.Lock()   # 主/切歌/启动三线程共用写播放列表
        root.report_callback_exception = self._on_ui_error
        self.ctl = self.sync = self.port = self.ctrl = self.watch = None
        self.switcher = self.ax_switcher = self.kb_port = self.kbd_win = None
        self.juno_shift = 0             # JUNO 全局移调累计值（半音，±24）
        self.pedal_held = {}            # 延音踏板键按住计数（kbd_auto.PEDAL_NOTES）
        self.pedal = None
        self.pedal_win = None
        self.start_err = ""
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_exit)
        threading.Thread(target=self._startup, daemon=True).start()
        root.after(400, self._tick)

    # ---- 界面 ----

    def _build(self):
        # ---- 顶：NOW/NEXT 横幅（演出中最重要的信息，全窗最醒目）----
        banner = tk.Frame(self.root)
        banner.pack(fill="x", padx=12, pady=(10, 4))
        banner.columnconfigure(0, weight=1, uniform="b")   # NOW/NEXT 平分宽度
        banner.columnconfigure(1, weight=1, uniform="b")
        tk.Label(banner, text="NOW",
                 font=("Microsoft YaHei UI", 9, "bold"),
                 anchor="w").grid(row=0, column=0, sticky="w")
        self.now_lbl = Marquee(banner, max_chars=30,
                               font=("Microsoft YaHei UI", 20, "bold"))
        self.now_lbl.grid(row=1, column=0, sticky="ew")
        tk.Label(banner, text="NEXT",
                 font=("Microsoft YaHei UI", 9, "bold"),
                 anchor="w").grid(row=0, column=1, sticky="w", padx=(24, 0))
        self.next_lbl = Marquee(banner, max_chars=30,
                                font=("Microsoft YaHei UI", 13))
        self.next_lbl.grid(row=1, column=1, sticky="ew", padx=(24, 0))
        self.remain_lbl = tk.Label(banner, text="",
                                   font=("Microsoft YaHei UI", 14, "bold"),
                                   anchor="e")
        self.remain_lbl.grid(row=1, column=2, sticky="e")
        # 歌曲进度条（横贯整个顶部、歌名行下方 4px 细条）：远距一眼读
        # 进度，数字只是补充；无当前曲/无时长时隐藏（grid_remove 记住
        # 布局，恢复即原位）
        self.prog = tk.Canvas(banner, height=dpi.scale(self.root, 4),
                              highlightthickness=0, bg=dpi.FIELD)
        self.prog.grid(row=2, column=0, columnspan=3, sticky="ew",
                       pady=(3, 0))
        self.prog.grid_remove()
        # ---- 上：素材库 / 播放列表 ----
        pane = tk.Frame(self.root)
        pane.pack(fill="both", expand=True, padx=12, pady=4)
        pane.columnconfigure(0, weight=1, uniform="p")
        pane.columnconfigure(1, minsize=dpi.scale(self.root, 8))  # 组间距 8
        pane.columnconfigure(2, weight=1, uniform="p")
        pane.rowconfigure(1, weight=1)      # 列表随窗口纵向拉伸，不留空带
        libtop = tk.Frame(pane)
        libtop.grid(row=0, column=0, sticky="ew")
        tk.Label(libtop, text="素材库", anchor="w").pack(side="left")
        self.lib_q = tk.StringVar()
        q_ent = tk.Entry(libtop, textvariable=self.lib_q, width=12)
        q_ent.pack(side="right")
        tk.Label(libtop, text="搜索").pack(side="right", padx=(0, 4))
        q_ent.bind("<FocusIn>", lambda _e: q_ent.select_range(0, "end"))
        self.lib_q.trace_add("write", lambda *_: self._on_search())
        tk.Label(pane, text="播放列表", anchor="w").grid(
            row=0, column=2, sticky="w")
        self.lib = tk.Listbox(pane, height=8, activestyle="none",
                              font=("Microsoft YaHei UI", 10),
                              selectmode="extended", exportselection=False)
        self.lib.grid(row=1, column=0, sticky="nsew")
        self.lib.bind("<Double-Button-1>", self._on_lib_dbl)
        self.lib.bind("<<ListboxSelect>>", self._on_lib_sel)
        self.pl = tk.Listbox(pane, height=8, activestyle="none",
                             font=("Microsoft YaHei UI", 10),
                             exportselection=False)
        self.pl.grid(row=1, column=2, sticky="nsew")
        self.pl.bind("<Double-Button-1>", self._on_pl_dbl)
        self.pl.bind("<<ListboxSelect>>", self._on_pl_sel)
        self.pl_empty = tk.Label(pane, text="双击左侧歌曲加入",
                                 fg=dpi.MUT, bg=dpi.FIELD)
        # ---- 中：VJ 自动化 / 键盘自动化 监控栏（两栏同构：端口名称/
        #      端口状态 + 各自专属状态；键 (栏标识, 名称) 防重名冲突）----
        self.rows = {}
        mons = tk.Frame(self.root)
        mons.pack(fill="x", padx=12, pady=4)
        # 列几何与上方双列表完全同构（同权重 + 同 DPI 缩放间隔列），
        # 两栏左右边缘与素材库/播放列表精确对齐
        mons.columnconfigure(0, weight=1, uniform="m")
        mons.columnconfigure(1, minsize=dpi.scale(self.root, 8))
        mons.columnconfigure(2, weight=1, uniform="m")

        def mon_grid(fid, frame, names, marquee=()):
            for i, name in enumerate(names):
                r, c = divmod(i, 2)
                tk.Label(frame, text=name, width=10, anchor="w").grid(
                    row=r, column=c * 2, padx=(6, 0), pady=1, sticky="w")
                if name in marquee:
                    lbl = Marquee(frame, max_chars=24,
                                  font=("Microsoft YaHei UI", 9))
                else:
                    lbl = tk.Label(frame, text="…", anchor="w", fg=dpi.MUT,
                                   width=20)
                lbl.grid(row=r, column=c * 2 + 1, padx=(0, 6), sticky="we")
                frame.columnconfigure(c * 2 + 1, weight=1)
                self.rows[(fid, name)] = lbl

        vj = tk.LabelFrame(mons, text="VJ 自动化")
        vj.grid(row=0, column=0, sticky="nsew")
        mon_grid("vj", vj, ("端口名称", "端口状态", "OBS 状态", "走带跟随"))
        kb = tk.LabelFrame(mons, text="键盘自动化")
        kb.grid(row=0, column=2, sticky="nsew")
        mon_grid("kb", kb, ("端口名称", "端口状态", "音色映射", "最近切换"),
                 marquee=("音色映射", "最近切换"))
        # ---- 日志栏 ----
        logf = tk.LabelFrame(self.root, text="日志")
        logf.pack(fill="x", padx=12, pady=4)   # 固定高：纵向伸展全让给双列表
        tk.Button(logf, text="清空日志",
                  command=lambda: self.log.delete(0, "end")).pack(
            anchor="e", padx=6, pady=(2, 4))
        self.log = tk.Listbox(logf, height=7, activestyle="none",
                              font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        # ---- 下：控制区两行——行1(上)=播放配置独占整行（准备性操作）；
        #      行2(下)=演出中要按的 编排/播放/自动化（居中）+ 退出（靠右）----
        g4 = tk.LabelFrame(self.root, text="播放配置")
        g4.pack(fill="x", padx=12, pady=4)
        tk.Label(g4, text="配置目标：", anchor="w").pack(
            side="left", padx=(6, 0))
        # 独占整行后宽度充裕（minsize 下也有 2 倍余量），跑马灯弹性吸收
        # 中部剩余空间，右侧时长控件永不会被裁剪；行内间距统一 4
        self.tgt_lbl = Marquee(g4, max_chars=24,
                               font=("Microsoft YaHei UI", 9))
        self.tgt_lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.auto_var = tk.BooleanVar(value=self.auto_advance)  # 设置页/踩钉共用
        tk.Label(g4, text="时长").pack(side="left", padx=(4, 0))
        self.dur_var = tk.StringVar()
        self.dur_ent = tk.Entry(g4, textvariable=self.dur_var, width=7)
        self.dur_ent.pack(side="left", padx=(4, 0))
        self.dur_ent.bind(
            "<FocusIn>",
            lambda _e: self.dur_ent.select_range(0, "end"))  # 打字即覆盖预显示
        tk.Button(g4, text="写入时长", width=8,
                  command=self._set_duration).pack(side="left", padx=(4, 0))
        tk.Button(g4, text="重新识别", width=8,
                  command=self._redict).pack(side="left", padx=(4, 0))
        tk.Button(g4, text="设置", width=8,
                  command=self._open_settings).pack(side="left", padx=(4, 6))
        ctl = tk.Frame(self.root)
        ctl.pack(fill="x", padx=12, pady=(4, 10))
        ctl.columnconfigure(0, weight=1)
        ctl.columnconfigure(2, weight=1)
        left_sp = tk.Frame(ctl)
        left_sp.grid(row=0, column=0, sticky="e")
        right = tk.Frame(ctl)
        right.grid(row=0, column=2, sticky="ens")   # 纵向拉满、贴右
        # 退出=底栏动作按钮：宽度与编排/播放组一致（5字符），底边与各组
        # 按钮同一基线（pady=3，不再整格垂直居中），右边距与上方「设置」
        # 按钮一致（距容器内右缘 6px）
        tk.Button(right, text="退出", width=5,
                  command=self._on_exit).pack(side="bottom", padx=(0, 6),
                                              pady=3)
        # 左占位与右列（退出）等宽同步 → 两侧列自然宽相等，mid 严格居中
        right.bind("<Configure>",
                   lambda _e: left_sp.config(width=right.winfo_reqwidth()))
        mid = tk.Frame(ctl)
        mid.grid(row=0, column=1)
        g1 = tk.LabelFrame(mid, text="编排")
        g1.pack(side="left", padx=(0, 8))
        self.btn_add = tk.Button(g1, text="加入", width=5,
                                 command=self._add)
        self.btn_add.pack(side="left", padx=3, pady=3)
        self.btn_remove = tk.Button(g1, text="移除", width=5,
                                    command=self._remove)
        self.btn_remove.pack(side="left", padx=3, pady=3)
        self.btn_up = tk.Button(g1, text="上移", width=5,
                                command=lambda: self._move(-1))
        self.btn_up.pack(side="left", padx=3, pady=3)
        self.btn_down = tk.Button(g1, text="下移", width=5,
                                  command=lambda: self._move(1))
        self.btn_down.pack(side="left", padx=3, pady=3)
        self.btn_clear = tk.Button(g1, text="清空播放列表",
                                   command=self._clear_pl)
        self.btn_clear.pack(side="left", padx=3, pady=3)
        g2 = tk.LabelFrame(mid, text="播放")
        g2.pack(side="left", padx=(0, 8))
        self.tbtns = {}
        for text, cmd in (("开始", lambda: self._transport("play")),
                          ("暂停", lambda: self._transport("pause")),
                          ("继续", lambda: self._transport("resume")),
                          ("回零", lambda: self._transport("rewind")),
                          ("上一首", self._prev),
                          ("下一首", self._next)):
            b = tk.Button(g2, text=text, width=5, command=cmd)
            b.pack(side="left", padx=3, pady=3)
            self.tbtns[text] = b
        self.btn_panic = tk.Button(g2, text="全停", width=5,
                                   command=self._panic)
        self.btn_panic.pack(side="left", padx=(10, 3), pady=3)
        g3 = tk.LabelFrame(mid, text="自动化")
        g3.pack(side="left")
        tk.Button(g3, text="键盘自动化", width=9,
                  command=self._open_kbd).pack(side="left", padx=3, pady=3)
        tk.Button(g3, text="踩钉控制", width=9,
                  command=self._open_pedal).pack(side="left", padx=3, pady=3)
        dpi.darkify(self.root)
        self.root.attributes("-topmost", self.top_most)
        # 深色底上叠强调：主按钮绿、全停红（darkify 统一控件色，须在其后）；
        # 强调只改颜色不改字体——保持与同级按钮同尺寸
        self.tbtns["开始"].config(bg=dpi.C_OK, fg="#101418",
                                  activebackground="#7fe896")
        self.log.config(fg=dpi.LOG_FG)      # 日志是黑匣子，文字降一档不打扰
        self.btn_panic.config(bg="#a03030", fg="#ffffff",
                              activebackground="#c04444")
        # 长歌名滚动：横幅 NOW/NEXT、配置目标、音色映射、最近切换
        self.m_now = self.now_lbl
        self.m_next = self.next_lbl
        self.m_tgt = self.tgt_lbl
        self.m_map = self.rows[("kb", "音色映射")]
        self.m_last = self.rows[("kb", "最近切换")]
        self.root.update_idletasks()   # 三组等高：以自然最高者为准补内边距
        groups = (g1, g2, g3)
        h = max(f.winfo_reqheight() for f in groups)
        for f in groups:
            pad = max(0, (h - f.winfo_reqheight()) // 2)
            if pad:
                f.config(pady=pad)
        self._update_buttons()

    def _update_buttons(self):
        """编排组按钮随标蓝状态启停：加入=素材库有选中；移除/上移/下移=
        播放列表有选中（上移/下移还需不在顶/底边界）；清空=播放列表非空。"""
        sel = self.pl.curselection()
        i = sel[0] if sel else None
        pl_has = i is not None and i < len(self.pl_keys)
        for btn, on in ((self.btn_add, bool(self.lib.curselection())),
                        (self.btn_remove, pl_has),
                        (self.btn_up, pl_has and i > 0),
                        (self.btn_down, pl_has and i < len(self.pl_keys) - 1),
                        (self.btn_clear, bool(self.pl_keys))):
            btn.config(state=tk.NORMAL if on else tk.DISABLED)

    def _on_exit(self):
        """退出确认（切换中需二次确认）→ 关 MIDI 口/停后台线程。"""
        if (self.ctrl is not None and self.ctrl.busy
                and not messagebox.askyesno(
                    "退出", "正在切换工程，退出会中断切换流程。确定退出？")):
            return
        if not messagebox.askyesno(
                "退出", "确定退出Cube Setlist Manager？"):
            return
        for closer in ((lambda: self.port.close()),
                       (lambda: self.kb_port.close()),
                       (lambda: self.pedal.close())):
            try:
                closer()
            except Exception:
                pass
        # 非守护线程：窗口先关、进程等收尾做完——网络动作不占主线程，
        # 不会出现点退出后窗口长时间"未响应"；先趁 OBS 在线熄屏清文件，
        # 再按需关被控软件（顺序反了熄屏会随 OBS 退出而发不出去）
        threading.Thread(target=self._exit_worker, daemon=False).start()
        self.root.destroy()

    def _exit_worker(self):
        """退出收尾（窗口已关）。先停网页遥控栈（服务/翻谱口，热点按所有权
        决定关不关——热点停止放在 daemon 线程：PS 调用卡住不拖住退出），
        熄屏并清媒体源文件，OBS 下次启动不续播。"""
        if self.web is not None:
            # 限时等待：热点 PS 调用一般 1–3 秒；卡死时最多等 8 秒就放行退出
            t = threading.Thread(target=self._web_shutdown, daemon=True)
            t.start()
            t.join(8)
        try:
            if self.ctl is not None:
                self.ctl.stop_media()
                self.ctl.enabled = False
                self.ctl.shutdown()
        except Exception:
            pass
        if self.exit_close_apps:
            self._close_controlled()

    def _close_controlled(self):
        """三个被控软件**同时**发出关闭请求（先前是串行等待，Cubase 退出
        本身要几十秒，OBS/loopMIDI 白等）。Cubase 的保存弹窗仍由本线程
        照看到退完；OBS/loopMIDI 在子线程各自等待/兜底（loopMIDI 只缩
        托盘时到点终止）。"""
        for fn in (close_obs_app, close_loopmidi):
            threading.Thread(target=fn, daemon=True).start()
        cubase_ctrl.close_app(log=lambda *_: None)

    # ---- 后台线程：起服务 + 扫素材库 + 探时长 ----

    def _on_obs_connected(self):
        """OBS 连上（含重连）后回调（连接线程）：报状态，按设置恢复投影。"""
        self.q.put("OBS 已连接")
        if self.ctl.cfg.get("projectorMonitor", "") not in ("", None, -1) \
                and not self.ctl.apply_projector():
            self.q.put("VJ显示位置未恢复：%s" % self.ctl.last_error)

    def _ensure_cubase(self):
        """启动自检：Cubase 未运行就自动拉起（冷启动到 Hub 约 30 秒，之后
        切歌才能单实例转交）。进程在而界面全无=正在退出的空壳（上一局
        「退出关闭被控软件」的尾巴），等它退净再拉起，不算「已在运行」。
        尽力而为，失败只记日志不阻断启动。"""
        try:
            if find_processes_by_prefix("cubase"):
                if cubase_ctrl.ui_alive():
                    self.q.put("Cubase 已在运行")
                    return
                self.q.put("Cubase 正在退出（界面已无），退净后自动启动…")

                def launch():
                    if not cubase_ctrl.wait_exit():
                        # 等待期间窗口出来了（如正处冷启动）：不算失败
                        self.q.put("Cubase 已在运行" if cubase_ctrl.ui_alive()
                                   else "Cubase 无界面进程迟迟未退净，"
                                        "未自动启动")
                        return
                    self._launch_cubase("Cubase 已退净，已自动启动"
                                        "（冷启动约 30 秒）")
                threading.Thread(target=launch, daemon=True).start()
                return
            self._launch_cubase("Cubase 未运行，已自动启动（冷启动约 30 秒）")
        except Exception as e:
            self.q.put("Cubase 自检失败：%s" % _err(e))

    def _launch_cubase(self, ok_msg):
        """拉起 Cubase 本体（路径无效/启动失败只记日志，不抛出）。"""
        exe = self.ccfg.get("cubaseExe") or ""
        if not exe or not os.path.exists(exe):
            self.q.put("cubaseExe 路径不存在，无法自动拉起")
            return
        r = launch_detached(exe)
        if r > 32:
            self.q.put(ok_msg)
        else:
            self.q.put("Cubase 自动启动失败（ShellExecute 代码 %s）" % r)

    def _startup(self):
        """后台启动：按依赖分组逐项降级——一组失败只废该组功能并记红字，
        其余服务照常起（缺什么补什么，不再一票否决）。三大后端
        （Cubase/OBS/loopMIDI）未运行会在此阶段自动拉起。"""
        try:
            self._ensure_cubase()   # 最慢的后端最先拉起（冷启动约 30 秒）
            try:
                hint = self.vj_hint or self.kb_hint
                if hint:
                    mb.ensure_loopmidi(hint)
                    self.q.put("loopMIDI 端口就绪")
                elif not find_processes_by_prefix("loopmidi"):
                    # 停用联动也要 loopMIDI 在场：Cubase 工程引用它的端口，
                    # 不在则每次载入弹「未找到端口」。只拉起，不等端口。
                    exe = mb._loopmidi_exe()
                    if exe is None:
                        raise SystemExit("找不到 loopMIDI.exe")
                    if launch_detached(exe) <= 32:
                        raise SystemExit("拉起 loopMIDI 失败")
                    self.q.put("loopMIDI 已拉起（端口联动已停用）")
            except SystemExit as e:      # 单项降级：缺 loopMIDI 不拖垮其余服务
                self.q.put("loopMIDI 未就绪：%s（VJ 触发/走带跟随不可用）" % e)
            try:                         # OBS 未运行时连接循环会自动拉起，
                if not find_processes_by_prefix("obs64"):   # 这里只补一条状态
                    self.q.put("OBS 未运行，将自动拉起（首次连接等它就绪）")
            except Exception:
                pass
            # VJ 链：OBS 客户端 → 走带同步/自动推进 → MIDI 监听（相互依赖，
            # 整链一组；链内 MIDI 口缺失仍单独降级）
            try:
                self.ctl = ObsController(_load_config()["obs"])
                self.ctl.on_connected = self._on_obs_connected
                self.sync = mb.TransportSync(self.ctl, on_event=self.q.put)
                self.watch = advance.AdvanceWatch(
                    on_finished=lambda: self.calls.put(self._advance),
                    on_stop_transport=lambda: self.calls.put(
                        lambda: self._transport("stop")),
                    on_event=self.q.put,
                    clock_timeout=mb.CLOCK_TIMEOUT)
                self.watch.set_armed(self.auto_advance)   # 恢复持久化的勾选
                if self.auto_advance:
                    self.q.put("自动切换工程已恢复为开启")
                try:
                    if self.vj_hint:
                        self.port = mb.MidiIn(
                            self.vj_hint,
                            mb.note_handler(self.ctl, self.sync,
                                            report=self.q.put),
                            on_clock=lambda: (self.sync.on_clock(),
                                              self.watch.on_clock()))
                    else:
                        self.q.put("VJ 触发监听已停用")
                except SystemExit as e:  # 没有端口/被占用：只废 VJ 触发这一项
                    self.port = None
                    self.q.put("MIDI 监听未启动：%s" % e)
                self.ctl.enabled = True
                self.ctl.start()
                threading.Thread(target=self._watch, daemon=True).start()
                if self.port is not None:
                    self.q.put("MIDI 监听已启动（%s）" % self.port.name)
            except Exception as e:
                self.start_err = "VJ 链未启动：%s" % _err(e)
                self.q.put(self.start_err)
            # 切歌/走带控制（独立于 VJ 链）
            try:
                self.ctrl = cubase_ctrl.CubaseController(
                    self.ccfg["cubaseExe"], auto_save=self.ccfg["autoSave"],
                    log=self.q.put)
            except Exception as e:
                self.q.put("Cubase 控制未启动：%s（切歌/走带不可用）" % _err(e))
            # 键盘自动化：发送线程（JUNO + AX-09）+ 联动端口
            try:
                self.switcher = kbd_auto.ToneSwitcher(
                    self.jcfg, self.q.put, on_result=self._on_kb_result)
                self.ax_switcher = kbd_auto.ToneSwitcher(
                    self.axcfg, self.q.put, on_result=self._on_kb_result)
                try:
                    if self.kb_hint:
                        self.kb_port = kbd_auto.RawMidiIn(self.kb_hint,
                                                          self._on_kb_msg)
                        self.q.put("键盘自动化监听已启动（%s）"
                                   % self.kb_port.name)
                    else:
                        self.q.put("键盘自动化监听已停用")
                except kbd_auto.PortNotFound as e:
                    self.kb_port = None
                    self.kb_err = str(e)
                    self.q.put("键盘自动化停用：%s" % e)
            except Exception as e:
                self.kb_port = None
                self.kb_err = str(e)
                self.q.put("键盘自动化未启动：%s" % _err(e))
            # 踩钉
            try:
                self.pedal = pedal.PedalListener(
                    on_action=lambda a: self.calls.put(
                        lambda: self._pedal_action(a)))
                self.pedal.apply(self.pedal_hint, self.pedal_binds)
                if self.pedal.try_open():
                    self.q.put("CC 踩钉监听已启动（%s）" % self.pedal.name)
                elif self.pedal_hint:
                    self.q.put("未找到踩钉口「%s」，每 10 秒自动重试"
                               % self.pedal_hint)
            except Exception as e:
                self.q.put("踩钉监听未启动：%s" % _err(e))
            # 移动端遥控 + 翻谱推送（整组独立降级：总开关关=只报停用）
            try:
                self.web = web_remote.WebRemote(self, self.web_cfg)
                self.web.startup()      # 内部按总开关决定起不起（热点要数秒）
            except Exception as e:
                self.q.put("移动端遥控未启动：%s" % _err(e))
            try:
                self._load_songs()
            except Exception as e:
                self.q.put("素材库加载失败：%s" % _err(e))
        except SystemExit as e:
            self.start_err = str(e)
            self.q.put("启动失败：%s" % e)
        except Exception as e:              # 后台线程兜底：任何异常都进日志
            self.start_err = self.start_err or "启动异常：%s" % _err(e)
            self.q.put(self.start_err)

    def _load_songs(self):
        root = self.ccfg["projectsRoot"]
        if not os.path.isdir(root):
            self.q.put("工程库不存在：%s（改 config.json 的 cubase.projectsRoot）"
                       % root)
            return
        songs = scan_library(root)
        self.by_key = {s["key"]: s for s in songs}
        self.songs = songs
        # 播放列表清掉库里已不存在的项
        self.pl_keys = [k for k in self.pl_keys if k in self.by_key]
        # 重启恢复：Cubase 里还开着的工程按标题对回播放列表
        ws = cubase_ctrl.current_project()
        name = cubase_ctrl.project_name_from_title(ws[1]) if ws else None
        if name:
            for i, k in enumerate(self.pl_keys):
                if self.by_key[k]["name"] == name:
                    self.cur = i
                    song = self.by_key[k]
                    d = self.durations.get(k) or \
                        cpr_meta.read_duration(song["path"]) or 0.0
                    self.durations[k] = d
                    self.watch.set_duration(d)
                    self.slots = kbd_auto.load_slots(song["path"])
                    self.ax_slots = kbd_auto.load_slots(song["path"], "ax")
                    self.q.put("已恢复当前工程：《%s》" % name)
                    break
        self._probe_durations(songs)
        self.calls.put(self._refresh)
        self.q.put("素材库 %d 首，播放列表 %d 首（%s）"
                   % (len(songs), len(self.pl_keys), root))

    def _probe_durations(self, songs):
        """全量重探自动来源的工程时长并持久化（后台线程；65 首/66MB 实测
        约 0.15s）：在 Cubase 里改过定位条的歌，重启即跟上。手动设定不被
        覆盖；值全没变就不落盘，安静返回。"""
        fresh = _reprobe(songs, self.durations, self.dur_src)
        if not fresh:
            return
        self.durations.update(fresh)
        self._persist()
        self.q.put("已重探工程时长：%d 首有更新（%d 首未知，需手填）"
                   % (len(fresh),
                      sum(1 for s in songs
                          if not self.durations.get(s["key"]))))

    def _on_lib_sel(self, _e=None):
        """双列表唯一选中：选素材库即清播放列表的标蓝。"""
        if self._sel_lock:
            return
        self._sel_lock = True
        try:
            self.pl.selection_clear(0, "end")
        finally:
            self._sel_lock = False
        self._update_dur_target()
        self._update_buttons()

    def _on_pl_sel(self, _e=None):
        if self._sel_lock:
            return
        self._sel_lock = True
        try:
            self.lib.selection_clear(0, "end")
        finally:
            self._sel_lock = False
        self._update_dur_target()
        self._update_buttons()

    def _lib_view(self):
        """素材库按搜索词过滤后的视图；_refresh/_add/_sel_target 共用同一
        计算，保证显示下标与数据下标一致。"""
        q = self._lib_query
        if not q:
            return self.songs
        return [s for s in self.songs
                if q in s["name"].lower() or q in s["team"].lower()]

    def _on_search(self):
        self._lib_query = self.lib_q.get().strip().lower()
        self.lib.selection_clear(0, "end")
        self._refresh()

    def _sel_target(self):
        """当前标蓝项 → (来源, 下标, key)；两列表都没选返回 None。"""
        sel = self.pl.curselection()
        if sel and sel[0] < len(self.pl_keys):
            return ("pl", sel[0], self.pl_keys[sel[0]])
        view = self._lib_view()
        sel = self.lib.curselection()
        if sel and sel[0] < len(view):
            return ("lib", sel[0], view[sel[0]]["key"])
        return None

    def _update_dur_target(self, *_):
        """选中变化：配置目标标签（标蓝曲）+ 输入框预显示（自动时长/数值/未知）。"""
        t = self._sel_target()
        if t:
            s = self.by_key.get(t[2])
            if s is not None:
                self.m_tgt.set(s["name"], dpi.FG)
                text, _c = self._dur_status(t[2], for_entry=True)
                self.dur_var.set(text)
                return
        self.tgt_lbl.config(text="（未选中）", fg=dpi.MUT)
        self.dur_var.set("")

    def _dur_status(self, key, for_entry=False):
        """时长三态 → (显示文本, 颜色)：自动=绿/手动=数值黄/未知=红。
        列表行里自动态直接显示时长数值（降噪）；输入框预显示用「自动时长」
        占位（for_entry=True），其值不可直接覆盖，需清空后输入。"""
        d = self.durations.get(key, 0.0)
        if not d:
            return "未知", dpi.C_ERR
        if self.dur_src.get(key) == "manual":
            return cpr_meta.fmt_mmss(d), dpi.C_WARN
        return ("自动时长" if for_entry else cpr_meta.fmt_mmss(d)), dpi.C_OK

    def _refresh(self):
        lib_sel = list(self.lib.curselection())
        pl_sel = list(self.pl.curselection())
        lib_scroll, pl_scroll = self.lib.yview()[0], self.pl.yview()[0]
        lib_x, pl_x = self.lib.xview()[0], self.pl.xview()[0]
        self.lib.delete(0, "end")
        pl_set = set(self.pl_keys)
        for i, s in enumerate(self._lib_view()):
            text, color = self._dur_status(s["key"])
            mark = " ·" if s["key"] in pl_set else ""
            self.lib.insert("end", "[%s] %s%s（%s）" % (
                s["team"], s["name"], mark, text))
            self.lib.itemconfigure(i, foreground=color)
        self.pl.delete(0, "end")
        for i, key in enumerate(self.pl_keys):
            s = self.by_key.get(key)
            if s is None:
                continue
            text, color = self._dur_status(key)
            mark = " ←" if i == self.cur else ""
            self.pl.insert("end", "%d. %s（%s）%s" % (
                i + 1, s["name"], text, mark))
            self.pl.itemconfigure(i, foreground=color)
            if i == self.cur:               # 当前行底色：未选中也能一眼定位
                self.pl.itemconfigure(i, background=dpi.CUR_BG)
        # 重建后恢复用户选中（标蓝即时长/识别的目标，不能丢）；
        # 没有用户选中才回落到当前曲行——两者并存会让配置目标读错行
        self._sel_lock = True
        try:
            for i in lib_sel:
                self.lib.selection_set(i)
            if pl_sel:
                for i in pl_sel:
                    if i < self.pl.size():
                        self.pl.selection_set(i)
            elif self.cur is not None and self.cur < self.pl.size():
                self.pl.selection_set(self.cur)
        finally:
            self._sel_lock = False
        self.lib.yview_moveto(lib_scroll)       # 重建不丢滚动位置
        self.pl.yview_moveto(pl_scroll)
        self.lib.xview_moveto(lib_x)
        self.pl.xview_moveto(pl_x)
        if self.pl_keys:                    # 空状态提示：仅列表为空时出现
            self.pl_empty.place_forget()
        else:
            self.pl_empty.place(in_=self.pl, relx=0.5, rely=0.5,
                                anchor="center")
        self._update_dur_target()
        self._update_buttons()

    def _watch(self):
        while True:
            try:
                self.sync.poll()
                self.watch.poll()
            except Exception as e:
                # 看门狗线程绝不允许静默死亡
                try:
                    self.q.put("后台监控异常（已自动恢复）：%s" % _err(e))
                except Exception:
                    pass
            time.sleep(mb.POLL_SEC)

    def _on_kb_msg(self, status, d1, d2):
        """Keyboard Automation 端口音符：36-39→JUNO 全局移调（连按累加）、
        60-69→JUNO、72-77→AX-09 查映射切音色；E2(40)/A2(45)=延音踏板键，
        按住发 CC64=127、松开发 0（重叠音符按计数去重，状态翻转才发）。"""
        st = status & 0xF0
        if st == 0x90 and d2 == 0:
            st = 0x80                   # Note On vel=0 等价 Note Off
        if st not in (0x90, 0x80):
            return
        down = st == 0x90
        if d1 in kbd_auto.SHIFT_NOTES:
            if not down:
                return
            if self.ctrl is not None and self.ctrl.busy:
                self.q.put("切歌中，忽略移调音符 %s" % kbd_auto.note_name(d1))
                return
            self.juno_shift = max(
                -kbd_auto.SHIFT_LIMIT,
                min(kbd_auto.SHIFT_LIMIT,
                    self.juno_shift + kbd_auto.SHIFT_NOTES[d1]))
            if self.switcher:
                self.switcher.submit_shift(
                    self.juno_shift,
                    why="JUNO 全局移调 %+d 半音" % self.juno_shift)
            else:
                self.q.put("JUNO 移调 %+d 半音（输出未就绪，未发送）"
                           % self.juno_shift)
            return
        if d1 in kbd_auto.PEDAL_NOTES:
            before = self.pedal_held.get(d1, 0)
            after = max(0, before + (1 if down else -1))
            self.pedal_held[d1] = after
            if (before == 0) == (after == 0):
                return                  # 按住/松开状态没翻转，不重发
            ax = kbd_auto.PEDAL_NOTES[d1] == "ax"
            switcher = self.ax_switcher if ax else self.switcher
            if switcher is None:
                self.q.put("%s 延音踏板（输出未就绪，未发送）"
                           % ("AX-09" if ax else "JUNO"))
                return
            switcher.submit_msgs(
                kbd_auto.pedal_msgs(after > 0,
                                    self.axcfg["ch"] if ax
                                    else self.jcfg["patchCh"]),
                why="%s 延音%s" % ("AX-09" if ax else "JUNO",
                                   "踩下" if after > 0 else "抬起"))
            return
        if not down:
            return
        if d1 in kbd_auto.SLOT_NOTES:
            slots, switcher = self.slots, self.switcher
        elif d1 in kbd_auto.AX_NOTES:
            slots, switcher = self.ax_slots, self.ax_switcher
        else:
            return
        if self.ctrl is not None and self.ctrl.busy:
            self.q.put("切歌中，忽略音色音符 %s" % kbd_auto.note_name(d1))
            return
        slot = slots.get(d1)
        if not slot:
            if d1 not in self._kb_warned:   # 循环播放时同一告警只记一次
                self._kb_warned.add(d1)
                self.q.put("音符 %s 未配置音色映射（用「键盘自动化」窗口录制）"
                           % kbd_auto.note_name(d1))
            return
        switcher.submit(slot, why=kbd_auto.note_name(d1))

    def _on_kb_result(self, desc, err):
        """ToneSwitcher 线程回调：最近一次音色切换结果（进监控栏，只写属性）。
        失败也带动作名（音色描述/延音踩下抬起），否则看不出哪个动作失败。"""
        self._kb_last = (("%s失败：%s" % (desc, err)) if err
                         else ("已发送：%s" % desc),
                         dpi.C_ERR if err else dpi.C_OK)

    # ---- 播放列表编排（主线程） ----

    def _on_lib_dbl(self, _e):
        self._add()

    def _add(self):
        sel = self.lib.curselection()
        if not sel:
            self.q.put("先在素材库里选中再加入")
            return
        view = self._lib_view()
        added = []
        for i in sel:
            if i >= len(view):
                continue
            s = view[i]
            if s["key"] in self.pl_keys:
                continue
            self.pl_keys.append(s["key"])
            added.append(s["name"])
        if not added:
            self.q.put("所选歌曲均已在播放列表里")
            return
        self._persist()
        self._refresh()
        self.q.put("已加入播放列表：%s" %
                   "、".join("《%s》" % n for n in added))

    def _on_pl_dbl(self, _e):
        sel = self.pl.curselection()
        if not sel:
            return
        i = sel[0]
        if (self.switch_confirm and 0 <= i < len(self.pl_keys)
                and i != self.cur
                and cubase_ctrl.current_project()):
            # 双击是最易误触的手势、切歌会关掉当前工程：有工程在开时默认
            # 确认（覆盖无时钟工程——那种工程"在不在播"无从判断）；
            # 无打开工程时双击=直接打开，没有可被关掉的东西，不弹确认
            cur = (self.by_key.get(self.pl_keys[self.cur], {}).get("name", "？")
                   if self.cur is not None else "？")
            dst = self.by_key.get(self.pl_keys[i], {}).get("name", "？")
            if not messagebox.askyesno(
                    "切换工程", "确定从《%s》切换到《%s》？\n"
                    "当前工程将被关闭（自动保存）。" % (cur, dst)):
                return
        self._switch(i, "手动")

    def _remove(self):
        sel = self.pl.curselection()
        if not sel:
            self.q.put("先在播放列表里选中一项再移除")
            return
        i = sel[0]
        key = self.pl_keys.pop(i)
        if self.cur is not None:
            if i < self.cur:
                self.cur -= 1
            elif i == self.cur:
                # 删到列表空时 cur 归 None（-1 会以负下标炸掉轮询循环）
                self.cur = (min(self.cur, len(self.pl_keys) - 1)
                            if self.pl_keys else None)
                if self.cur is not None:
                    self.q.put("已移除当前歌曲，自动指向下一首（工程保持打开）")
        self._persist()
        self._refresh()
        self.q.put("已从播放列表移除：《%s》" % key.split("/")[-1])

    def _clear_pl(self):
        if not self.pl_keys:
            return
        if not messagebox.askyesno("清空播放列表",
                                   "确定清空播放列表？（素材库不受影响）"):
            return
        self.pl_keys = []
        self.cur = None
        self._persist()
        self._refresh()
        self.q.put("播放列表已清空（素材库不受影响）")

    def _move(self, delta):
        sel = self.pl.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + delta
        if not (0 <= j < len(self.pl_keys)):
            return
        self.pl_keys[i], self.pl_keys[j] = self.pl_keys[j], self.pl_keys[i]
        if self.cur == i:
            self.cur = j
        elif self.cur == j:
            self.cur = i
        self._persist()
        self._refresh()
        self.pl.selection_clear(0, "end")       # browse 模式下 set 会叠加，先清
        self.pl.selection_set(j)
        self._update_buttons()

    def _switch(self, i, via, play_after=False):
        """切到播放列表第 i 项；返回 False=没开始（越界/正忙/库缺项）。"""
        if not (0 <= i < len(self.pl_keys)):
            if via == "advance":
                self.q.put("自动切换：已到播放列表末尾，不切换")
            elif not self.pl_keys:
                self.q.put("播放列表为空：先从素材库加入歌曲")
            else:
                self.q.put("已在播放列表末尾")
            return False
        key = self.pl_keys[i]
        song = self.by_key.get(key)
        if song is None:
            self.q.put("歌单项不在素材库里：%s" % key)
            return False
        if via == "手动" and i == self.cur:
            ws = cubase_ctrl.current_project()
            if ws and cubase_ctrl.project_name_from_title(ws[1]) == song["name"]:
                self.q.put("《%s》已是当前工程，不重载" % song["name"])
                return False
        if self.ctrl is None or not self.ctrl.switch_to(
                song["path"], on_done=self._switch_done):
            if via == "手动":       # 忙时保留最后一次用户请求，完成后自动执行
                self._pending_switch = i
                self.q.put("切换中：《%s》已排队，完成后自动执行"
                           % song["name"])
            else:
                self.q.put("正忙，忽略%s切换请求：《%s》" % (via, song["name"]))
            return False
        self._switch_key = key      # 快照：完成时按 key 归属，防编排变动张冠李戴
        self._play_after_switch = play_after
        self.watch.reset()
        self.cur = i
        self.slots = {}            # 切歌开始，旧映射立即失效
        self.ax_slots = {}
        self._kb_warned = set()
        self._refresh()
        self.q.put("切换到《%s》…（%s）"
                   % (song["name"], "已排队" if via == "排队" else via))
        return True

    def _switch_done(self, name):
        key = self._switch_key
        self._switch_key = None
        play_after = self._play_after_switch
        self._play_after_switch = False
        if name is None or not key:
            return
        song = self.by_key.get(key)
        if song is None:
            return
        # 打开后再测一次时长；手动设定不被自动识别覆盖（需要时点「重新识别」）
        d = cpr_meta.read_duration(song["path"]) or 0.0
        if self.dur_src.get(key) == "manual":
            self.q.put("《%s》保留手动时长 %s（工程内为 %s，可点「重新识别」改用）"
                       % (song["name"], cpr_meta.fmt_mmss(self.durations.get(key)),
                          cpr_meta.fmt_mmss(d)))
        elif d and d != self.durations.get(key):
            self.durations[key] = d
            self._persist()
            self.q.put("《%s》时长更新为 %s" % (song["name"],
                                                cpr_meta.fmt_mmss(d)))
        elif not d and not self.durations.get(key):
            self.q.put("《%s》时长未知（工程未设循环定位条）：可用「写入时长」手填"
                       % song["name"])
        self.watch.set_duration(d)
        self.calls.put(self._refresh)
        self.slots = kbd_auto.load_slots(song["path"])
        self.ax_slots = kbd_auto.load_slots(song["path"], "ax")
        if self.slots or self.ax_slots:
            self.q.put("音色映射已载入：JUNO %d 个 + AX-09 %d 个音符（%s）"
                       % (len(self.slots), len(self.ax_slots),
                          kbd_auto.SLOT_FILE))
        if play_after:
            # 等待加载的循环放后台线程，别堵主线程的状态轮询
            self.calls.put(lambda: threading.Thread(
                target=self._auto_play, daemon=True).start())
            self.q.put("加载完成，自动开始播放")
        else:
            self._regain_focus()      # 单实例转交激活了 Cubase，焦点收回

    def _auto_play(self):
        """切换完成后自动播放：窗口标题出现≠加载完成——过早发播放键会被
        未就绪的 Cubase 吞掉（实测需再点一次）。等「正在加载」浮层消失
        （至多等 45 秒）再发播放键。"""
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.cur is None:
                return                      # 用户已手动切走
            loading = [t for _, t, c in cubase_ctrl._windows()
                       if c.startswith(cubase_ctrl.WIN_CLASS_PREFIX)
                       and t.startswith("正在加载")]
            if not loading:
                break
            time.sleep(0.5)
        # 回主线程执行：本方法跑在后台线程，_transport 会摸 Tk 状态
        self.calls.put(lambda: self._transport("play"))

    def _set_duration(self):
        t = self._sel_target()
        if t is None:
            self.q.put("先在素材库或播放列表选中一首再写入时长")
            return
        _, _, key = t
        raw = self.dur_var.get().strip()
        if raw in ("自动时长", "未知"):
            self.q.put("请输入时长：秒数或 分:秒（如 213 或 3:33）")
            return
        sec = cpr_meta.parse_mmss(raw)
        if not sec:
            self.q.put("请输入时长：秒数或 分:秒（如 213 或 3:33）")
            return
        self.durations[key] = sec
        self.dur_src[key] = "manual"
        self._persist()
        self._refresh()
        loaded = (self.pl_keys[self.cur] if self.cur is not None
                  and self.cur < len(self.pl_keys) else None)
        if key == loaded:
            self.watch.set_duration(sec)
        self.q.put("《%s》手动时长已写为 %s" % (key.split("/")[-1],
                                                cpr_meta.fmt_mmss(sec)))

    def _redict(self):
        """重新识别：清手填值，重读工程文件恢复工程内原时长设定。"""
        t = self._sel_target()
        if t is None:
            self.q.put("先在素材库或播放列表选中一首再识别")
            return
        _, _, key = t
        song = self.by_key.get(key)
        if song is None:
            self.q.put("歌单项不在素材库里：%s" % key)
            return
        d = cpr_meta.read_duration(song["path"])
        if d:
            self.durations[key] = d
            self.dur_src.pop(key, None)     # 回归自动来源
            self._persist()
            self._refresh()
            loaded = (self.pl_keys[self.cur] if self.cur is not None
                      and self.cur < len(self.pl_keys) else None)
            if key == loaded:
                self.watch.set_duration(d)
            self.q.put("已识别《%s》工程内原时长 %s" % (song["name"],
                                                        cpr_meta.fmt_mmss(d)))
        else:
            self.q.put("《%s》工程内未设循环定位条，无法识别（保留现有值）"
                       % song["name"])

    def _persist(self):
        # 串行化：并发写会踩同一个临时文件（Windows 上报错或写出交错内容）
        with self._persist_lock:
            try:
                save_playlist(self.pl_keys, self.durations, self.dur_src)
            except (ValueError, OSError, RuntimeError) as e:
                self.q.put("播放列表保存失败：%s" % e)

    def _next(self):
        self._switch(0 if self.cur is None else self.cur + 1, "手动")

    def _prev(self):
        if self.cur is None:
            self._switch(0, "手动")
        elif self.cur > 0:
            self._switch(self.cur - 1, "手动")
        else:
            self.q.put("已在播放列表开头")

    def _advance(self):
        if self.ctrl is not None and self.ctrl.busy:
            self.q.put("自动切换：正在切换中，跳过")
            return
        self._switch(self.cur + 1 if self.cur is not None else 0, "自动",
                     play_after=self.cont_play)

    def _transport(self, action):
        if self.ctrl is None:
            self.q.put("服务未就绪（启动未完成）")
            return
        if action == "play" and not cubase_ctrl.current_project():
            # 没有打开的工程：自动加载播放列表当前项（无指针则第一首）再播放
            if not self.pl_keys:
                self.q.put("播放列表为空，先从素材库加入歌曲再播放")
                return
            i = min(self.cur if self.cur is not None else 0,
                    len(self.pl_keys) - 1)
            if self._switch(i, "自动加载", play_after=True):
                return
        threading.Thread(target=self._transport_thread, args=(action,),
                         daemon=True).start()

    def _pedal_action(self, action):
        """踩钉 CC 上升沿 → 功能分发（经 calls 队列在主线程执行）。"""
        if action in ("play", "stop", "rewind"):
            self._transport(action)
        elif action == "next":
            self._next()
        elif action == "panic":
            self._panic()
        elif action == "auto":
            self.auto_var.set(not self.auto_var.get())
            self._toggle_auto()

    def _open_pedal(self):
        if self.pedal_win is None or not self.pedal_win.winfo_exists():
            self.pedal_win = pedal.PedalWindow(self)
        self.pedal_win.lift()

    def _panic(self):
        if self.ctrl is None:
            self.q.put("服务未就绪（启动未完成）")
            return

        def run():
            self.ctrl.panic()
            self._regain_focus()

        threading.Thread(target=run, daemon=True).start()
        self.watch.reset()          # 急停不得被误判成"播完"而自动切歌
        if self.ctl is not None:
            self.ctl.stop_media()   # 顺带熄掉 OBS 视频
        self.q.put("全停：走带已停，自动切换已复位，视频已熄灭")

    def _open_settings(self):
        if self.settings_win is None or not self.settings_win.winfo_exists():
            self.settings_win = SettingsWindow(self)
        self.settings_win.lift()

    def _apply_ports(self):
        """设置页改了端口名称后热切换监听（主线程调用；找不到新端口只记
        红字并保留状态，改回或重启可恢复）。"""
        if self.ctl is not None and self.sync is not None:
            try:
                if self.port is not None:
                    self.port.close()
            except Exception:
                pass
            self.port = None
            if not self.vj_hint:
                self.q.put("VJ 监听已停用")
            else:
                try:
                    self.port = mb.MidiIn(
                        self.vj_hint,
                        mb.note_handler(self.ctl, self.sync,
                                        report=self.q.put),
                        on_clock=lambda: (self.sync.on_clock(),
                                          self.watch.on_clock()))
                    self.q.put("VJ 监听已切换（%s）" % self.port.name)
                except SystemExit as e:
                    self.q.put("VJ 监听未启动：%s" % e)
        else:
            self.q.put("VJ 服务未就绪：端口名称已保存，重启程序后生效")
        if self.kb_port is not None:
            try:
                self.kb_port.close()
            except OSError:
                pass
        self.kb_port = None
        if not self.kb_hint:
            self.q.put("键盘自动化监听已停用")
        else:
            try:
                self.kb_port = kbd_auto.RawMidiIn(self.kb_hint, self._on_kb_msg)
                self.q.put("键盘自动化监听已切换（%s）" % self.kb_port.name)
            except kbd_auto.PortNotFound as e:
                self.kb_err = str(e)
                self.q.put("键盘自动化停用：%s" % e)

    def _regain_focus(self):
        """把键盘焦点收回本程序（工作线程调用）。置顶只保视觉 Z 序，
        不保焦点——走带键发完后焦点在 Cubase，若不收回，用户接着打字
        会打进 Cubase（空格=停走带）。复用 cubase_ctrl.focus 的前台手法。"""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if hwnd:
                cubase_ctrl.focus(hwnd, tries=2)
        except Exception:
            pass

    def _transport_thread(self, action):
        if self.ctrl.transport(action) and action == "rewind" \
                and self.watch is not None:
            self.watch.reset()      # 回零=停在零点，已播计时同步归零
        self._regain_focus()

    def _toggle_auto(self):
        if self.watch is not None:
            self.watch.set_armed(self.auto_var.get())
            self.q.put("自动切换工程%s" % ("已开启（播完自动切下一首）"
                                        if self.auto_var.get() else "已关闭"))
        self._persist_config(autoAdvance=bool(self.auto_var.get()))

    def _persist_config(self, **kv):
        try:
            cfg = _load_config()
            cfg.update(kv)
            _save_config(cfg)
        except (ValueError, OSError, RuntimeError) as e:
            self.q.put("配置保存失败：%s" % e)

    # ---- 移动端遥控 ----

    def _web_config_payload(self, **fields):
        """webRemote 持久化载荷：设备表永远取注册表现值（网页认领随时在改，
        不能被设置页的旧快照覆盖），其余字段取调用方给的最新值。"""
        cfg = dict(self.web_cfg)
        cfg.update(fields)
        if self.web is not None:
            cfg["devices"] = self.web.registry.snapshot()
        return cfg

    def _persist_web_remote(self):
        # 注册表 on_change 经 calls 队列到主线程；设置页保存也走这里合流
        self._persist_config(webRemote=self._web_config_payload())

    def _apply_web(self):
        """设置页改了遥控配置后热应用（热点 PS 调用要数秒，后台线程跑）。"""
        if self.web is None:
            self.q.put("移动端遥控模块未就绪：重启程序后生效")
            return
        wc = self.web_cfg

        def run():
            try:
                self.web.apply(enabled=bool(wc.get("enabled")),
                               serverPort=wc.get("serverPort"),
                               taskerPort=wc.get("taskerPort"),
                               midiIn=str(wc.get("midiIn") or ""))
            except Exception as e:
                self.q.put("移动端遥控应用失败：%s" % _err(e))

        threading.Thread(target=run, daemon=True).start()

    def _web_shutdown(self):
        try:
            if self.web is not None:
                self.web.shutdown()
        except Exception:
            pass

    def _open_kbd(self):
        t = self._sel_target()
        song = self.by_key.get(t[2]) if t else None
        if song is None and self.cur is not None \
                and self.cur < len(self.pl_keys):
            song = self.by_key.get(self.pl_keys[self.cur])
        if song is None:
            self.q.put("先在素材库或播放列表选中一首再配置键盘自动化")
            return
        if self.kbd_win is None or not self.kbd_win.winfo_exists():
            self.kbd_win = kbd_auto.KeyboardAutoWindow(self)
        self.kbd_win.set_song(song)          # 初开与再点统一：绑定所选曲
        self.kbd_win.lift()

    # ---- 主线程轮询：状态 + 日志 + 跨线程调用 ----

    DOT_CELLS = {("vj", "端口状态"), ("vj", "OBS 状态"),
                 ("vj", "走带跟随"), ("kb", "端口状态")}

    def _set(self, name, text, color=dpi.MUT):
        # 状态格加圆点：色块先行、文字冗余（色弱友好、远距易辨识）；
        # 名称/内容格不加，占位符不加（保持列节奏）
        if name in self.DOT_CELLS and text not in ("-", ""):
            text = "● " + text
        self.rows[name].config(text=text, fg=color)

    def _on_ui_error(self, exc, val, _tb):
        """Tk 回调异常统一进日志框：noconsole 下无 stderr，不接就"点了没反应"。"""
        self.q.put("界面异常：%s：%s" % (exc.__name__, val))

    def _update_transport_buttons(self):
        """暂停/继续随走带状态启停（仅工程发时钟时状态可知）：
        播放中禁「继续」（空格会停走带）、停止禁「暂停」（NUM0 会跳回
        上次起播位置）；未发时钟的工程无从判断，两键保持可用。"""
        live = (self.watch.is_transport_live()
                if self.sync is not None and self.sync.is_following()
                and self.watch is not None else None)
        self.tbtns["继续"].config(state=tk.DISABLED if live is True
                                  else tk.NORMAL)
        self.tbtns["暂停"].config(state=tk.DISABLED if live is False
                                  else tk.NORMAL)

    def _tick(self):
        try:
            self._tick_body()
        except Exception as e:
            # 轮询循环绝不允许死（死一次=状态/队列全部冻结=界面假死）
            try:
                self.log.insert("end", time.strftime("[%H:%M:%S] ")
                                + "状态刷新异常（已自动恢复）：%s" % _err(e))
                self.log.itemconfigure(self.log.size() - 1,
                                       foreground=dpi.C_ERR)
                self.log.see("end")
            except Exception:
                pass
        self.root.after(400, self._tick)

    def _tick_body(self):
        if (self._pending_switch is not None and self.ctrl is not None
                and not self.ctrl.busy):
            i = self._pending_switch
            self._pending_switch = None
            self._switch(i, "排队")     # busy 期间用户点的歌，完成后接续执行
        if self.vj_hint:
            hit = mb._pick(mb._in_devices(), self.vj_hint)
            self._set(("vj", "端口名称"), hit[1] if hit else "未找到",
                      dpi.C_OK if hit else dpi.C_ERR)
            self._set(("vj", "端口状态"),
                      "监听中" if self.port is not None else "未启动",
                      dpi.C_OK if self.port is not None else dpi.C_ERR)
        else:
            self._set(("vj", "端口名称"), "—")
            self._set(("vj", "端口状态"), "已停用")
        if self.ctl is None:
            self._set(("vj", "OBS 状态"), self.start_err or "启动中…", dpi.C_ERR)
        else:
            ok = self.ctl.is_connected()
            extra = "（%s）" % self.ctl.last_error if self.ctl.last_error else ""
            self._set(("vj", "OBS 状态"), "已连接" if ok else "未连接" + extra,
                      dpi.C_OK if ok else dpi.C_WARN)
        if self.sync is None:
            self._set(("vj", "走带跟随"), "-")
        elif not self.sync.is_following():
            self._set(("vj", "走带跟随"), "未启用（未收到时钟）", dpi.C_WARN)
        else:
            vs = self.sync.video_state
            name = self.sync.current_video
            text = FOLLOW.get(vs, "?")
            if name and vs in ("playing", "paused"):
                text = "%s：%s" % (text, name)
            self._set(("vj", "走带跟随"), text,
                      dpi.C_OK if vs == "playing"
                      else dpi.C_WARN if vs == "paused" else dpi.MUT)
        if self.kb_port is not None:
            self._set(("kb", "端口名称"), self.kb_port.name, dpi.C_OK)
            self._set(("kb", "端口状态"), "监听中", dpi.C_OK)
        elif not self.kb_hint:
            self._set(("kb", "端口名称"), "—")
            self._set(("kb", "端口状态"), "已停用")
        elif self.kb_err:
            self._set(("kb", "端口名称"), "未找到", dpi.C_ERR)
            self._set(("kb", "端口状态"), "未启动", dpi.C_ERR)
        else:
            self._set(("kb", "端口名称"), "…", dpi.MUT)
            self._set(("kb", "端口状态"), "启动中…", dpi.MUT)
        cur_key = (self.pl_keys[self.cur] if self.cur is not None
                   and self.cur < len(self.pl_keys) else None)
        cur_song = self.by_key.get(cur_key) if cur_key else None
        if cur_song is None:
            self._set(("kb", "音色映射"), "（未加载工程）", dpi.MUT)
        else:
            n = len(self.slots)
            a = len(self.ax_slots)
            self.m_map.set("%s：JUNO %d/10 + AX %d/6" % (cur_song["name"], n, a),
                           dpi.C_OK if n or a else dpi.C_WARN)
        text, color = self._kb_last
        self.m_last.set(text, color)
        if (self.pedal is not None and not self.pedal.connected
                and self.pedal.hint and time.time() > self._pedal_retry):
            self._pedal_retry = time.time() + 10
            if self.pedal.try_open():
                self.q.put("CC 踩钉已连接（%s）" % self.pedal.name)
        self._update_transport_buttons()
        self._tick_banner()
        while True:
            try:
                fn = self.calls.get_nowait()
            except queue.Empty:
                break
            fn()
        follow = self.log.yview()[1] > 0.99    # 上翻看历史时不强行滚回底部
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", time.strftime("[%H:%M:%S] ") + msg)
            if msg.startswith("界面异常"):     # 日志整体降噪，异常行才标红
                self.log.itemconfigure(self.log.size() - 1,
                                       foreground=dpi.C_ERR)
        if self.log.size() > LOG_MAX:      # 先裁剪再回底：删头部后 see 才准
            self.log.delete(0, self.log.size() - LOG_MAX)
        if follow:
            self.log.see("end")

    def _tick_banner(self):
        """顶部 NOW/NEXT 横幅：NOW=实际打开的工程（真实状态，切错红警），
        右侧与歌名行对齐常显「已播 | 剩余」；暂停保持已播值，未知显黄字。"""
        now, color, remain, nxt = "…", dpi.MUT, "", ""
        frac = None                         # None=不显示进度条
        has_proj = False                    # 同步给网页 /state 快照
        if self.ctrl is None:
            now, color = "启动中…", dpi.C_ERR
        elif self.ctrl.busy:
            now, color = "切换中…", dpi.C_WARN
            has_proj = True
        else:
            ws = cubase_ctrl.current_project()
            has_proj = bool(ws)
            if not ws:
                now, color = "（无打开的工程）", dpi.MUT
            else:
                name = cubase_ctrl.project_name_from_title(ws[1])
                want = (self.by_key.get(self.pl_keys[self.cur], {}).get("name")
                        if self.cur is not None and self.cur < len(self.pl_keys)
                        else None)
                if want and name != want:
                    now, color = ("%s（应为 %s）" % (name, want)), dpi.C_ERR
                else:
                    now, color = "%s" % name, dpi.C_OK
                    if want:
                        nk = (self.pl_keys[self.cur + 1]
                              if self.cur + 1 < len(self.pl_keys) else None)
                        ns = self.by_key.get(nk) if nk else None
                        nxt = "%s" % ns["name"] if ns else "（末尾）"
                        d = self.durations.get(self.pl_keys[self.cur], 0.0)
                        if not d:
                            remain = "时长未知"
                        else:
                            played = min(d, max(
                                0.0, self.watch.active()
                                if self.watch is not None else 0.0))
                            frac = min(1.0, played / d)
                            rem = max(0, int(d - played))
                            remain = ("%d:%02d | %d:%02d"
                                      % (int(played) // 60, int(played) % 60,
                                         rem // 60, rem % 60))
        self._banner(now, color, nxt, remain)
        self._progress(frac)
        self._web_snap = web_remote.build_snapshot(self, has_proj)

    def _progress(self, frac):
        """NOW 下方 4px 进度条：None=隐藏（grid_remove 记住原位）。
        每 400ms 随 tick 重绘，窗口拉伸后下个 tick 自然跟上。"""
        if frac is None:
            self.prog.grid_remove()
            return
        self.prog.grid()
        w = self.prog.winfo_width()
        if w <= 1:                          # 刚恢复尚未布局：下个 tick 再画
            return
        self.prog.delete("all")
        self.prog.create_rectangle(0, 0, max(2, int(w * frac)),
                                   self.prog.winfo_height(),
                                   fill=dpi.C_OK, width=0)

    def _banner(self, now, color, nxt, remain):
        """顶部 NOW/NEXT 大字横幅（演出第一眼信息）；长歌名跑马灯。"""
        self.m_now.set(now, color)
        self.m_next.set(nxt or "（无）", dpi.FG if nxt else dpi.MUT)
        self.remain_lbl.config(
            text=remain, fg=dpi.C_WARN if remain == "时长未知" else dpi.FG)


_ABSENT = "（当前不可用）"   # 下拉幽灵项标注：已存设定名在当前环境不在场


class SettingsWindow(tk.Toplevel):
    """设置页：联动端口名称 / 自动播放 / 前台 / 切换确认 / 目录 / VJ显示位置。
    保存即应用——端口热切换监听、目录热生效（工程库变更触发重扫）、VJ显示
    位置热开/关投影，并写 config 持久化。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("设置")
        self.geometry(dpi.scale(self, 600, 420))
        # pack 的 padx/pady 是裸像素不随 DPI 缩放，字大边距小就会顶满，
        # 边距一律过 dpi.scale（下同：键盘自动化/踩钉两窗）
        pad = dpi.scale(self, 12)
        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=pad,
                  pady=(pad, dpi.scale(self, 8)))

        def row(label, var):
            f = tk.Frame(body)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, width=15, anchor="w").pack(side="left")
            tk.Entry(f, textvariable=var).pack(
                side="left", fill="x", expand=True)

        self._menus = []    # 下拉不在 darkify 覆盖范围，建完统一在 darkify 后套色

        def menu_row(label, var, values):
            f = tk.Frame(body)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, width=15, anchor="w").pack(side="left")
            m = tk.OptionMenu(f, var, *values)
            m.config(anchor="w", direction="below")
            m.pack(side="left", fill="x", expand=True)
            self._menus.append(m)

        obs_cfg = (app.ctl.cfg if app.ctl is not None
                   else _load_config().get("obs") or {})
        # VJ显示位置：列本机显示器（Windows 枚举，不依赖 OBS 在线）；已存
        # 屏名不在当前清单则以幽灵项标注显示（设备可能只是没上电），保存
        # 不动它，改选其它项即替换
        mons = [n for n, _r in list_screens()]
        saved_mon = str(obs_cfg.get("projectorMonitor", "") or "")
        mon_opts = ["无"] + mons
        if saved_mon and saved_mon not in mons:
            mon_opts.append(saved_mon + _ABSENT)
        mon_val = (saved_mon if saved_mon in mons
                   else saved_mon + _ABSENT if saved_mon else "无")
        self.mon_var = tk.StringVar(value=mon_val)
        menu_row("VJ显示位置", self.mon_var, mon_opts)
        self.mute_var = tk.BooleanVar(value=bool(obs_cfg.get("vjMute")))
        tk.Checkbutton(body, text="VJ静音播放（视频不出声）",
                       variable=self.mute_var).pack(anchor="w", pady=1)

        tk.Label(body, text="联动端口",
                 anchor="w").pack(fill="x", pady=(pad, 3))
        # 监听端口下拉：列当前在线的输入端口（loopMIDI 虚拟端口就是普通
        # winmm 端口，一并出现）；已存提示名前缀命中在线端口就显示完整
        # 端口名，命中不到则显示幽灵项（端口可能还没建/设备未上电）
        live = list(dict.fromkeys(n for _i, n in mb._in_devices()))
        ins = ["无"] + live
        for hint in dict.fromkeys(h for h in (app.vj_hint, app.kb_hint) if h):
            if not any(hint in n for n in live):
                ins.append(hint + _ABSENT)

        def port_var(hint):
            # 「无」=停用该联动；已存提示名在线显示全名，否则挂幽灵项
            return tk.StringVar(value=next(
                (n for n in live if hint and hint in n),
                hint + _ABSENT if hint else "无"))

        self.vj_var = port_var(app.vj_hint)
        self.kb_var = port_var(app.kb_hint)
        menu_row("VJ 端口名称", self.vj_var, ins)
        menu_row("键盘端口名称", self.kb_var, ins)
        # 移动端遥控：总开关（右侧热点状态）+ 翻谱端口 + 拼好的网页地址
        wcfg = app.web_cfg
        tk.Label(body, text="移动端遥控",
                 anchor="w").pack(fill="x", pady=(pad, 3))
        wf = tk.Frame(body)
        wf.pack(fill="x", pady=1)
        self.web_var = tk.BooleanVar(value=bool(wcfg.get("enabled")))
        tk.Checkbutton(wf, text="启用移动端遥控（热点+网页控制+翻谱推送）",
                       variable=self.web_var).pack(side="left")
        self.web_status = tk.Label(wf, text="热点查询中…", fg=dpi.MUT)
        self.web_status.pack(side="right")
        self.web_ip = "192.168.137.1"    # 热点查询后更新（state().ip）
        self.pg_var = port_var(str(wcfg.get("midiIn") or ""))
        menu_row("翻谱端口名称", self.pg_var, ins)
        self.srv_var = tk.StringVar(value=str(wcfg.get("serverPort") or 8765))
        self.app_var = tk.StringVar(value=str(wcfg.get("appPort") or 8767))
        self.tsk_var = tk.StringVar(value=str(wcfg.get("taskerPort") or 8766))
        # 网页地址行：名字栏与其它行同宽，IP 前缀+端口框拼出完整地址，
        # 右侧指示器=本机端口是否监听中
        wf2 = tk.Frame(body)
        wf2.pack(fill="x", pady=2)
        tk.Label(wf2, text="网页地址", width=15, anchor="w").pack(side="left")
        # 三行地址的 IP 前缀统一定宽（=最长占位行 23 字符），端口框对齐
        self.web_prefix = tk.Label(wf2, text="http://%s:" % self.web_ip,
                                   width=23, anchor="w")
        self.web_prefix.pack(side="left")
        tk.Entry(wf2, textvariable=self.srv_var, width=6).pack(side="left")
        self.web_ok = tk.Label(wf2, text="…", fg=dpi.MUT)
        self.web_ok.pack(side="right")
        # APP 连接地址行：APP 版页面（WebView 内），端口可配置
        wf3 = tk.Frame(body)
        wf3.pack(fill="x", pady=2)
        tk.Label(wf3, text="APP 连接地址", width=15, anchor="w").pack(side="left")
        self.app_prefix = tk.Label(wf3, text="http://%s:" % self.web_ip,
                                   width=23, anchor="w")
        self.app_prefix.pack(side="left")
        tk.Entry(wf3, textvariable=self.app_var, width=6).pack(side="left")
        self.app_ok = tk.Label(wf3, text="…", fg=dpi.MUT)
        self.app_ok.pack(side="right")
        # APP 翻谱地址行：设备 IP 各异 → XXX 占位；端口全局统一
        wf4 = tk.Frame(body)
        wf4.pack(fill="x", pady=2)
        tk.Label(wf4, text="APP 翻谱地址", width=15, anchor="w").pack(side="left")
        tk.Label(wf4, text="http://XXX.XXX.XXX.XXX:", width=23, anchor="w",
                 fg=dpi.MUT).pack(side="left")
        tk.Entry(wf4, textvariable=self.tsk_var, width=6).pack(side="left")
        threading.Thread(target=self._load_web_status, daemon=True).start()
        threading.Thread(target=self._load_web_status, daemon=True).start()
        tk.Label(body, text="自动播放", anchor="w").pack(
            fill="x", pady=(pad, 3))
        self.auto_var = tk.BooleanVar(value=app.auto_var.get())
        self.cont_var = tk.BooleanVar(value=app.cont_play)
        self.top_var = tk.BooleanVar(value=app.top_most)
        self.confirm_var = tk.BooleanVar(value=app.switch_confirm)
        tk.Checkbutton(body, text="自动切换工程（播完自动切下一首）",
                       variable=self.auto_var).pack(anchor="w", pady=1)
        tk.Checkbutton(
            body, text="连续播放工程（切完自动开始播放）",
            variable=self.cont_var, command=self._on_cont).pack(
            anchor="w", pady=1)
        tk.Checkbutton(body, text="保持软件前台（窗口置顶）",
                       variable=self.top_var).pack(anchor="w", pady=1)
        tk.Checkbutton(body, text="切换工程需确认（双击切换时）",
                       variable=self.confirm_var).pack(anchor="w", pady=1)
        tk.Label(body, text="目录", anchor="w").pack(fill="x", pady=(pad, 3))
        self.proj_var = tk.StringVar(value=app.ccfg.get("projectsRoot", ""))
        self.vid_var = tk.StringVar(value=obs_cfg.get("videoRoot", ""))
        row("Cubase 工程库", self.proj_var)
        row("VJ 视频目录", self.vid_var)
        self.closeapps_var = tk.BooleanVar(value=app.exit_close_apps)
        tk.Checkbutton(body, text="退出时关闭被控软件（Cubase/OBS/loopMIDI）",
                       variable=self.closeapps_var).pack(anchor="w",
                                                         pady=(pad, 1))

        btns = tk.Frame(self)
        btns.pack(fill="x", padx=pad, pady=(0, dpi.scale(self, 10)))
        # 主操作绿（与主窗「开始」同级），与「取消」等宽等高、只以颜色区分
        self.save_btn = tk.Button(btns, text="保存并应用", width=10,
                                  command=self._save)
        self.save_btn.pack(side="right")
        tk.Button(btns, text="取消", width=10,
                  command=self.destroy).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.attributes("-topmost", True)
        dpi.darkify(self)
        dpi.flatten(self)       # 表单页文字直接坐窗口底色，去掉面板色斑
        # 下拉不在 darkify 覆盖范围（Menubutton/Menu），统一手动套同族深色
        for m in self._menus:
            m.config(
                bg=dpi.PANEL, fg=dpi.FG, activebackground="#33363d",
                activeforeground=dpi.FG, relief="flat", bd=0,
                highlightthickness=1, highlightbackground=dpi.BORDER,
                highlightcolor=dpi.C_OK, padx=8, pady=3)
            m["menu"].config(
                bg=dpi.FIELD, fg=dpi.FG, activebackground=dpi.SELECT,
                activeforeground=dpi.FG)
        self.save_btn.config(bg=dpi.C_OK, fg="#101418",
                             activebackground="#7fe896")
        # 尺寸适配：最小=内容自然需求；初始不低于规划值与需求值
        self.update_idletasks()
        w = max(dpi.scale(self, 600), self.winfo_reqwidth())
        h = max(dpi.scale(self, 420), self.winfo_reqheight())
        self.geometry("%dx%d" % (w, h))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())

    def _on_cont(self):
        if self.cont_var.get():
            self.auto_var.set(True)

    def _load_web_status(self):
        """勾选行右侧的热点状态 + 网页地址前缀的 IP：PS 子进程要数秒，
        后台线程查完回主线程刷新。"""
        st = hotspot.state()

        def apply():
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            if not st.get("ok"):
                txt, fg = "热点不可用", dpi.C_ERR
            elif st.get("on"):
                txt, fg = "热点已开", dpi.C_OK
            else:
                txt, fg = "热点未开（启用后自动开）", dpi.MUT
            self.web_status.config(text=txt, fg=fg)
            ip = st.get("ip") or self.web_ip
            self.web_ip = ip
            self.web_prefix.config(text="http://%s:" % ip)
            self.app_prefix.config(text="http://%s:" % ip)
            self._refresh_port_status()

    def _refresh_port_status(self):
        """网页/APP 两个本机端口的指示器：connect 探测（服务绑热点 IP）。
        打开设置页与保存后各刷一次；探测在后台线程。"""
        def probe(port, lbl):
            try:
                s = socket.create_connection((self.web_ip, port), timeout=1.5)
                s.close()
                ok = True
            except OSError:
                ok = False

            def apply():
                try:
                    if not self.winfo_exists():
                        return
                except tk.TclError:
                    return
                lbl.config(text="端口可用" if ok else "未监听",
                           fg=dpi.C_OK if ok else dpi.C_ERR)
            try:
                self.after(0, apply)
            except tk.TclError:
                pass
        for var, lbl in ((self.srv_var, self.web_ok),
                         (self.app_var, self.app_ok)):
            try:
                p = int(var.get())
            except ValueError:
                p = 0
            if 0 < p < 65536:
                threading.Thread(target=lambda p=p, l=lbl: probe(p, l),
                                 daemon=True).start()

        try:
            self.after(0, apply)
        except tk.TclError:     # 窗口已关：结果作废
            pass

    def _save(self):
        app = self.app

        def raw(v):
            # 幽灵项=设备暂不在场，去标注保留原设定名，设备恢复即自动接上
            return v[:-len(_ABSENT)] if v.endswith(_ABSENT) else v

        mon = raw(self.mon_var.get())
        mon = "" if mon == "无" else mon
        vj = raw(self.vj_var.get())
        kb = raw(self.kb_var.get())
        vj = "" if vj == "无" else (vj or mb.PORT_HINT)
        kb = "" if kb == "无" else (kb or kbd_auto.KB_PORT_HINT)
        # 移动端遥控：端口数值解析（非法回退默认并提示）
        try:
            srv = int(self.srv_var.get().strip())
            assert srv > 0
        except (ValueError, AssertionError):
            srv = 8765
            app.q.put("网页端口非法，按 8765 处理")
        try:
            tsk = int(self.tsk_var.get().strip())
            assert tsk > 0
        except (ValueError, AssertionError):
            tsk = 8766
            app.q.put("APP 翻谱地址端口非法，按 8766 处理")
        try:
            app_p = int(self.app_var.get().strip())
            assert app_p > 0
        except (ValueError, AssertionError):
            app_p = 8767
            app.q.put("APP 连接地址端口非法，按 8767 处理")
        pg = raw(self.pg_var.get())
        pg = "" if pg == "无" else pg
        web_fields = {"enabled": self.web_var.get(), "serverPort": srv,
                      "appPort": app_p, "taskerPort": tsk, "midiIn": pg}
        web_changed = any(app.web_cfg.get(k) != v
                          for k, v in web_fields.items())
        app.web_cfg.update(web_fields)
        cont = self.cont_var.get()
        auto = self.auto_var.get() or cont
        top = self.top_var.get()
        confirm = self.confirm_var.get()
        mute = self.mute_var.get()
        closeapps = self.closeapps_var.get()
        proj = self.proj_var.get().strip()
        vid = self.vid_var.get().strip()
        # 自动播放 / 前台 / 切换确认：即时生效
        app.cont_play = cont
        app.auto_var.set(auto)
        app._toggle_auto()
        app.top_most = top
        app.root.attributes("-topmost", top)
        app.switch_confirm = confirm
        app.exit_close_apps = closeapps
        # VJ静音：即时生效（没连 OBS 就只存配置，连上后自动同步）
        if app.ctl is not None:
            app.ctl.cfg["vjMute"] = mute
            if app.ctl.is_connected() and not app.ctl.apply_mute():
                app.q.put("VJ静音未生效：%s" % app.ctl.last_error)
        # 目录：视频热生效；工程库变更触发重扫
        if app.ctl is not None and vid:
            app.ctl.cfg["videoRoot"] = vid
        # VJ显示位置：热开/关投影（没连 OBS 就只存配置，连上后自动恢复）
        if app.ctl is not None:
            app.ctl.cfg["projectorMonitor"] = mon
            if mon and not app.ctl.apply_projector():
                app.q.put("VJ显示位置未生效：%s" % app.ctl.last_error)
            elif not mon:
                app.ctl.close_projector()
        rescan = bool(proj) and proj != app.ccfg.get("projectsRoot")
        if proj:
            app.ccfg["projectsRoot"] = proj
        if rescan:      # 后台重扫：目录在网络盘时主线程扫描会卡界面数秒
            app.q.put("工程库已变更，后台重扫…")
            threading.Thread(target=app._load_songs, daemon=True).start()
        # 端口：热切换
        ports_changed = (vj != app.vj_hint) or (kb != app.kb_hint)
        app.vj_hint, app.kb_hint = vj, kb
        # 持久化
        try:
            cfg = _load_config()
            cfg.update({"autoAdvance": auto, "autoPlay": cont,
                        "topMost": top, "switchConfirm": confirm,
                        "exitCloseApps": closeapps,
                        "vjPortHint": vj, "kbPortHint": kb})
            obs = cfg.get("obs") or {}
            if vid:
                obs["videoRoot"] = vid
            obs["projectorMonitor"] = mon
            obs["vjMute"] = mute
            cfg["obs"] = obs
            cub = cfg.get("cubase") or {}
            if proj:
                cub["projectsRoot"] = proj
            cfg["cubase"] = cub
            cfg["webRemote"] = app._web_config_payload()
            _save_config(cfg)
        except (ValueError, OSError) as e:
            app.q.put("配置保存失败：%s" % e)
        if ports_changed:
            app._apply_ports()
        if web_changed:
            app._apply_web()      # 服务重建；端口状态在下次打开设置页时刷新
        app.q.put("设置已保存")
        self.destroy()


_MUTEX = None


def _acquire_single_instance():
    """命名互斥体防双开：双开会双份发走带键/双份监听 MIDI，行为错乱。
    句柄存全局防 GC（句柄关闭=互斥体销毁）；进程退出内核自动释放。"""
    global _MUTEX
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, "Local\\CubeSetlistManager")
    return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS


def main():
    global _CRASH_LOG
    dpi.enable()
    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            0, "Cube Setlist Manager 已在运行", "Cube Setlist Manager", 0x30)
        return
    try:                        # noconsole exe 无 stderr：崩溃栈落 crash.log
        _cl = _HERE / "crash.log"
        if _cl.exists() and _cl.stat().st_size > 512 * 1024:   # 轮转防无限增长
            _cl.replace(_cl.with_name("crash.log.1"))
        _CRASH_LOG = open(_cl, "a", encoding="utf-8")
        faulthandler.enable(_CRASH_LOG)
    except OSError:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
