# -*- coding: utf-8 -*-
"""提取旧页成品 HTML 到 out/_legacy_pages/（供 CDP 实机渲染测试用）。"""
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import web_remote

D = pathlib.Path(__file__).resolve().parent / "_legacy_pages"
D.mkdir(exist_ok=True)
for name in ("browser", "app"):
    io.open(str(D / ("%s.html" % name)), "w",
            encoding="utf-8").write(getattr(web_remote, "PAGE_" + name.upper()))
print("written:", D)
