# -*- coding: utf-8 -*-
"""只读探测 OBS 媒体源静音/监听实况（诊断 VJ 静音不稳定）。
不发任何 Set 请求；连接凭据取 config.json obs 段（缺则 config.example.json）。
用法：py tools/probe_obs_mute.py [采样次数] [间隔秒]"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from obs_ws import ObsWs, ObsError  # noqa: E402
from obs_ctrl import read_obs_websocket_password  # noqa: E402


def _obs_cfg():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, "config.json")
    if not os.path.exists(src):      # 根 config.json 不再随仓库存在
        src = os.path.join(here, "config.example.json")
    with open(src, encoding="utf-8-sig") as f:
        obs = (json.load(f).get("obs") or {})
    if str(obs.get("password") or "").startswith("dpapi:"):
        print("注：obs.password 是 DPAPI 密文，探针按空密码连"
              "（OBS 未设 WebSocket 密码时不影响）")
        obs = dict(obs, password="")
    return obs


def main():
    n = int(sys.argv[1]) if len(sys.argv[1:]) else 1
    gap = float(sys.argv[2]) if len(sys.argv[2:]) else 2.0
    cfg = _obs_cfg()
    try:
        ws = ObsWs(cfg.get("host", "127.0.0.1"),
                   int(cfg.get("port", 4455)),
                   (cfg.get("password", "")
                    or read_obs_websocket_password()), timeout=3)
        ver = ws.request("GetVersion")
    except (ObsError, OSError) as e:
        print("OBS 不在线或连不上: %s" % e)
        return
    print("OBS %s / obs-websocket %s" % (ver.get("obsVersion"),
                                         ver.get("pluginVersion")))
    media = [x["inputName"] for x in
             ws.request("GetInputList")["inputs"]
             if x["inputKind"] == "ffmpeg_source"]
    for i in range(n):
        if i:
            time.sleep(gap)
        for name in media:
            mute = ws.request("GetInputMute", {"inputName": name})
            try:
                mt = ws.request("GetInputAudioMonitorType",
                                {"inputName": name})
            except ObsError as e:
                mt = {"monitorType": "请求不可用(%s)" % e}
            st = ws.request("GetMediaInputStatus", {"inputName": name})
            print("[%s] %s | mute=%s | monitor=%s | state=%s"
                  % (i, name, mute["inputMuted"], mt["monitorType"],
                     st.get("mediaState")))
    ws.close()


if __name__ == "__main__":
    main()
