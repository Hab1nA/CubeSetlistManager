# -*- coding: utf-8 -*-
"""手势校准探针：生产同路径（DeviceBridge 归属过滤 + Learner 分类器）。
    python tools/probe_gesture_calib.py [设备身份子串]
不传设备则用 dist config 的 hidDeviceHint。每脚实时打印边沿与按压时长，
安静 0.6 秒后给出「软件会判定成什么」。Ctrl+C 结束。只读，不注入。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pedal                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "dist", "Cube Setlist Manager Cubase", "config.json")

cfg = json.load(open(CFG, encoding="utf-8"))
p = cfg.get("pedal") or {}
device = sys.argv[1] if len(sys.argv) > 1 else (p.get("hidDeviceHint") or "")
double_t = p.get("doubleWindow", pedal.DOUBLE_WINDOW)
if not device:
    print("config 未选输入设备：请把设备身份子串作为参数传入")
    sys.exit(2)

print("设备：%s（%s）" % (pedal.device_display(device), device))
print("当前阈值：双踩窗 %.2fs / 抖动闸 30ms" % double_t)
print("-" * 60)

state = {"learner": None, "rearm_at": 0.0}


def stamp():
    t = time.time()
    return time.strftime("[%H:%M:%S.", time.localtime(t)) + "%03d]" \
        % int((t % 1) * 1000)


def show(msg):
    print(stamp() + " " + msg, flush=True)


def rearm():
    state["learner"] = pedal.Learner("__calib__", device, bridge,
                                     double_window=double_t)
    # 包一层打印：学习器实际入账的每条边沿（含被闸滤掉的）都可见
    raw_cap = state["learner"]._raw_capture

    def cap(vk, down, t):
        print(stamp() + " %s VK 0x%02X"
              % ("DOWN" if down else "UP  ", vk), flush=True)
        raw_cap(vk, down, t)

    bridge.begin_capture(cap)


bridge = pedal.DeviceBridge(None)
bridge.configure(device_hint=device)

rearm()
bridge.start()
t0 = time.monotonic()
while not (bridge.running and bridge.raw_ok) and time.monotonic() - t0 < 5:
    time.sleep(0.05)
print("桥运行：running=%s raw_ok=%s（Ctrl+C 结束）" % (bridge.running, bridge.raw_ok),
      flush=True)

try:
    while True:
        time.sleep(0.1)
        lr = state["learner"]
        if lr is None:
            if time.monotonic() >= state["rearm_at"]:
                rearm()
                show("—— 就绪，请踩 ——")
            continue
        r = lr.result()
        if r is not None:
            kind, dev, code, gesture = r
            zh = {"single": "单踩", "double": "双踩"}[gesture]
            show("★ 软件判定：%s（%s VK 0x%02X）" % (zh, kind, code))
            lr.close()
            state["learner"] = None
            rearm()                                 # 立即重就绪，无缝隙丢脚
except KeyboardInterrupt:
    pass
finally:
    if state["learner"]:
        state["learner"].close()
    bridge.stop()
    print("结束", flush=True)
