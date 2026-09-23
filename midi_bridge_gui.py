# -*- coding: utf-8 -*-
"""VJ Automator 状态窗：四个服务状态 + 事件日志。
直接 `python midi_bridge_gui.py` 运行，或用 PyInstaller 打包成 exe。"""
import queue
import threading
import time
import tkinter as tk

import dpi
import midi_bridge as mb
from obs_ctrl import ObsController

FOLLOW = {"playing": "播放中", "paused": "已暂停", "stopped": "已停止"}


class App:
    def __init__(self, root):
        self.root = root
        root.title("VJ Automator")
        root.geometry(dpi.scale(root, 640, 400))
        self.rows = {}
        head = tk.Frame(root)
        head.pack(fill="x", padx=12, pady=(10, 6))
        for i, name in enumerate(("loopMIDI 端口", "OBS 状态", "MIDI 监听",
                                  "走带跟随")):
            tk.Label(head, text=name, width=14, anchor="w").grid(
                row=i, column=0)
            lbl = tk.Label(head, text="…", anchor="w", fg=dpi.MUT)
            lbl.grid(row=i, column=1, sticky="we")
            head.columnconfigure(1, weight=1)
            self.rows[name] = lbl
        tk.Frame(root, height=dpi.scale(root, 1),
                 bg="#33363d").pack(fill="x", padx=12)
        self.log = tk.Listbox(root, height=14, activestyle="none",
                              font=("Microsoft YaHei UI", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=6)
        bar = tk.Frame(root)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(bar, text="退出", command=self._on_exit).pack(side="right")
        tk.Button(bar, text="清空日志",
                  command=lambda: self.log.delete(0, "end")).pack(
            side="right", padx=(0, 6))
        dpi.darkify(root)          # 与Cube Setlist Manager同一套深色演出主题

        self.q = queue.Queue()
        self.ctl = self.sync = self.port = None
        self.start_err = ""
        threading.Thread(target=self._startup, daemon=True).start()
        root.after(400, self._tick)

    def _on_exit(self):
        try:
            if self.port is not None:
                self.port.close()
        except Exception:
            pass
        self.root.destroy()

    # ---- 后台线程：起服务（窗口先出现，不卡界面） ----

    def _startup(self):
        try:
            mb.ensure_loopmidi()
            self.q.put("loopMIDI 端口就绪")
            self.ctl = ObsController(mb.load_obs_cfg())
            self.ctl.on_connected = lambda: self.q.put("OBS 已连接")
            self.sync = mb.TransportSync(self.ctl, on_event=self.q.put)
            self.port = mb.MidiIn(
                mb.PORT_HINT,
                mb.note_handler(self.ctl, self.sync, report=self.q.put),
                on_clock=self.sync.on_clock)
            self.ctl.enabled = True
            self.ctl.start()
            threading.Thread(target=self._watch, daemon=True).start()
            self.q.put("MIDI 监听已启动（%s）" % self.port.name)
        except SystemExit as e:
            self.start_err = str(e)
            self.q.put("启动失败：%s" % e)

    def _watch(self):
        while True:
            self.sync.poll()
            time.sleep(mb.POLL_SEC)

    # ---- 主线程：状态轮询 + 日志出队 ----

    def _set(self, name, text, color=dpi.MUT):
        self.rows[name].config(text=text, fg=color)

    def _tick(self):
        loop_ok = mb._pick(mb._in_devices()) is not None
        self._set("loopMIDI 端口", "就绪" if loop_ok else "未找到",
                  dpi.C_OK if loop_ok else dpi.C_ERR)
        if self.ctl is None:
            self._set("OBS 状态", self.start_err or "启动中…", dpi.C_ERR)
        else:
            ok = self.ctl.is_connected()
            extra = "（%s）" % self.ctl.last_error if self.ctl.last_error else "…"
            self._set("OBS 状态", "已连接" if ok else "未连接" + extra,
                      dpi.C_OK if ok else dpi.C_WARN)
        if self.port is not None:
            self._set("MIDI 监听", "监听中（%s）" % self.port.name, dpi.C_OK)
        else:
            self._set("MIDI 监听", "未启动", dpi.C_ERR)
        if self.sync is None:
            self._set("走带跟随", "-")
        elif not self.sync.is_following():
            self._set("走带跟随", "未启用（未收到时钟）")
        else:
            self._set("走带跟随", FOLLOW.get(self.sync.video_state, "?"))
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", time.strftime("[%H:%M:%S] ") + msg)
            self.log.see("end")
        self.root.after(400, self._tick)


def main():
    dpi.enable()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
