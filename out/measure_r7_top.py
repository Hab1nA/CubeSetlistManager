# -*- coding: utf-8 -*-
"""r7 D 批验收：演出模式窗口置顶（WS_EX_TOPMOST 位实测）。"""
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

u32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8


def find_cube_hwnd():
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        u32.GetWindowTextW(h, buf, 64)
        if buf.value == "Cube Setlist Manager" and u32.IsWindowVisible(h):
            hwnds.append(h)
        return True
    u32.EnumWindows(cb, 0)
    return hwnds[0] if hwnds else None


def main():
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--remote-debugging-port=9224 --remote-allow-origins=*")
    p = subprocess.Popen([sys.executable, "webui.py", "1280", "800"],
                         env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        tab = None
        for _ in range(30):
            time.sleep(1.0)
            try:
                tabs = json.load(urllib.request.urlopen(
                    "http://127.0.0.1:9224/json", timeout=2))
                tab = next((t for t in tabs if t.get("type") == "page"
                            and "/app" in t.get("url", "")), None)
                if tab:
                    break
            except Exception:
                pass
        assert tab, "tab not found"
        time.sleep(2.0)
        hwnd = find_cube_hwnd()
        assert hwnd, "window not found"
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)
        n = [0]

        def ev(expr):
            n[0] += 1
            ws.send(json.dumps({"id": n[0], "method": "Runtime.evaluate",
                                "params": {"expression": expr,
                                           "returnByValue": True,
                                           "awaitPromise": True}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == n[0]:
                    return m.get("result", {}).get("result", {}).get("value")

        def topmost():
            return bool(u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)

        base = topmost()
        # 进演出模式 → 置顶
        ev("document.getElementById('btn-show').click()")
        time.sleep(1.0)
        on = topmost()
        # 退出演出模式 → 取消置顶
        ev("document.getElementById('show-exit').click()")
        time.sleep(1.0)
        off = topmost()
        errs = ev("JSON.stringify(window.__errs)")
        print("base=%s show=%s exit=%s errs=%s" % (base, on, off, errs))
        ok1 = base is False and on is True and off is False
        print("D-N1 置顶位随演出模式进出翻转：", "PASS" if ok1 else "FAIL")
        ws.close()
        return 0 if ok1 else 1
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()


if __name__ == "__main__":
    sys.exit(main())
