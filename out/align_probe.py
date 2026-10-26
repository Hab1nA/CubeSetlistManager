# -*- coding: utf-8 -*-
"""双列对齐根因定位：两卡 rect/md-head/首个子元素逐项测量。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9233
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

print(js(
    "document.querySelector('.b-console').click();"
    "await new Promise(r=>setTimeout(r,800));"
    "const g=document.querySelector('.console-cols');"
    "const cards=[...g.children];"
    "return cards.map(c=>{const r=c.getBoundingClientRect();"
    "const h=c.querySelector('.md-head');"
    "const hr=h?h.getBoundingClientRect():null;"
    "const kids=[...c.children].map(k=>{const kr=k.getBoundingClientRect();"
    "return (String(k.className)||k.tagName).slice(0,22)+':T'"
    "+Math.round(kr.top)+'+H'+Math.round(kr.height)});"
    "const cs=getComputedStyle(c);"
    "return {card:(c.className||'').slice(0,22),top:Math.round(r.top),"
    "hgt:Math.round(r.height),pt:cs.paddingTop,mt:cs.marginTop,"
    "mb:cs.marginBottom,"
    "headTop:hr?Math.round(hr.top):null,kids,disp:cs.display}})"))
ws.close()
probe.terminate()
