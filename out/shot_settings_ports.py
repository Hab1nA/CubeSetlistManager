# -*- coding: utf-8 -*-
"""设置页底部截图（滚动到移动端遥控卡）+ IP 前缀几何。"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9235
OUTD = pathlib.Path("out") / "ui_shots"
env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
probe = subprocess.Popen([sys.executable, "webui.py"], env=env,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
tab = None
for i in range(30):
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
    probe.terminate()
    sys.exit("no tab")
time.sleep(2.5)
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)


def send(m, p=None):
    ws.send(json.dumps({"id": send.n, "method": m, "params": p or {}}))
    send.n += 1
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == send.n - 1:
            return r.get("result", {})


send.n = 1
send("Runtime.enable")


def js(b):
    r = send("Runtime.evaluate", {
        "expression": "(async()=>{%s})()" % b,
        "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


for i in range(15):
    if js("return fetch('/state').then(r=>r.json()).then("
          "j=>'songs' in j)") is True:
        break
    time.sleep(1.0)

print(js("document.querySelector('.b-settings').click();"
         "await new Promise(r=>setTimeout(r,900));"
         "document.getElementById('st-root').scrollIntoView({block:'start'});"
         "window.scrollBy(0,40);"
         "const pre=document.getElementById('st-srv-host');"
         "const r=pre.getBoundingClientRect();"
         "const inp=document.getElementById('st-srv').getBoundingClientRect();"
         "return {pre:pre.textContent,"
         "fused:Math.abs(r.right-inp.left)<2&&Math.abs(r.height-inp.height)<3,"
         "preW:Math.round(r.width)}"))
r = send("Page.captureScreenshot", {"format": "png"})
(OUTD / "page_settings_ports.png").write_bytes(
    base64.b64decode(r["data"]))
print("shot page_settings_ports.png")
ws.close()
probe.terminate()
