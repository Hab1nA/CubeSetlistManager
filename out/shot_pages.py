# -*- coding: utf-8 -*-
"""逐页目视：起 webui，CDP 逐页点击导航并截图 + 抓 JS 错误。"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

OUTD = pathlib.Path("out") / "ui_shots"
OUTD.mkdir(parents=True, exist_ok=True)
PAGES = ["console", "setlist", "points", "pedal", "kbd", "vj",
         "settings"]

env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=9224 --remote-allow-origins=*")
probe = subprocess.Popen([sys.executable, "webui.py"], env=env,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
tab = None
for i in range(30):
    time.sleep(1.0)
    try:
        tabs = json.load(urllib.request.urlopen(
            "http://127.0.0.1:9224/json", timeout=2))
        tab = next((t for t in tabs if t.get("type") == "page"
                    and "/app" in t.get("url", "")), None)
        if tab:
            print("tab up @%ds" % i, flush=True)
            break
    except Exception:
        pass
if tab:
    time.sleep(2.0)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)
    errs = []

    def send(method, params=None):
        ws.send(json.dumps({"id": send.n, "method": method,
                            "params": params or {}}))
        send.n += 1
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == send.n - 1:
                return m.get("result", {})
    send.n = 1
    ws.send(json.dumps({"id": 0, "method": "Runtime.enable"}))
    for p in PAGES:
        send("Runtime.evaluate", {"expression":
              "document.querySelector('.b-%s').click()" % p})
        time.sleep(1.2)
        if p == "setlist":
            # 先截未选中态，再选首曲补选中态（顺序不可颠倒）
            r = send("Page.captureScreenshot", {"format": "png"})
            png = base64.b64decode(r["data"])
            (OUTD / ("page_%s.png" % p)).write_bytes(png)
            print("shot", p, len(png), flush=True)
            send("Runtime.evaluate", {"expression":
                  "const r=document.querySelector('#lib .row')||"
                  "document.querySelector('#pl .row');r&&r.click()"})
            time.sleep(1.6)
            r = send("Page.captureScreenshot", {"format": "png"})
            png = base64.b64decode(r["data"])
            (OUTD / "page_setlist_selected.png").write_bytes(png)
            print("shot setlist_selected", len(png), flush=True)
            continue
        r = send("Page.captureScreenshot", {"format": "png"})
        png = base64.b64decode(r["data"])
        (OUTD / ("page_%s.png" % p)).write_bytes(png)
        print("shot", p, len(png), flush=True)
    e = send("Runtime.evaluate", {"expression":
             "window.__errs ? JSON.stringify(window.__errs) : '[]'"})
    print("JS errors:", e.get("result", {}).get("value"), flush=True)
    ws.close()
probe.terminate()
