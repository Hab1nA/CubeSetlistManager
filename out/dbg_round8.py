# -*- coding: utf-8 -*-
"""排查：窄窗 pl 页签 .show 丢失 / popover ESC 焦点还回。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9228
env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
probe = subprocess.Popen([sys.executable, "webui.py", "880", "760"], env=env,
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


def send(method, params=None):
    ws.send(json.dumps({"id": send.n, "method": method,
                        "params": params or {}}))
    send.n += 1
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == send.n - 1:
            return m.get("result", {})


send.n = 1
send("Runtime.enable")


def js(body):
    r = send("Runtime.evaluate", {
        "expression": "(async()=>{%s})()" % body,
        "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


for i in range(15):
    if js("return fetch('/state').then(r=>r.json()).then("
          "j=>'songs' in j)") is True:
        break
    time.sleep(1.0)

# ---- 疑点1：窄窗点「播放列表」页签 ----
print(js(
    "document.querySelector('.b-setlist').click();"
    "await new Promise(r=>setTimeout(r,700));"
    "const t=[...document.querySelectorAll('#sl-tabs button')];"
    "window.__seq=[];"
    "t.forEach(b=>b.addEventListener('click',()=>window.__seq.push("
    "b.dataset.col+':'+[...document.querySelectorAll('.cols .col.show')]"
    ".map(c=>c.dataset.col).join(','))));"
    "t[1].click();"
    "await new Promise(r=>setTimeout(r,500));"
    "return {seq:window.__seq,"
    "showCols:[...document.querySelectorAll('.cols .col.show')]"
    ".map(c=>c.dataset.col),"
    "tabOn:t[1].classList.contains('on')}"))
# 再点一次 dt 然后回点 pl（怀疑首次点击前后有别的切换）
print(js(
    "const t=[...document.querySelectorAll('#sl-tabs button')];"
    "t[2].click();await new Promise(r=>setTimeout(r,200));"
    "t[1].click();await new Promise(r=>setTimeout(r,300));"
    "return {seq2:window.__seq,"
    "showCols:[...document.querySelectorAll('.cols .col.show')]"
    ".map(c=>c.dataset.col)}"))

# ---- 疑点2：popover ESC 后焦点 ----
print(js(
    "const more=document.getElementById('pl-more');"
    "window.__focusLog=[];"
    "const logFocus=()=>window.__focusLog.push("
    "document.activeElement&&document.activeElement.id"
    "||document.activeElement&&document.activeElement.tagName);"
    "more.click();"
    "await new Promise(r=>setTimeout(r,250));"
    "logFocus();"
    "const popHadFocus=document.querySelector('.pop')&&"
    "document.querySelector('.pop').contains(document.activeElement);"
    "document.dispatchEvent(new KeyboardEvent('keydown',"
    "{key:'Escape',bubbles:true}));"
    "await new Promise(r=>setTimeout(r,250));"
    "logFocus();"
    "return {focusLog:window.__focusLog,popHadFocus,"
    "activeNow:document.activeElement&&document.activeElement.id,"
    "popGone:!document.querySelector('.pop')}"))
# 锚点 focus() 单独验证（不经 ESC）
print(js(
    "const more=document.getElementById('pl-more');"
    "more.focus();"
    "return {focused:document.activeElement===more,"
    "ae:document.activeElement.id||document.activeElement.tagName}"))

ws.close()
probe.terminate()
