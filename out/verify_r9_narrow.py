# -*- coding: utf-8 -*-
"""窄窗走带行验证（复核 P1-3）：900px 视口下 #transport 不横向溢出、
全停钮完整可见（flex-wrap 生效）。"""
import json
import sys

sys.path.insert(0, "out")
from probe_ui import boot, js  # noqa: E402
from probe_ui import websocket  # noqa: E402

probe, _send = boot()
# boot 返回的 send 是 JS 透传；Emulation 命令走独立裸 ws（同 tab）
import urllib.request as _u
tabs = json.loads(_u.urlopen("http://127.0.0.1:9225/json").read())
raw = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=10)


def cmd(mid, method, params):
    raw.send(json.dumps({"id": mid, "method": method, "params": params}))
    while True:
        m = json.loads(raw.recv())
        if m.get("id") == mid:
            return m


try:
    cmd(1, "Emulation.setDeviceMetricsOverride",
        {"width": 900, "height": 800, "deviceScaleFactor": 0, "mobile": False})
    import time
    time.sleep(1.2)
    r = js(_send, """
const main=document.getElementById('main');
const tp=document.getElementById('transport');
const panic=document.querySelector('[data-cmd=panic]');
const r1=main.getBoundingClientRect(), r2=tp.getBoundingClientRect(),
      r3=panic.getBoundingClientRect();
return {mainW:r1.width, tpRight:r2.right, panicRight:r3.right,
        overflow:r2.right>r1.right+1, panicVisible:r3.right<=r1.right+1
                 && r3.left>=r1.left-1,
        h:tp.getBoundingClientRect().height};""")
    ok1 = not r["overflow"]
    ok2 = r["panicVisible"]
    ok3 = r["h"] > 60   # 900px 下必然换行（单行 48px）
    print("r9n.transport.narrow-no-overflow  %s %s"
          % ("PASS" if ok1 else "FAIL", json.dumps(r)))
    print("r9n.transport.panic-visible       %s" % ("PASS" if ok2 else "FAIL"))
    print("r9n.transport.wraps               %s h=%.0f"
          % ("PASS" if ok3 else "FAIL", r["h"]))
    cmd(2, "Emulation.clearDeviceMetricsOverride", {})
    sys.exit(0 if (ok1 and ok2 and ok3) else 1)
finally:
    raw.close()
    probe.terminate()
