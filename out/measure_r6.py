# -*- coding: utf-8 -*-
"""第五轮间距返工实测（一次性）：9 项逐条几何 + 长文本/对齐抽查。"""
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9241
OUTD = pathlib.Path(__file__).resolve().parent / "ui_shots"

# 端口残留守卫：连到上一轮残留的旧 webui 实例=对旧 CSS/旧代码跑断言
# （假阳性集中营——本轮 6/7 图标/窄窗 flex/桩时序全假 FAIL 的真凶）
import socket as _socket
with _socket.socket() as _s:
    if _s.connect_ex(("127.0.0.1", CDP)) == 0:
        sys.exit("CDP 端口 %d 被残留实例占用：先结束旧 webui/杀进程再跑" % CDP)

env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
probe = subprocess.Popen([sys.executable, "webui.py"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tab = None
for i in range(40):
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
assert tab, "CDP tab 未就绪"
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
ws.send(json.dumps({"id": 0, "method": "Runtime.enable"}))


def js(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                  "awaitPromise": True})
    return r.get("result", {}).get("value")


def shot(name):
    r = send("Page.captureScreenshot", {"format": "png"})
    # name 全为文件内字面量；resolve 后校验仍在 OUTD 下，杜绝穿越面
    path = (OUTD / (name + ".png")).resolve()
    if not str(path).startswith(str(OUTD.resolve())):
        raise ValueError("非法截图名: %r" % name)
    path.write_bytes(base64.b64decode(r["data"]))
    print("shot", name, flush=True)


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra, flush=True)


UNSTUB = ("if(!window.__realfetch){window.__realfetch="
          "window.__fetch0||window.fetch;}"
          "window.fetch=window.__realfetch;window.__fetch0=null;"
          "window.__fmode='';")

# ---- 桩：定位点 2 点 + MMC 未就绪（显示提示）+ 音色 60 长 desc ----
STUB = (UNSTUB + "window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{"
        "if(String(u)==='/points')return Promise.resolve(new Response("
        "JSON.stringify({cubase:true,mmcReady:false,projects:{"
        "'R6/曲一':{name:'曲一',enabled:true,points:["
        "{id:1,name:'主歌',start:30,end:90},"
        "{id:2,name:'副歌',start:120,end:210}]}}}),{status:200}));"
        "if(String(u)==='/playlist')return Promise.resolve(new Response("
        "JSON.stringify({lib:[{key:'R6/曲一',team:'R6',name:'曲一',"
        "dur:'1:00',durSrc:'auto'}],pl:[{key:'R6/曲一',name:'曲一',"
        "dur:'1:00',durSrc:'auto',pt:false}],cur:0}),{status:200}));"
        "if(String(u)==='/kbd'||String(u).startsWith('/kbd?'))"
        "return Promise.resolve(new Response(JSON.stringify({"
        "editSong:{key:'R6/曲一',name:'曲一',loading:false,"
        "slots:{'60':{pc:63,msb:1,lsb:2,"
        "desc:'USER:0521 超长音色名称测试用例字符串'}},"
        "axSlots:{}}}),{status:200}));"
        "return window.__fetch0(u,o)};")
js("document.querySelector('.b-setlist').click()")
time.sleep(0.8)
js(STUB)
time.sleep(2.0)
for _ in range(10):
    if js("return !!document.querySelector(\"#pl .row[data-k='R6/曲一']\")"):
        break
    time.sleep(0.5)
js("document.querySelector(\"#pl .row[data-k='R6/曲一']\").click()")
time.sleep(1.4)
for _ in range(10):
    if js("return document.querySelectorAll('#pt-list .row').length>0"):
        break
    time.sleep(0.5)
js("document.querySelector('#pt-list .row').click()")
time.sleep(0.8)

# 1) 定位点/音色映射卡头文字顶距 = 16±1（高控件顶对齐后）
g1 = js("""(()=>{const out={};
for(const c of document.querySelectorAll('#dt-body .card')){
const t=c.childNodes[0];const txt=(c.querySelector('.md-head')||{});
const head=c.querySelector('.md-head');
const range=document.createRange();
range.selectNodeContents(head.childNodes[0]);
const tr=range.getBoundingClientRect();
out[head.childNodes[0].textContent.trim().slice(0,4)]=
Math.round(tr.top-c.getBoundingClientRect().top);}
return JSON.stringify(out)})()""")
print("1 cardhead text-top:", g1)
check("1.cardhead-16",
      all(abs(v - 16) <= 2 for v in json.loads(g1).values()), g1)

# 2) MMC 提示与 pt-list 间距 = 12
g2 = js("(()=>{const m=document.getElementById('pt-mmc');"
        "const l=document.getElementById('pt-list');"
        "return {hidden:m.hidden,"
        "gap:Math.round(l.getBoundingClientRect().top"
        "-m.getBoundingClientRect().bottom)}})()")
print("2 mmc-gap:", g2)
check("2.mmc-gap-12", not g2.get("hidden", True)
      and abs(g2.get("gap", -1) - 12) <= 2, json.dumps(g2))

# 3) 结束输入框底与「时间轴分布」lbl 顶 = 12（field-err 空不占位）
g3 = js("(()=>{const e=document.getElementById('pt-e');"
        "const lbl=[...document.querySelectorAll('#pt-form .lbl')]"
        ".find(x=>x.textContent.includes('时间轴'));"
        "const hint=document.getElementById('pt-hint');"
        "return {hintH:Math.round(hint.getBoundingClientRect().height),"
        "gap:Math.round(lbl.getBoundingClientRect().top"
        "-e.getBoundingClientRect().bottom)}})()")
print("3 tl-gap:", g3)
check("3.tl-gap-12", abs(g3.get("gap", -1) - 12) <= 2,
      json.dumps(g3))

# 4) 时间轴 lbl 不粗体、无「只读」
g4 = js("(()=>{const lbl=[...document.querySelectorAll('#pt-form .lbl')]"
        ".find(x=>x.textContent.includes('时间轴'));"
        "return {w:getComputedStyle(lbl).fontWeight,"
        "txt:lbl.textContent}})()")
print("4 tl-lbl:", g4)
check("4.tl-lbl-regular", g4.get("w") in ("400", "normal")
       and "只读" not in g4.get("txt", ""), json.dumps(g4, ensure_ascii=False))

# 5/6) slot：ops 按钮底与音色名顶间距 ≥4；长名省略不溢出
g5 = js("""(()=>{const c=document.querySelector('#dt-slots .slot');
const pc=c.querySelector('.s-pc');
const ops=c.querySelector('.s-ops');
const cs=getComputedStyle(pc);
return {gapBtnPc:Math.round(pc.getBoundingClientRect().top
-ops.getBoundingClientRect().bottom),
clip:cs.whiteSpace==='nowrap'&&cs.overflowX==='hidden'
&&cs.textOverflow==='ellipsis',
within:pc.getBoundingClientRect().right
<=c.getBoundingClientRect().right-8}})()""")
print("5/6 slot:", g5)
check("5.slot-ops-pc-gap", g5.get("gapBtnPc", -1) >= 4, json.dumps(g5))
check("6.slot-longname-clip", g5.get("clip") and g5.get("within"),
      json.dumps(g5))

# 7/9) 踩钉：三卡首内容块起始线一致；evt-log 距 lbl 文字 ≈16
js("document.querySelector('.b-pedal').click()")
time.sleep(1.6)
g7 = js("""(()=>{const cards=[...document.querySelectorAll(
'#p-pedal .stat-cards .card')];
const first=c=>{const s=c.querySelector('.stat-big,#pedal-evts');
return Math.round(s.getBoundingClientRect().top
-c.querySelector('.stat-head').getBoundingClientRect().bottom)};
const evt=cards[2];
const lbl=evt.querySelector('.lbl');
const range=document.createRange();
range.selectNodeContents(lbl);
return {firstTops:cards.map(first),
evtToLbl:Math.round(document.getElementById('pedal-evts')
.getBoundingClientRect().top-range.getBoundingClientRect().bottom),
lblTop:Math.round(range.getBoundingClientRect().top
-evt.getBoundingClientRect().top)}})()""")
print("7/9 pedal:", g7)
d7 = g7 if isinstance(g7, dict) else json.loads(g7)
check("9.first-content-uniform", len(set(d7["firstTops"])) == 1, g7)
check("7.evt-lbl-gap-16", abs(d7["evtToLbl"] - 16) <= 3, g7)
check("9b.lbl-top-16", abs(d7["lblTop"] - 16) <= 2, g7)

# 8) 拦截开关与双踩输入框底对齐
g8 = js("(()=>{const s=document.getElementById('pd-intercept')"
        ".closest('.switch');"
        "const i=document.getElementById('pd-win');"
        "return {dBottom:Math.round(s.getBoundingClientRect().bottom"
        "-i.getBoundingClientRect().bottom)}})()")
print("8 switch-align:", g8)
check("8.switch-bottom-align",
      isinstance(g8, dict) and abs(g8.get("dBottom", 999)) <= 3, str(g8))
js(UNSTUB)

# ---- G1 长文本扫描：全页动态文本容器溢出检查 ----
navs = ["console", "setlist", "pedal", "kbd", "vj", "settings"]
ovr = []
for p in navs:
    js("document.querySelector('.b-%s').click()" % p)
    time.sleep(1.2)
    r = js("""(()=>{const bad=[];
document.querySelectorAll('body *').forEach(e=>{
if(!e.children.length||e.classList.contains('log')||
e.classList.contains('evt-log')||e.id==='log')return;
const cs=getComputedStyle(e);
if(cs.overflow!=='visible')return;
if(e.scrollWidth>e.clientWidth+2&&e.clientWidth>0)
bad.push((e.id||e.className+'').toString().slice(0,30)+'|sw'+
e.scrollWidth+'|cw'+e.clientWidth);});
return bad.slice(0,8)})()""")
    if r:
        ovr.append({p: r})
print("G1 overflow scan:", json.dumps(ovr, ensure_ascii=False))
check("G1.no-horizontal-overflow", not ovr, json.dumps(ovr,
      ensure_ascii=False)[:300])

# ---- 截图目视 ----
js("document.querySelector('.b-setlist').click()")
time.sleep(1.2)
js(STUB)
time.sleep(1.8)
js("const r=document.querySelector(\"#pl .row[data-k='R6/曲一']\");"
   "r&&r.click()")
time.sleep(1.4)
shot("r6_setlist_detail")
js(UNSTUB)
js("document.querySelector('.b-pedal').click()")
time.sleep(1.4)
shot("r6_pedal")

errs = js("JSON.stringify(window.__errs||[])")
check("no-js-errors", errs == "[]", errs)
probe.terminate()
