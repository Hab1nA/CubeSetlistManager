# -*- coding: utf-8 -*-
"""E2E 探针：键盘音色全链路（无 JUNO 硬件）。
真实构造 setlist_gui.App（GUI 线程不进 mainloop，后台启动线程照常跑），
向 Keyboard Automation 端口注入音符 60/65，验证：
  监听端口 → App._on_kb_msg 分发 → 查 slots → ToneSwitcher 串行线程
  → send_slot 对 JUNO 缺席的优雅降级（错误进日志，不崩）。
用法：py -u probe_kb_pipeline.py"""
import os
import sys
import time
import tkinter as tk

import midi_bridge as mb
import setlist_gui

OK = True


def wait(fn, timeout, desc):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.3)
    print("FAIL（%.0fs 超时）：%s" % (timeout, desc), flush=True)
    return False


def main():
    global OK
    root = tk.Tk()
    root.withdraw()                     # 无界面跑
    app = setlist_gui.App(root)
    if not wait(lambda: app.kb_port is not None, 30,
                "Keyboard Automation 监听启动"):
        OK = False
    if not wait(lambda: app.switcher is not None, 5, "发送线程创建"):
        OK = False

    app.slots = {60: {"msb": 85, "lsb": 64, "pc": 3}}   # 模拟已录制的映射

    hits = [(i, n) for i, n in mb._out_devices()
            if "Keyboard Automation" in n]
    assert hits, "Keyboard Automation 输出端口不存在"
    mb.send_note(hits[0][0], 60)        # 已配置 → 走完整链路 → JUNO 缺席报错
    if not wait(lambda: any("音色切换" in m and "Performance" in m
                            for m in drain(app)), 10, "note60 切换日志"):
        OK = False
    mb.send_note(hits[0][0], 65)        # 未配置 → 忽略并提示
    if not wait(lambda: any("未配置音色映射" in m for m in drain(app)),
                10, "note65 未配置提示"):
        OK = False

    # 延音踏板键：E2(40)→JUNO、A2(45)→AX-09，按住=CC64 踩下、松开=抬起。
    # 断言用 app._kb_last 属性（发送线程直接写）：不与主线程抢日志队列；
    # 重复按住/松开被去重时不发送，属性 tuple 引用不变，用 is 验证
    def wait_last(tag):
        return wait(lambda: tag in app._kb_last[0], 10, "%s 结果" % tag)

    mb.send_note(hits[0][0], 40)        # 按住 → JUNO 延音踩下
    if not wait_last("JUNO 延音踩下"):
        OK = False
    snap = app._kb_last                 # 重叠按住 → 状态未翻转不重发
    mb.send_note(hits[0][0], 40)
    time.sleep(1.5)
    if app._kb_last is not snap:
        OK = False
        print("FAIL：重复按住未去重（_kb_last 被更新）", flush=True)
    mb.send_note(hits[0][0], 40, vel=0)  # 第一次松开：只抵消重叠的那条
    time.sleep(1.5)
    if app._kb_last is not snap:
        OK = False
        print("FAIL：重叠音符的第一次松开就抬起了（计数错）", flush=True)
    mb.send_note(hits[0][0], 40, vel=0)  # 第二次松开：全部释放 → 抬起
    if not wait_last("JUNO 延音抬起"):
        OK = False
    snap = app._kb_last                 # 第三次松开（多余）→ 去重
    mb.send_note(hits[0][0], 40, vel=0)
    time.sleep(1.5)
    if app._kb_last is not snap:
        OK = False
        print("FAIL：多余松开未去重（_kb_last 被更新）", flush=True)
    mb.send_note(hits[0][0], 45)        # A2 → AX-09 延音踩下
    if not wait_last("AX-09 延音踩下"):
        OK = False
    mb.send_note(hits[0][0], 45, vel=0)
    if not wait_last("AX-09 延音抬起"):
        OK = False

    logs = drain(app)
    print("---- GUI 日志（全部）----", flush=True)
    for m in logs:
        print(" ", m, flush=True)
    root.destroy()
    print("结论：%s" % ("全链路 ✓" if OK else "存在问题 ✗"), flush=True)
    sys.exit(0 if OK else 1)


ALL = []


def drain(app):
    while True:
        try:
            ALL.append(app.q.get_nowait())
        except Exception:
            return ALL


if __name__ == "__main__":
    main()
