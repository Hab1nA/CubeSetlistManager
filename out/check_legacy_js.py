# -*- coding: utf-8 -*-
"""旧页（PAGE_BROWSER/PAGE_APP）成品 JS 语法与关键内容检查（node --check）。"""
import io
import re
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import web_remote

OUT = pathlib.Path(__file__).resolve().parent / "_chk"
OUT.mkdir(exist_ok=True)

for name, html in (("browser", web_remote.PAGE_BROWSER),
                   ("app", web_remote.PAGE_APP)):
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    js = "\n;\n".join(s for s in scripts if "src=" not in s)
    f = OUT / ("chk_%s.js" % name)
    io.open(str(f), "w", encoding="utf-8").write(js)
    r = subprocess.run(["node", "--check", str(f)],
                       capture_output=True, text=True)
    print(name, "OK" if r.returncode == 0 else "FAIL\n" + r.stderr[:400])

joined = web_remote.PAGE_APP + web_remote.PAGE_BROWSER
for probe in ('pendK=k||""', "onRow.bind(null,i,",
              'cmd("switch",i,k)', "key:key||undefined",
              "该曲目已不在播放列表"):
    assert probe in joined, probe
print("content ok")
