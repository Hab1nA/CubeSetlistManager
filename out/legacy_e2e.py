# -*- coding: utf-8 -*-
"""旧页（PAGE_BROWSER）pendK 防线实机验证：真渲染+确认窗+key 重解析。

桩法：Page.addScriptToEvaluateOnNewDocument 在页面脚本前拦 fetch；
/state 回可变 window.__state，POST 全记录；file:// 直开成品 HTML。"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9230
P = {}
ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE_URL = (ROOT / "out" / "_legacy_pages" / "browser.html").as_uri()


def ok(name, cond, extra=""):
    P[name] = (bool(cond), extra)
    print("%-40s %s %s" % (name, "PASS" if cond else "FAIL", extra),
          flush=True)


env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=* --allow-file-"
    "access-from-files" % CDP)
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
time.sleep(2.0)
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
send("Page.enable")

STUB = r"""
window.__posts=[];
window.__state={ready:true,busy:false,open:true,live:false,
 projName:"歌一",tstate:"stopped",pos:0,dur:0,cur:0,confirm:true,
 songs:[{key:"a",name:"歌一",dur:"1:30"},{key:"b",name:"歌二",dur:"2:00"}],
 webIp:"",appPort:0};
window.__mk=o=>Promise.resolve(new Response(JSON.stringify(o),
 {status:200,headers:{"Content-Type":"application/json"}}));
window.fetch=(u,o)=>{const us=String(u);
 if(us==="/state")return window.__mk(window.__state);
 window.__posts.push([us,(o&&o.body)||""]);
 return window.__mk({ok:true});};
"""
send("Page.addScriptToEvaluateOnNewDocument", {"source": STUB})
send("Page.navigate", {"url": PAGE_URL})
time.sleep(2.5)


def js(body):
    r = send("Runtime.evaluate", {
        "expression": "(async()=>{%s})()" % body,
        "returnByValue": True, "awaitPromise": True})
    return r.get("result", {}).get("value")


# ---- L1：渲染 + 确认流正向（带 key 发包）----
l1 = js("const rows=document.querySelectorAll('#list li');"
         "return {n:rows.length,"
         "second:rows[1]?rows[1].textContent:''}")
ok("lg.l1.render", l1.get("n") == 2 and "歌二" in (l1.get("second") or ""),
   json.dumps(l1, ensure_ascii=False))
js("document.querySelectorAll('#list li')[1].click()")
time.sleep(0.4)
l2 = js("return {modal:document.getElementById('m-confirm')"
        ".classList.contains('show'),"
        "txt:document.getElementById('c-text').textContent}")
ok("lg.l2.confirm-modal", l2.get("modal") and "歌二" in (l2.get("txt") or ""),
   json.dumps(l2, ensure_ascii=False)[:120])
js("window.__posts=[];document.getElementById('c-yes').click()")
time.sleep(0.4)
l3 = js("return {posts:(window.__posts||[]).filter(x=>x[0]==='/cmd')}")
ok("lg.l3.confirm-fires-with-key",
   any('\"key\":\"b\"' in b and "switch" in b
       for _u, b in l3.get("posts", [])),
   json.dumps(l3, ensure_ascii=False)[:160])

# ---- L2：确认窗内目标被移除 → 重解析中止，不发旧下标 ----
js("window.__state=Object.assign({},window.__state,"
   "{confirm:true,songs:[{key:\"a\",name:\"歌一\",dur:\"1:30\"},"
   "{key:\"c\",name:\"歌三\",dur:\"2:00\"}]});return 1")
time.sleep(1.5)          # 等一轮 1s 轮询把 st 洗成新表
js("document.querySelectorAll('#list li')[1].click()")   # 武装 c
time.sleep(0.4)
# 武装后移除目标 c，等轮询刷新 st，再确认
js("window.__state=Object.assign({},window.__state,"
   "{songs:[{key:\"a\",name:\"歌一\",dur:\"1:30\"}]});return 1")
time.sleep(1.5)
js("window.__posts=[];document.getElementById('c-yes').click()")
time.sleep(0.4)
l5 = js("return {posts:(window.__posts||[]).filter(x=>x[0]==='/cmd'),"
        "toast:document.getElementById('toast').textContent,"
        "toastShow:document.getElementById('toast')"
        ".classList.contains('show')}")
ok("lg.l5.stale-key-abort",
   not l5.get("posts")
   and "不在播放列表" in (l5.get("toast") or ""),
   json.dumps(l5, ensure_ascii=False)[:200])

errs = js("return window.__errs?window.__errs:[]")
ws.close()
probe.terminate()
print("\n%d/%d PASS" % (sum(1 for v in P.values() if v[0]), len(P)))
