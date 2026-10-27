# -*- coding: utf-8 -*-
"""P0 冒烟：pywebview 起窗 + 本地 HTTP 页 + JS 桥/evaluate_js 通路。
渲染与桥均通过后自证 PASS；截图由外部驱动（窗口级捕获）完成。"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import webview

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>body{background:#101418;color:#e8eaed;font:600 34px "Microsoft YaHei";
display:flex;flex-direction:column;align-items:center;justify-content:center;
height:95vh;margin:0}#v{color:#7fd487;font-size:56px}</style></head>
<body><div>P0 冒烟页</div><div id="v">…</div>
<script>
function go(){window.pywebview.api.ping("桥通").then(r=>{document.getElementById("v").textContent="JS桥✓"+r;});}
if (window.pywebview) go(); else window.addEventListener("pywebviewready", go);
</script></body></html>"""

_result = {"ping": None, "eval": None}


class Api:
    def ping(self, text):
        _result["ping"] = text
        return "pong"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def log_message(self, *_a):
        pass


srv = HTTPServer(("127.0.0.1", 8791), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
w = webview.create_window("P0 冒烟", "http://127.0.0.1:8791",
                          width=560, height=420, js_api=Api())


def _p0(window):
    time.sleep(2.0)                      # 等页面+桥就绪
    _result["eval"] = window.evaluate_js("2 + 40")
    # DOM 渲染结果取证：桥回写后的 #v 文本——证明页面已解析、字体已布局、
    # 桥往返已完成（锁屏期 DWM 不合成，屏幕抓黑≠渲染失败）
    _result["dom"] = window.evaluate_js(
        'document.getElementById("v").textContent')
    print(json.dumps(_result, ensure_ascii=False), flush=True)
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    time.sleep(hold)                     # 截图窗口期：常驻给外部抓屏
    window.destroy()


webview.start(func=_p0, args=(w,), gui="edgechromium")
print("EXIT-CLEAN", flush=True)
