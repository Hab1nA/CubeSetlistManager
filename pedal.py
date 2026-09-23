# -*- coding: utf-8 -*-
"""CC 踩钉快捷键：踩钉（USB 直连或经声卡 MIDI IN，两者都是 winmm 输入设备）
发 CC 控制软件功能。触发=上升沿（值上穿 64）+100ms 级去抖——瞬时踩/开关踩
通吃，踩钉无需改任何设置。绑定来自「学习」：监听所有空闲 MIDI 输入口，
踩一下捕获 设备名+CC 号，写 config.json 的 pedal 段（GUI 侧持久化）。
热插拔：未连接时由 GUI 轮询 try_open() 重连。"""
import threading
import time
import tkinter as tk

import dpi
import midi_bridge as mb
from kbd_auto import RawMidiIn, PortNotFound

ACTIONS = (("play", "开始"), ("stop", "停止"), ("rewind", "回零"),
           ("next", "下一首"), ("panic", "全停"), ("auto", "自动切换"))
RISE = 64               # 上升沿阈值
DEBOUNCE = 0.15         # 两次触发最小间隔（秒）
EXCLUDE = ("loopMIDI", "JUNO", "AX-09", "Lucina")  # 软件虚拟口/两台琴的 MIDI 口，学习时不当踩钉候选


def fire(state, cc, val, now):
    """上升沿+去抖判定；state: {cc: (上个值, 上次触发时刻)}，原地更新。"""
    prev, last = state.get(cc, (0, 0.0))
    state[cc] = (val, last)
    if val >= RISE and prev < RISE and now - last >= DEBOUNCE:
        state[cc] = (val, now)
        return True
    return False


def load_binding(cfg):
    """完整 config dict → (设备名提示, {动作: cc 号})。"""
    p = cfg.get("pedal") or {}
    binds = {}
    for k, v in (p.get("bindings") or {}).items():
        if k in dict(ACTIONS) and isinstance(v, int):
            binds[k] = v
    return p.get("deviceHint", ""), binds


class PedalListener:
    """按 deviceHint 常驻监听踩钉口；CC 上升沿 → on_action(动作名)。
    回调在 winmm 线程触发，GUI 侧自行转投主线程。"""

    def __init__(self, on_action):
        self.on_action = on_action
        self.hint = ""
        self.binds = {}
        self.port = None
        self.name = ""
        self._state = {}
        self._lock = threading.Lock()

    def apply(self, hint, binds):
        with self._lock:
            self.hint, self.binds = hint, binds
        self.close()

    def try_open(self):
        with self._lock:
            if self.port:
                return True
            if not self.hint:
                return False
            try:
                self.port = RawMidiIn(self.hint, self._msg)
                self.name = self.port.name
                return True
            except PortNotFound:
                self.port = None
                return False

    @property
    def connected(self):
        return self.port is not None

    def _msg(self, status, d1, d2):
        if status & 0xF0 != 0xB0:
            return
        if fire(self._state, d1, d2, time.time()):
            action = self.binds.get(d1)
            if action:
                self.on_action(action)

    def close(self):
        if self.port:
            try:
                self.port.close()
            except OSError:
                pass
            self.port = None


class CCLearner:
    """学习：监听若干空闲输入口（已知踩钉时只听它），捕获首个 CC。"""

    def __init__(self, hint=""):
        self.cc = None                 # (设备名, cc 号)
        self.ports = []
        devs = mb._in_devices()
        if hint:
            cands = [(i, n) for i, n in devs if hint in n]
        else:
            cands = [(i, n) for i, n in devs
                     if not any(x in n for x in EXCLUDE)]
        for _idx, name in cands:
            try:
                self.ports.append(RawMidiIn(name, self._make(name)))
            except PortNotFound:
                pass                   # 被其他程序占用的口跳过

    def _make(self, name):
        def feed(status, d1, d2):
            if self.cc is None and status & 0xF0 == 0xB0:
                self.cc = (name, d1)
        return feed

    def result(self):
        return self.cc

    def close(self):
        for p in self.ports:
            try:
                p.close()
            except OSError:
                pass
        self.ports = []


LEARN_TIMEOUT = 8.0        # 学习等待踩踏的时限（秒）


class PedalWindow(tk.Toplevel):
    """六行功能 × [学习][清除]，底部监听状态。绑定存 config.json 的 pedal 段。"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.learner = None
        self.title("踩钉控制")
        self.geometry(dpi.scale(self, 560, 360))
        pad = dpi.scale(self, 12)   # pack 边距是裸像素，高 DPI 下须换算
        tk.Label(self, text="学习：点「学习」后踩一下踩钉；"
                   "清除：删除该绑定。").pack(anchor="w", padx=pad,
                                            pady=(pad, 4))
        grid = tk.Frame(self)
        grid.pack(fill="both", expand=True, padx=pad)
        for c, t in enumerate(("功能", "绑定", "操作")):
            tk.Label(grid, text=t, anchor="w", fg=dpi.MUT).grid(
                row=0, column=c, sticky="w", pady=(0, 2),
                padx=(8, 0) if c == 1 else (0, 0))  # 对齐数据列左缩进
        self._bind_lbl = {}
        for r, (action, name) in enumerate(ACTIONS, start=1):
            tk.Label(grid, text=name, anchor="w").grid(
                row=r, column=0, sticky="w", pady=2)
            lbl = tk.Label(grid, text="未设置", anchor="w", fg=dpi.MUT)
            lbl.grid(row=r, column=1, sticky="we", padx=(8, 8), pady=2)
            grid.columnconfigure(1, weight=1)
            cell = tk.Frame(grid)
            cell.grid(row=r, column=2, sticky="w", pady=2)
            tk.Button(cell, text="学习", width=8,
                      command=lambda a=action: self._learn(a)).pack(
                side="left", padx=2)
            tk.Button(cell, text="清除", width=8,
                      command=lambda a=action: self._clear(a)).pack(
                side="left", padx=2)
            self._bind_lbl[action] = lbl
        self.status = tk.Label(self, text="…", anchor="w", fg=dpi.MUT)
        self.status.pack(fill="x", padx=pad, pady=(6, dpi.scale(self, 8)))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.attributes("-topmost", True)   # 与主窗一致保持可见
        dpi.darkify(self)
        dpi.flatten(self)       # 表单页文字直接坐窗口底色，去掉面板色斑
        # 尺寸适配：最小=内容自然需求；初始不低于规划值与需求值
        self.update_idletasks()
        w = max(dpi.scale(self, 560), self.winfo_reqwidth())
        h = max(dpi.scale(self, 360), self.winfo_reqheight())
        self.geometry("%dx%d" % (w, h))
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.after(300, self._tick)
        self._refresh()

    def _refresh(self):
        for action, _name in ACTIONS:
            cc = self.app.pedal_binds.get(action)
            self._bind_lbl[action].config(
                text="CC %d" % cc if cc is not None else "未设置",
                fg=dpi.FG if cc is not None else dpi.MUT)

    def _set_status(self, text, color=dpi.MUT):
        self.status.config(text=text, fg=color)

    def _save(self):
        import setlist_gui as sg       # 延迟导入避免循环
        cfg = sg._load_config()
        cfg["pedal"] = {"deviceHint": self.app.pedal_hint,
                        "bindings": self.app.pedal_binds}
        sg._save_config(cfg)

    def _learn(self, action):
        self._cancel_learn("已取消上一次学习")
        self.learner = (action, CCLearner(self.app.pedal_hint),
                        time.time() + LEARN_TIMEOUT)
        self._set_status("学习「%s」：请踩一下踩钉（%d 秒内）"
                         % (dict(ACTIONS)[action], LEARN_TIMEOUT), dpi.C_ERR)

    def _cancel_learn(self, msg):
        if self.learner is None:
            return
        self.learner[1].close()
        self.learner = None
        if msg:
            self._set_status(msg)

    def _clear(self, action):
        if self.app.pedal_binds.pop(action, None) is None:
            return
        self._save()
        self.app.pedal.apply(self.app.pedal_hint, self.app.pedal_binds)
        self.app.pedal.try_open()
        self._refresh()
        self._set_status("已清除「%s」" % dict(ACTIONS)[action])

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self.learner is not None:
            action, learner, deadline = self.learner
            res = learner.result()
            if res is not None:
                dev, ccnum = res
                learner.close()
                self.learner = None
                self.app.pedal_hint = dev
                self.app.pedal_binds[action] = ccnum
                self._save()
                self.app.pedal.apply(self.app.pedal_hint,
                                     self.app.pedal_binds)
                self.app.pedal.try_open()
                self._refresh()
                self._set_status("「%s」已绑定 %s 的 CC %d"
                                 % (dict(ACTIONS)[action], dev, ccnum), dpi.C_OK)
            elif time.time() > deadline:
                self._cancel_learn("学习超时：没收到 CC（踩钉模式/接线请检查）")
            else:
                self._set_status("学习「%s」中（剩 %.0f 秒），请踩一下踩钉"
                                 % (dict(ACTIONS)[action],
                                    deadline - time.time()), dpi.C_ERR)
        else:
            p = self.app.pedal
            if p is None:
                self._set_status("服务启动中…")
            elif p.connected:
                self._set_status("监听中：%s" % p.name, dpi.C_OK)
            elif p.hint:
                self._set_status("未找到踩钉口「%s」，每 10 秒自动重试"
                                 % p.hint, dpi.C_WARN)
            else:
                self._set_status("还没有任何绑定：点任一「学习」开始", dpi.MUT)
        self.after(300, self._tick)

    def _close(self):
        self._cancel_learn("")
        self.destroy()
