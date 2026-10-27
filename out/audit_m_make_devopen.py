# -*- coding: utf-8 -*-
"""审计用临时脚本：生成「设置面板打开态」的 app.html 副本（只加一行 JS，
不改任何样式/逻辑），供 headless 截图 out/ui_shots/m_app_*_devopen.png。"""
import io
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "_legacy_pages" / "app.html"
html = io.open(str(SRC), encoding="utf-8").read()
assert "</body>" in html
html = html.replace(
    "</body>",
    "<script>document.addEventListener('DOMContentLoaded',function(){"
    "var m=document.getElementById('m-dev');"
    "if(m)m.classList.add('show');"
    "var i=document.getElementById('t-ind');"
    "if(i){i.className='ind ok';i.textContent='8527 端口监听中'}"
    "});</script>\n</body>",
    1)
dst = HERE / "_legacy_pages" / "app_devopen.html"
io.open(str(dst), "w", encoding="utf-8").write(html)
print("written:", dst)
