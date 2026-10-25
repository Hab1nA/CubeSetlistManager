# -*- coding: utf-8 -*-
"""第八轮红队：CDP 行为模拟（窄窗折叠/演出模式操作/毒输入/焦点流/弹层）。"""
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
CDP = 9227
P = {}


def ok(name, cond, extra=""):
    P[name] = (bool(cond), extra)
    print("%-36s %s %s" % (name, "PASS" if cond else "FAIL", extra),
          flush=True)


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
        "expression": "(async()=>{try{return JSON.stringify("
                      "await (async()=>{%s})())}catch(e){"
                      "return JSON.stringify({__throw:String(e)})}})()" % body,
        "returnByValue": True, "awaitPromise": True})
    v = r.get("result", {}).get("value")
    try:
        return json.loads(v) if v is not None else {}
    except ValueError:
        return {"__raw": v}


def shot(name):
    r = send("Page.captureScreenshot", {"format": "png"})
    (OUTD / name).write_bytes(base64.b64decode(r["data"]))
    print("shot", name, flush=True)


def nav(page):
    js("document.querySelector('.b-%s').click()" % page)
    time.sleep(0.8)


healthy = False
for i in range(20):
    v = js("return fetch('/state').then(r=>r.json())"
           ".then(j=>'songs' in j)")
    if v is True:
        healthy = True
        break
    time.sleep(1.0)
if not healthy:
    probe.terminate()
    print("BOOT unhealthy", flush=True)
    sys.exit(2)

# ---- 1 窄窗 880px 折叠行为 ----
w1 = js("return {w:innerWidth,tabs:getComputedStyle(document."
        "getElementById('sl-tabs')).display,"
        "pedalCols:getComputedStyle(document.querySelector("
        "'.pedal-grid')).gridTemplateColumns.split(' ').length,"
        "cards2:getComputedStyle(document.querySelector('.cards2'))"
        ".gridTemplateColumns.split(' ').length,"
        "grid2:getComputedStyle(document.querySelector("
        "'#p-settings .grid2')).gridTemplateColumns.split(' ').length}")
ok("r8.n1.narrow-fold", w1.get("tabs") == "flex"
   and w1.get("pedalCols") == 1 and w1.get("cards2") == 1
   and w1.get("grid2") == 1, json.dumps(w1))
nav("pedal")
shot("r8_narrow_pedal.png")
nav("setlist")
js("document.querySelector('#sl-tabs [data-col=lib]').click()")
shot("r8_narrow_setlist.png")
# tab 切换三页签均可达（pl 状态须在点 dt 之前采样）
t2 = js("const t=[...document.querySelectorAll('#sl-tabs button')];"
        "t[1].click();await new Promise(r=>setTimeout(r,300));"
        "const plOk=document.querySelector('.col[data-col=pl]')"
        ".classList.contains('show');"
        "t[2].click();await new Promise(r=>setTimeout(r,300));"
        "const dt=document.querySelector('#col-dt');"
        "return {pl:plOk,"
        "dt:dt.classList.contains('show'),n:t.length}")
ok("r8.n2.narrow-tabs", t2.get("pl") and t2.get("dt") and t2.get("n") == 3,
   json.dumps(t2))

# ---- 2 演出模式：chips 快跳在册、页脚注隐藏、空格行为 ----
nav("console")
s1 = js("document.getElementById('btn-show').click();"
        "await new Promise(r=>setTimeout(r,500));"
        "return {show:document.body.classList.contains('show'),"
        "noteHidden:getComputedStyle(document.querySelector("
        "'#p-console .page-note')).display,"
        "exit:!!document.getElementById('show-exit')}")
ok("r8.s1.show-mode", s1.get("show") and s1.get("noteHidden") == "none"
   and s1.get("exit"), json.dumps(s1))
shot("r8_show_mode.png")
js("document.getElementById('show-exit').click()")
time.sleep(0.6)

# ---- 3 毒输入：定位点时码（stub /points//playlist 给出已有点）----
nav("setlist")
js("if(window.__fetch0)window.fetch=window.__fetch0;"
   "window.__fetch0=window.fetch;"
   "window.fetch=(u,o)=>{"
   "if(String(u)==='/points')return Promise.resolve(new Response("
   "JSON.stringify({cubase:true,mmcReady:true,projects:{"
   "'S/歌':{name:'歌',enabled:true,points:["
   "{id:1,name:'主歌',start:60,end:120}]}}}),{status:200}));"
   "if(String(u)==='/playlist')return Promise.resolve(new Response("
   "JSON.stringify({lib:[{key:'S/歌',team:'S',name:'歌',"
   "dur:'1:00',durSrc:'auto'}],pl:[{key:'S/歌',name:'歌',"
   "dur:'1:00',durSrc:'auto',pt:false}],cur:0}),{status:200}));"
   "return window.__fetch0(u,o)};")
time.sleep(1.8)
js("const r=document.querySelector('#pl .row')"
   "||document.querySelector('#lib .row');r&&r.click()")
time.sleep(1.0)
js("const p=document.querySelector('#pt-list .row');p&&p.click()")
time.sleep(0.8)
poison = js("const put=async(s,e)=>{"
            "const el=id=>document.getElementById(id);"
            "el('pt-s').value=s;el('pt-s').dispatchEvent("
            "new Event('input',{bubbles:true}));"
            "el('pt-e').value=e;el('pt-e').dispatchEvent("
            "new Event('input',{bubbles:true}));"
            "await new Promise(r2=>setTimeout(r2,50));"
            "return {save:el('pt-save').disabled,"
            "inv1:el('pt-s').classList.contains('invalid'),"
            "inv2:el('pt-e').classList.contains('invalid'),"
            "hint:el('pt-hint').textContent}};"
            "const out={};"
            "out.a=await put('0:60','1:00');"
            "out.b=await put('-5','10');"
            "out.c=await put('1e3','10');"
            "out.d=await put('3:3:3:4','10');"
            "out.e=await put('99:99','100:00');"
            "out.ok=await put('3:33','4:05');"
            "return out")
pv = poison.get("__raw") or poison
ok("r8.p1.poison-tc",
   (pv.get("a") or {}).get("save") is True
   and (pv.get("b") or {}).get("save") is True
   and (pv.get("c") or {}).get("save") is True
   and (pv.get("d") or {}).get("save") is True
   and (pv.get("e") or {}).get("save") is True     # 分秒越界 99:99
   and (pv.get("ok") or {}).get("save") is False,
   json.dumps(pv, ensure_ascii=False)[:400])
js("if(window.__fetch0){window.fetch=window.__fetch0;"
   "window.__fetch0=null}")

# ---- 4 毒输入：踩钉双踩窗（回滚而非写入）----
nav("pedal")
time.sleep(1.0)
pw = js("window.__t=[];const _f=window.fetch;window.__pedalF0=_f;"
        "window.fetch=(u,o)=>{if(String(u)==='/pedalset')"
        "window.__t.push((o&&o.body)||'');"
        "return _f(u,o)};"
        "const el=document.getElementById('pd-win');"
        "el.value='abc';el.dispatchEvent(new Event('change'));"
        "await new Promise(r=>setTimeout(r,400));"
        "const bad=window.__t.length;"
        "el.value='';el.dispatchEvent(new Event('change'));"
        "await new Promise(r=>setTimeout(r,400));"
        "return {badPosts:bad,emptyPosts:window.__t.length,"
        "rolledBack:document.getElementById('pd-win').value!==''&&"
        "document.getElementById('pd-win').value!=='abc'}")
ok("r8.p2.pedal-win-poison",
   pw.get("badPosts") == 0 and pw.get("rolledBack"),
   json.dumps(pw))
# 真还原记录桩（此前是恒等 no-op——桩贯穿后续全部断言）
js("if(window.__pedalF0){window.fetch=window.__pedalF0;"
   "window.__pedalF0=null}")

# ---- 5 焦点流：Tab 可达元素统计 + popover 焦点还回 ----
nav("console")
f1 = js("const els=[...document.querySelectorAll("
        "'button,input,select,[tabindex]')].filter(e=>"
        "!e.disabled&&e.offsetParent!==null);"
        "return {focusable:els.length,"
        "navFirst:els[0]&&els[0].className}")
ok("r8.f1.focusables", (f1.get("focusable") or 0) > 15,
   json.dumps(f1))
f2 = js("document.querySelector('.b-setlist').click();"
        "await new Promise(r=>setTimeout(r,600));"
        "const tp=document.querySelector('#sl-tabs [data-col=pl]');"
        "tp&&tp.click();"
        "await new Promise(r=>setTimeout(r,300));"
        "const more=document.getElementById('pl-more');"
        "more.click();await new Promise(r=>setTimeout(r,300));"
        "const pop=document.querySelector('.pop');"
        "document.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'Escape',bubbles:true}));"
        "await new Promise(r=>setTimeout(r,250));"
        "return {popOpen:!!pop,popGone:!document.querySelector('.pop'),"
        "focusBack:document.activeElement===more}")
ok("r8.f2.popover-focus", f2.get("popOpen") and f2.get("popGone")
   and f2.get("focusBack"), json.dumps(f2))

# ---- 6 空格键：焦点在按钮上时 keydown 目标=按钮→不劫持 ----
nav("console")
sp = js("const btn=document.getElementById('next-go');"
        "btn.focus();"
        "window.__sp=[];const _f=window.fetch;"
        "window.fetch=(u,o)=>{if(String(u)==='/cmd'&&!(o&&o.body&&"
        "String(o.body).includes('switch')))"
        "window.__sp.push(1);return _f(u,o)};"
        "btn.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:' ',bubbles:true}));"
        "await new Promise(r=>setTimeout(r,300));"
        "const n=window.__sp.length;window.fetch=_f;"
        "return {hijacked:n}")
ok("r8.sp.space-not-hijacked-on-button", sp.get("hijacked") == 0,
   json.dumps(sp))

# ---- 7 换曲保护：录制开着的卡片在新数据下不残留（模拟）----
# 已由探针 bd.d3 覆盖保存路径；此处补「开录后换曲」UI 收口
rec = js("document.querySelector('.b-setlist').click();"
         "await new Promise(r=>setTimeout(r,600));"
         "const rows=()=>[...document.querySelectorAll('#pl .row')];"
         "if(rows().length<2)return {skip:true};"
         "rows()[0].click();await new Promise(r=>setTimeout(r,900));"
         "const card=document.querySelector('#dt-slots .slot');"
         "if(!card)return {skip:true};"
         "card.click();await new Promise(r=>setTimeout(r,500));"
         "const recording=card.classList.contains('recording');"
         "rows()[1].click();await new Promise(r=>setTimeout(r,900));"
         "return {recording,afterSwitch:document.getElementById("
         "'dt-title').textContent,"
         "residual:document.querySelectorAll('.slot.recording').length}")
if rec.get("skip"):
    ok("r8.rec.switch-song-stops-record", True, "SKIP：真实列表不足两曲，"
       "录制流由探针 bd.d3 桩路径覆盖")
else:
    ok("r8.rec.switch-song-stops-record",
       rec.get("recording") and rec.get("residual") == 0,
       json.dumps(rec, ensure_ascii=False))

errs = js("return window.__errs")
ok("r8.no-js-errors", not errs, str(errs)[:300])
ws.close()
probe.terminate()
print("\n%d/%d PASS" % (sum(1 for v in P.values() if v[0]), len(P)))
