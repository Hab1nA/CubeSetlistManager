# -*- coding: utf-8 -*-
"""四轮实机验证：moveStuck 断网保门→轮询落地自动放门（行为级探针）。

桩法：/plcmd 与 /playlist 全拦（不动真实播放列表），/playlist 可切换
失败/成功；用「↑ 点击产生的 move 包数」观测门的开合。"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9232
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

# 先放行让 2 曲桩落地（行数确定），再断网
js("window.__posts=[];window.__plFail=false;"
   "window.__f0=window.fetch;"
   "window.fetch=(u,o)=>{const us=String(u);"
   "if(us==='/plcmd'){window.__posts.push((o&&o.body)||'');"
   "return Promise.resolve(new Response('{\"ok\":true}',"
   "{status:200,headers:{'Content-Type':'application/json'}}))}"
   "if(us==='/playlist'){"
   "if(window.__plFail)return Promise.resolve(new Response('{}',"
   "{status:500}));"
   "return Promise.resolve(new Response(JSON.stringify({"
   "lib:[{key:'a',team:'T',name:'甲',dur:'1:00',durSrc:'auto'},"
   "{key:'b',team:'T',name:'乙',dur:'2:00',durSrc:'auto'}],"
   "pl:[{key:'a',name:'甲',dur:'1:00',durSrc:'auto',pt:false},"
   "{key:'b',name:'乙',dur:'2:00',durSrc:'auto',pt:false}],cur:0}),"
   "{status:200}))}"
   "return window.__f0(u,o)};")
time.sleep(2.0)          # 等 boot 后的一次 refreshPlaylist 消化

nav = js("document.querySelector('.b-setlist').click();"
         "await new Promise(r=>setTimeout(r,1800));"
         "return document.querySelectorAll('#pl .row').length")
ok("r4.rows", nav == 2, str(nav))

# 断网态（/playlist 恒 500）下点 ↑：POST 发出、回显两拉皆败→保门
js("window.__plFail=true;window.__posts=[];"
   "const up=document.querySelector('#pl .row:nth-child(2) "
   ".ops button[title=上移]');up&&up.click();return 1")
time.sleep(1.5)
p1 = js("return (window.__posts||[]).length")
# 保门期再点 ↑：应被吞（无新包）
js("const up=document.querySelector('#pl .row:nth-child(2) "
   ".ops button[title=上移]');up&&up.click();return 1")
time.sleep(0.8)
p2 = js("return (window.__posts||[]).length")
ok("r4.stuck-gate-holds", p1 == 1 and p2 == 1,
   "first=%s second=%s" % (p1, p2))

# 网络恢复：轮询下一拍 /playlist 成功→自动放门→再点 ↑ 有新包
js("window.__plFail=false;return 1")
time.sleep(2.2)          # 等 1.5s 轮询拍落地并放门
js("const up=document.querySelector('#pl .row:nth-child(2) "
   ".ops button[title=上移]');up&&up.click();return 1")
time.sleep(0.8)
p3 = js("return (window.__posts||[]).length")
ok("r4.poll-auto-release", p3 == 2, "after-recover=%s" % p3)

js("window.fetch=window.__f0;window.__f0=null;return 1")
errs = js("return window.__errs||[]")
ok("r4.no-js-errors", not errs, str(errs)[:200])
ws.close()
probe.terminate()
print("\n%d/%d PASS" % (sum(1 for v in P.values() if v[0]), len(P)))
