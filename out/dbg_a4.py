# -*- coding: utf-8 -*-
"""A4 插桩：逐步打印 plCache/stState/armed 状态。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9229
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

# /state 桩（confirm=true, cur=0, 两曲）
print("stub:", js(
    "if(window.__fetch0){window.fetch=window.__fetch0;window.__fetch0=null}"
    "window.__fetch0=window.fetch;"
    "window.fetch=(u,o)=>{"
    "if(String(u)==='/state')return Promise.resolve(new Response("
    "JSON.stringify({ready:true,busy:false,open:true,projName:'歌一',"
    "tstate:'playing',pos:3,dur:90,cur:0,confirm:true,"
    "songs:[{name:'歌一',dur:'1:30'},{name:'歌二',dur:'2:00'}],"
    "pedalRemote:null,obs:false,loop:false,webIp:'',appPort:8767}),"
    "{status:200}));"
    "if(String(u)==='/playlist')"
    "return Promise.resolve(new Response(JSON.stringify({lib:[],"
    "pl:[{key:'a',name:'歌一',dur:'1:30',durSec:90,durSrc:'auto',pt:false},"
    "{key:'b',name:'歌二',dur:'2:00',durSec:120,durSrc:'auto',pt:false}],"
    "cur:0}),{status:200}));"
    "return window.__fetch0(u,o)};"
    "return 1"))
time.sleep(1.8)
print("arm:", js(
    "window.__posts=[];"
    "const b=document.getElementById('next-go');"
    "b.disabled=false;b.click();"
    "return {armed:!!b.dataset.armed,txt:b.textContent}"))
# 换桩：/playlist 只剩 1 曲；/state 仍走旧桩（经 __fetch0？——错：
# __fetch0 现在是真 fetch，必须保留 /state 分支）
print("swap:", js(
    "const inner=window.fetch;"   # 当前桩
    "window.fetch=(u,o)=>{"
    "if(String(u)==='/playlist')"
    "return Promise.resolve(new Response(JSON.stringify({lib:[],"
    "pl:[{key:'a',name:'歌一',dur:'1:30',durSec:90,durSrc:'auto',pt:false}],"
    "cur:0}),{status:200}));"
    "return inner(u,o)};"
    "return 1"))
time.sleep(1.9)
print("mid:", js(
    "const b=document.getElementById('next-go');"
    "return {armed:!!b.dataset.armed,txt:b.textContent,"
    "dis:b.disabled}"))
print("fire:", js(
    "window.__posts=[];"
    "const b=document.getElementById('next-go');"
    "b.disabled=false;b.click();"
    "return {stillArmed:!!b.dataset.armed,"
    "posts:(window.__posts||[]).filter(x=>x[0]==='/cmd'),"
    "toast:[...document.querySelectorAll('.toast')].map(t=>t.textContent)}"
    .replace("window.__posts||[]", "window.__posts||[]")))
print("errs:", js("return window.__errs"))
ws.close()
probe.terminate()
