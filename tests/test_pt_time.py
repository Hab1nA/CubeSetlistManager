# -*- coding: utf-8 -*-
"""定位点时码输入模糊识别（webapp/app.js parseTC）：以 webapp 真实源码为
被测对象（node 提取函数求值），防测试副本与产物漂移。node 缺席=整文件
skip（CI ubuntu 自带 node）。"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

_APP = pathlib.Path(__file__).resolve().parent.parent / "webapp" / "app.js"

CASES = [
    # (输入, isEnd, 期望秒) —— 中文冒号 / Cubase h:mm:ss.mms / 取整方向
    ("3：33", False, 213), ("3：33", True, 213),
    ("0:25:23.902", False, 1523), ("0:25:23.902", True, 1524),
    ("2：05：08.5", False, 7508), ("2：05：08.5", True, 7509),
    ("3:33", False, 213), ("4:05", True, 245),
    ("25:23.5", False, 1523), ("25:23.5", True, 1524),
    ("83.5", False, 83), ("83.5", True, 84),
    ("1:02:03", False, 3723), ("0:00", False, 0), ("0:00", True, 0),
    # 每分钟最后一秒（59.xxx）：整数部分参与上界比较，必须接受
    ("0:25:59.902", False, 1559), ("0:25:59.902", True, 1560),
    ("23:59.999", False, 1439), ("23:59.999", True, 1440),
    ("0:59.9", False, 59), ("0:59.9", True, 60),
    ("3:59.5", False, 239), ("3:59.5", True, 240),
    # 拒绝
    ("abc", False, None), ("1:2:3:4", False, None), ("1:75", False, None),
    ("-5", False, None), ("", False, None),
    ("1:60", False, None), ("0:25:60", False, None),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_parse_tc_fuzzy():
    src = _APP.read_text(encoding="utf-8")
    m = re.search(r"const parseTC = [\s\S]*?\n};", src)
    assert m, "app.js 里找不到 parseTC 定义"
    js = (m.group(0)
          + "\nconst cases = %s;"
            "\nconsole.log(JSON.stringify("
            "cases.map(([v, ie]) => parseTC(v, ie))));"
          % json.dumps([c[:2] for c in CASES]))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for (v, ie, want), g in zip(CASES, got):
        assert g == want, "%r isEnd=%s → %s（期望 %s）" % (v, ie, g, want)
