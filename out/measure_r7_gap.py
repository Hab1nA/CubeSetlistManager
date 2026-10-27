# -*- coding: utf-8 -*-
"""r7 取证：编排页底部截断问题几何实测（多窗口尺寸）。"""
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
CDP = 9224


def run(w, h):
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
    p = subprocess.Popen([sys.executable, "webui.py", str(w), str(h)],
                         env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        tab = None
        for _ in range(30):
            time.sleep(1.0)
            try:
                tabs = json.load(urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % CDP, timeout=2))
                tab = next((t for t in tabs if t.get("type") == "page"
                            and "/app" in t.get("url", "")), None)
                if tab:
                    break
            except Exception:
                pass
        if not tab:
            print("[%dx%d] tab not found" % (w, h))
            return
        time.sleep(2.0)
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)

        def send(method, params=None):
            ws.send(json.dumps({"id": send.n, "method": method,
                                "params": params or {}}))
            send.n += 1
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == send.n - 1:
                    return m.get("result", {})
        send.n = 1

        send("Runtime.evaluate", {"expression":
              "document.querySelector('.b-setlist').click()"})
        time.sleep(1.0)
        send("Runtime.evaluate", {"expression":
              "(document.querySelector('#lib .row')||"
              "document.querySelector('#pl .row')||{}).click"
              " && document.querySelector('#lib .row').click()"})
        time.sleep(1.6)
        r = send("Runtime.evaluate", {"expression": r"""
JSON.stringify((() => {
  const R = s => { const e = document.querySelector(s);
    if (!e) return null; const r = e.getBoundingClientRect();
    return {t: +r.top.toFixed(1), b: +r.bottom.toFixed(1),
            h: +r.height.toFixed(1), ch: e.clientHeight,
            sh: e.scrollHeight}; };
  return { win: innerHeight, main: R('#main'), page: R('#p-setlist'),
           cols: R('.cols'), lib: R('[data-col=lib]'), pl: R('[data-col=pl]'),
           dt: R('#col-dt'), dtScroll: R('#dt-scroll'),
           dtBody: R('#dt-body'), plList: R('#pl .list'),
           libList: R('#lib .list'), sb: R('#statusbar'),
           mainScroll: (() => { const m = document.querySelector('#main');
             return {ch: m.clientHeight, sh: m.scrollHeight}; })() };
})())""", "returnByValue": True})
        print("[%dx%d]" % (w, h), r.get("result", {}).get("value"))
        ws.close()
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
        time.sleep(1.0)


for size in [(1280, 800), (1280, 700), (1024, 640)]:
    run(*size)
print("done")
