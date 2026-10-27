# -*- coding: utf-8 -*-
"""r8 验收：用户复查 5 项（宽窗底距/音名左距/红字/卡头间距/方向键）。"""
import base64
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9224
OUTD = pathlib.Path("out") / "ui_shots"
fails = []


def ok(name, cond, extra=""):
    print("%-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails.append(name)


with socket.socket() as _s:
    if _s.connect_ex(("127.0.0.1", CDP)) == 0:
        sys.exit("CDP 端口 %d 被占用：先清残留 webui" % CDP)

env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
p = subprocess.Popen([sys.executable, "webui.py", "1280", "800"],
                     env=env, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
try:
    tab = None
    for _ in range(30):
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
    assert tab, "tab not found"
    time.sleep(2.0)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8)
    n = [0]

    def ev(expr):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": "Runtime.evaluate",
                            "params": {"expression": expr,
                                       "returnByValue": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == n[0]:
                return m.get("result", {}).get("result", {}).get("value")

    def jv(expr):
        return json.loads(ev(expr))

    # ---- P1 宽窗：编排页内容区下缘距 #main 底 = 8px（VJ 页 page-foot 同值） ----
    ev("document.querySelector('.b-setlist').click()")
    time.sleep(1.0)
    g = jv(r"""JSON.stringify((() => {
      const r = s => document.querySelector(s).getBoundingClientRect().bottom;
      return { cols: r('.cols'), main: r('#main'),
               pad: getComputedStyle(document.querySelector('#p-setlist'))
                        .paddingBottom }; })())""")
    ok("P1 编排内容区下缘距底=8px",
       abs(g["main"] - g["cols"] - 8) <= 1,
       "gap=%.1f pad=%s" % (g["main"] - g["cols"], g["pad"]))
    ev("document.querySelector('.b-vj').click()")
    time.sleep(1.2)
    g2 = jv(r"""JSON.stringify((() => {
      const f = document.querySelector('#p-vj .page-foot')
          .getBoundingClientRect().bottom;
      const m = document.querySelector('#main')
          .getBoundingClientRect().bottom;
      return { foot: f, main: m }; })())""")
    ok("P1 与 VJ 页脚距底一致(8px)",
       abs((g2["main"] - g2["foot"]) - (g["main"] - g["cols"])) <= 1.5,
       "vj gap=%.1f" % (g2["main"] - g2["foot"]))

    # ---- P2 触发槽位/全局移调音名：无键帽内距，左缘=文字左缘 ----
    ev("document.querySelector('.b-vj').click()")
    time.sleep(0.8)
    p2 = jv(r"""JSON.stringify((() => {
      const el = document.querySelector('#vjmap .s-note');
      const cs = getComputedStyle(el);
      const cell = el.closest('.vj-cell').getBoundingClientRect();
      const s = el.getBoundingClientRect();
      const sub = el.closest('.vj-cell').querySelector('.s-pc')
          .getBoundingClientRect();
      return { pad: cs.paddingLeft, bg: cs.backgroundColor,
               leftDiff: +(s.left - cell.left).toFixed(1),
               alignSub: +(s.left - sub.left).toFixed(1) }; })())""")
    ok("P2 槽位音名无键帽底/内距", p2["pad"] == "0px"
       and p2["bg"] == "rgba(0, 0, 0, 0)", p2["pad"] + " " + p2["bg"])
    ok("P2 音名左缘=卡内边距(12)且与下文字对齐",
       abs(p2["leftDiff"] - 12) <= 1 and abs(p2["alignSub"]) <= 1,
       "left+%s align%s" % (p2["leftDiff"], p2["alignSub"]))
    ev("document.querySelector('.b-kbd').click()")
    time.sleep(0.8)
    p2b = jv(r"""JSON.stringify((() => {
      const el = document.querySelector('#p-kbd .vj-cell .s-note');
      const cs = getComputedStyle(el);
      const cell = el.closest('.vj-cell').getBoundingClientRect();
      const s = el.getBoundingClientRect();
      const sub = el.closest('.vj-cell').querySelector('.s-pc')
          .getBoundingClientRect();
      return { pad: cs.paddingLeft, leftDiff: +(s.left - cell.left).toFixed(1),
               alignSub: +(s.left - sub.left).toFixed(1) }; })())""")
    ok("P2 移调卡音名同步(无内距/对齐)",
       p2b["pad"] == "0px" and abs(p2b["leftDiff"] - 12) <= 1
       and abs(p2b["alignSub"]) <= 1,
       "left+%s align%s" % (p2b["leftDiff"], p2b["alignSub"]))

    # ---- P3 sb-note.err 恢复摘除：sbNote 在 module 作用域，走真实
    #      500ms 轮询路径（poll 成功分支必调 sbNote） ----
    ev("""(function(){ const el = document.querySelector('#sb-note');
      el.classList.add('err'); el.textContent = '断连残留'; })()""")
    time.sleep(1.2)   # 覆盖至少两拍 /state 轮询
    p3b = jv("""JSON.stringify({ cls:
        document.querySelector('#sb-note').className })""")
    ok("P3 sbNote 摘除断连红粗样式", "err" not in p3b["cls"], p3b["cls"])

    # ---- P4 三页状态卡 子标题→首行元素 间距一致（踩钉为准） ----
    ev("document.querySelector('.b-pedal').click()")
    time.sleep(1.0)
    ped = jv(r"""JSON.stringify((() => {
      const c = document.querySelector('#p-pedal .stat-cards .card.stat');
      const l = c.querySelector('.lbl').getBoundingClientRect();
      const b = c.querySelector('.stat-big, .evt-log, .stat-song')
          .getBoundingClientRect();
      return +(b.top - l.top).toFixed(1); })())""")
    ev("document.querySelector('.b-kbd').click()")
    time.sleep(0.8)
    kbd = jv(r"""JSON.stringify((() => {
      const c = document.querySelector('#p-kbd .stat-cards .card.stat');
      const l = c.querySelector('.lbl').getBoundingClientRect();
      const b = c.querySelector('.stat-song, .stat-big')
          .getBoundingClientRect();
      return +(b.top - l.top).toFixed(1); })())""")
    ev("document.querySelector('.b-vj').click()")
    time.sleep(0.8)
    vj = jv(r"""JSON.stringify((() => {
      const c = document.querySelector('#p-vj .stat-cards .card.stat');
      const l = c.querySelector('.lbl').getBoundingClientRect();
      const b = c.querySelector('.stat-big').getBoundingClientRect();
      return +(b.top - l.top).toFixed(1); })())""")
    ok("P4 踩钉/键盘/VJ 卡头间距一致",
       abs(ped - kbd) <= 1 and abs(ped - vj) <= 1,
       "pedal=%s kbd=%s vj=%s" % (ped, kbd, vj))

    # ---- P5 方向键切歌（stub state：cur=null 未切过歌） ----
    ev(r"""(function(){ const songs = [{key:"k0",name:"甲",dur:"1:00"},
          {key:"k1",name:"乙",dur:"2:00"},{key:"k2",name:"丙",dur:"3:00"}];
      window.__sent = [];
      window.__cur = null;   // 有状态 stub：switch 落地后 cur 跟进（同真实后端）
      window.fetch = (function(real){ return function(u, o){
        if (u === '/cmd' && o && o.body) { const b = JSON.parse(o.body);
          if (b.action === 'switch') window.__cur = b.index;
          window.__sent.push(o.body);
          return Promise.resolve(new Response('{"ok":true}',
            {status:200})); }
        if (u === '/state') return Promise.resolve(new Response(
          JSON.stringify({open:true,projName:"P",tstate:"stopped",
            pos:0,dur:180,cur:window.__cur,songs:songs,busy:false}),
          {status:200}));
        if (u === '/playlist') return Promise.resolve(new Response(
          JSON.stringify({cur:window.__cur,pl:songs,lib:songs}),
          {status:200}));
        return real.apply(this, arguments); };})(window.fetch); })()""")
    time.sleep(1.2)   # 等 /state 轮询吃 stub
    ev("""document.querySelector('.b-console').click();
document.body.focus();""")
    time.sleep(0.6)
    ev("""(function(){ document.dispatchEvent(new KeyboardEvent('keydown',
      {key:'ArrowRight', bubbles:true})); })()""")
    time.sleep(0.4)
    sent = jv("JSON.stringify(window.__sent)")
    ok("P5 cur=null 时 →=切第一首", len(sent) == 1
       and '"index":0' in sent[0], sent)
    time.sleep(1.2)   # 等 /state 轮询拉到 cur=0 且过 900ms 单发去重窗
    ev("""(function(){ document.dispatchEvent(new KeyboardEvent('keydown',
      {key:'ArrowRight', bubbles:true})); })()""")
    time.sleep(0.4)
    sent2 = jv("JSON.stringify(window.__sent)")
    ok("P5 第二次 →=切第二首（cur 已跟进）", len(sent2) == 2
       and '"index":1' in sent2[1], sent2)

    shot = send_ok = None
    n[0] += 1
    ws.send(json.dumps({"id": n[0], "method": "Page.captureScreenshot",
                        "params": {"format": "png"}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == n[0]:
            (OUTD / "r8_console.png").write_bytes(
                base64.b64decode(m["result"]["data"]))
            break
    errs = ev("JSON.stringify(window.__errs)")
    ok("全程无 JS 错误", errs == "[]", errs)
    ws.close()
finally:
    p.terminate()
    try:
        p.wait(timeout=8)
    except Exception:
        p.kill()

print("\n%d FAIL" % len(fails), fails)
sys.exit(1 if fails else 0)
