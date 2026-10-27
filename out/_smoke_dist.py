# -*- coding: utf-8 -*-
"""dist 冒烟：起打包版 exe，CDP 检查 /app 页装载、导航、无 JS 错。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9233
EXE = os.path.join("dist", "Cube Setlist Manager Cubase",
                   "Cube Setlist Manager Cubase.exe")
assert os.path.exists(EXE), EXE
env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
proc = subprocess.Popen([EXE], env=env)
tab = None
try:
    for _ in range(50):
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
        sys.exit("SMOKE FAIL: no /app tab")
    time.sleep(2.5)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
    _n = [0]

    def send(method, params=None):
        _n[0] += 1
        ws.send(json.dumps({"id": _n[0], "method": method,
                            "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _n[0]:
                return m.get("result", {})

    def js(expr):
        r = send("Runtime.evaluate",
                 {"expression": "(async()=>{" + expr + "})()",
                  "awaitPromise": True, "returnByValue": True})
        if r.get("exceptionDetails"):
            return {"__throw": str(r["exceptionDetails"])[:200]}
        return r.get("result", {}).get("value")

    checks = {}
    checks["state"] = js("const r=await fetch('/state');"
                         "return r.ok && (await r.json()).ready !== undefined")
    for pg in ["setlist", "pedal", "kbd", "vj", "settings", "console"]:
        checks["nav-" + pg] = js(
            "document.querySelector('.b-%s').click();"
            "return document.querySelector('.page.on').id" % pg)
    checks["nav-setlist"] = js(
        "document.querySelector('.b-setlist').click();return 1")
    time.sleep(1.0)
    checks["cols"] = js(
        "return {three:document.querySelectorAll('.cols .col').length,"
        "dt:!!document.getElementById('dt-body'),"
        "logCard:!!document.getElementById('logcard')&&"
        "!!document.getElementById('logcard').closest('#p-console')}")
    checks["errs"] = js("return window.__errs||[]")
    ws.close()
    bad = [k for k, v in checks.items()
           if k != "errs" and v in (False, None)]
    print(json.dumps(checks, ensure_ascii=False, indent=1))
    if bad or checks["errs"]:
        sys.exit("SMOKE FAIL: " + str(bad) + " " + str(checks["errs"]))
    print("SMOKE PASS")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
