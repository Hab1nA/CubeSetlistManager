# -*- coding: utf-8 -*-
"""七项反馈实机验证：端口 IP 前缀 + 侧栏退出真退出。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9234
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

# 1 设置页端口 IP 前缀
r1 = js("document.querySelector('.b-settings').click();"
        "await new Promise(r=>setTimeout(r,900));"
        "return {srv:document.getElementById('st-srv-host').textContent,"
        "app:document.getElementById('st-app-host').textContent,"
        "tsk:document.getElementById('st-tsk-host').textContent}")
print("ip-hints:", json.dumps(r1, ensure_ascii=False))
ok1 = all((r1 or {}).get(k) for k in ("srv", "app", "tsk"))
print("PASS ip-hints" if ok1 else "FAIL ip-hints（可能遥控未启用无 webIp）")

# 2 底栏右侧提示词随页切换
r2 = js("document.querySelector('.b-console').click();"
        "await new Promise(r=>setTimeout(r,400));"
        "const a=document.getElementById('sb-note').textContent;"
        "document.querySelector('.b-setlist').click();"
        "await new Promise(r=>setTimeout(r,400));"
        "const b=document.getElementById('sb-note').textContent;"
        "return {console:a,setlist:b}")
print("sb-note:", json.dumps(r2, ensure_ascii=False))
ok2 = (r2 or {}).get("console") != (r2 or {}).get("setlist") and \
      (r2 or {}).get("setlist")
print("PASS sb-note" if ok2 else "FAIL sb-note")

# 3 侧栏退出：两段式后进程应退出
js("document.getElementById('btn-exit').click();return 1")
time.sleep(0.4)
js("document.getElementById('btn-exit').click();return 1")
print("exit clicked, waiting process death...")
dead = False
for i in range(12):
    time.sleep(1.0)
    if probe.poll() is not None:
        dead = True
        break
try:
    tabs = json.load(urllib.request.urlopen(
        "http://127.0.0.1:%d/json" % CDP, timeout=1))
    print("CDP still alive?", bool(tabs))
except Exception:
    print("CDP gone (window closed)")
print("PASS exit-works" if dead else "FAIL exit-works（进程仍在）")
