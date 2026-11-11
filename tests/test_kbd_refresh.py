# -*- coding: utf-8 -*-
"""键盘自动化窗「刷新」链路回归：BLE 强制重扫接线 + 端口清单签名守卫。

背景：midi_ble.invalidate() 的 docstring 写明「设备行『刷新』钮用」却从未
被调用——刷新钮只重读 TTL 缓存，BLE 端点插拔后 5 秒内点刷新拿到的仍是
旧清单，且下拉不会随缓存换新自动重建（要再点一次才见效）。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import automator_gui as ag  # noqa: E402  窗体类已迁 Automator 产品线
import kbd_auto  # noqa: E402
import midi_ble  # noqa: E402


def _src():
    return (ROOT / "automator_gui.py").read_text(encoding="utf-8")


class _StubWin:
    """离线窗壳：只装 _poll_ports/_refresh_page 碰到的件，其余录调用。"""

    # 类体内直接赋函数会变实例方法（self 多占一位），staticmethod 裹回
    _side_line = staticmethod(ag.KeyboardAutoWindow._side_line)

    def __init__(self):
        class App:
            jcfg = {"inHint": "", "outHint": "JUNO", "dev": 0}
            axcfg = {"inHint": "", "outHint": "", "dev": 0}
        self.app = App()
        self._page_key = "juno"
        self._dev_sig = {}
        self.rebuilt = []
        self.polled = 0

    def _rebuild_device_menu(self, key):
        self.rebuilt.append(key)

    def _poll_ports(self):
        self.polled += 1


def test_invalidate_forces_ble_rescan():
    """invalidate 只清时间戳不动清单：后台线程 ≤0.5s 内即触发重扫。"""
    midi_ble._cache_at["in"] = midi_ble._cache_at["out"] = 999.0
    midi_ble.invalidate()
    assert midi_ble._cache_at["in"] == 0.0
    assert midi_ble._cache_at["out"] == 0.0


def test_refresh_page_wires_invalidate(monkeypatch):
    """页顶/设备行刷新（共用 _refresh_page）：先 invalidate 再重建+复核。"""
    calls = []
    monkeypatch.setattr(kbd_auto.midi_ble, "invalidate",
                        lambda: calls.append(1))
    w = _StubWin()
    ag.KeyboardAutoWindow._refresh_page(w)
    assert calls == [1]          # 不再是纯读缓存的假刷新
    assert w.rebuilt == ["juno"]
    assert w.polled == 1
    # 源码锚：设备行按钮不再绕过 invalidate 只调 _rebuild_device_menu
    src = _src()
    assert "command=self._refresh_page)" in src
    assert "command=lambda k=key: self._rebuild_device_menu(k))" not in src


def test_poll_ports_rebuilds_menu_only_on_change(monkeypatch):
    """签名守卫：清单/改选变了才重建下拉——BLE 重扫结果自动落进菜单。"""
    devs = [(0, "JUNO")]
    monkeypatch.setattr(kbd_auto, "all_in_devices", lambda: list(devs))
    monkeypatch.setattr(kbd_auto, "all_out_devices", lambda: list(devs))

    class W(_StubWin):
        def _poll_ports(self):
            pass                 # 防递归：真方法由测试直接驱动

        def _port_row(self, key, text, ok):
            pass
    w = W()
    poll = ag.KeyboardAutoWindow._poll_ports
    poll(w)
    assert w.rebuilt == ["juno", "ax"]       # 首拍建基线
    poll(w)
    assert w.rebuilt == ["juno", "ax"]       # 清单没变：不重建
    devs.append((1, "WIDI（BLE）"))          # BLE 缓存换新（插设备）
    poll(w)
    assert w.rebuilt == ["juno", "ax", "juno", "ax"]
    w.app.jcfg["dev"] = 1                    # 改选落盘也换签名
    poll(w)
    assert w.rebuilt[-1] == "juno" and w.rebuilt[-2] == "ax"
