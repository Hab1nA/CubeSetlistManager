# -*- coding: utf-8 -*-
"""ObsController 连接纪元通知单测（11-13 评审 C2）：
「断开→连上」跃迁必须通知 on_connected，全部断开路径都要复位纪元标志——
动作失败路径原漏接（_act 清 _ws 不复位），OBS 重启后投影不再恢复。"""
import time

import obs_ctrl
from obs_ctrl import ObsController
from obs_ws import ObsError


class _FakeWs:
    """最小 ws：request 按方法返回形状正确的响应；fail=True 时抛 ObsError。"""

    def __init__(self):
        self.fail = False
        self.requests = []

    def request(self, method, data=None, timeout=5):
        self.requests.append(method)
        if self.fail:
            raise ObsError("注入失败")
        if method == "GetInputList":
            return {"inputs": [{"inputName": "舞台视频"}]}
        return {}

    def close(self):
        pass


def _wait_until(pred, timeout=5.0, step=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return False


def _mk(monkeypatch, notified):
    """构造假连接的控制器：ObsWs/拉起探测打桩，回调记账 notified。"""
    ctl = ObsController({"retrySec": 0.02, "healthSec": 0.05})
    ws = _FakeWs()
    monkeypatch.setattr(obs_ctrl, "ObsWs", lambda *a, **k: ws)
    monkeypatch.setattr(ctl, "_ensure_obs_running", lambda: False)
    ctl.on_connected = lambda: notified.append(1)
    ctl.enabled = True
    ctl.start()
    return ctl, ws


def test_reconnect_after_action_failure_notifies_again(monkeypatch):
    """动作失败断链（_act 清 _ws）后重连必须再通知（投影恢复依赖）。"""
    notified = []
    ctl, ws = _mk(monkeypatch, notified)
    try:
        assert _wait_until(lambda: ctl.is_connected() and len(notified) == 1)
        # 健康探活周期不该重复通知
        _wait_until(lambda: ws.requests.count("GetVersion") >= 2)
        assert len(notified) == 1, "健康期重复通知"
        # 动作失败断链 → 重连 → 必须再通知
        ws.fail = True
        assert ctl.apply_mute() is False
        ws.fail = False
        assert _wait_until(lambda: len(notified) == 2), "断链重连未再通知"
    finally:
        ctl.shutdown()


def test_renotify_after_disable_enable_cycle(monkeypatch):
    """停用→再启用同属「断开→连上」跃迁，必须再通知（同一纪元机制）。"""
    notified = []
    ctl, _ws = _mk(monkeypatch, notified)
    try:
        assert _wait_until(lambda: ctl.is_connected() and len(notified) == 1)
        ctl.enabled = False
        assert _wait_until(lambda: not ctl.is_connected())
        assert len(notified) == 1
        ctl.enabled = True
        assert _wait_until(lambda: len(notified) == 2)
    finally:
        ctl.shutdown()
