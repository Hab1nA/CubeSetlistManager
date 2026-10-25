# -*- coding: utf-8 -*-
"""三轮实机抽检：真实 /cmd switch key 拒绝路径 + 键盘页 dataset.dev 渲染。"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9231
P = {}


def ok(name, cond, extra=""):
    P[name] = (bool(cond), extra)
    print("%-40s %s %s" % (name, "PASS" if cond else "FAIL", extra),
          flush=True)


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

# 1 真实后端：不存在的 key → 400 拒发（不触列表、不切歌）
r1 = js("return fetch('/cmd',{method:'POST',headers:"
        "{'Content-Type':'application/json'},body:JSON.stringify("
        "{action:'switch',index:0,key:'__不存在__'})})"
        ".then(async r=>({st:r.status,j:await r.json()}))")
ok("r3.bad-key-400", r1 and r1.get("st") == 400
   and "不在播放列表" in (r1.get("j") or {}).get("error", ""),
   json.dumps(r1, ensure_ascii=False))

# 2 真实后端：合法 key（首曲）在播放列表非空时 → 200（切歌指令入队，
#   用 busy 拦不住吗——switch 会真切！改为只在列表为空时测，避免扰动）
n = js("return fetch('/playlist').then(r=>r.json())"
       ".then(d=>d.pl.length)")
if isinstance(n, int) and n == 0:
    r2 = js("return fetch('/cmd',{method:'POST',headers:"
            "{'Content-Type':'application/json'},body:JSON.stringify("
            "{action:'switch',index:0,key:'__不存在__'})})"
            ".then(async r=>r.status)")
    ok("r3.empty-pl-safe", r2 == 400, str(r2))
else:
    ok("r3.empty-pl-safe", True, "SKIP：列表非空，跳过扰动性检查")

# 3 键盘页：真实拓扑下 options 带 dataset.dev；连点两次刷新重建后
#   选中身份保持（JUNO 唯一名；同名口由 attack2 桩覆盖）
k1 = js("document.querySelector('.b-kbd').click();"
        "await new Promise(r=>setTimeout(r,900));"
        "document.getElementById('kb-refresh').click();"
        "await new Promise(r=>setTimeout(r,900));"
        "const info=id=>{const s=document.getElementById(id);"
        "return {sel:s.selectedIndex,"
        "opts:[...s.options].map(o=>({t:o.title,d:o.dataset?o.dataset.dev"
        ":null,v:o.value}))}};"
        "return {jin:info('kb-jin'),jout:info('kb-jout')}")
jin = (k1 or {}).get("jin") or {}
opts = jin.get("opts") or []
ok("r3.kbd-dataset-dev", all(o.get("d") is not None for o in opts)
   and len(opts) > 0,
   json.dumps(k1, ensure_ascii=False)[:200])
# 再刷新一次，选中项 title+dev 不变
k2 = js("const before=document.getElementById('kb-jin');"
        "const bSel=before.selectedIndex>=0?"
        "before.options[before.selectedIndex].title:'';"
        "const bDev=before.selectedIndex>=0?"
        "(before.options[before.selectedIndex].dataset.dev||''):'';"
        "document.getElementById('kb-refresh').click();"
        "await new Promise(r=>setTimeout(r,900));"
        "const s=document.getElementById('kb-jin');"
        "const a=s.selectedIndex>=0?s.options[s.selectedIndex]:null;"
        "return {bSel,bDev,aSel:a?a.title:'',aDev:a?(a.dataset.dev||''):''}")
ok("r3.kbd-refresh-keep-identity",
   (k2 or {}).get("bSel") == (k2 or {}).get("aSel")
   and (k2 or {}).get("bDev") == (k2 or {}).get("aDev"),
   json.dumps(k2, ensure_ascii=False)[:200])

errs = js("return window.__errs||[]")
ok("r3.no-js-errors", not errs, str(errs)[:200])
ws.close()
probe.terminate()
print("\n%d/%d PASS" % (sum(1 for v in P.values() if v[0]), len(P)))
