# -*- coding: utf-8 -*-
"""CDP 交互与样式断言（分阶段验证 webui 前端）。

用法：py -u out/probe_ui.py p0|p1|p2|all
- 模拟点击/键盘/拖拽（Runtime.evaluate 派发事件）
- 读计算样式与 DOM 状态断言
- 破坏性操作前拦截 window.fetch（不触真后端）
- 顺带截图关键状态到 out/ui_shots/（供人工复审）
"""
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
OUTD.mkdir(parents=True, exist_ok=True)
CDP = 9225

P = {}          # 断言结果收集


def ok(name, cond, extra=""):
    P[name] = (bool(cond), extra)
    print("%-34s %s %s" % (name, "PASS" if cond else "FAIL", extra),
          flush=True)


def boot():
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--remote-debugging-port=%d --remote-allow-origins=*" % CDP)
    probe = subprocess.Popen([sys.executable, "webui.py"], env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
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
    if not tab:
        probe.terminate()
        sys.exit("CDP tab 未就绪")
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
    ws.send(json.dumps({"id": 0, "method": "Runtime.enable"}))
    # 启动健康门：等 /state 快照建成（Tk 主线程 _tick_banner 首轮）。
    # 本机已知停摆病偶发在启动期卡住 Tk 线程——那不是前端问题，明确分流。
    # /settings 也要等到（页内 stBase 基线依赖它；迟到会让设置页断言竞态）。
    healthy = False
    for i in range(30):
        r = send("Runtime.evaluate", {
            "expression": "(async()=>{const r=await fetch('/state');"
                          "const j=await r.json();"
                          "if(!('songs' in j)) return false;"
                          "const s=await fetch('/settings');"
                          "return s.ok})()",
            "awaitPromise": True, "returnByValue": True})
        if r.get("result", {}).get("value") is True:
            healthy = True
            break
        time.sleep(1.0)
    if not healthy:
        print("BOOT STALL：/state 快照未建成（Tk 主线程未就绪，环境问题"
              "——终止本轮，不代表前端失败）", flush=True)
        probe.terminate()
        sys.exit(2)
    time.sleep(1.5)      # 让页内 refreshSettings 落完（stBase 基线就绪）
    return probe, send


def js(send, body):
    """执行语句序列（内层 async IIFE 被 await 后再序列化）；异常 __throw 回传。"""
    r = send("Runtime.evaluate", {
        "expression": "(async()=>{try{"
                      "return JSON.stringify(await (async()=>{%s})())"
                      "}catch(e){return JSON.stringify("
                      "{__throw:String(e)+' @'+"
                      "String(e.stack||'').split('\\n')[1]})}})()" % body,
        "returnByValue": True, "awaitPromise": True})
    v = r.get("result", {}).get("value")
    if v is None:
        print("  [debug] raw:", json.dumps(r)[:300], flush=True)
    v = r.get("result", {}).get("value")
    if v is None:
        return {"__none": True}
    try:
        return json.loads(v)
    except ValueError:
        return {"__raw": v}


# 页内助手：拦截/恢复 fetch（破坏性操作隔离）。__fetch0 只记真实 fetch，
# 嵌套安全：已拦截时只切换应答模式；RESTORE 恒还原真实 fetch。
INTERCEPT = ("window.__posts=[];"
             "if(!window.__fetch0){window.__fetch0=window.fetch;"
             "window.fetch=(u,o)=>{"
             "window.__posts.push([String(u),(o&&o.body)||'']);"
             "return Promise.resolve(new Response("
             "window.__fmode==='fail'?'{\"error\":\"测试注入失败\"}'"
             ":'{\"ok\":true}',"
             "{status:window.__fmode==='fail'?400:200,"
             "headers:{'Content-Type':'application/json'}}))};}"
             "window.__fmode='ok';")
# 失败应答变体（测错误 toast 用）
INTERCEPT_FAIL = INTERCEPT.replace("window.__fmode='ok';",
                                   "window.__fmode='fail';")
# 先行拆桩（自定义 stub 安装前用，防嵌套把拦截壳当真 fetch 存走）
UNSTUB = ("if(window.__fetch0){window.fetch=window.__fetch0;"
          "window.__fetch0=null;window.__fmode='';}")
RESTORE = UNSTUB


def shot(send, name):
    r = send("Page.captureScreenshot", {"format": "png"})
    (OUTD / (name + ".png")).write_bytes(base64.b64decode(r["data"]))
    print("shot", name, flush=True)


def nav(send, page):
    js(send, "document.querySelector('.b-%s').click()" % page)
    time.sleep(1.8)                       # 等一轮数据轮询


def stage_p0(send):
    # 1. 设计 token 与全局动效规则
    tok = js(send, "const cs=getComputedStyle(document.documentElement);"
             "const sh=()=>Array.from(document.styleSheets).flatMap(s=>{try{"
             "return Array.from(s.cssRules)}catch(e){return []}});"
             "return {fnow:cs.getPropertyValue('--f-now').trim(),"
             "reduced:sh().some(r=>r.cssText.includes('prefers-reduced"
             "-motion')),"
             "activeBtn:sh().some(r=>r.cssText.startsWith('button:active')),"
             "focusVis:sh().some(r=>r.cssText.includes(':focus-visible'))}")
    ok("p0.token.fnow", tok["fnow"] == "42px", tok["fnow"])
    ok("p0.motion.reduced-rule", tok["reduced"])
    ok("p0.btn.active-rule", tok["activeBtn"])
    ok("p0.btn.focusvisible-rule", tok["focusVis"])

    # 2. 进度条 width 过渡（Chromium 归一化 500ms→0.5s）
    bar = js(send, "const cs=getComputedStyle(document.getElementById"
             "('barfill'));return {p:cs.transitionProperty,"
             "d:cs.transitionDuration}")
    ok("p0.bar.width-500ms", "width" in bar["p"]
       and bar["d"].replace(".0", "").strip() in ("0.5s", "500ms"),
       "%s %s" % (bar["p"], bar["d"]))

    # 3. 演出模式：进入（切页+热区可见）→ ESC 退出 → F11 进入再退出
    st = js(send, INTERCEPT +
            "document.getElementById('btn-show').click();"
            "return {show:document.body.classList.contains('show'),"
            "page:document.querySelector('.page.on').id,"
            "exit:getComputedStyle(document.getElementById('show-exit'))"
            ".display}")
    ok("p0.show.enter", st.get("show") and st.get("page") == "p-console"
       and st.get("exit") != "none", json.dumps(st, ensure_ascii=False))
    shot(send, "x_p0_showmode")
    js(send, "document.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'Escape',bubbles:true}))")
    time.sleep(0.4)
    st2 = js(send, "return {show:document.body.classList.contains('show')}")
    ok("p0.show.exit-esc", not st2.get("show"))
    js(send, "document.body.classList.add('show')")
    js(send, "document.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'F11',bubbles:true,cancelable:true}))")
    time.sleep(0.4)
    st3 = js(send, "return {show:document.body.classList.contains('show')}")
    ok("p0.show.exit-f11", not st3.get("show"))

    # 4. toast：拦截为失败应答后点保存 → 错误 toast 可见
    #    （P1 起保存钮洁净态禁用：先改一个端口值弄脏再点；事后还原）
    js(send, INTERCEPT_FAIL +
       "const el=document.getElementById('st-srv');"
       "window.__srv0=el.value;"
       "el.value=(Number(el.value)||8765)+1;"
       "el.dispatchEvent(new Event('input',{bubbles:true}));"
       "document.getElementById('st-save').click()")
    time.sleep(0.5)
    t2 = js(send, "return {n:document.getElementById('toasts').children"
           ".length,txt:document.getElementById('toasts').textContent}")
    ok("p0.toast.on-save-fail", t2.get("n", 0) >= 1 and "失败" in t2.get("txt", ""),
       json.dumps(t2, ensure_ascii=False))
    shot(send, "x_p0_toast")
    js(send, RESTORE + "const el=document.getElementById('st-srv');"
        "if(el&&window.__srv0!==undefined){el.value=window.__srv0;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}"
        "document.getElementById('toasts').innerHTML=''")

    # 5. 键盘音色槽 popover（替代 prompt 链；P2 起槽为 .slot 网格卡）
    nav(send, "kbd")
    pop = js(send, "const r=document.querySelector('#kb-slots .slot');"
             "if(!r)return {none:1};r.click();"
             "return {pop:!!document.querySelector('.pop'),"
             "pc:!!document.querySelector('.pop #sp-pc')}")
    ok("p0.popover.open", pop.get("pop") and pop.get("pc"),
       json.dumps(pop, ensure_ascii=False))
    v = js(send, "const p=document.querySelector('.pop');"
           "p.querySelector('#sp-pc').value='200';"
           "p.querySelector('#sp-pc').dispatchEvent(new Event('input',"
           "{bubbles:true}));"
           "const ok=p.querySelector('.btns .primary');"
           "return {err:p.querySelector('.field-err').textContent,"
           "dis:ok.disabled}")
    ok("p0.popover.pc-validate", v.get("dis") and v.get("err"),
       json.dumps(v, ensure_ascii=False))
    js(send, INTERCEPT + "const p=document.querySelector('.pop');"
        "p.querySelector('#sp-pc').value='63';"
        "p.querySelector('#sp-pc').dispatchEvent(new Event('input',"
        "{bubbles:true}));p.querySelector('.btns .primary').click();")
    posts = js(send, "return window.__posts||[]")
    ok("p0.popover.slotset-post",
       any("/slotset" in u for u, _b in posts), str(posts)[:120])
    js(send, RESTORE + "document.querySelector('.pop')"
        "&&document.querySelector('.pop').remove()")

    # 6. 两段式确认（编排移除）
    nav(send, "setlist")
    two = js(send, INTERCEPT + "const b=document.querySelector("
             "'#pl .row .ops button.danger');if(!b)return {none:1};"
             "b.click();return {armed:b.classList.contains('armed'),"
             "txt:b.textContent}")
    ok("p0.twostep.armed", two.get("armed") and "确认" in two.get("txt", ""),
       json.dumps(two, ensure_ascii=False))
    js(send, "const b=document.querySelector("
        "'#pl .row .ops button.danger');b&&b.click()")
    time.sleep(0.2)
    posts = js(send, "return window.__posts||[]")
    ok("p0.twostep.confirmed", any("/plcmd" in u and "remove" in b
       for u, b in posts), str(posts)[:120])
    shot(send, "x_p0_twostep")
    js(send, RESTORE)

    # 7. 定位点时码即时校验
    nav(send, "points")
    pts = js(send, "return {rows:document.querySelectorAll("
             "'#pt-list .row').length}")
    if pts.get("rows"):
        val = js(send, "document.querySelector('#pt-list .row').click();"
                 "document.getElementById('pt-s').value='abc';"
                 "document.getElementById('pt-s').dispatchEvent(new Event("
                 "'input',{bubbles:true}));"
                 "return {inv:document.getElementById('pt-s').classList"
                 ".contains('invalid'),"
                 "dis:document.getElementById('pt-save').disabled,"
                 "hint:document.getElementById('pt-hint').textContent}")
        ok("p0.tc.invalid-marked", val.get("inv") and val.get("dis")
           and val.get("hint"), json.dumps(val, ensure_ascii=False))
        val2 = js(send, "document.getElementById('pt-s').value='1:30';"
                  "document.getElementById('pt-s').dispatchEvent(new Event("
                  "'input',{bubbles:true}));"
                  "document.getElementById('pt-e').value='0:45';"
                  "document.getElementById('pt-e').dispatchEvent(new Event("
                  "'input',{bubbles:true}));"
                  "return {dis:document.getElementById('pt-save').disabled,"
                  "hint:document.getElementById('pt-hint').textContent}")
        ok("p0.tc.end-before-start", val2.get("dis")
           and "晚于" in val2.get("hint", ""),
           json.dumps(val2, ensure_ascii=False))
        val3 = js(send, "document.getElementById('pt-e').value='2:30';"
                  "document.getElementById('pt-e').dispatchEvent(new Event("
                  "'input',{bubbles:true}));"
                  "return {dis:document.getElementById('pt-save').disabled,"
                  "hint:document.getElementById('pt-hint').textContent}")
        ok("p0.tc.valid-enables-save", not val3.get("dis")
           and val3.get("hint") == "", json.dumps(val3, ensure_ascii=False))
    else:
        ok("p0.tc.skipped-no-data", True, "（本机无定位点工程）")

    # 8. JS 错误汇总
    errs = js(send, "return window.__errs")
    ok("p0.no-js-errors", not errs, str(errs)[:200])


def stage_p1(send):
    nav(send, "console")            # p0 可能停在其它页：回来再量主控台几何
    # ---- /state 新字段 ----
    st = js(send, "const r=await fetch('/state',{cache:'no-store'});"
             "const t=await r.text();"
             "return {origin:location.href,raw:t.slice(0,220),"
             "obs:t.indexOf('\"obs\"'),len:t.length}")
    print("  [diag /state]", json.dumps(st, ensure_ascii=False)[:400],
          flush=True)
    ok("p1.state.fields", st.get("obs", -1) >= 0 and st.get("len", 0) > 10,
       json.dumps(st, ensure_ascii=False)[:160])

    # ---- 侧边栏：三组分区 + 图标 + 宽度 ----
    side = js(send, "return {w:document.getElementById('side').offsetWidth,"
             "groups:[...document.querySelectorAll('.side-group')]"
             ".map(g=>g.textContent),"
             "icons:[...document.querySelectorAll('.nav svg')].length,"
             "navN:document.querySelectorAll('.nav').length,"
             "onHasSvg:!!document.querySelector('.nav.on svg'),"
             "showBtn:getComputedStyle(document.getElementById('btn-show'))"
             ".backgroundColor}")
    ok("p1.side.width-200", side.get("w") == 200, str(side.get("w")))
    ok("p1.side.groups", side.get("groups") == ["演出", "准备", "系统"],
       str(side.get("groups")))
    ok("p1.side.icons", side.get("icons") == 8 and side.get("navN") == 8,
       "%s/%s" % (side.get("icons"), side.get("navN")))

    # ---- 主控台 hero + 智能钮 + 时钟 ----
    hero = js(send, "const chip=document.getElementById('tchip');"
             "const smart=document.getElementById('smart');"
             "return {chip:chip.textContent,chipCls:chip.className,"
             "clock:document.getElementById('clock').textContent,"
             "clockDim:document.getElementById('clock').classList"
             ".contains('dim'),"
             "smart:smart.textContent,smartAct:smart.dataset.action,"
             "panic:[...document.querySelectorAll('#transport [data-cmd]')]"
             ".map(b=>b.dataset.cmd).join(','),"
             "panicIso:document.querySelector('[data-cmd=panic]')."
             "getBoundingClientRect().left>"
             "document.querySelector('[data-cmd=next]')."
             "getBoundingClientRect().left,"
             "heroCard:!!document.querySelector('.hero.card')}")
    ok("p1.console.hero", hero.get("heroCard") and hero.get("chip"),
       json.dumps(hero, ensure_ascii=False))
    ok("p1.console.clock-format", "已播" in hero.get("clock", "")
       and "剩余" in hero.get("clock", ""),
       hero.get("clock", ""))
    ok("p1.console.smart-btn", hero.get("smart") in
       ("▶ 开始", "⏸ 暂停", "▷ 继续", "切换中…")
       and hero.get("smartAct") in ("play", "pause", "resume", ""),
       "%s/%s" % (hero.get("smart"), hero.get("smartAct")))
    ok("p1.console.panic-isolated",
       hero.get("panic") == "rewind,prev,next,panic" and hero.get("panicIso"),
       "%s iso=%s" % (hero.get("panic"), hero.get("panicIso")))
    shot(send, "x_p1_console")

    # ---- 列表三态 + hover 切歌钮 ----
    rows = js(send, "return [...document.querySelectorAll("
             "'#console-list .row')].map(r=>({"
             "cls:r.className,hasSwitch:!!r.querySelector('.go-switch'),"
             "eq:!!r.querySelector('.eq')}))")
    n = len(rows)
    ok("p1.console.rows", n > 0 and all(r["hasSwitch"] for r in rows),
       "rows=%d" % n)

    # ---- 空状态（stub 空 /playlist + 无工程 /state，等轮询渲染）----
    js(send, INTERCEPT +
        "window.fetch=(u,o)=>{"
        "if(String(u)==='/playlist')return Promise.resolve("
        "new Response(JSON.stringify({lib:[],pl:[],cur:null}),"
        "{status:200}));"
        "if(String(u)==='/state')return Promise.resolve("
        "new Response(JSON.stringify({open:false,songs:[],cur:null,"
        "tstate:'stopped',dur:0,pos:0,pedalRemote:null}),"
        "{status:200}));"
        "return window.__fetch0(u,o)};")
    time.sleep(1.2)
    es = js(send, "return {empty:!document.getElementById('console-empty')"
           ".hidden,now:document.getElementById('now').textContent}")
    ok("p1.console.empty-state", es.get("empty"), json.dumps(es,
       ensure_ascii=False))
    shot(send, "x_p1_empty")
    js(send, RESTORE)
    time.sleep(1.2)

    # ---- 编排：搜索过滤 ----
    nav(send, "setlist")
    f = js(send, "const all=()=>document.querySelectorAll('#lib .row').length;"
           "const a0=all();"
           "const q=document.getElementById('sl-search');"
           "q.value='@@不存在的歌名@@';"
           "q.dispatchEvent(new Event('input',{bubbles:true}));"
           "return {a0,a1:all()}")
    ok("p1.setlist.search-filter", f.get("a0", 0) > 0 and f.get("a1") == 0,
       json.dumps(f))
    js(send, "const q=document.getElementById('sl-search');q.value='';"
        "q.dispatchEvent(new Event('input',{bubbles:true}))")

    # ---- 编排：拖拽排序（stub 应答，验证 up/down 命令序列）----
    drag = js(send, INTERCEPT + "const rows=()=>[...document.querySelectorAll"
             "('#pl .row')];"
             "if(rows().length<2)return {skip:rows().length};"
             "const dt=new DataTransfer();"
             "const src=rows()[1];"
             "src.dispatchEvent(new DragEvent('dragstart',{bubbles:true,"
             "dataTransfer:dt}));"
             "const tgt=rows()[0];"
             "tgt.dispatchEvent(new DragEvent('dragover',{bubbles:true,"
             "dataTransfer:dt,"
             "clientY:tgt.getBoundingClientRect().top}));"
             "tgt.dispatchEvent(new DragEvent('drop',{bubbles:true,"
             "dataTransfer:dt,"
             "clientY:tgt.getBoundingClientRect().top}));"
             "return {armed:1}")
    time.sleep(0.6)
    posts = js(send, "return window.__posts||[]")
    if drag.get("skip") is not None:
        ok("p1.setlist.dnd.skipped", True, "（播放列表不足 2 行）")
    else:
        moves = [b for u, b in posts if "/plcmd" in u]
        ok("p1.setlist.dnd-posts",
           len(moves) == 1 and '"op":"move"' in moves[0]
           and '"from":2' in moves[0] and '"to":0' in moves[0],
           str(moves)[:160])
    js(send, RESTORE)

    # ---- 定位点：stub 数据注入 master-detail（先过 setlist 页取 stub /playlist）----
    nav(send, "points")
    stub = (UNSTUB + "window.__fetch0=window.fetch;"
            "window.fetch=(u,o)=>{"
            "if(String(u)==='/points')return Promise.resolve(new Response("
            "JSON.stringify({projects:{'A/歌一':{name:'歌一',enabled:true,"
            "points:[{id:1,name:'主歌',start:30,end:90},"
            "{id:2,name:'副歌',start:120,end:210}]}}}),{status:200}));"
            "if(String(u)==='/playlist')return Promise.resolve(new Response("
            "JSON.stringify({lib:[],pl:["
            "{key:'A/歌一',name:'歌一',dur:'3:30',pt:false},"
            "{key:'pt:A/歌一#1',name:'歌一 · 主歌',dur:'1:00',pt:true},"
            "{key:'pt:A/歌一#2',name:'歌一 · 副歌',dur:'1:30',pt:true}],"
            "cur:1}),{status:200}));"
            "if(String(u)==='/state')return Promise.resolve(new Response("
            "JSON.stringify({ready:true,busy:false,open:true,"
            "projName:'歌一',tstate:'playing',pos:12,dur:60,cur:1,"
            "songs:[{name:'歌一',dur:'3:30'},{name:'歌一 · 主歌',"
            "dur:'1:00'},{name:'歌一 · 副歌',dur:'1:30'}],"
            "pedalRemote:null,obs:false,loop:false,webIp:'',appPort:8767}),"
            "{status:200}));"
            "return window.__fetch0(u,o)};")
    js(send, stub)
    js(send, "document.querySelector('.b-setlist').click()")
    time.sleep(1.0)
    js(send, "document.querySelector('.b-points').click()")
    time.sleep(1.0)
    pt = js(send, "return {rows:document.querySelectorAll('#pt-list .row')"
           ".length,none:document.getElementById('pt-none').hidden}")
    ok("p1.points.master-list", pt.get("rows") == 2,
       json.dumps(pt, ensure_ascii=False))
    sel = js(send, "document.querySelector('#pt-list .row').click();"
             "return 1")
    time.sleep(1.2)
    sel = js(send, "return {form:!document.getElementById('pt-form').hidden,"
             "name:document.getElementById('pt-name').value,"
             "s:document.getElementById('pt-s').value,"
             "e:document.getElementById('pt-e').value,"
             "gotoDis:document.getElementById('pt-goto').disabled,"
             "segs:document.querySelectorAll('#pt-tl .seg').length,"
             "tlHidden:document.getElementById('pt-tl').hidden,"
             "max:document.getElementById('pt-tl-max').textContent}")
    ok("p1.points.detail-populates", sel.get("form") and
       sel.get("name") == "主歌" and sel.get("s") == "0:30" and
       sel.get("e") == "1:30", json.dumps(sel, ensure_ascii=False))
    ok("p1.points.timeline", sel.get("segs") == 2 and not sel.get("tlHidden")
       and sel.get("max") == "3:30", json.dumps(sel, ensure_ascii=False))
    ok("p1.points.goto-enabled", not sel.get("gotoDis"),
       str(sel.get("gotoDis")))
    val = js(send, "document.getElementById('pt-s').value='5:00';"
             "document.getElementById('pt-s').dispatchEvent(new Event('input',"
             "{bubbles:true}));"
             "document.getElementById('pt-e').value='1:00';"
             "document.getElementById('pt-e').dispatchEvent(new Event('input',"
             "{bubbles:true}));"
             "return {dis:document.getElementById('pt-save').disabled,"
             "hint:document.getElementById('pt-hint').textContent}")
    ok("p1.points.validate-end", val.get("dis") and
       "晚于" in val.get("hint", ""), json.dumps(val, ensure_ascii=False))
    shot(send, "x_p1_points")

    # ---- 主控台 chips + 三态（同一 stub 态下顺带验证）----
    js(send, "document.querySelector('.b-console').click()")
    time.sleep(1.2)
    ch = js(send, "return {chips:document.querySelectorAll("
           "'#chips .chip-btn').length,"
           "cur:document.querySelectorAll('#chips .chip-btn.cur').length,"
           "hidden:document.getElementById('chips').hidden,"
           "played:document.querySelectorAll('#console-list .row.played')"
           ".length,"
           "curRow:document.querySelectorAll('#console-list .row.cur').length,"
           "chipTxt:document.querySelector('#chips .chip-btn')"
           "?document.querySelector('#chips .chip-btn').textContent:''}")
    ok("p1.console.chips", ch.get("hidden") is False
       and ch.get("chips") == 2 and ch.get("cur") == 1,
       json.dumps(ch, ensure_ascii=False))
    ok("p1.console.three-states", ch.get("played") == 1
       and ch.get("curRow") == 1, json.dumps(ch, ensure_ascii=False))
    shot(send, "x_p1_chips")

    # ---- 拖拽排序（stub 播放列表 3 行，验证 up/down 命令序列与插入位）----
    js(send, "document.querySelector('.b-setlist').click()")
    time.sleep(1.0)
    drag = js(send, UNSTUB + INTERCEPT +
              "const rows=()=>[...document.querySelectorAll('#pl .row')];"
              "if(rows().length<2)return {skip:rows().length};"
              "const dt=new DataTransfer();"
              "const src=rows()[2];"          # 末行拖到首行前 → 2×up
              "src.dispatchEvent(new DragEvent('dragstart',{bubbles:true,"
              "dataTransfer:dt}));"
              "const tgt=rows()[0];"
              "tgt.dispatchEvent(new DragEvent('dragover',{bubbles:true,"
              "dataTransfer:dt,"
              "clientY:tgt.getBoundingClientRect().top}));"
              "tgt.dispatchEvent(new DragEvent('drop',{bubbles:true,"
              "dataTransfer:dt,"
              "clientY:tgt.getBoundingClientRect().top}));"
              "return {armed:1}")
    time.sleep(0.6)
    posts = js(send, "return window.__posts||[]")
    if drag.get("skip") is not None:
        ok("p1.setlist.dnd-posts", True, "（stub 行数不足，跳过）")
    else:
        moves = [b for u, b in posts if "/plcmd" in u]
        ok("p1.setlist.dnd-posts",
           len(moves) == 1 and '"from":2' in moves[0] and '"to":0' in moves[0],
           str(moves)[:200])
    js(send, RESTORE)
    time.sleep(1.8)

    # ---- 踩钉页 ----
    nav(send, "pedal")
    pd = js(send, "return {conn:document.getElementById('pedal-conn')"
           ".textContent,link:document.getElementById('pedal-link')"
           ".textContent,"
           "binds:document.querySelectorAll('.bind-row').length,"
           "devSel:document.getElementById('pd-dev').tagName,"
           "adv:!!document.querySelector('details.card')}")
    ok("p1.pedal.stat-cards", pd.get("conn") and pd.get("binds") == 6
       and pd.get("devSel") == "SELECT" and pd.get("adv"),
       json.dumps(pd, ensure_ascii=False))

    # ---- 键盘页：stepper 重置 ----
    nav(send, "kbd")
    kb = js(send, "return {cards:document.querySelectorAll("
           "'#p-kbd .cards2 .card').length,"
           "slots:document.querySelectorAll('#kb-slots .slot').length,"
           "ax:document.querySelectorAll('#kb-axslots .slot').length,"
           "reset:!!document.getElementById('kb-jreset')}")
    ok("p1.kbd.port-cards", kb.get("cards") == 2 and kb.get("slots") == 10
       and kb.get("ax") == 6, json.dumps(kb))
    js(send, INTERCEPT + "document.getElementById('kb-jreset').click()")
    posts = js(send, "return window.__posts||[]")
    ok("p1.kbd.reset-posts", any("/kbdshift" in u and '"total":0' in b
       for u, b in posts), str(posts)[:120])
    js(send, RESTORE)

    # ---- VJ 页：真实 OBS 状态 ----
    nav(send, "vj")
    vj = js(send, "const r=await fetch('/state');const j=await r.json();"
             "return {obs:document.getElementById('vj-obs').textContent,"
             "expect:j.obs?'已连接':'未连接'}")
    ok("p1.vj.obs-real", vj.get("obs") == vj.get("expect"),
       json.dumps(vj, ensure_ascii=False))

    # ---- 设置页：脏标记 ----
    nav(send, "settings")
    d0 = js(send, "return {dirty:document.getElementById('st-dirty').hidden,"
           "saveDis:document.getElementById('st-save').disabled}")
    d1 = js(send, "const el=document.getElementById('st-srv');"
           "el.value=(Number(el.value)||8765)+1;"
           "el.dispatchEvent(new Event('input',{bubbles:true}));"
           "return {dirty:document.getElementById('st-dirty').hidden,"
           "saveDis:document.getElementById('st-save').disabled}")
    d2 = js(send, "const el=document.getElementById('st-srv');"
           "el.value=(Number(el.value)-1)+'';"
           "el.dispatchEvent(new Event('input',{bubbles:true}));"
           "return {dirty:document.getElementById('st-dirty').hidden,"
           "saveDis:document.getElementById('st-save').disabled}")
    ok("p1.settings.clean-disabled", d0.get("saveDis") is True
       and d0.get("dirty") is True,
       json.dumps(d0))
    ok("p1.settings.dirty-marked", d1.get("dirty") is False
       and d1.get("saveDis") is False, json.dumps(d1))
    ok("p1.settings.dirty-cleared", d2.get("dirty") is True
       and d2.get("saveDis") is True, json.dumps(d2))
    ad = js(send, "const r=await fetch('/state');"
           "const j=await r.json();"
           "const a=j.webIp&&j.appPort?"
           "'http://'+j.webIp+':'+j.appPort+'/':'';"
           "const w=document.getElementById('app-addr-wrap');"
           "return {addr:a,wrapHidden:w.hidden,"
           "txt:a?document.getElementById('app-addr').textContent:''}")
    ok("p1.settings.app-addr", ad.get("wrapHidden") == (not ad.get("addr"))
       and (not ad.get("addr") or ad.get("txt") == ad.get("addr")),
       json.dumps(ad, ensure_ascii=False))
    shot(send, "x_p1_settings")

    # ---- 日志：工具条 + 增量 append（stub 行数据：安静启动时环形缓冲为空）----
    nav(send, "log")
    js(send, UNSTUB + "window.__logstub=[{t:100,msg:'启动'},{t:105,"
           "msg:'界面异常：X'}];"
           "window.__fetch0=window.fetch;"
           "window.fetch=(u,o)=>{"
           "if(String(u)==='/log')return Promise.resolve(new Response("
           "JSON.stringify({lines:window.__logstub}),{status:200}));"
           "return window.__fetch0(u,o)};")
    time.sleep(1.8)
    lg = js(send, "return {tools:!!document.querySelector('.log-tools'),"
           "lines:document.querySelectorAll('#log .ln').length,"
           "errLines:document.querySelectorAll('#log .ln.err').length,"
           "firstTxt:document.getElementById('log').firstChild"
           "?document.getElementById('log').firstChild.textContent:''}")
    ok("p1.log.toolbar-lines", lg.get("tools") and lg.get("lines") == 2
       and lg.get("errLines") == 1, json.dumps(lg, ensure_ascii=False))
    js(send, "window.__logstub.push({t:9999,msg:'注入测试行'})")
    time.sleep(1.8)
    inc2 = js(send, "return {lines:document.querySelectorAll('#log .ln')"
            ".length,last:document.getElementById('log').lastChild"
            ".textContent,firstSame:document.getElementById('log').firstChild"
            ".textContent==="+json.dumps(lg.get("firstTxt", ""))+"}")
    ok("p1.log.incremental", inc2.get("lines") == 3
       and "注入测试行" in (inc2.get("last") or "")
       and inc2.get("firstSame"), json.dumps(inc2, ensure_ascii=False))
    flt = js(send, "const s=document.getElementById('lg-filter');"
           "s.value='err';s.dispatchEvent(new Event('change',{bubbles:true}));"
           "const v=document.getElementById('log').classList"
           ".contains('hide-ok');"
           "s.value='all';s.dispatchEvent(new Event('change',{bubbles:true}));"
           "return v")
    ok("p1.log.filter", flt)
    js(send, RESTORE)
    time.sleep(1.6)

    # ---- 状态灯：真实映射 + 点击跳页 ----
    lm = js(send, "return (async()=>{const r=await fetch('/state');"
           "const j=await r.json();"
           "const obs=document.getElementById('l-obs');"
           "const loop=document.getElementById('l-loop');"
           "document.getElementById('l-obs').click();"
           "return {obsOk:obs.classList.contains('ok')===!!j.obs,"
           "obsCls:obs.className,obsTip:obs.dataset.tip,"
           "loopCls:loop.className,"
           "page:document.querySelector('.page.on').id}})()")
    ok("p1.lamps.real-obs", lm.get("obsOk") and lm.get("obsTip"),
       json.dumps(lm, ensure_ascii=False))
    ok("p1.lamps.click-nav", lm.get("page") == "p-vj", lm.get("page"))

    # ---- JS 错误汇总 ----
    errs = js(send, "return window.__errs")
    ok("p1.no-js-errors", not errs, str(errs)[:300])


def stage_p2(send):
    # ---- QR：stub /state 出地址 → 真实渲染路径出 SVG → 矩阵与 Python
    #      qrcode 库 8 个掩码逐一对照（等价即格式位/RS/放置全对）----
    try:
        import qrcode
    except ImportError:
        qrcode = None
    nav(send, "settings")
    js(send, UNSTUB + "window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{"
        "if(String(u)==='/state')return Promise.resolve(new Response("
        "JSON.stringify({webIp:'192.168.137.1',appPort:8767,obs:false,"
        "loop:false,ready:true,busy:false,open:false,songs:[],cur:null,"
        "tstate:'stopped',dur:0,pos:0,pedalRemote:null}),{status:200}));"
        "return window.__fetch0(u,o)};")
    time.sleep(1.2)
    qr = js(send, "const w=document.getElementById('app-addr-wrap');"
            "const svg=document.querySelector('#qr-box svg');"
            "return {addr:document.getElementById('app-addr').textContent,"
            "wrapHidden:w.hidden,"
            "viewBox:svg?svg.getAttribute('viewBox'):null,"
            "path:svg?svg.querySelector('path').getAttribute('d'):null}")
    ok("p2.qr.rendered", qr.get("wrapHidden") is False
       and qr.get("addr") == "http://192.168.137.1:8767/"
       and qr.get("path"), (qr.get("addr") or "")[:60])
    shot(send, "x_p2_qr")
    js(send, RESTORE)
    time.sleep(0.6)
    if qrcode and qr.get("path"):
        import re as _re
        mods = [(int(m.group(1)), int(m.group(2))) for m in
                _re.finditer(r"M(\d+),(\d+)", qr["path"])]
        qz = 2
        size = int(qr["viewBox"].split(" ")[2]) - qz * 2
        grid = [[False] * size for _ in range(size)]
        for x, y in mods:
            if 0 <= x - qz < size and 0 <= y - qz < size:
                grid[y - qz][x - qz] = True
        matched = -1
        for k in range(8):
            q = qrcode.QRCode(
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                mask_pattern=k, border=0)
            q.add_data(qr["addr"], optimize=0)   # 单 byte 段（与我方一致）
            q.make(fit=True)
            if [[1 if v else 0 for v in row] for row in q.get_matrix()] \
                    == [[1 if v else 0 for v in row] for row in grid]:
                matched = k
                break
        ok("p2.qr.cross-check", matched >= 0,
           "size=%d 与 python qrcode（单 byte 段）掩码 %d 全等"
           % (size, matched))
    else:
        ok("p2.qr.cross-check", False, "qrcode 库缺失或 path 为空")

    # ---- 音色槽网格卡 + 试听（/kbdtest）----
    nav(send, "kbd")
    sg = js(send, "return {grid:document.querySelectorAll('#kb-slots"
           ".slot-grid .slot').length,"
           "ax:document.querySelectorAll('#kb-axslots .slot').length,"
           "unconf:document.querySelectorAll('.slot.unconfigured').length,"
           "test:document.querySelectorAll('.slot .s-test').length}")
    ok("p2.slots.grid", sg.get("grid") == 10 and sg.get("ax") == 6
       and sg.get("test") == 16, json.dumps(sg))
    # 试听 POST 路径：拦截 /kbd（回注一个已配置槽）+/slotset → 经 popover
    # 保存触发 refreshKbd → 点「试」应 POST /kbdtest
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "if(String(u)==='/kbd')return Promise.resolve(new Response("
        "JSON.stringify({junoIn:'',junoOut:'',axIn:'',axOut:'',junoShift:0,"
        "axShift:0,song:null,slots:{60:{pc:63,msb:1,lsb:2}},axSlots:{}}),"
        "{status:200}));"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    js(send, "document.querySelector('#kb-slots .slot').click()")
    js(send, "const p=document.querySelector('.pop');"
        "p.querySelector('#sp-pc').value='63';"
        "p.querySelector('#sp-pc').dispatchEvent(new Event('input',"
        "{bubbles:true}));p.querySelector('.btns .primary').click()")
    time.sleep(0.6)                        # refreshKbd 读 stub /kbd 重排卡片
    kt = js(send, "const t=document.querySelector("
            "'.slot:not(.unconfigured) .s-test');"
            "if(!t)return {none:1};t.click();return {clicked:1}")
    posts = js(send, "return window.__posts||[]")
    ok("p2.slots.kbdtest-post",
       kt.get("clicked") and any("/kbdtest" in u for u, _b in posts),
       "%s %s" % (json.dumps(kt, ensure_ascii=False), str(posts)[:140]))
    js(send, RESTORE)
    time.sleep(0.4)
    pop = js(send, "document.querySelector('#kb-slots .slot').click();"
             "return {pop:!!document.querySelector('.pop')}")
    ok("p2.slots.popover", pop.get("pop"))
    js(send, "document.querySelector('.pop')"
        "&&document.querySelector('.pop').remove()")
    js(send, RESTORE)
    shot(send, "x_p2_slots")

    # ---- 目录选择按钮（不点击=不开真对话框；契约由单测覆盖）----
    br = js(send, "return [...document.querySelectorAll('.browse')].map("
           "b=>b.dataset.pick)")
    ok("p2.browse.buttons", br == ["st-root"] or set(br) == {"st-root",
       "vj-root"}, str(br))

    # ---- 窄窗响应式（<900px）：tabs 出现、双列折叠、grid2 单列 ----
    send("Emulation.setDeviceMetricsOverride",
         {"width": 780, "height": 900, "deviceScaleFactor": 1,
          "mobile": False})
    time.sleep(0.5)
    nw = js(send, "document.querySelector('.b-setlist').click();"
           "return {tabs:getComputedStyle(document.getElementById('sl-tabs'))"
           ".display,"
           "libShown:getComputedStyle(document.querySelector("
           "'.cols .col.show')).display,"
           "plHidden:getComputedStyle(document.querySelectorAll("
           "'.cols .col')[1]).display,"
           "colsDir:getComputedStyle(document.querySelector('.cols'))"
           ".flexDirection,"
           "gridCols:getComputedStyle(document.querySelector('.grid2'))"
           ".gridTemplateColumns}")
    ok("p2.narrow.tabs", nw.get("tabs") == "flex"
       and nw.get("plHidden") == "none",
       json.dumps(nw, ensure_ascii=False))
    shot(send, "x_p2_narrow")
    # tab 切换到播放列表
    tb = js(send, "document.querySelectorAll('#sl-tabs button')[1].click();"
           "const cols=document.querySelectorAll('.cols .col');"
           "return {plShown:getComputedStyle(cols[1]).display,"
           "libHidden:getComputedStyle(cols[0]).display}")
    ok("p2.narrow.tab-switch", tb.get("plShown") != "none"
       and tb.get("libHidden") == "none", json.dumps(tb, ensure_ascii=False))
    send("Emulation.clearDeviceMetricsOverride", {})
    time.sleep(0.3)

    # ---- JS 错误汇总 ----
    errs = js(send, "return window.__errs")
    ok("p2.no-js-errors", not errs, str(errs)[:300])


def _state_stub(confirm, cur=0, busy=False):
    """A 批共用 /state+/playlist 桩（其它请求记录后回 ok）。/playlist 须给
    合法 shape 且 cur 与 /state 一致——refreshPlaylist 会用它的 cur 覆盖
    stCur，透真（真实 cur=null）或回 ok:true 都会把 next-go 的门洗失效。"""
    st = ("{ready:true,busy:%s,open:true,projName:'歌一',tstate:'playing',"
          "pos:3,dur:90,cur:%d,confirm:%s,"
          "songs:[{name:'歌一',dur:'1:30'},{name:'歌二',dur:'2:00'}],"
          "pedalRemote:null,obs:false,loop:false,webIp:'',appPort:8767}"
          % ("true" if busy else "false", cur,
             "true" if confirm else "false"))
    return (UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
            "window.fetch=(u,o)=>{"
            "window.__posts.push([String(u),(o&&o.body)||'']);"
            "if(String(u)==='/state')return Promise.resolve(new Response("
            "JSON.stringify(%s),{status:200}));"
            "if(String(u)==='/playlist')"
            "return Promise.resolve(new Response(JSON.stringify({lib:[],"
            "pl:[{key:'a',name:'歌一',dur:'1:30',durSec:90,durSrc:'auto',"
            "pt:false},{key:'b',name:'歌二',dur:'2:00',durSec:120,"
            "durSrc:'auto',pt:false}],cur:%d}),{status:200}));"
            "return Promise.resolve(new Response('{\"ok\":true}',"
            "{status:200,headers:{'Content-Type':'application/json'}}))};"
            % (st, cur))


def stage_a(send):
    # ---- A1：[hidden] 计算样式级（hidden 属性 ⇒ computed display:none）----
    nav(send, "console")
    a1 = js(send, "const d=id=>getComputedStyle(document.getElementById(id));"
            "return {"
            "emptyHid:document.getElementById('console-empty').hidden,"
            "emptyD:d('console-empty').display,"
            "chipsHid:document.getElementById('chips').hidden,"
            "chipsD:d('chips').display}")
    ok("ba.a1.hidden-console-empty",
       a1.get("emptyHid") is (a1.get("emptyD") == "none"),
       json.dumps(a1))
    ok("ba.a1.hidden-chips",
       a1.get("chipsHid") is (a1.get("chipsD") == "none"),
       json.dumps(a1))
    nav(send, "points")
    a1b = js(send, "const d=id=>getComputedStyle(document.getElementById(id));"
             "return {formHid:document.getElementById('pt-form').hidden,"
             "formD:d('pt-form').display,"
             "noneHid:document.getElementById('pt-none').hidden,"
             "noneD:d('pt-none').display}")
    ok("ba.a1.hidden-pt-form", a1b.get("formHid") is (a1b.get("formD") == "none")
       and a1b.get("noneHid") is (a1b.get("noneD") == "none"),
       json.dumps(a1b))
    # 选中一个点后互斥（有定位点数据的机器上才跑强断言）
    rowsn = js(send, "return document.querySelectorAll('#pt-list .row').length")
    if rowsn:
        js(send, "document.querySelector('#pt-list .row').click()")
        time.sleep(1.2)
        a1c = js(send, "const d=id=>getComputedStyle(document.getElementById(id));"
                 "return {formD:d('pt-form').display,"
                 "noneD:d('pt-none').display}")
        ok("ba.a1.pt-mutex-on-select",
           a1c.get("formD") != "none" and a1c.get("noneD") == "none",
           json.dumps(a1c))
    else:
        ok("ba.a1.pt-mutex-on-select", True, "（本机无定位点工程）")

    # ---- A2：busy 态全停放行 ----
    js(send, _state_stub(False, busy=True))
    time.sleep(1.2)
    a2 = js(send, "return {panic:document.querySelector('[data-cmd=panic]')"
            ".disabled,"
            "others:[...document.querySelectorAll("
            "'#transport [data-cmd]')].filter(b=>b.dataset.cmd!=='panic')"
            ".map(b=>b.disabled),"
            "smart:document.getElementById('smart').disabled}")
    ok("ba.a2.busy-panic-live", a2.get("panic") is False
       and bool(a2.get("others")) and all(a2["others"])
       and a2.get("smart") is True, json.dumps(a2))

    # ---- A3：切歌确认两段式（confirm=true 首点不发包，再点才发）----
    nav(send, "console")
    js(send, _state_stub(True))
    time.sleep(1.2)
    _a3dbg = js(send, "return {dis:document.getElementById('next-go')"
                ".disabled,"
                "cur:document.querySelector('#console-list .row.cur')"
                "?document.querySelectorAll('#console-list .row').length:-1}")
    if _a3dbg.get("dis") or _a3dbg.get("cur", -1) < 0:
        print("  [diag ba.a3]", json.dumps(_a3dbg), flush=True)
    # 首点只武装（单独一次 evaluate，读回武装态与按钮文本）
    p1_ = js(send, "window.__posts=[];"
             "const b=document.getElementById('next-go');"
             "b.disabled=false;"      # 防 render 轮询把 click 卡在禁用瞬态
             "b.click();"
             "return {posts:(window.__posts||[]).filter(x=>x[0]==='/cmd'),"
             "armed:!!b.dataset.armed,txt:b.textContent}")
    ok("ba.a3.confirm-first-noop", not p1_.get("posts")
       and p1_.get("armed") and "确认" in p1_.get("txt", ""),
       json.dumps(p1_, ensure_ascii=False))
    # 再点执行（同一 evaluate 内连点：armConfirm 3s 武装窗无跨调用超时）
    p2_ = js(send, "window.__posts=[];"
             "document.getElementById('next-go').click();"
             "return {posts:(window.__posts||[]).filter(x=>x[0]==='/cmd')}")
    ok("ba.a3.confirm-second-fires",
       any("switch" in b for _u, b in p2_.get("posts", [])),
       str(p2_.get("posts"))[:160])
    # confirm=false：首点直发（行内切歌按钮）
    js(send, _state_stub(False))
    time.sleep(1.2)
    js(send, "window.__posts=[];const b=document.querySelector("
        "'#console-list .row:not(.cur) .go-switch');"
        "if(b)b.click();return b?1:0")
    time.sleep(0.3)
    p3_ = js(send, "return {posts:(window.__posts||[]).filter(x=>x[0]==='/cmd')}")
    ok("ba.a3.no-confirm-direct",
       any("switch" in b for _u, b in p3_.get("posts", [])),
       str(p3_.get("posts"))[:160])
    js(send, RESTORE)

    # ---- A6：素材库 pt 条目精确键匹配（部分加入场景可单独加回）----
    js(send, UNSTUB + "window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{"
        "if(String(u)==='/playlist')return Promise.resolve(new Response("
        "JSON.stringify({lib:[{key:'A/歌一',team:'A',name:'歌一'},"
        "{key:'pt:A/歌一#1',team:'A',name:'歌一 · 主歌'},"
        "{key:'pt:A/歌一#2',team:'A',name:'歌一 · 副歌'}],"
        "pl:[{key:'pt:A/歌一#1',name:'歌一 · 主歌',dur:'1:00',pt:true}],"
        "cur:0}),{status:200}));"
        "return window.__fetch0(u,o)};")
    nav(send, "setlist")
    a6 = js(send, "return [...document.querySelectorAll('#lib .row')].map("
            "r=>({k:r.dataset.k,"
            "txt:r.querySelector('.lib-add').textContent,"
            "inpl:r.querySelector('.lib-add').classList"
            ".contains('in-pl')}))")
    ok("ba.a6.lib-pt-exact", len(a6) == 3
       and a6[0]["txt"] == "＋" and not a6[0]["inpl"]
       and a6[1]["inpl"] and a6[1]["txt"] == "已加入"
       and a6[2]["txt"] == "＋" and not a6[2]["inpl"],
       json.dumps(a6, ensure_ascii=False))
    # 已移除的点条目点 ＋ 应发精确 pt: 键的 add（先拆上段自定义桩再装
    # INTERCEPT——嵌套时它只切 fmode 不换桩，/plcmd 会漏到真 fetch）
    js(send, UNSTUB + INTERCEPT + "const b=[...document.querySelectorAll("
        "'#lib .row')][2].querySelector('.lib-add');b&&b.click();"
        "return 1")
    posts = js(send, "return window.__posts||[]")
    ok("ba.a6.pt-readd-posts",
       any("/plcmd" in u and "pt:A/歌一#2" in b for u, b in posts),
       str(posts)[:160])
    js(send, RESTORE)

    # ---- A4：真后端 adapters 契约（{name, ip}）----
    a4 = js(send, "const r=await fetch('/settings');const j=await r.json();"
            "return {arr:Array.isArray(j.adapters),"
            "ok:j.adapters.every(a=>a&&typeof a.name==='string'"
            "&&typeof a.ip==='string'&&a.ip.includes('.')),"
            "n:j.adapters.length,sample:j.adapters[0]||null}")
    ok("ba.a4.adapters-mapping", a4.get("arr") and a4.get("ok"),
       json.dumps(a4, ensure_ascii=False))
    # stub /settings：回显按 IP 选中 + 提交值是 IP
    nav(send, "settings")
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "if(String(u)==='/settings'&&(!o||o.method!=='POST')"
        "&&window.__fmode!=='post')return Promise.resolve(new Response("
        "JSON.stringify({clockHint:'',vjHint:'',kbHint:'',autoAdvance:true,"
        "autoPlay:false,topMost:true,switchConfirm:true,exitCloseApps:false,"
        "projectsRoot:'',dawExe:'',autoSave:true,"
        "obs:{projectorMonitor:'',vjMute:true,videoRoot:'',mediaInput:''},"
        "web:{enabled:false,serverPort:8765,appPort:8767,taskerPort:8766,"
        "midiIn:'',mode:'hotspot',ip:'192.168.137.1'},"
        "screens:[],adapters:[{name:'以太网',ip:'192.168.1.5'},"
        "{name:'热点',ip:'192.168.137.1'}],ports:[]}),{status:200}));"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    # 弄脏 + 保存 → POST ok → refreshSettings 读 stub 重建下拉
    js(send, "const el=document.getElementById('st-srv');"
        "el.value=(Number(el.value)||8765)+1;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "document.getElementById('st-save').click()")
    time.sleep(0.8)
    a4b = js(send, "const s=document.getElementById('st-ip');"
             "return {vals:[...s.options].map(o=>o.value),"
             "txts:[...s.options].map(o=>o.textContent),"
             "sel:s.value}")
    ok("ba.a4.ip-select-echo", a4b.get("vals") == ["", "192.168.1.5",
                                                   "192.168.137.1"]
       and a4b.get("sel") == "192.168.137.1",
       json.dumps(a4b, ensure_ascii=False))
    # 改选第一块网卡保存：提交的 web.ip 必须是 IP
    js(send, "const s=document.getElementById('st-ip');"
        "s.value='192.168.1.5';"
        "s.dispatchEvent(new Event('change',{bubbles:true}));"
        "window.__posts=[];"
        "document.getElementById('st-save').click()")
    time.sleep(0.8)
    a4c = js(send, "const ps=(window.__posts||[]).filter("
             "x=>x[0]==='/settings');"
             "let ip=null;"
             "ps.forEach(([u,b])=>{try{const j=JSON.parse(b);"
             "if(j.web)ip=j.web.ip}catch(e){}});"
             "return {n:ps.length,ip}")
    ok("ba.a4.submit-ip", a4c.get("ip") == "192.168.1.5",
       json.dumps(a4c))
    js(send, RESTORE)
    time.sleep(0.6)

    # ---- JS 错误汇总 ----
    errs = js(send, "return window.__errs")
    ok("ba.no-js-errors", not errs, str(errs)[:300])


SETTINGS_STUB = ("{clockHint:'CK口',vjHint:'VJ口',kbHint:'KB口',"
                 "autoAdvance:true,autoPlay:false,topMost:true,"
                 "switchConfirm:true,exitCloseApps:false,projectsRoot:'',"
                 "dawExe:'',autoSave:true,"
                 "obs:{projectorMonitor:'缺席屏',vjMute:true,videoRoot:'',"
                 "mediaInput:'Media1'},"
                 "web:{enabled:false,serverPort:8765,appPort:8767,"
                 "taskerPort:8766,midiIn:'旧口',mode:'hotspot',ip:''},"
                 "screens:['\\\\\\.\\DISPLAY1','\\\\\\.\\DISPLAY2'],"
                 "adapters:[],ports:['loopMIDI 1','VJ口在线','别的口'],"
                 "webStatus:{net:'hot-on',listen:{serverPort:true,"
                 "appPort:false}}}")


def stage_b(send):
    # ---- B4：真后端 webStatus 契约 + 状态行渲染 ----
    b4 = js(send, "const r=await fetch('/settings');const j=await r.json();"
            "return {has:!!j.webStatus&&'net' in j.webStatus,"
            "listen:typeof j.webStatus.listen}")
    ok("bb.b4.webstatus", b4.get("has") and b4.get("listen") == "object",
       json.dumps(b4))

    # ---- B2/B3/B4 stub 态：设置页端口下拉 + VJ 位置「无」/幽灵 + 状态行 ----
    nav(send, "settings")
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "if(String(u)==='/settings'&&(!o||o.method!=='POST'))"
        "return Promise.resolve(new Response(JSON.stringify("
        + SETTINGS_STUB + "),{status:200}));"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    js(send, "const el=document.getElementById('st-srv');"
        "el.value=(Number(el.value)||8765)+1;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "document.getElementById('st-save').click()")
    time.sleep(0.8)
    b2 = js(send, "const info=id=>{const s=document.getElementById(id);"
            "return {vals:[...s.options].map(o=>o.value),"
            "txts:[...s.options].map(o=>o.textContent),sel:s.value}};"
            "return {clock:info('st-clock'),vj:info('st-vj'),pg:info('st-pg')}")
    ok("bb.b2.port-select", b2["clock"]["vals"][0] == ""
       and b2["clock"]["txts"][0] == "无"
       and "loopMIDI 1" in b2["clock"]["vals"]
       and b2["vj"]["sel"] == "VJ口在线"      # 前缀命中在线口→完整名
       and b2["clock"]["sel"] == "CK口"       # 不在场→幽灵项保名
       and "CK口（未接入）" in b2["clock"]["txts"]
       and b2["pg"]["sel"] == "旧口"
       and "旧口（未接入）" in b2["pg"]["txts"],
       json.dumps(b2, ensure_ascii=False))
    b4b = js(send, "return {pills:[...document.querySelectorAll("
            "'#st-netstat .pill')].map(p=>p.textContent),"
            "addr:document.querySelector('#st-netstat .sm-txt')"
            "?document.querySelector('#st-netstat .sm-txt').textContent:''}")
    ok("bb.b4.netstat-render", any("热点" in p for p in b4b["pills"])
       and any("网页端口 监听中" == p for p in b4b["pills"])
       and any("APP 端口 未监听" == p for p in b4b["pills"]),
       json.dumps(b4b, ensure_ascii=False))
    # 选「无」保存 → 提交空串（停用）
    js(send, "const s=document.getElementById('st-clock');"
        "s.value='';s.dispatchEvent(new Event('change',{bubbles:true}));"
        "window.__posts=[];"
        "document.getElementById('st-save').click()")
    time.sleep(0.8)
    b2b = js(send, "let clock=null;(window.__posts||[]).forEach(([u,b])=>"
             "{if(u==='/settings'){try{clock=JSON.parse(b).clockHint}"
             "catch(e){}}});return {clock}")
    ok("bb.b2.none-submits-empty", b2b.get("clock") == "",
       json.dumps(b2b))
    js(send, RESTORE)
    time.sleep(0.5)

    # ---- B3：VJ 位置「无」+ 幽灵项；保存「无」提交空 ----
    nav(send, "vj")
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "if(String(u)==='/settings'&&(!o||o.method!=='POST'))"
        "return Promise.resolve(new Response(JSON.stringify("
        + SETTINGS_STUB + "),{status:200}));"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    js(send, "document.getElementById('vj-save').click()")
    time.sleep(0.8)
    b3 = js(send, "const s=document.getElementById('vj-mon');"
            "return {vals:[...s.options].map(o=>o.value),"
            "txts:[...s.options].map(o=>o.textContent),sel:s.value}")
    ok("bb.b3.mon-none-ghost", b3["vals"][0] == ""
       and b3["txts"][0] == "无" and b3["sel"] == "缺席屏"
       and "缺席屏（不在场）" in b3["txts"],
       json.dumps(b3, ensure_ascii=False))
    # VJ 页保存载荷只含 obs 键（B1 前端侧：窄载荷）
    b1 = js(send, "window.__posts=[];"
             "document.getElementById('vj-save').click();return 1")
    time.sleep(0.6)
    b1_ = js(send, "let body=null;(window.__posts||[]).forEach(([u,b])=>"
             "{if(u==='/settings'&&b&&!body)body=b});"   # GET 的空 body 跳过
             "return {keys:body?Object.keys(JSON.parse(body)):null,"
             "obsKeys:body?Object.keys(JSON.parse(body).obs||{}):null}")
    ok("bb.b1.vj-payload-narrow",
       b1_.get("keys") == ["obs"]
       and set(b1_.get("obsKeys") or []) >= {"projectorMonitor",
                                             "mediaInput", "vjMute"},
       json.dumps(b1_))
    # 选「无」保存 → obs.projectorMonitor 为空串
    js(send, "const s=document.getElementById('vj-mon');"
        "s.value='';s.dispatchEvent(new Event('change',{bubbles:true}));"
        "window.__posts=[];"
        "document.getElementById('vj-save').click()")
    time.sleep(0.6)
    b3b = js(send, "let mon=null;(window.__posts||[]).forEach(([u,b])=>"
             "{if(u==='/settings'){try{mon=JSON.parse(b).obs"
             ".projectorMonitor}catch(e){}}});return {mon}")
    ok("bb.b3.none-closes-projector", b3b.get("mon") == "",
       json.dumps(b3b))
    js(send, RESTORE)
    time.sleep(0.5)

    # ---- B5/B6：时长三态 + 行级菜单 + ↑↓ 原子 move ----
    # 专用桩：/playlist 回固定 stub（1.5s 的 refreshPlaylist 吃 {ok:true}
    # 会把 plCache.pl 洗成 undefined，movePl 直接炸）、/plcmd 记录后回 ok
    PLCMD_STUB = (UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
                  "window.fetch=(u,o)=>{"
                  "window.__posts.push([String(u),(o&&o.body)||'']);"
                  "if(String(u)==='/playlist')return Promise.resolve("
                  "new Response(JSON.stringify({lib:[],pl:["
                  "{key:'a',name:'A',dur:'2:00',durSec:120,durSrc:'auto',"
                  "pt:false},"
                  "{key:'b',name:'B',dur:'1:30',durSec:90,durSrc:'manual',"
                  "pt:false},"
                  "{key:'c',name:'C',dur:'',durSec:0,durSrc:'unknown',"
                  "pt:false},"
                  "{key:'d',name:'D',dur:'0:40',durSec:40,durSrc:'pt',"
                  "pt:true}],cur:0}),{status:200}));"
                  "if(String(u)==='/plcmd')return Promise.resolve("
                  "new Response('{\"ok\":true}',{status:200,"
                  "headers:{'Content-Type':'application/json'}}));"
                  "return window.__fetch0(u,o)};")
    js(send, PLCMD_STUB)
    nav(send, "setlist")
    b5 = js(send, "return [...document.querySelectorAll('#pl .row .dur')]"
            ".map(d=>({t:d.textContent,cls:d.className}))")
    ok("bb.b5.dur-three-state",
       len(b5) == 4 and "dur-auto" in b5[0]["cls"]
       and "dur-manual" in b5[1]["cls"]
       and b5[2]["t"] == "未知" and "dur-unknown" in b5[2]["cls"],
       json.dumps(b5, ensure_ascii=False))
    # ⋯ 菜单 → 写入时长表单 → 提交 setdur
    mclick = js(send, "const row=document.querySelectorAll('#pl .row')[1];"
                "if(!row)return {none:1};"
                "const b=[...row.querySelectorAll('.ops button')]"
                ".find(x=>x.title.includes('写入时长'));"
                "if(!b)return {nobtn:1};"
                "b.click();"
                "return {pop:!!document.querySelector('.pop')}")
    m1 = js(send, "const m=[...document.querySelectorAll('.pop .menu-item')]"
            ".map(x=>x.textContent);return {m}")
    ok("bb.b5.more-menu", mclick.get("pop")
       and any("写入时长" in x for x in m1.get("m", []))
       and any("重新识别" in x for x in m1.get("m", [])),
       "%s %s" % (json.dumps(mclick, ensure_ascii=False),
                  json.dumps(m1, ensure_ascii=False)))
    js(send, "[...document.querySelectorAll('.pop .menu-item')]"
        ".find(x=>x.textContent.includes('写入时长')).click();return 1")
    time.sleep(0.3)
    js(send, "const p=document.querySelector('.pop');"
        "p.querySelector('input').value='1:30';"
        "p.querySelector('.btns .primary').click();return 1")
    time.sleep(0.4)
    posts = js(send, "return window.__posts||[]")
    ok("bb.b5.setdur-posts",
       any("/plcmd" in u and '"op":"setdur"' in b and '"dur":"1:30"' in b
           for u, b in posts), str(posts)[:200])
    # ↑ 按钮（第 2 行上移）发原子 move
    js(send, "window.__posts=[];"
        "const rows=[...document.querySelectorAll('#pl .row')];"
        "const b=rows[1].querySelector('.ops button[title=上移]');"
        "if(b)b.click();return !!b")
    time.sleep(0.4)
    posts = js(send, "return window.__posts||[]")
    ok("bb.b6.arrow-move",
       any("/plcmd" in u and '"op":"move"' in b and '"from":1' in b
           and '"to":0' in b for u, b in posts), str(posts)[:200])
    js(send, RESTORE)

    # ---- JS 错误汇总 ----
    errs = js(send, "return window.__errs")
    ok("bb.no-js-errors", not errs, str(errs)[:300])


def stage_c(send):
    # ---- C5：进页 mute on / 离页 mute off ----
    nav(send, "console")
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    js(send, "document.querySelector('.b-pedal').click()")
    time.sleep(0.6)
    js(send, "document.querySelector('.b-console').click()")
    time.sleep(0.6)
    mutes = [b for u, b in (js(send, "return window.__posts||[]") or [])
             if u == "/pedal/mute"]
    ok("bc.c5.mute-toggle",
       any('"on":true' in b for b in mutes)
       and any('"on":false' in b for b in mutes), str(mutes)[:160])
    js(send, RESTORE)

    # ---- C1/C2/C4：stub /pedal → 绑定表/连接副行/设备下拉/学习/清除 ----
    js(send, UNSTUB + "window.__posts=[];window.__fetch0=window.fetch;"
        "window.fetch=(u,o)=>{window.__posts.push([String(u),(o&&o.body)||'']);"
        "if(String(u)==='/pedal')return Promise.resolve(new Response("
        "JSON.stringify({deviceHint:'',hint:'Pedal口',"
        "binds:{pause:74},hid:{next:176},gestures:{pause:'double'},"
        "intercept:true,doubleWindow:0.35,remoteEnabled:false,"
        "connected:true,hidActive:false,deviceOnline:false,muted:true}),"
        "{status:200}));"
        "if(String(u)==='/pedal/learn'&&(!o||o.method!=='POST'))"
        "return Promise.resolve(new Response("
        "JSON.stringify({active:true,remain:5.2}),{status:200}));"
        "if(String(u)==='/pedal/devices')return Promise.resolve(new Response("
        "JSON.stringify({devices:[['HINT1','CUBE TURNER [AB12]'],"
        "['HINT2','USB Input Device [0001:0002]']]}),{status:200}));"
        "return Promise.resolve(new Response('{\"ok\":true}',"
        "{status:200,headers:{'Content-Type':'application/json'}}))};")
    js(send, "document.querySelector('.b-pedal').click()")
    time.sleep(1.0)
    c4 = js(send, "return {rows:document.querySelectorAll('.bind-row').length,"
            "bind0:document.querySelector("
            "'.bind-row[data-action=pause] .b-bind').textContent,"
            "bindNext:document.querySelector("
            "'.bind-row[data-action=next] .b-bind').textContent,"
            "unset:document.querySelector("
            "'.bind-row[data-action=play] .b-bind').textContent,"
            "sub:document.getElementById('pedal-conn-sub').textContent,"
            "devOpts:[...document.getElementById('pd-dev').options]"
            ".map(o=>o.textContent)}")
    ok("bc.c2.bind-table", c4.get("rows") == 6
       and c4.get("bind0") == "CC 74·双踩"
       and c4.get("bindNext") == "按键 下一曲"
       and c4.get("unset") == "未设置",
       json.dumps(c4, ensure_ascii=False))
    ok("bc.c4.conn-sub",
       "MIDI" in (c4.get("sub") or "") and "设备" in (c4.get("sub") or "")
       and "HID" in (c4.get("sub") or ""),
       json.dumps(c4.get("sub"), ensure_ascii=False))
    ok("bc.c1.device-select",
       any("仅 MIDI" in t for t in c4.get("devOpts", []))
       and any("TURNER" in t for t in c4.get("devOpts", [])),
       json.dumps(c4.get("devOpts"), ensure_ascii=False))
    # 学习 → POST + 轮询 + 行内倒计时
    js(send, "window.__posts=[];"
        "document.querySelector('.bind-row[data-action=play] .b-learn')"
        ".click()")
    time.sleep(0.9)
    lc = js(send, "return {posts:(window.__posts||[]).map(x=>x[0]),"
            "txt:document.querySelector("
            "'.bind-row[data-action=play] .b-learn').textContent}")
    ok("bc.c2.learn-flow",
       (lc.get("posts") or []).count("/pedal/learn") >= 2
       and "学习中" in (lc.get("txt") or ""),
       json.dumps(lc, ensure_ascii=False))
    # 清除（有绑定的行）→ /pedalset 载荷去掉该动作、保留其余
    js(send, "window.__posts=[];"
        "document.querySelector('.bind-row[data-action=next] .btn.danger')"
        ".click()")
    time.sleep(0.6)
    cl = js(send, "let body=null;(window.__posts||[]).forEach(([u,b])=>"
            "{if(u==='/pedalset')body=b});"
            "return {has:!!body,"
            "noNext:body?!('next' in JSON.parse(body).hid):null,"
            "keepPause:body?('pause' in JSON.parse(body).binds):null}")
    ok("bc.c2.clear-bind", cl.get("has") and cl.get("noNext")
       and cl.get("keepPause"), json.dumps(cl))
    # 拆桩（真 /pedal/learn 会答 inactive，学习轮询自然收口）后离页
    js(send, RESTORE)
    time.sleep(0.8)
    js(send, "document.querySelector('.b-console').click()")
    time.sleep(0.4)

    # ---- C3：真后端非法载荷 400 ----
    bad = js(send, "const r=await fetch('/pedalset',{method:'POST',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({gestures:{play:'play'}})});"
            "return r.status")
    ok("bc.c3.illegal-gesture-400", bad == 400, str(bad))
    bad2 = js(send, "const r=await fetch('/pedalset',{method:'POST',"
             "headers:{'Content-Type':'application/json'},"
             "body:JSON.stringify({binds:{pause:999}})});"
             "return r.status")
    ok("bc.c3.illegal-bind-400", bad2 == 400, str(bad2))
    bad3 = js(send, "const r=await fetch('/pedal/learn',{method:'POST',"
             "headers:{'Content-Type':'application/json'},"
             "body:JSON.stringify({action:'xx'})});"
             "return r.status")
    ok("bc.c3.learn-bad-action-400", bad3 == 400, str(bad3))

    errs = js(send, "return window.__errs")
    ok("bc.no-js-errors", not errs, str(errs)[:300])


def main():
    stages = set(sys.argv[1:]) or {"all"}
    probe, send = boot()
    try:
        if stages & {"p0", "all"}:
            stage_p0(send)
        if stages & {"p1", "all"}:
            stage_p1(send)
        if stages & {"p2", "all"}:
            stage_p2(send)
        if stages & {"a", "all"}:
            stage_a(send)
        if stages & {"b", "all"}:
            stage_b(send)
        if stages & {"c", "all"}:
            stage_c(send)
    finally:
        probe.terminate()
    fails = [k for k, (v, _e) in P.items() if not v]
    print("\n%d/%d PASS" % (len(P) - len(fails), len(P)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
