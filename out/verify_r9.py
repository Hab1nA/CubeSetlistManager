# -*- coding: utf-8 -*-
"""第九轮视觉几何验证（本轮三项 UI 修改的硬数字断言）：
1. 走带七钮=全文本+内联 SVG 图标：图标 1em 定尺寸、与文字垂直居中（±1.2px）
2. 回零钮位于 上一首/下一首 之间（普通+演出模式）
3. 踩钉/键盘/VJ 三页状态卡行距统一（踩钉 kv 节奏为基准 ±1.5px）
复用 probe_ui 的 boot/js 基建；结果打印 PASS/FAIL 不入 P 表。
用法：py -u out/verify_r9.py
"""
import json
import sys

sys.path.insert(0, "out")
from probe_ui import boot, js  # noqa: E402  (import 即清残留实例)

fails = []


def ok(name, cond, extra=""):
    print("%-34s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails.append(name)


probe, send = boot()
try:
    # ---- 1+2. 走带钮：全文本 / SVG 尺寸 / 垂直居中 / 回零居中 ----
    t = js(send, """
const btns=[...document.querySelectorAll('#transport .btn')];
return btns.map(b=>{
  const ic=b.querySelector('svg.ic'), sp=b.querySelector('span');
  const r=b.getBoundingClientRect(), i=ic&&ic.getBoundingClientRect(),
        s=sp&&sp.getBoundingClientRect();
  return {cmd:b.dataset.cmd||b.id, txt:(sp&&sp.textContent)||'',
          hasIcon:!!ic,
          iconH:i?i.height:0,
          fs:parseFloat(getComputedStyle(b).fontSize),
          iconMid:i?(i.top+i.height/2):0,
          txtMid:s?(s.top+s.height/2):0,
          x:r.left, w:r.width};
});""")
    ok("r9.transport.full-text",
       all(b["txt"].strip() for b in t) and len(t) == 7,
       json.dumps([b["txt"] for b in t], ensure_ascii=False))
    ok("r9.transport.icons-1em",
       all(b["hasIcon"] and (b["iconH"] > 0 or b["cmd"] == "show-exit")
           and (b["iconH"] == 0 or abs(b["iconH"] - b["fs"]) <= 1.0)
           for b in t),
       " ".join("%s:%.1f/%.0f" % (b["cmd"] or b["txt"], b["iconH"], b["fs"])
                for b in t))
    ok("r9.transport.v-centered",
       all(b["iconH"] == 0 or abs(b["iconMid"] - b["txtMid"]) <= 1.2
           for b in t),
       " ".join("%s:%.2f" % (b["cmd"] or b["txt"],
                             abs(b["iconMid"] - b["txtMid"])) for b in t))
    bx = {b["cmd"]: b["x"] for b in t}
    ok("r9.transport.rewind-middle",
       bx["prev"] < bx["rewind"] < bx["next"],
       "prev=%.0f rewind=%.0f next=%.0f" % (bx["prev"], bx["rewind"],
                                            bx["next"]))

    # 演出模式同轴复验（body.show 下同一排按钮；退出演出钮此时才显示）
    js(send, "document.body.classList.add('show');return 1")
    t2 = js(send, "const btns=[...document.querySelectorAll("
            "'#transport .btn')];"
            "return btns.map(b=>{"
            "const i=b.querySelector('svg.ic').getBoundingClientRect(),"
            "s=b.querySelector('span').getBoundingClientRect(),"
            "r=b.getBoundingClientRect();"
            "return {cmd:b.dataset.cmd||b.id,"
            "iconMid:i.top+i.height/2, txtMid:s.top+s.height/2,"
            "iconH:i.height,"
            "fs:parseFloat(getComputedStyle(b).fontSize),"
            "x:r.left};});")
    js(send, "document.body.classList.remove('show');return 1")
    ok("r9.show.v-centered",
       len(t2) == 7
       and all(abs(b["iconMid"] - b["txtMid"]) <= 1.2 for b in t2),
       " ".join("%s:%.2f" % (b["cmd"], abs(b["iconMid"] - b["txtMid"]))
                for b in t2))
    ok("r9.show.icon-1em",
       all(abs(b["iconH"] - b["fs"]) <= 1.0 for b in t2),
       " ".join("%s:%.1f/%.0f" % (b["cmd"], b["iconH"], b["fs"])
                for b in t2))
    bx2 = {b["cmd"]: b["x"] for b in t2}
    ok("r9.show.rewind-middle",
       bx2["prev"] < bx2["rewind"] < bx2["next"])

    # ---- 3. 三页状态卡行距（大字行→下一小字行的视觉间隙） ----
    # 隐藏页 rect=0：进页实测（pedal→vj→kbd 逐页导航）
    import time as _t
    for pg in ("pedal", "vj", "kbd"):
        js(send, "document.querySelector('.b-%s').click()" % pg)
        _t.sleep(0.9)
        g = js(send, """
const gap=(a,b)=>{const r1=a.getBoundingClientRect(),
  r2=b.getBoundingClientRect();return r2.top-r1.bottom;};
// 行距基准=「文字到文字」：kv 行量内部 .v 文字；无子元素的副行
// （VJ dim 行）量 padding 后的文字顶；stat-pair 量首个子元素
const textTop=el=>{const c=el.firstElementChild;
  if(c)return c.getBoundingClientRect().top;
  return el.getBoundingClientRect().top
    +parseFloat(getComputedStyle(el).paddingTop);};
// 标题→首个内容=文字到文字（Range 精确文本盒）
const rng=document.createRange();
const first=document.querySelector('%(firstq)s');
rng.selectNodeContents(first);
const rng2=document.createRange();
rng2.selectNodeContents(document.querySelector('%(bigq)s'));
const titleGap=rng2.getBoundingClientRect().top
    -rng.getBoundingClientRect().bottom;
return {
 titleGap,
 pedal: gap(document.getElementById('pedal-conn'),
            document.querySelector('#pedal-kv-midi')),
 pedalKv: gap(document.querySelector('#pedal-kv-midi').parentNode,
             document.querySelector('#pedal-kv-dev').parentNode),
 vj: textTop(document.getElementById('vj-obs-sub'))
     -document.getElementById('vj-obs').getBoundingClientRect().bottom,
 vj2: textTop(document.getElementById('vj-video'))
     -document.getElementById('vj-follow').getBoundingClientRect().bottom,
 kbd: textTop(document.querySelector('.stat-pair'))
     -document.getElementById('kb-song').getBoundingClientRect().bottom};""" % {
            "firstq": {"pedal": "#p-pedal .stat-head .lbl",
                       "vj": "#p-vj .card.stat > .lbl",
                       "kbd": "#p-kbd .card.stat > .lbl"}[pg],
            "bigq": {"pedal": "#pedal-conn", "vj": "#vj-obs",
                     "kbd": "#kb-song"}[pg]})
        vals = {k: v for k, v in g.items() if isinstance(v, (int, float))}
        if pg == "pedal":
            base = vals["pedal"]
            ok("r9.rhythm.pedal-base", base > 10,
               "base=%.1f title=%.1f" % (vals["pedal"], vals["titleGap"]))
        elif pg == "vj":
            ok("r9.rhythm.uniform-vj",
               all(abs(vals[k] - base) <= 1.5 for k in ("vj", "vj2")),
               "vj=%.1f vj2=%.1f vs base=%.1f"
               % (vals["vj"], vals["vj2"], base))
        else:
            ok("r9.rhythm.uniform-kbd",
               abs(vals["kbd"] - base) <= 1.5,
               "kbd=%.1f vs base=%.1f" % (vals["kbd"], base))
        # 标题→内容 与 内容节奏 同一间距（第二项行距统一，R10）
        ok("r9.rhythm.title-gap-%s" % pg,
           abs(vals["titleGap"] - 13.0) <= 1.0,
           "title=%.2f (target 13)" % vals["titleGap"])

    # ---- 5. 键盘页幽灵项=裸名（未接入后缀已删，徽章单显） ----
    ghost = js(send, """
const s=document.getElementById('kb-ain');
const o=[...s.options].find(o=>o.dataset.dev==='ghost');
return o?{txt:o.textContent,title:o.title,
  badge:document.getElementById('kb-ain-st').textContent}:{none:1}""")
    ok("r9.ghost.bare-name",
       ("none" in ghost)
       or ("未接入" not in ghost["txt"] and "未接" in ghost.get("badge", "")),
       json.dumps(ghost, ensure_ascii=False))
finally:
    probe.terminate()

print("\n%d/%d PASS" % (9 - len(fails), 9))
sys.exit(1 if fails else 0)
