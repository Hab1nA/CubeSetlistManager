# -*- coding: utf-8 -*-
"""Studio One 7 底座真机采样探针（M0 校准用，docs/StudioOne迁移调研.md §四）。
采样事实表四要素：进程名 / 窗口类与标题格式 / 弹窗全集 / CLI 转交行为。
只读为主；发键验证需显式 --send（默认 dry-run 只打印将发的键序）。
子命令：
  proc              列 Studio One 进程（事实表 proc_prefix 命中情况）
  win               列全部可见顶层窗口，标出疑似工程窗（按 S1 事实表识别）
  watch             实时监测：工程窗变化 + 弹窗出现（只打印，绝不按键）
  open <song路径>   按 CLI 方式把 .song 交给 S1（转交 or 新实例？）+ watch
  transport <动作>  [--send] 打印（或发送）S1 事实表键序：play/stop/rewind
  clock             列 MIDI 输入端口（人工在 S1 External Devices 勾 Send
                    MIDI Clock 指向 loopMIDI 后，用它看脉冲是否到达）
用法：py -u probe_s1.py <子命令> [参数]"""
import sys
import time

import daw_ctrl
from obs_ctrl import find_processes_by_prefix, launch_detached

daw_ctrl.set_active(daw_ctrl.STUDIOONE)


def _dump_windows():
    ws = daw_ctrl._windows()
    proj = dict(daw_ctrl.project_windows())
    for h, t, c in ws:
        mark = "  ← 工程窗" if h in proj else ""
        print("  %-8d %-28s %s%s" % (h, c, t, mark))
    return ws


def cmd_proc():
    hits = find_processes_by_prefix(daw_ctrl.STUDIOONE["proc_prefix"])
    print("proc_prefix=%r 命中：%s"
          % (daw_ctrl.STUDIOONE["proc_prefix"], hits or "（无）"))


def cmd_win():
    print("可见顶层窗口（hwnd / 类 / 标题）：")
    _dump_windows()
    print("project_windows 命中 %d 个；title_mark=%r"
          % (len(daw_ctrl.project_windows()),
             daw_ctrl.STUDIOONE["title_mark"]))


def cmd_watch():
    print("实时监测（Ctrl+C 停）：工程窗与弹窗只打印、绝不按键。")
    seen_proj, seen_dlg = set(), set()
    try:
        while True:
            for h, t in daw_ctrl.project_windows():
                if (h, t) not in seen_proj:
                    seen_proj.add((h, t))
                    print("[工程] %d「%s」→ 歌名=%r"
                          % (h, t, daw_ctrl.project_name_from_title(t)))
            for h, t in daw_ctrl._dialogs(daw_ctrl.STUDIOONE):
                if (h, t) not in seen_dlg:
                    seen_dlg.add((h, t))
                    print("[弹窗] %d「%s」" % (h, t))
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n结束。")


def cmd_open(path):
    print("CLI 打开：%r（观察：转交当前实例 or 新实例）" % path)
    r = launch_detached(_s1_exe(), '"%s"' % path)
    print("ShellExecute=%s（>32 为已提交）" % r)
    cmd_watch()


def _s1_exe():
    import setlist_gui
    return setlist_gui.DAW_DEFAULTS["studioone"]["exe"]


def cmd_transport(action):
    keys = daw_ctrl.STUDIOONE["transport"].get(action)
    if keys is None:
        print("未知动作 %r（可选：%s）"
              % (action, "/".join(daw_ctrl.STUDIOONE["transport"])))
        return
    print("键序 %s：%s" % (action, " → ".join(keys)))
    if "--send" not in sys.argv:
        print("（dry-run；加 --send 才真正发键，需 S1 工程窗在前台）")
        return
    ws = daw_ctrl.current_project()
    if not ws:
        print("没有工程窗口，不发")
        return
    daw_ctrl.focus(ws[0])
    for k in keys:
        daw_ctrl.tap(daw_ctrl.VK[k])
    print("已发送。")


def cmd_clock():
    import midi_bridge as mb
    hint = sys.argv[2] if len(sys.argv) > 2 else "loopMIDI"
    print("MIDI 输入端口（S1 勾 Send MIDI Clock 后等脉冲）：")
    for idx, name in mb._in_devices():
        print("  %d %s" % (idx, name))
    hit = mb._pick(mb._in_devices(), hint)
    if not hit:
        print("（没有匹配 %r 的端口）" % hint)
        return
    n = [0]

    def on_clock():
        n[0] += 1
    port = mb.MidiIn(hit[1], lambda *a: None, on_clock=on_clock)
    print("监听 %s 5 秒…" % port.name)
    t0 = time.time()
    while time.time() - t0 < 5:
        time.sleep(0.2)
    print("5 秒收到时钟脉冲 %d 个（>0 = S1 时钟已到 %s）" % (n[0], port.name))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    table = {"proc": cmd_proc, "win": cmd_win, "watch": cmd_watch,
             "transport": cmd_transport, "clock": cmd_clock}
    if cmd == "open" and arg:
        cmd_open(arg)
    elif cmd == "transport" and arg:
        cmd_transport(arg)
    elif cmd in table:
        table[cmd]()
    else:
        print(__doc__)
