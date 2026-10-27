# -*- coding: utf-8 -*-
"""r7 取证：编排页宽/窄两态截图 + 正确选择器几何。"""
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
CDP = 9224


def run(w, h, tag):
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
            print("[%s] tab not found" % tag)
            return
        time.sleep(2.0)
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)

        def ev(expr):
            r = send("Runtime.evaluate", {"expression": expr,
                                          "returnByValue": True})
            return r.get("result", {}).get("value")

        def send(method, params=None):
            ws.send(json.dumps({"id": send.n, "method": method,
                                "params": params or {}}))
            send.n += 1
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == send.n - 1:
                    return m.get("result", {})
        send.n = 1

        ev("document.querySelector('.b-setlist').click()")
        time.sleep(1.0)
        ev("document.querySelector('#lib .row').click()")
        time.sleep(1.6)
        print("[%s]" % tag, ev(r"""
JSON.stringify((() => {
  const R = s => { const e = document.querySelector(s);
    if (!e) return null; const r = e.getBoundingClientRect();
    return {t:+r.top.toFixed(1), b:+r.bottom.toFixed(1), ch:e.clientHeight,
            sh:e.scrollHeight}; };
  return { iw: innerWidth, ih: innerHeight,
    narrow: matchMedia('(max-width:900px)').matches,
    main: R('#main'), cols: R('.cols'),
    lib: R('.cols [data-col=lib]'), pl: R('.cols [data-col=pl]'),
    dt: R('.cols #col-dt'), dtScroll: R('#dt-scroll'),
    libList: R('#lib'), plList: R('#pl') };
})())"""))
        shot = send("Page.captureScreenshot", {"format": "png"})
        (OUTD / ("r7_setlist_%s.png" % tag)).write_bytes(
            base64.b64decode(shot["data"]))
        ws.close()
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
        time.sleep(1.0)


run(1280, 800, "wide")
run(900, 700, "narrow")
print("done")
