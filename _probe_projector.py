# -*- coding: utf-8 -*-
"""真机自检（只读，不弹投影窗）：「VJ显示位置」的屏名能否对上 OBS 显示器。

apply_projector 的完整验证要开全屏投影窗（肉眼可见）；此脚本只做匹配
dry-run——枚举本机屏 → 连 OBS 取 GetMonitorList → 按 apply_projector 同样的
规则算出目标 monitorIndex，不发开投影请求。
"""
import json

import dpi
dpi.enable()
from obs_ctrl import _screen_match, list_screens
from obs_ws import ObsError, ObsWs

cfg = dict(json.load(open("config.json", encoding="utf-8"))["obs"])
wins = list_screens()
print("本机显示器:", wins)
name, _rect = wins[0]
rank = 0                # wins[0] 在 (y,x) 整序里的排名
same_rank = 0           # 同名子集里的序号（单屏时都是 0）

try:
    ws = ObsWs(cfg.get("host", "127.0.0.1"), int(cfg.get("port", 4455)),
               cfg.get("password", ""), timeout=5)
    obss = ws.request("GetMonitorList")["monitors"]
    obss.sort(key=lambda m: (m["monitorPositionY"],
                             m["monitorPositionX"]))
    print("OBS 显示器:", [(m["monitorIndex"], m["monitorName"],
                          (m["monitorPositionX"], m["monitorPositionY"]))
                         for m in obss])
    hits = [m for m in obss if _screen_match(m["monitorName"], name)]
    if len(hits) == 1:
        idx, how = hits[0]["monitorIndex"], "按名匹配"
    elif hits:
        idx = hits[min(same_rank, len(hits) - 1)]["monitorIndex"]
        how = "按名匹配(同名%d块,取序号%d)" % (len(hits), same_rank)
    elif rank < len(obss):
        idx = obss[rank]["monitorIndex"]
        how = "排名兜底（Windows 名 %r 对不上 OBS 名）" % name
    else:
        raise ObsError("OBS 报告的显示器清单为空")
    print("「%s」→ OBS monitorIndex=%d（%s）" % (name, idx, how))
    ws.close()
    print("PASS")
except (ObsError, OSError) as e:
    print("连不上 OBS（真实使用时连上才投，不影响本结论）：%s" % e)
