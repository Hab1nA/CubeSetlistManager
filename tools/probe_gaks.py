# -*- coding: utf-8 -*-
"""被动探针：GetAsyncKeyState 全键轮询，实时打印每个翻转边沿。
    python tools/probe_gaks.py [秒数=300]
与 Raw Input 探针（probe_gesture_calib.py）并行运行可对比：
GAKS 有边沿而校准探针静默 = 按下沿被热键消费或键值不在注册页。
VERDICT: GAKS-OK / GAKS-DEAD。只读，不注入。"""
import ctypes
import sys
import time

u32 = ctypes.windll.user32
u32.GetAsyncKeyState.restype = ctypes.c_short
u32.GetAsyncKeyState.argtypes = (ctypes.c_int,)

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 300

prev = {vk: False for vk in range(0x01, 0xFF)}


def stamp():
    t = time.time()
    return time.strftime("[%H:%M:%S.", time.localtime(t)) + "%03d]" \
        % int((t % 1) * 1000)


edges = []
t0 = time.monotonic()
while time.monotonic() - t0 < SECONDS:
    for vk in prev:
        pressed = bool(u32.GetAsyncKeyState(vk) & 0x8000)
        if pressed != prev[vk]:
            prev[vk] = pressed
            edges.append((vk, pressed))
            print(stamp() + (" DOWN" if pressed else " UP  ")
                  + " VK 0x%02X" % vk, flush=True)
    time.sleep(0.01)

downs = [e for e in edges if e[1]]
print("edges=%d downs=%d" % (len(edges), len(downs)))
print("VERDICT:", "GAKS-OK" if downs else "GAKS-DEAD")
