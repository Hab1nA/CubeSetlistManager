# -*- coding: utf-8 -*-
"""审计用临时脚本：经 CDP 用 Emulation.setDeviceMetricsOverride 精确设置
视口后截图（规避 msedge headless 在 Windows 高 DPI 下的 window-size 失真）。
输出 out/ui_shots/m_*.png。"""
import asyncio
import base64
import json
import pathlib
import subprocess
import urllib.request

import websockets

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "ui_shots"
PAGES = HERE / "_legacy_pages"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9337

# (输出名, 文件, 宽, 高, mobile)
JOBS = [
    ("m_browser_phone", "browser.html", 390, 844, True),
    ("m_browser_tablet", "browser.html", 768, 1024, False),
    ("m_app_phone", "app.html", 390, 844, True),
    ("m_app_tablet", "app.html", 768, 1024, False),
    ("m_app_phone_devopen", "app_devopen.html", 390, 844, True),
    ("m_app_tablet_devopen", "app_devopen.html", 768, 1024, False),
    ("m_browser_phone_play", "browser_play.html", 390, 844, True),
    ("m_browser_tablet_play", "browser_play.html", 768, 1024, False),
    ("m_app_phone_play", "app_play.html", 390, 844, True),
    ("m_app_phone_confirm", "app_confirm.html", 390, 844, True),
]


def http_json(path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path)) as r:
        return json.loads(r.read().decode("utf-8"))


async def shoot(ws_url, out_name, fname, w, h, mobile):
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        mid = 0

        async def send(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg

        await send("Page.enable")
        await send("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1,
                    "mobile": mobile})
        await send("Page.navigate",
                   {"url": "file:///%s" % str(PAGES / fname).replace("\\", "/")})
        await asyncio.sleep(1.6)          # 等 JS 初始化/弹窗注入
        r = await send("Page.captureScreenshot",
                       {"format": "png", "captureBeyondViewport": False})
        (SHOTS / (out_name + ".png")).write_bytes(base64.b64decode(
            r["result"]["data"]))
        print("shot:", out_name, w, h)


async def main():
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--remote-debugging-port=%d" % PORT,
         "--user-data-dir=%s" % (HERE / "_audit_edge_profile"),
         "--no-first-run", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                http_json("/json/version")
                break
            except Exception:
                await asyncio.sleep(0.2)
        targets = http_json("/json/list")
        page = next(t for t in targets if t["type"] == "page")
        ws_url = page["webSocketDebuggerUrl"]
        for name, fname, w, h, mobile in JOBS:
            await shoot(ws_url, name, fname, w, h, mobile)
    finally:
        proc.kill()


asyncio.run(main())
