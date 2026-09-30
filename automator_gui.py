# -*- coding: utf-8 -*-
"""Cube Automator：Studio One 纯自动化集线器（完整版的简化兄弟产品）。
不做歌单/切歌/走带控制——DAW 由使用者自行操作，本程序只做联动：
VJ 视频跟随（时钟+音符触发+熄屏/投影）、键盘自动化（音色切换+移调+
延音踏板）、自动翻谱推送、移动端翻谱配置页（网页端=提示+下载 APP）。
当前工程靠窗口标题识别（Studio One - 歌名）：400ms 轮询，标题变化即在
工程库里按名匹配 .song，自动装载旁挂的 keyboard_automation.json。
键盘自动化窗口直接对应当前识别到的工程（替代完整版的手动选曲）。
与完整版同源模块：midi_bridge/kbd_auto/web_remote/obs_ctrl/daw_ctrl
（只用其只读窗口识别函数，不实例化 DawController）。"""
import ctypes
import collections
import faulthandler
import json
import os
import pathlib
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, filedialog

import advance
import daw_ctrl
import dpi
import hotspot
import kbd_auto
import midi_bridge as mb
import stallguard
import web_remote
from obs_ctrl import ObsController, natural_key, find_processes_by_prefix, \
    list_screens

FOLLOW = {"playing": "播放中", "paused": "已暂停", "stopped": "已停止"}
LOG_MAX = 2000


def _find_cubase_exe():
    r"""扫 Program Files\Steinberg 各版本目录，取版本号最高的 Cubase<N>.exe。"""
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


def _find_s1_exe():
    r"""扫 Program Files\PreSonus 各版本目录，取版本号最高的 Studio One.exe。"""
    base = r"C:\Program Files\PreSonus"
    try:
        dirs = os.listdir(base)
    except OSError:
        return None
    best = None
    for d in dirs:
        if "studio one" not in d.lower():
            continue
        try:
            if "Studio One.exe" not in os.listdir(os.path.join(base, d)):
                continue
        except OSError:
            continue
        m = re.search(r"(\d+)", d)
        v = int(m.group(1)) if m else 0
        if best is None or v > best[0]:
            best = (v, os.path.join(base, d, "Studio One.exe"))
    return best[1] if best else None


# 各底座的默认路径（工程库默认值；Automator 只用 projectsRoot）
DAW_DEFAULTS = {
    "cubase": {"exe": (_find_cubase_exe()
                       or r"C:\Program Files\Steinberg\Cubase 15\Cubase15.exe"),
               "root": r"C:\Users\XKZ\Documents\Cubase Projects"},
    "studioone": {"exe": (_find_s1_exe()
                          or r"C:\Program Files\PreSonus\Studio One 7"
                             r"\Studio One.exe"),
                  "root": r"C:\Users\XKZ\Documents\Studio One\Songs"},
}


def daw_settings(cfg, daw):
    """DAW 路径段归一（与完整版同规则：dawSettings > 旧 cubase 段 > 默认）。"""
    s = dict(autoSave=True)
    s.update(cfg.get("cubase") or {})
    s.update(cfg.get("dawSettings") or {})
    legacy = s.pop("cubaseExe", None)
    if legacy:
        s.setdefault("dawExe", legacy)
    s.setdefault("dawExe", DAW_DEFAULTS[daw]["exe"])
    s.setdefault("projectsRoot", DAW_DEFAULTS[daw]["root"])
    return s

if getattr(sys, "frozen", False):   # PyInstaller exe：数据文件放 exe 同目录
    _HERE = pathlib.Path(sys.executable).resolve().parent
else:
    _HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = _HERE / "config.json"
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


def _err(e):
    """异常进日志的统一格式：消息为空时退回异常类名。"""
    return str(e).strip() or type(e).__name__


def scan_library(root, ext=".cpr"):
    """工程库：<root>/<队伍>/<歌>/<歌><ext> → [{key, team, name, path}]。"""
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
            path = tdir / name / (name + ext)
            if path.exists():
                out.append({"key": "%s/%s" % (team, name), "team": team,
                            "name": name, "path": str(path)})
    return out


class Marquee(tk.Entry):
    """跑马灯：只读 Entry + xview 像素滚动（与完整版同款组件）。"""

    NO_RING = True
    STEP_MS = 40
    SPEED = 3.0
    PAUSE_TICKS = 8

    def __init__(self, master, max_chars=30, **kw):
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("justify", "left")
        kw.setdefault("takefocus", False)
        kw.setdefault("bg", dpi.PANEL)
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
            self._hold = self.PAUSE_TICKS
        if fg:
            self.config(fg=fg, disabledforeground=fg)
        self._schedule()

    def _overflow(self):
        w = self.winfo_width()
        if w <= 1:
            return len(self._full) > self.max_chars
        return self._font.measure(self._full) > w - 8

    def _schedule(self):
        if self._overflow() and self._job is None:
            self._job = self.after(self.STEP_MS, self._step)

    def _step(self):
        self._job = None
        try:
            cont = self._step_body()
        except tk.TclError:
            return                          # 组件销毁：跑马灯自然退场
        except Exception:
            return                          # 单步异常不断链：下个 set() 重挂
        if cont:
            self._job = self.after(self.STEP_MS, self._step)

    def _step_body(self):
        if not self._overflow():
            self._cancel()
            self.xview_moveto(0)
            return False
        total = max(1, self._font.measure(self._full))
        span = max(0.0, 1.0 - self.winfo_width() / total)
        cpx = total / max(1, len(self._full))
        if self._hold > 0:
            self._hold -= 1
        else:
            if self._frac >= span and self._dir == 1:
                self._dir = -1
                self._hold = self.PAUSE_TICKS
            elif self._frac <= 0 and self._dir == -1:
                self._dir = 1
                self._hold = self.PAUSE_TICKS
            else:
                self._frac = min(span, max(
                    0.0, self._frac
                    + self._dir * self.SPEED * cpx * self.STEP_MS / 1000
                    / total))
                self.xview_moveto(self._frac)
        return True

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
        cfg = _load_config()
        self.daw = str(cfg.get("daw") or "studioone")   # Automator 缺省 S1 底座
        self.facts = daw_ctrl.FACTS.get(self.daw) or daw_ctrl.STUDIOONE
        daw_ctrl.set_active(self.facts)
        root.title("Cube Automator " + self.facts["display_name"])
        root.geometry(dpi.scale(root, 860, 560))
        root.minsize(dpi.scale(root, 820), dpi.scale(root, 520))
        self.ccfg = daw_settings(cfg, self.daw)
        self.lite = True                # web_remote 简化版分流标志（页面/门禁）
        self.vj_hint = str(cfg.get("vjPortHint") or "")
        self.kb_hint = str(cfg.get("kbPortHint") or "")
        self.clock_hint = str(cfg.get("clockPortHint") or "")
        self.settings_win = None
        self.jcfg = dict(kbd_auto.DEFAULT_JUNO)
        self.jcfg.update(cfg.get("juno") or {})
        self.axcfg = dict(kbd_auto.DEFAULT_AX)
        self.axcfg.update(cfg.get("ax09") or {})
        self.web_cfg = dict(web_remote.DEFAULT_WEB_REMOTE)
        self.web_cfg.update(cfg.get("webRemote") or {})
        self.web = None
        self._web_snap = {}
        # web_remote.build_snapshot 需要的桩字段（简化版无歌单语义，恒空）
        self.pl_keys = []
        self.by_key = {}
        self.by_name = {}               # 歌名 → song（标题匹配索引）
        self.durations = {}
        self.cur = None
        self.switch_confirm = False
        self.ctrl = None                # 无 DawController：走带/切歌不存在
        # 当前工程（标题识别）与音色映射
        self.slots = {}
        self.ax_slots = {}
        self.cur_song = None
        self.cur_song_path = None       # kbd 窗热同步判定（kbd_auto）
        self._cur_title = None
        self._kb_warned = set()
        self.kb_err = ""
        self._kb_last = ("待机", dpi.MUT)
        self.juno_shift = 0
        self.pedal_held = {}
        self.q = queue.Queue()
        self.calls = stallguard.BoundedCallQueue(200)   # 跨线程 GUI 调用
        self.calls_urgent = collections.deque()  # 紧急调用（对称预留）
        self._obs_apply_lock = threading.Lock()  # 设置保存的 OBS 热应用串行化
        root.report_callback_exception = self._on_ui_error
        self.ctl = self.sync = self.port = self.watch = None
        self.clock_port = None
        self._startup_done = False       # 门控 _apply_ports：启动期禁热切换
        self._pending_ports = False      # 启动期内保存过端口（完成后补热切换）
        self.kb_port = self.switcher = self.ax_switcher = None
        self.kbd_win = None
        self.start_err = ""
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_exit)
        # 主线程停摆黑匣子（完整版同款）：停摆现场落盘 stall.log
        self._hb = time.monotonic()     # 主线程最近心跳（monotonic）
        self._hb_tag = ""               # 正在执行的主线程回调名（取证）
        self._stall_wd = stallguard.StallWatchdog(
            heartbeat=lambda: self._hb,
            snapshot=lambda: {"calls": self.calls.qsize(),
                              "q": self.q.qsize(),
                              "dropped": getattr(self.calls, "dropped", 0),
                              "tag": self._hb_tag},
            path=_HERE / "stall.log",
            notify=self.q.put)
        self._stall_wd.start()
        threading.Thread(target=self._startup, daemon=True).start()
        root.after(400, self._tick)
        root.after(50, self._drain_calls)

    # ---- 界面 ----

    def _build(self):
        # 顶：当前工程横幅（与完整版 NOW/NEXT 横幅同构：已播与歌名同行右侧，
        # 无内容不占行=完整版进度条手法）。「当前状态」在底部栏最左=完整版同位
        banner = tk.Frame(self.root)
        banner.pack(fill="x", padx=12, pady=(10, 4))
        banner.columnconfigure(0, weight=1)
        tk.Label(banner, text="当前工程",
                 font=("Microsoft YaHei UI", 9, "bold"),
                 anchor="w").grid(row=0, column=0, sticky="w")
        self.now_lbl = Marquee(banner, max_chars=30,
                               font=("Microsoft YaHei UI", 20, "bold"))
        self.now_lbl.grid(row=1, column=0, sticky="ew")
        self.elapsed_lbl = tk.Label(banner, text="",
                                    font=("Microsoft YaHei UI", 14, "bold"))
        self.elapsed_lbl.grid(row=1, column=1, sticky="e", padx=(16, 0))
        self.elapsed_lbl.grid_remove()   # 无内容不占行（同完整版进度条手法）
        # 中：VJ / 键盘自动化 监控栏（与完整版同构）
        self.rows = {}
        mons = tk.Frame(self.root)
        mons.pack(fill="x", padx=12, pady=4)
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
        # 日志栏
        logf = tk.LabelFrame(self.root, text="日志")
        logf.pack(fill="both", expand=True, padx=12, pady=4)
        tk.Button(logf, text="清空日志",
                  command=lambda: self.log.delete(0, "end")).pack(
            anchor="e", padx=6, pady=(2, 4))
        self.log = tk.Listbox(logf, height=8, activestyle="none",
                              font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        # 底：操作行（无走带控制），与完整版同构：「当前状态」在底栏最左，
        # 「自动化」组在状态右侧剩余空间内居中（空列与右列等权重均分剩余
        # 空间；状态 padx 收进本列，左右间隙严格对称），「退出」底边与
        # 组内按钮同一基线
        ctl = tk.Frame(self.root)
        ctl.pack(fill="x", padx=12, pady=(4, 10))
        ctl.columnconfigure(1, weight=1)
        ctl.columnconfigure(3, weight=1)
        self.state_lbl = tk.Label(ctl, text="当前状态：未在播放",
                                  font=("Microsoft YaHei UI", 14, "bold"))
        self.state_lbl.grid(row=0, column=0, sticky="w", padx=(12, 0))
        mid = tk.Frame(ctl)
        mid.grid(row=0, column=2)
        g1 = tk.LabelFrame(mid, text="自动化")
        g1.pack(side="left")
        tk.Button(g1, text="键盘自动化", width=9,
                  command=self._open_kbd).pack(side="left", padx=3, pady=3)
        tk.Button(g1, text="设置", width=8,
                  command=self._open_settings).pack(side="left", padx=3, pady=3)
        self.btn_black = tk.Button(g1, text="熄屏", width=5,
                                   command=self._blackout)
        self.btn_black.pack(side="left", padx=(10, 3), pady=3)
        right = tk.Frame(ctl)
        right.grid(row=0, column=3, sticky="ens")   # 纵向拉满、贴右
        tk.Button(right, text="退出", width=5,
                  command=self._on_exit).pack(side="bottom", padx=(0, 6),
                                              pady=3)
        dpi.darkify(self.root)
        self.log.config(fg=dpi.LOG_FG)
        self.btn_black.config(bg="#a03030", fg="#ffffff",
                              activebackground="#c04444")
        self.m_now = self.now_lbl
        self.m_map = self.rows[("kb", "音色映射")]
        self.m_last = self.rows[("kb", "最近切换")]

    # ---- 退出 ----

    def _on_exit(self):
        if not messagebox.askyesno("退出", "确定退出 Cube Automator？"):
            return
        for closer in ((lambda: self.port.close()),
                       (lambda: self.kb_port.close()),
                       (lambda: self.clock_port.close())):
            try:
                closer()
            except Exception:
                pass
        threading.Thread(target=self._exit_worker, daemon=False).start()
        self.root.destroy()

    def _exit_worker(self):
        """退出收尾（窗口已关）：停网页栈→熄屏清源。不关闭任何其它软件
        （OBS/loopMIDI/DAW 生命周期均由用户自管）。"""
        if self.web is not None:
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

    # ---- 熄屏（VJ 一键黑场）----

    def _blackout(self):
        def run():
            if self.ctl is None:
                self.q.put("OBS 未就绪，熄屏未执行")
                return
            if self.ctl.stop_media():
                self.q.put("已熄屏（视频已停并清空，下次启动不续播）")
            else:
                self.q.put("熄屏失败：%s" % self.ctl.last_error)

        threading.Thread(target=run, daemon=True).start()

    # ---- 后台启动 ----

    def _on_obs_connected(self):
        """OBS 连上（含重连）后回调（连接线程）：报状态，按设置恢复投影。"""
        self.q.put("OBS 已连接")
        if self.ctl.cfg.get("projectorMonitor", "") not in ("", None, -1) \
                and not self.ctl.apply_projector():
            self.q.put("VJ显示位置未恢复：%s" % self.ctl.last_error)

    def _startup(self):
        """后台启动：loopMIDI 探测（不拉起）→ VJ 链（OBS 连接/时钟/音符）→
        键盘自动化 → 移动端遥控 → 工程库扫描。逐组降级，一组失败只废该组。"""
        try:
            # 简化版不管理其它软件的生命周期：loopMIDI 不在场只提示，
            # 由用户手动启动（obs.autoStart=false 同理，OBS 只连不拉起）
            if not find_processes_by_prefix("loopmidi"):
                self.q.put("loopMIDI 未运行——请手动启动后重启本程序"
                           "（简化版不自动拉起其它软件）")
            # VJ 链：OBS 客户端 → 走带同步 → MIDI 监听
            try:
                obs_cfg = _load_config()["obs"]
                # 简化版铁律：绝不拉起 OBS（只连不拉），不受配置残留影响
                obs_cfg["autoStart"] = False
                self.ctl = ObsController(obs_cfg)
                self.ctl.on_connected = self._on_obs_connected
                self.sync = mb.TransportSync(self.ctl, on_event=self.q.put)
                # AdvanceWatch 只做观测（三态/已播）：永不 armed，无自动推进
                self.watch = advance.AdvanceWatch(
                    on_finished=lambda: None, on_stop_transport=lambda: None,
                    on_event=self.q.put, clock_timeout=mb.CLOCK_TIMEOUT)
                try:
                    if self.vj_hint:
                        self.port = mb.MidiIn(
                            self.vj_hint,
                            mb.note_handler(self.ctl, self.sync,
                                            report=self.q.put))
                    else:
                        self.q.put("VJ 触发监听已停用")
                except SystemExit as e:
                    self.port = None
                    self.q.put("MIDI 监听未启动：%s" % e)
                # 时钟监听：独立端口（与 VJ 音符口解耦）
                try:
                    if self.clock_hint:
                        self.clock_port = mb.MidiIn(
                            self.clock_hint, lambda n, v: None,
                            on_clock=lambda: (self.sync.on_clock(),
                                              self.watch.on_clock()))
                        self.q.put("时钟监听已启动（%s）" % self.clock_port.name)
                    else:
                        self.q.put("时钟监听已停用（未设时钟端口：走带三态/"
                                   "已播不可用）")
                except SystemExit as e:
                    self.clock_port = None
                    self.q.put("时钟监听未启动：%s（走带三态不可用）" % e)
                self.ctl.enabled = True
                self.ctl.start()
                threading.Thread(target=self._watch, daemon=True).start()
                if self.port is not None:
                    self.q.put("MIDI 监听已启动（%s）" % self.port.name)
            except Exception as e:
                self.start_err = "VJ 链未启动：%s" % _err(e)
                self.q.put(self.start_err)
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
            # 移动端遥控 + 翻谱推送（lite 页面分流由 self.lite 决定）
            try:
                self.web = web_remote.WebRemote(self, self.web_cfg)
                self.web.startup()
            except Exception as e:
                self.q.put("移动端遥控未启动：%s" % _err(e))
            try:
                self._scan_library()
            except Exception as e:
                self.q.put("工程库扫描失败：%s" % _err(e))
        except (Exception, SystemExit) as e:   # SystemExit=MidiIn 端口降级
            self.start_err = self.start_err or "启动异常：%s" % _err(e)
            self.q.put(self.start_err)
        finally:
            self._startup_done = True
            if self._pending_ports:      # 消费启动期票据：补热切换（幂等清零）
                self._pending_ports = False
                self.calls.put(self._apply_ports)

    def _watch(self):
        while True:
            try:
                self.sync.poll()
                self.watch.poll()
            except Exception as e:
                try:
                    self.q.put("后台监控异常（已自动恢复）：%s" % _err(e))
                except Exception:
                    pass
            time.sleep(mb.POLL_SEC)

    # ---- 工程库与标题识别 ----

    def _scan_library(self):
        root = self.ccfg["projectsRoot"]
        if not os.path.isdir(root):
            self.q.put("工程库不存在：%s（设置页「%s 工程库」选择）"
                       % (root, self.facts["display_name"]))
            return
        songs = scan_library(root, self.facts["song_ext"])
        self.by_key = {s["key"]: s for s in songs}
        by_name = {}
        for s in songs:
            if s["name"] in by_name:
                self.q.put("工程库有重名歌曲《%s》，标题匹配取第一个" % s["name"])
            else:
                by_name[s["name"]] = s
        self.by_name = by_name
        self.q.put("工程库扫描：%d 首（%s）" % (len(songs), root))
        self.calls.put(lambda: self._apply_title(force=True))

    def _apply_title(self, force=False):
        """标题→当前工程：变化时匹配工程库并装载音色映射（主线程调用；
        装载 IO 后台执行——.song 旁挂 JSON 在网络盘时主线程读=无界卡顿，
        审计 D 项；代际守卫防装载期间标题又变时的跨歌错装）。
        未打开工程/库里没有 → 清空映射；匹配到 → 后台装载并同步键盘窗。"""
        ws = daw_ctrl.current_project()
        name = daw_ctrl.project_name_from_title(ws[1]) if ws else None
        if not force and name == self._cur_title:
            return
        self._cur_title = name
        self._title_gen = getattr(self, "_title_gen", 0) + 1
        if self.watch is not None:
            self.watch.reset()   # 换工程=已播从头计（横幅时长语义按歌）
        if not name:
            self.slots, self.ax_slots = {}, {}
            self.cur_song = self.cur_song_path = None
            self.q.put("未打开工程（在 %s 里载入后自动识别）"
                       % self.facts["display_name"])
            return
        song = self.by_name.get(name)
        if song is None:
            # 回退：包含式匹配。S1 标题显示的是歌名元数据，与 .song 文件名
            # 可能不完全一致（实测《3.21演出》↔lingo3.21演出.song）；
            # 唯一命中才采用，多个/零个都按未匹配处理（防错装映射）
            cand = [s for s in self.by_name.values()
                    if name in s["name"] or s["name"] in name]
            if len(cand) == 1:
                song = cand[0]
                self.q.put("《%s》按包含式匹配到 %s" % (name, song["name"]))
        if song is None:
            self.slots, self.ax_slots = {}, {}
            self.cur_song = self.cur_song_path = None
            self.q.put("《%s》不在工程库，未载入音色映射（检查工程库根目录）"
                       % name)
            return
        self.slots, self.ax_slots = {}, {}      # 旧映射即刻失效，装载后补
        self._kb_warned = set()
        gen = self._title_gen
        path, song_name = song["path"], song["name"]

        def run():
            slots = kbd_auto.load_slots(path)
            ax = kbd_auto.load_slots(path, "ax")
            if gen != self._title_gen:
                return              # 装载期间标题又变：丢弃旧结果
            self.slots = slots
            self.ax_slots = ax
            self.cur_song = song
            self.cur_song_path = path
            self.q.put("已识别当前工程《%s》：JUNO %d + AX-09 %d 个音符映射"
                       % (song_name, len(slots), len(ax)))
            self.calls.put(lambda: self._kbd_sync(song))
        threading.Thread(target=run, daemon=True).start()

    def _kbd_sync(self, song):
        """键盘自动化窗口热同步（仅主线程跑——worker 摸 Tk 会封送阻塞）。"""
        if self.kbd_win is not None and self.kbd_win.winfo_exists():
            self.kbd_win.set_song(song)

    # ---- 键盘自动化（音符分发与完整版同构，无切歌忽略分支）----

    def _on_kb_msg(self, status, d1, d2):
        """Keyboard Automation 端口音符：36-39→JUNO 全局移调、60-69→JUNO、
        72-77→AX-09 查映射切音色；E2(40)/A2(45)=延音踏板键。"""
        st = status & 0xF0
        if st == 0x90 and d2 == 0:
            st = 0x80
        if st not in (0x90, 0x80):
            return
        down = st == 0x90
        if d1 in kbd_auto.SHIFT_NOTES:
            if not down:
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
        slot = slots.get(d1)
        if not slot:
            if d1 not in self._kb_warned:
                self._kb_warned.add(d1)
                self.q.put("音符 %s 未配置音色映射（用「键盘自动化」窗口录制）"
                           % kbd_auto.note_name(d1))
            return
        switcher.submit(slot, why=kbd_auto.note_name(d1))

    def _on_kb_result(self, desc, err):
        """ToneSwitcher 线程回调：最近一次结果（进监控栏，只写属性）。"""
        self._kb_last = (("%s失败：%s" % (desc, err)) if err
                         else ("已发送：%s" % desc),
                         dpi.C_ERR if err else dpi.C_OK)

    def _open_kbd(self):
        if self.cur_song is None:
            self.q.put("当前没有匹配到的工程（在 %s 里载入 .song 后自动识别）"
                       % self.facts["display_name"])
            return
        if self.kbd_win is None or not self.kbd_win.winfo_exists():
            self.kbd_win = kbd_auto.KeyboardAutoWindow(self)
        self.kbd_win.set_song(self.cur_song)      # 跟随当前识别到的工程
        self.kbd_win.lift()

    def _open_settings(self):
        if self.settings_win is None or not self.settings_win.winfo_exists():
            self.settings_win = SettingsWindow(self)
        self.settings_win.lift()

    # ---- 端口/遥控热切换与持久化（与完整版同构）----

    def _apply_ports(self):
        """设置页改了端口名称后热切换监听（主线程调用）。启动未完成时禁
        热切换——启动线程与主线程无锁交叉会泄漏句柄。"""
        if not self._startup_done:
            # 幂等票据：置位后由启动线程 finally（补投队列）或本方法的
            # 后续重入执行——谁消费谁清零，双消费路径天然互斥不双跑
            self._pending_ports = True
            self.q.put("端口名称已保存，启动完成后自动热切换")
            return
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
                                        report=self.q.put))
                    self.q.put("VJ 监听已切换（%s）" % self.port.name)
                except SystemExit as e:
                    self.q.put("VJ 监听未启动：%s" % e)
        else:
            self.q.put("VJ 服务未就绪：端口名称已保存，重启程序后生效")
        # 时钟监听热切换（停用/换口=走带基线复位，仅时钟口实际变化时执行）
        clock_changed = getattr(self, "_clock_changed", True)
        if clock_changed and self.clock_port is not None:
            try:
                self.clock_port.close()
            except Exception:
                pass
        if clock_changed:
            self.clock_port = None
            if self.sync is not None:
                self.sync.reset()
            if self.watch is not None:
                self.watch.reset()
        if self.ctl is not None and self.sync is not None \
                and self.watch is not None:
            if not self.clock_hint:
                if clock_changed:
                    self.q.put("时钟监听已停用（未设时钟端口：走带三态/已播"
                               "不可用）")
            elif clock_changed:
                try:
                    self.clock_port = mb.MidiIn(
                        self.clock_hint, lambda n, v: None,
                        on_clock=lambda: (self.sync.on_clock(),
                                          self.watch.on_clock()))
                    self.q.put("时钟监听已切换（%s）" % self.clock_port.name)
                except SystemExit as e:
                    self.q.put("时钟监听未启动：%s" % e)
        else:
            self.q.put("时钟服务未就绪：端口名称已保存，重启程序后生效")
        if self.kb_port is not None:
            try:
                self.kb_port.close()
            except Exception:
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

    def _persist_config(self, **kv):
        try:
            cfg = _load_config()
            cfg.update(kv)
            _save_config(cfg)
        except (ValueError, OSError, RuntimeError) as e:
            self.q.put("配置保存失败：%s" % e)

    def _web_config_payload(self, **fields):
        """webRemote 持久化载荷：设备表永远取注册表现值。"""
        cfg = dict(self.web_cfg)
        cfg.update(fields)
        if self.web is not None:
            cfg["devices"] = self.web.registry.snapshot()
        return cfg

    def _persist_web_remote(self):
        self._persist_config(webRemote=self._web_config_payload())

    def _apply_web(self):
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

    # ---- 主线程轮询 ----

    DOT_CELLS = {("vj", "端口状态"), ("vj", "OBS 状态"),
                 ("vj", "走带跟随"), ("kb", "端口状态")}

    def _set(self, name, text, color=dpi.MUT):
        if name in self.DOT_CELLS and text not in ("-", ""):
            text = "● " + text
        self.rows[name].config(text=text, fg=color)

    def _on_ui_error(self, exc, val, _tb):
        self.q.put("界面异常：%s：%s" % (exc.__name__, val))

    def _run_call(self, fn):
        """执行单个跨线程回调：tag 供看门狗取证；按 BaseException 捕——
        MidiIn 端口降级抛 SystemExit，py3.14 实测漏过去会冲出 mainloop
        终止进程（report_callback_exception 都不经过=闪退）。"""
        self._hb_tag = getattr(fn, "__name__", "<lambda>")
        try:
            fn()
        except BaseException as e:
            try:
                self.log.insert("end", time.strftime("[%H:%M:%S] ")
                                + "动作执行异常（已恢复）：%s: %s"
                                % (type(e).__name__, e))
                self.log.itemconfigure(self.log.size() - 1,
                                       foreground=dpi.C_ERR)
                self.log.see("end")
            except Exception:
                pass
        self._hb_tag = ""

    def _drain_calls(self):
        """跨线程 GUI 调用队列的快速排空（50ms 独立循环，完整版同款；
        原来搭在 400ms 状态轮询车上且逐条无兜底）。紧急队列优先+普通批次
        合流（幂等回调恢复后成批重复，只执行最后一次）。"""
        self._hb = time.monotonic()     # 喂看门狗心跳
        while self.calls_urgent:
            self._run_call(self.calls_urgent.popleft())
        batch = []
        while True:
            try:
                batch.append(self.calls.get_nowait())
            except queue.Empty:
                break
        for fn in stallguard.coalesce(batch):
            self._run_call(fn)
        self.root.after(50, self._drain_calls)

    def urgent(self, fn):
        """紧急 GUI 调用：插队到普通指令前执行（对称预留）。"""
        self.calls_urgent.append(fn)

    def _tick(self):
        self._hb = time.monotonic()     # 喂看门狗心跳
        self._hb_tag = "_tick_body"
        try:
            self._tick_body()
        except BaseException as e:      # SystemExit 实测会冲出 mainloop 闪退
            try:
                self.log.insert("end", time.strftime("[%H:%M:%S] ")
                                + "状态刷新异常（已自动恢复）：%s" % _err(e))
                self.log.itemconfigure(self.log.size() - 1,
                                       foreground=dpi.C_ERR)
                self.log.see("end")
            except Exception:
                pass
        self._hb_tag = ""
        self.root.after(400, self._tick)

    def _tick_body(self):
        # 标题识别（主线程 400ms；完整版 _tick_banner 同款轮询点）
        self._apply_title()
        if self.vj_hint:
            # winmm 枚举降频到 ~2s（每 5 个 tick 查一次）：主线程少碰与
            # midiInOpen/Close 共进程级锁的 winmm 调用（完整版同款）
            if getattr(self, "_enum_n", 0) <= 0:
                self._vj_hit = mb._pick(mb._in_devices(), self.vj_hint)
                self._enum_n = 5
            self._enum_n -= 1
            hit = self._vj_hit
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
        # 音色映射格：当前标题识别到的工程
        if self.cur_song is not None:
            n, a = len(self.slots), len(self.ax_slots)
            self.m_map.set("%s：JUNO %d/10 + AX %d/6"
                           % (self.cur_song["name"], n, a),
                           dpi.C_OK if n or a else dpi.C_WARN)
        elif self._cur_title:
            self.m_map.set("《%s》不在工程库" % self._cur_title, dpi.C_WARN)
        else:
            self._set(("kb", "音色映射"), "（未匹配工程）", dpi.MUT)
        text, color = self._kb_last
        self.m_last.set(text, color)
        # 横幅：歌名 + 状态 + 已播
        if self._cur_title:
            self.m_now.set(self._cur_title,
                           dpi.C_OK if self.cur_song else dpi.C_WARN)
        else:
            self.m_now.set("（无打开的工程）", dpi.MUT)
        st_t, st_c = {"playing": ("播放中", dpi.C_OK),
                      "paused": ("已暂停", dpi.C_WARN),
                      "stopped": ("未在播放", dpi.MUT)}[self._transport_state()]
        self.state_lbl.config(text="当前状态：" + st_t, fg=st_c)
        played = self.watch.active() if self.watch is not None else 0.0
        shown = played > 0
        self.elapsed_lbl.config(
            text=("已播 %d:%02d" % (int(played) // 60, int(played) % 60))
            if shown else "")
        # 无已播时长不占行：歌名行保持贴顶（同完整版进度条 grid_remove 手法）
        self.elapsed_lbl.grid() if shown else self.elapsed_lbl.grid_remove()
        self._web_snap = web_remote.build_snapshot(
            self, bool(self._cur_title), self._cur_title)
        follow = self.log.yview()[1] > 0.99
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", time.strftime("[%H:%M:%S] ") + msg)
            if msg.startswith("界面异常"):
                self.log.itemconfigure(self.log.size() - 1,
                                       foreground=dpi.C_ERR)
        if self.log.size() > LOG_MAX:
            self.log.delete(0, self.log.size() - LOG_MAX)
        if follow:
            self.log.see("end")

    def _transport_state(self):
        """走带三态：playing=时钟活跃；paused=收过时钟但断流；stopped=从未
        收到（简化版无切歌控制器，直接看时钟）。"""
        w = self.watch
        if w is None:
            return "stopped"
        if w.is_transport_live():
            return "playing"
        return "paused" if w.ever_live() else "stopped"


_ABSENT = "（当前不可用）"


class SettingsWindow(tk.Toplevel):
    """设置页（简化版）：VJ显示位置/静音、联动端口、移动端遥控（翻谱）、
    目录（工程库/视频）。保存即应用并持久化。
    （无「行为」栏——简化版不置顶、退出不关其它软件，见 _exit_worker。）"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("设置")
        self.geometry(dpi.scale(self, 600, 430))
        pad = dpi.scale(self, 12)
        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=pad,
                  pady=(pad, dpi.scale(self, 8)))

        def row(label, var, browse=False):
            f = tk.Frame(body)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, width=15, anchor="w").pack(side="left")
            tk.Entry(f, textvariable=var).pack(
                side="left", fill="x", expand=True)
            if browse:
                def pick():
                    d = filedialog.askdirectory(
                        initialdir=var.get() or "/", parent=self,
                        title="选择文件夹")
                    if d:
                        var.set(d.replace("/", "\\"))

                tk.Button(f, text="浏览…", width=6,
                          command=pick).pack(side="left", padx=(6, 0))

        self._menus = []

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
        mons = [n for n, _r in list_screens()]
        saved_mon = str(obs_cfg.get("projectorMonitor", "") or "")
        mon_opts = ["无"] + mons
        if saved_mon and saved_mon not in mons:
            mon_opts.append(saved_mon + _ABSENT)
        mon_val = (saved_mon if saved_mon in mons
                   else saved_mon + _ABSENT if saved_mon else "无")
        self.mon_var = tk.StringVar(value=mon_val)
        menu_row("VJ显示位置", self.mon_var, mon_opts)
        self.mute_var = tk.BooleanVar(value=bool(obs_cfg.get("vjMute", True)))
        tk.Checkbutton(body, text="VJ静音播放（视频不出声）",
                       variable=self.mute_var).pack(anchor="w", pady=1)

        tk.Label(body, text="联动端口",
                 anchor="w").pack(fill="x", pady=(pad, 3))
        live = list(dict.fromkeys(n for _i, n in mb._in_devices()))
        ins = ["无"] + live
        for hint in dict.fromkeys(
                h for h in (app.vj_hint, app.kb_hint, app.clock_hint) if h):
            if not any(hint in n for n in live):
                ins.append(hint + _ABSENT)

        def port_var(hint):
            return tk.StringVar(value=next(
                (n for n in live if hint and hint in n),
                hint + _ABSENT if hint else "无"))

        self.clock_var = port_var(app.clock_hint)
        self.vj_var = port_var(app.vj_hint)
        self.kb_var = port_var(app.kb_hint)
        menu_row("时钟端口名称", self.clock_var, ins)
        menu_row("VJ 端口名称", self.vj_var, ins)
        menu_row("键盘端口名称", self.kb_var, ins)
        # 移动端遥控（翻谱）
        wcfg = app.web_cfg
        tk.Label(body, text="移动端遥控",
                 anchor="w").pack(fill="x", pady=(pad, 3))
        wf = tk.Frame(body)
        wf.pack(fill="x", pady=1)
        self.web_var = tk.BooleanVar(value=bool(wcfg.get("enabled")))
        tk.Checkbutton(wf, text="启用移动端遥控（热点+网页+翻谱推送）",
                       variable=self.web_var).pack(side="left")
        self.web_status = tk.Label(wf, text="热点查询中…", fg=dpi.MUT)
        self.web_status.pack(side="right")
        self.web_ip = "192.168.137.1"
        self.pg_var = port_var(str(wcfg.get("midiIn") or ""))
        menu_row("翻谱端口名称", self.pg_var, ins)
        self.srv_var = tk.StringVar(value=str(wcfg.get("serverPort") or 8765))
        self.app_var = tk.StringVar(value=str(wcfg.get("appPort") or 8767))
        self.tsk_var = tk.StringVar(value=str(wcfg.get("taskerPort") or 8766))
        wf2 = tk.Frame(body)
        wf2.pack(fill="x", pady=2)
        tk.Label(wf2, text="网页地址", width=15, anchor="w").pack(side="left")
        self.web_prefix = tk.Label(wf2, text="http://%s:" % self.web_ip,
                                   width=23, anchor="w")
        self.web_prefix.pack(side="left")
        tk.Entry(wf2, textvariable=self.srv_var, width=6).pack(side="left")
        self.web_ok = tk.Label(wf2, text="…", fg=dpi.MUT)
        self.web_ok.pack(side="right")
        wf3 = tk.Frame(body)
        wf3.pack(fill="x", pady=2)
        tk.Label(wf3, text="APP 连接地址", width=15, anchor="w").pack(side="left")
        self.app_prefix = tk.Label(wf3, text="http://%s:" % self.web_ip,
                                   width=23, anchor="w")
        self.app_prefix.pack(side="left")
        tk.Entry(wf3, textvariable=self.app_var, width=6).pack(side="left")
        self.app_ok = tk.Label(wf3, text="…", fg=dpi.MUT)
        self.app_ok.pack(side="right")
        wf4 = tk.Frame(body)
        wf4.pack(fill="x", pady=2)
        tk.Label(wf4, text="APP 翻谱地址", width=15, anchor="w").pack(side="left")
        tk.Label(wf4, text="http://XXX.XXX.XXX.XXX:", width=23, anchor="w",
                 fg=dpi.MUT).pack(side="left")
        tk.Entry(wf4, textvariable=self.tsk_var, width=6).pack(side="left")
        threading.Thread(target=self._load_web_status, daemon=True).start()
        # 目录
        tk.Label(body, text="目录", anchor="w").pack(fill="x", pady=(pad, 3))
        self.proj_var = tk.StringVar(value=app.ccfg.get("projectsRoot", ""))
        self.vid_var = tk.StringVar(value=obs_cfg.get("videoRoot", ""))
        row("%s 工程库" % app.facts["display_name"], self.proj_var, browse=True)
        row("VJ 视频目录", self.vid_var, browse=True)

        btns = tk.Frame(self)
        btns.pack(fill="x", padx=pad, pady=(0, dpi.scale(self, 10)))
        self.save_btn = tk.Button(btns, text="保存并应用", width=10,
                                  command=self._save)
        self.save_btn.pack(side="right")
        tk.Button(btns, text="取消", width=10,
                  command=self.destroy).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.attributes("-topmost", True)
        dpi.darkify(self)
        dpi.flatten(self)
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
        self.update_idletasks()
        w = max(dpi.scale(self, 600), self.winfo_reqwidth())
        h = max(dpi.scale(self, 430), self.winfo_reqheight())
        self.geometry("%dx%d" % (w, h))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())

    def _load_web_status(self):
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
        # worker→主线程走 calls 队列（self.after 封送会阻塞 worker，完整版同款）
        self.app.calls.put(apply)

    def _refresh_port_status(self, retries=3):
        def probe(port, lbl, left):
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
                if not ok and left > 0:
                    lbl.config(text="探测中…", fg=dpi.MUT)
                    t = threading.Timer(1.2, lambda: probe(port, lbl,
                                                           left - 1))
                    t.daemon = True
                    t.start()               # 复探在 Timer 线程：网络不占主线程
                    return
                lbl.config(text="端口可用" if ok else "未监听",
                           fg=dpi.C_OK if ok else dpi.C_ERR)
            self.app.calls.put(apply)
        for var, lbl in ((self.srv_var, self.web_ok),
                         (self.app_var, self.app_ok)):
            try:
                p = int(var.get())
            except ValueError:
                p = 0
            if 0 < p < 65536:
                threading.Thread(target=lambda p=p, l=lbl: probe(p, l, retries),
                                 daemon=True).start()

    def _save(self):
        app = self.app

        def raw(v):
            return v[:-len(_ABSENT)] if v.endswith(_ABSENT) else v

        mon = raw(self.mon_var.get())
        mon = "" if mon == "无" else mon
        vj = raw(self.vj_var.get())
        kb = raw(self.kb_var.get())
        ck = raw(self.clock_var.get())
        vj = "" if vj == "无" else vj
        kb = "" if kb == "无" else kb
        ck = "" if ck == "无" else ck
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
        mute = self.mute_var.get()
        proj = self.proj_var.get().strip()
        vid = self.vid_var.get().strip()
        # VJ静音/视频目录/VJ显示位置：OBS 热应用全放后台（完整版 _save 同款）
        def apply_obs():
            ctl = app.ctl
            if ctl is None:
                return
            # 串行化：连续两次保存的两个 apply_obs 线程不许交错（完整版同款）
            with app._obs_apply_lock:
                ctl.cfg["vjMute"] = mute
                if vid:
                    ctl.cfg["videoRoot"] = vid
                ctl.cfg["projectorMonitor"] = mon
                if ctl.is_connected() and not ctl.apply_mute():
                    app.q.put("VJ静音未生效：%s" % ctl.last_error)
                if mon and not ctl.apply_projector():
                    app.q.put("VJ显示位置未生效：%s" % ctl.last_error)
                elif not mon:
                    ctl.close_projector()

        if app.ctl is not None:
            threading.Thread(target=apply_obs, daemon=True).start()
        rescan = bool(proj) and proj != app.ccfg.get("projectsRoot")
        if proj:
            app.ccfg["projectsRoot"] = proj
        if rescan:
            app.q.put("工程库已变更，后台重扫…")
            threading.Thread(target=app._scan_library, daemon=True).start()
        ports_changed = ((vj != app.vj_hint) or (kb != app.kb_hint)
                         or (ck != app.clock_hint))
        app._clock_changed = ck != app.clock_hint   # 供 _apply_ports 判基线
        app.vj_hint, app.kb_hint, app.clock_hint = vj, kb, ck
        try:
            cfg = _load_config()
            cfg.update({"vjPortHint": vj, "kbPortHint": kb,
                        "clockPortHint": ck})
            obs = cfg.get("obs") or {}
            if vid:
                obs["videoRoot"] = vid
            obs["projectorMonitor"] = mon
            obs["vjMute"] = mute
            cfg["obs"] = obs
            daw_sec = cfg.get("dawSettings") or cfg.get("cubase") or {}
            if proj:
                daw_sec["projectsRoot"] = proj
            cfg["dawSettings"] = daw_sec
            cfg["webRemote"] = app._web_config_payload()
            _save_config(cfg)
        except (ValueError, OSError) as e:
            app.q.put("配置保存失败：%s" % e)
        if ports_changed:
            app._apply_ports()
        if web_changed:
            app._apply_web()
        app.q.put("设置已保存")
        self.destroy()


_MUTEX = None


def _acquire_single_instance():
    """命名互斥体防双开；名字带产品+底座维度：与两个完整版可并存运行。"""
    global _MUTEX
    daw = "studioone"
    try:
        daw = str(_load_config().get("daw") or "studioone")
    except Exception:
        pass
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, "Local\\CubeAutomator-" + daw)
    return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS


def main():
    global _CRASH_LOG
    dpi.enable()
    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            0, "Cube Automator 已在运行", "Cube Automator", 0x30)
        return
    try:
        _cl = _HERE / "crash.log"
        if _cl.exists() and _cl.stat().st_size > 512 * 1024:
            _cl.replace(_cl.with_name("crash.log.1"))
        _CRASH_LOG = open(_cl, "a", encoding="utf-8")
        faulthandler.enable(_CRASH_LOG)
    except OSError:
        pass
    root = tk.Tk()
    ico = os.path.join(getattr(sys, "_MEIPASS", "") or ".", "app.ico")
    if os.path.exists(ico):
        root.iconbitmap(ico)
        hicon = ctypes.windll.user32.LoadImageW(
            None, ico, 1, 0, 0, 0x40 | 0x10)
        if hicon:
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or \
                root.winfo_id()
            u32 = ctypes.windll.user32
            u32.SendMessageW(hwnd, 0x80, 1, hicon)   # ICON_BIG
            u32.SendMessageW(hwnd, 0x80, 0, hicon)   # ICON_SMALL
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
