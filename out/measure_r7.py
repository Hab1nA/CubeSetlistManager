# -*- coding: utf-8 -*-
"""r7 A 批验收：F1-F5 + G1 + N2 几何与行为实测（CDP）。"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = 9224
fails = []

import socket as _socket
with _socket.socket() as _s:   # 残留 webui=对旧代码跑断言：fail-fast
    if _s.connect_ex(("127.0.0.1", CDP)) == 0:
        sys.exit("CDP 端口 %d 被占用：请先结束残留 webui 进程" % CDP)


def ok(name, cond, extra=""):
    print("%-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails.append(name)


def main():
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

        # ---------- F5 侧栏：底簇等距 + 无品牌字标 + 导航控件 ----------
        g = jv(r"""JSON.stringify((() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const st = r('.b-settings'), sh = r('#btn-show'), ex = r('#btn-exit');
          const nc = r('#nav-ctl'), grp = r('.side-group');
          return { g1: +(sh.top - st.bottom).toFixed(1),
                   g2: +(ex.top - sh.bottom).toFixed(1),
                   brandGone: !document.querySelector('#brand'),
                   ctlBottomGap: +(grp.top - nc.bottom).toFixed(1),
                   ctlBtns: document.querySelectorAll('#nav-ctl button').length };
        })())""")
        ok("F5 设置→演出 与 演出→退出 等距", abs(g["g1"] - g["g2"]) <= 0.6,
           "%s vs %s" % (g["g1"], g["g2"]))
        ok("F5 Cube 字标已移除", g["brandGone"])
        ok("F5 导航控件组三钮+间距收窄(<=10)", g["ctlBtns"] == 3
           and 0 <= g["ctlBottomGap"] <= 10, "gap=%s" % g["ctlBottomGap"])

        # ---------- N2 历史栈行为 ----------
        ev("document.querySelector('.b-setlist').click()")
        time.sleep(0.3)
        s1 = jv(r"""JSON.stringify({
          backDis: document.querySelector('#nav-back').disabled,
          fwdDis: document.querySelector('#nav-fwd').disabled})""")
        ok("N2 进编排后：后退可用/前进禁用",
           s1["backDis"] is False and s1["fwdDis"] is True)
        ev("document.querySelector('#nav-back').click()")
        time.sleep(0.3)
        s2 = jv(r"""JSON.stringify({
          page: document.querySelector('.page.on').id,
          fwdDis: document.querySelector('#nav-fwd').disabled})""")
        ok("N2 后退回主控台且前进可用",
           s2["page"] == "p-console" and s2["fwdDis"] is False)
        ev("document.querySelector('#nav-fwd').click()")
        time.sleep(0.3)
        s3 = ev("document.querySelector('.page.on').id")
        ok("N2 前进回编排", s3 == "p-setlist")

        # ---------- F1 音名裸格式（编排详情 + 键盘 + VJ 同口径） ----------
        ev("document.querySelector('#lib .row').click()")
        for _ in range(16):   # 映射装载（后台线程+700ms 自拉）：等就位
            if (ev("document.querySelectorAll('#dt-slots .s-note').length")
                    or 0) >= 3:
                break
            time.sleep(0.5)
        f1 = jv(r"""JSON.stringify({
          dt: Array.from(document.querySelectorAll('#dt-slots .s-note'))
              .slice(0, 3).map(e => e.textContent),
          kbd: Array.from(document.querySelectorAll('#p-kbd .vj-cell .s-note'))
              .slice(0, 3).map(e => e.textContent),
          vj: Array.from(document.querySelectorAll('#vjmap .s-note'))
              .slice(0, 3).map(e => e.textContent) })""")
        import re as _re
        bare = lambda arr: len(arr) >= 3 and all(
            _re.fullmatch(r"[A-G]#?\d", x) for x in arr)
        ok("F1 编排音色映射卡=裸音名(≥3卡)", bare(f1["dt"]), f1["dt"])
        ok("F1 键盘全局移调=裸音名", bare(f1["kbd"]), f1["kbd"])
        ok("F1 VJ 触发槽位=裸音名", bare(f1["vj"]), f1["vj"])

        # ---------- G1 跑马灯：合成节点直测（确定性；真实元素会被
        # 1.5s 轮询回写同名文本，与断言存在竞态） ----------
        mq = jv(r"""JSON.stringify((() => {
          const el = document.createElement('div');
          el.className = 'mq'; el.style.width = '200px';
          document.body.appendChild(el);
          el.textContent = '超长歌名测试数据'.repeat(12);
          window.__marq(el);
          const r = { marq: el.classList.contains('marq'),
                      dist: el.style.getPropertyValue('--marq') };
          el.textContent = '短名';
          window.__marq(el);
          r.off = !el.classList.contains('marq');
          el.remove();
          return r; })())""")
        ok("G1 溢出文本挂 marq 动画", mq["marq"] and mq["dist"].startswith("-"),
           mq["dist"])
        ok("G1 短文本不挂动画", mq["off"] is True)

        # ---------- F3 VJ 播放声音（静音）开关同构 ----------
        ev("document.querySelector('.b-vj').click()")
        time.sleep(1.2)
        f3 = jv(r"""JSON.stringify((() => {
          const sw = document.getElementById('vj-mute').closest('label');
          const grid = sw.closest('.grid2');
          const r = sw.getBoundingClientRect();
          const g2 = document.getElementById('vj-root')
              .closest('.field, .input-row');
          const cs = getComputedStyle(sw);
          return { isSwitch: sw.classList.contains('switch'),
                   inGrid: !!grid, spanPx: getComputedStyle(
                       sw.querySelector('span')).fontSize,
                   wide: sw.classList.contains('wide'),
                   spanTxt: sw.querySelector('span').textContent,
                   center: cs.alignItems,
                   swW: +r.width.toFixed(0),
                   gridW: +grid.getBoundingClientRect().width.toFixed(0) };
        })())""")
        ok("F3 静音=平铺 switch（无 field 包裹）", f3["isSwitch"] and f3["inGrid"])
        ok("F3 说明文字正文尺寸(15px)且在框后", f3["spanPx"] == "15px"
           and f3["spanTxt"] == "静音", f3["spanPx"])
        ok("F3 开关内容垂直居中且独立成行",
           f3["center"] == "center" and f3["swW"] >= f3["gridW"] - 2,
           "align=%s w=%s/%s" % (f3["center"], f3["swW"], f3["gridW"]))

        # ---------- F4 设置文案 ----------
        ev("document.querySelector('.b-settings').click()")
        time.sleep(1.0)
        f4 = jv(r"""JSON.stringify({
          c: document.querySelector('#st-confirm + span, '
              + 'label:has(#st-confirm) span').textContent,
          l: document.querySelector('label:has(#st-launch) span').textContent })""")
        ok("F4 手动切歌确认", f4["c"] == "手动切歌确认", f4["c"])
        ok("F4 启动时打开被控软件", f4["l"] == "启动时打开被控软件", f4["l"])

        # ---------- F2 窄窗（<900px）编排页吃满高度 ----------
        n0 = 9225
        ws.send(json.dumps({"id": n0, "method": "Emulation.setDeviceMetricsOverride",
                            "params": {"width": 860, "height": 700,
                                       "deviceScaleFactor": 0,
                                       "mobile": False}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == n0:
                break
        ev("document.querySelector('.b-setlist').click()")
        time.sleep(1.0)
        ev(r"""(function(){ const b = document.querySelector(
            '#sl-tabs [data-col=lib]'); if (b) b.click(); })()""")
        time.sleep(0.5)
        nw = jv(r"""JSON.stringify((() => {
          const r = s => { const e = document.querySelector(s);
            const x = e.getBoundingClientRect();
            return {t:+x.top.toFixed(1), b:+x.bottom.toFixed(1),
                    ch:e.clientHeight, sh:e.scrollHeight}; };
          const cols = document.querySelector('.cols');
          const col = document.querySelector('.cols .col.show');
          return { narrow: matchMedia('(max-width:900px)').matches,
            main: r('#main'), page: r('#p-setlist'), cols: r('.cols'),
            col: col ? {b: +col.getBoundingClientRect().bottom.toFixed(1),
                        ch: col.clientHeight} : null,
            list: r('#lib') };
        })())""")
        ok("F2 窄窗生效", nw["narrow"])
        ok("F2 可见列底=cols底（不再截断留空）",
           nw["col"] and abs(nw["col"]["b"] - nw["cols"]["b"]) <= 1,
           "%s vs %s" % (nw["col"] and nw["col"]["b"], nw["cols"]["b"]))
        ok("F2 cols 底=页底(r8 起贴底)",
           abs(nw["cols"]["b"] - nw["page"]["b"]) <= 1,
           "%s vs %s" % (nw["cols"]["b"], nw["page"]["b"]))
        ok("F2 列表内部滚动可用（sh>=ch）", nw["list"]["sh"] >= nw["list"]["ch"],
           "%s/%s" % (nw["list"]["sh"], nw["list"]["ch"]))
        # 详情列同规
        ev(r"""(function(){ const b = document.querySelector(
            '#sl-tabs [data-col=dt]'); if (b) b.click(); })()""")
        time.sleep(0.5)
        dt = jv(r"""JSON.stringify({b: +document.querySelector('#col-dt')
            .getBoundingClientRect().bottom.toFixed(1),
          cb: +document.querySelector('.cols')
            .getBoundingClientRect().bottom.toFixed(1)})""")
        ok("F2 详情列窄窗也吃满", abs(dt["b"] - dt["cb"]) <= 1,
           "%s vs %s" % (dt["b"], dt["cb"]))
        # 还原仿真
        n0 += 1
        ws.send(json.dumps({"id": n0, "method": "Emulation.clearDeviceMetricsOverride"}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == n0:
                break
        errs = ev("window.__errs ? JSON.stringify(window.__errs) : '[]'")
        ok("全程无 JS 错误", errs == "[]", errs)
        ws.close()
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
    print("\n%d FAIL" % len(fails), fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
