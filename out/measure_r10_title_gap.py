# -*- coding: utf-8 -*-
"""标题→内容间距测量（行距统一第二项）：三页状态卡 lbl 文字底 →
首个内容盒顶 的实测值，为 CSS 调参提供数字。"""
import sys

sys.path.insert(0, "out")
from probe_ui import boot, js  # noqa: E402

PROBE_JS = """
const first=%(firstq)s;
const statbig=%(bigq)s;
const rng=document.createRange();
rng.selectNodeContents(first);
const lblBox=first.getBoundingClientRect();
const lblTxt=rng.getBoundingClientRect();
const bigBox=statbig.getBoundingClientRect();
// 内容文字顶：stat-big 的行盒顶≈盒顶（块级首行），量文本用 Range
const rng2=document.createRange();
rng2.selectNodeContents(statbig);
const bigTxt=rng2.getBoundingClientRect();
return {lblTextBottomToBigTextTop: bigTxt.top-lblTxt.bottom,
        lblBoxToBigBox: bigBox.top-lblBox.bottom};
"""

JSB = {
    "pedal": PROBE_JS % {
        "firstq": "document.querySelector('#p-pedal .stat-head .lbl')",
        "bigq": "document.getElementById('pedal-conn')"},
    "vj": PROBE_JS % {
        "firstq": "document.querySelector('#p-vj .card.stat > .lbl')",
        "bigq": "document.getElementById('vj-obs')"},
    "kbd": PROBE_JS % {
        "firstq": "document.querySelector('#p-kbd .card.stat > .lbl')",
        "bigq": "document.getElementById('kb-song')"},
}

probe, send = boot()
try:
    import time
    for pg, body in JSB.items():
        js(send, "document.querySelector('.b-%s').click()" % pg)
        time.sleep(0.9)
        r = js(send, body)
        print(pg, r)
finally:
    probe.terminate()
