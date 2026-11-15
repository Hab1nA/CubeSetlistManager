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


def test_healthy_connection_not_rebuilt(monkeypatch):
    """健康期探活通过必须保持现有连接（原探活成功落回循环顶部=每
    healthSec 整轮重建连接+重同步静音，OBS 侧手动调整 ≤30s 被打回）。"""
    created = []

    def _factory(*_a, **_k):
        ws = _FakeWs()
        created.append(ws)
        return ws

    notified = []
    ctl = ObsController({"retrySec": 0.02, "healthSec": 0.05})
    monkeypatch.setattr(obs_ctrl, "ObsWs", _factory)
    monkeypatch.setattr(ctl, "_ensure_obs_running", lambda: False)
    ctl.on_connected = lambda: notified.append(1)
    ctl.enabled = True
    ctl.start()
    try:
        assert _wait_until(lambda: ctl.is_connected() and len(notified) == 1)
        assert _wait_until(
            lambda: created[0].requests.count("GetVersion") >= 2)
        time.sleep(0.2)               # 再熬过几个健康期
        assert len(created) == 1, "健康期重建了连接"
        assert len(notified) == 1
    finally:
        ctl.shutdown()


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


# ---- OBS WebSocket 密码自动读取（global.ini [WebSocketServerSettings] ServerPassword）----


def test_obs_password_reads_global_ini(tmp_path):
    ini = tmp_path / "global.ini"
    ini.write_text("[WebSocketServerSettings]\nServerEnabled=true\n"
                   "ServerPort=4455\nServerPassword=s3cret\n", encoding="utf-8")
    assert obs_ctrl.read_obs_websocket_password(str(ini)) == "s3cret"


def test_obs_password_missing_or_section_scoped(tmp_path):
    """缺失/键在其它段/键不存在 → 一律空串，不阻塞连接循环。"""
    assert obs_ctrl.read_obs_websocket_password(str(tmp_path / "none.ini")) == ""
    ini = tmp_path / "global.ini"
    ini.write_text("[Video]\nServerPassword=leak\n"
                   "[WebSocketServerSettings]\nServerEnabled=true\n",
                   encoding="utf-8")
    assert obs_ctrl.read_obs_websocket_password(str(ini)) == ""


def test_obs_password_fallback_and_precedence(monkeypatch):
    """config 留空 → 连接回退自动值；显式填写优先（接线+优先级测试）。"""
    seen = []
    reads = []
    monkeypatch.setattr(obs_ctrl, "read_obs_websocket_password",
                        lambda p=None: reads.append(1) or "auto-pw")
    factory = lambda h, p, pw, timeout=5: seen.append(pw) or _FakeWs()
    monkeypatch.setattr(obs_ctrl, "ObsWs", factory)

    ctl = ObsController({"retrySec": 0.02, "healthSec": 0.05})
    monkeypatch.setattr(ctl, "_ensure_obs_running", lambda: False)
    ctl.on_connected = lambda: None
    ctl.enabled = True
    ctl.start()
    ctl2 = None
    try:
        assert _wait_until(lambda: seen and seen[0] == "auto-pw"), "回退未生效"
        seen.clear()
        ctl2 = ObsController({"retrySec": 0.02, "healthSec": 0.05,
                              "password": "explicit-pw"})
        monkeypatch.setattr(ctl2, "_ensure_obs_running", lambda: False)
        ctl2.on_connected = lambda: None
        ctl2.enabled = True
        ctl2.start()
        assert _wait_until(lambda: seen and seen[0] == "explicit-pw"), "显式未优先"
        assert len(reads) == 1, "显式填写不应再读 global.ini"
    finally:
        ctl.shutdown()
        if ctl2 is not None:
            ctl2.shutdown()


def test_obs_password_bom_crlf(tmp_path):
    """真机 global.ini 实测带 UTF-8 BOM（文件头 357 273 277）+ CRLF 行尾。"""
    ini = tmp_path / "global.ini"
    ini.write_text("\ufeff[WebSocketServerSettings]\r\n"
                   "ServerPassword=bompw\r\n", encoding="utf-8")
    assert obs_ctrl.read_obs_websocket_password(str(ini)) == "bompw"
