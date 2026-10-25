# -*- coding: utf-8 -*-
"""第七轮改版后关键几何测量（页脚注可见性/opt-row 对齐/网格一致/f-st 位置）。"""
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
env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=9226 --remote-allow-origins=*")
probe = subprocess.Popen([sys.executable, "webui.py"], env=env,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
tab = None
for i in range(30):
    time.sleep(1.0)
    try:
        tabs = json.load(urllib.request.urlopen(
            "http://127.0.0.1:9226/json", timeout=2))
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
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)


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


def shot(name):
    r = send("Page.captureScreenshot", {"format": "png"})
    (OUTD / name).write_bytes(base64.b64decode(r["data"]))


# 1 主控台：页脚注是否无需滚动可见（视口 764 高）
print("console:", js(
    "const n=document.querySelector('#p-console .page-note');"
    "const r=n.getBoundingClientRect();"
    "return {noteBottom:Math.round(r.bottom),"
    "vh:innerHeight,visible:r.bottom<=innerHeight-4}"))
# 2 键盘页：f-st 徽右缘与 select 右缘对齐；两卡等高；pill 行
print("kbd:", js(
    "document.querySelector('.b-kbd').click();"
    "await new Promise(r=>setTimeout(r,600));"
    "const l=document.querySelector('#kb-jin-st').getBoundingClientRect();"
    "const s=document.querySelector('#kb-jin').getBoundingClientRect();"
    "const c1=document.querySelectorAll('.cards2 .card')[0].getBoundingClientRect();"
    "const c2=document.querySelectorAll('.cards2 .card')[1].getBoundingClientRect();"
    "return {stRight:Math.round(s.right-l.right),"
    "cardH1:Math.round(c1.height),cardH2:Math.round(c2.height)}"))
# 3 踩钉页：opt-row 内开关与输入框底对齐；双列两卡等高
print("pedal:", js(
    "document.querySelector('.b-pedal').click();"
    "await new Promise(r=>setTimeout(r,600));"
    "const sw=document.querySelector('#pd-intercept').closest('.switch')"
    ".getBoundingClientRect();"
    "const win=document.querySelector('#pd-win').getBoundingClientRect();"
    "const g=document.querySelector('.pedal-grid');"
    "const cs=getComputedStyle(g);"
    "const k1=g.children[0].getBoundingClientRect().height;"
    "const k2=g.children[1].getBoundingClientRect().height;"
    "return {swBottom:Math.round(sw.bottom),winBottom:Math.round(win.bottom),"
    "cols:cs.gridTemplateColumns.split(' ').length,"
    "leftH:Math.round(k1),rightH:Math.round(k2)}"))
shot("m7_pedal.png")
# 4 VJ：网格列数与卡高一致性；map 卡与表单卡均用满宽
print("vj:", js(
    "document.querySelector('.b-vj').click();"
    "await new Promise(r=>setTimeout(r,600));"
    "const cells=[...document.querySelectorAll('#vjmap .vj-cell')];"
    "const hs=new Set(cells.map(c=>Math.round(c.getBoundingClientRect()"
    ".height)));"
    "const top=cells[0].getBoundingClientRect().top;"
    "const cols=cells.filter(c=>Math.abs(c.getBoundingClientRect()"
    ".top-top)<2).length;"
    "const grid=document.querySelector('#vjmap').getBoundingClientRect();"
    "const card=grid.closest('.card').getBoundingClientRect();"
    "return {cells:cells.length,colHeights:[...hs],cols,"
    "gridW:Math.round(card.width)}"))
shot("m7_vj.png")
# 5 括号残留 DOM 级复核（排除日志/数据区）
print("parens:", js(
    "const skip=e=>e.closest('#log')||e.closest('.toast')||"
    "e.closest('#now')||e.closest('#next')||e.closest('#console-list')||"
    "e.closest('#lib')||e.closest('#pl')||e.closest('#pt-list')||"
    "e.closest('#dt-slots')||e.closest('#dt-axslots')||e.closest("
    "'#vjmap')||e.closest('#app-addr');const bad=[];"
    "document.querySelectorAll('body *').forEach(e=>{if(skip(e))return;"
    "if(!e.children.length&&e.textContent&&e.textContent.includes('（'))"
    "bad.push(e.textContent.trim().slice(0,40));"
    "['title','placeholder','data-tip'].forEach(a=>{const v="
    "e.getAttribute&&e.getAttribute(a);"
    "if(v&&v.includes('（'))bad.push('attr|'+v)})});"
    "return bad.slice(0,8)"))
ws.close()
probe.terminate()
