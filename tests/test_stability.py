# -*- coding: utf-8 -*-
"""稳定性修复回归：主线程停摆家族（UI 黑面+APP 指令积压+点击恢复集中执行）。
每条用例锚定一个已清除的主线程阻塞点或一个新增的韧性机制；源码锚断言沿用
test_clock_port 的既有风格（防回归复学回旧写法）。"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import web_remote  # noqa: E402


def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _slice(src, start, end):
    """按标记切片：start 标记到其后第一个 end 标记之间的源码。"""
    i = src.index(start)
    j = src.index(end, i + len(start))
    return src[i:j]


# ---- 修复1：全停按钮的 OBS 网络调用出主线程 ----

def test_panic_obs_network_off_main_thread():
    """stop_media=2 个 WebSocket 请求（各 5s 超时）+全程持 ctl._lock，
    必须在 run 后台线程体里；主线程只发线程+watch 记账。"""
    src = _slice(_src("setlist_gui.py"), "def _panic", "def _open_settings")
    assert "def run" in src
    run_body = _slice(src, "def run", "threading.Thread(target=run")
    assert "ctl.stop_media()" in run_body
    assert "self.ctl.stop_media()" not in src     # 旧主线程直调不回归


# ---- 修复2：设置保存的 OBS 热应用出主线程 ----

def test_settings_save_obs_off_main_thread():
    """「保存并应用」的静音/投影热应用（2×5s+2×5s+持锁）挪后台线程，
    双端同改；旧的主线程直调写法不得回归。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _slice(_src(name), "def _save", "_MUTEX = None")
        assert "def apply_obs" in src, name
        assert "threading.Thread(target=apply_obs" in src, name
        assert "not app.ctl.apply_mute()" not in src, name
        assert "not app.ctl.apply_projector()" not in src, name


# ---- 修复3：简化版独立 _drain_calls + 两端排水循环 BaseException 韧性 ----

def test_automator_independent_drain():
    """简化版补独立 50ms _drain_calls（原搭 400ms tick 且逐条无兜底，
    BaseException 连 400ms 轮询链一起杀死=永久假死）。"""
    src = _src("automator_gui.py")
    assert "root.after(50, self._drain_calls)" in src
    body = _slice(src, "def _drain_calls", "def _tick_body")
    assert "except BaseException" in body
    tick = _slice(src, "def _tick_body", "def _transport_state")
    assert "self.calls.get_nowait()" not in tick   # tick 不再排水


def test_setlist_drain_catches_baseexception():
    """MidiIn 端口降级抛 SystemExit（不在 Exception 之列）——漏过去
    after 重挂不执行=排水循环永久死亡。"""
    body = _slice(_src("setlist_gui.py"), "def _drain_calls", "def _tick(")
    assert "except BaseException" in body


# ---- 修复4：HTTP keep-alive 死连接超时回收 ----

def test_handler_dead_connection_timeout():
    """APP 掉网（无 FIN）时 handler 线程此前永久阻塞在 readline，
    僵尸线程逐个累积；30s 超时由 socketserver 优雅关连接。"""
    assert web_remote._Handler.timeout == 30


# ---- 修复5：worker 线程禁摸 Tk（焦点收回用预取 HWND） ----

def test_regain_focus_prefetched_hwnd():
    """winfo_id() 从 worker 调用会被 threaded Tcl 封送回主线程阻塞等待
    （主线程停摆=整批 worker 挂起的放大器）。"""
    src = _src("setlist_gui.py")
    assert "self._main_hwnd = ctypes.windll.user32.GetParent(" in src
    body = _slice(src, "def _regain_focus", "def _transport_thread")
    assert "self.root.winfo_id()" not in body      # 旧跨线程 Tk 调用不回归
    assert "self._main_hwnd" in body
