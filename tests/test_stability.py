# -*- coding: utf-8 -*-
"""稳定性修复回归：主线程停摆家族（UI 黑面+APP 指令积压+点击恢复集中执行）。
每条用例锚定一个已清除的主线程阻塞点或一个新增的韧性机制；源码锚断言沿用
test_clock_port 的既有风格（防回归复学回旧写法）。"""
import pathlib
import sys
import time

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
    body = _slice(src, "def _run_call", "def _tick_body")
    assert "except BaseException" in body
    tick = _slice(src, "def _tick_body", "def _transport_state")
    assert "self.calls.get_nowait()" not in tick   # tick 不再排水


def test_setlist_drain_catches_baseexception():
    """MidiIn 端口降级抛 SystemExit（不在 Exception 之列）——漏过去
    after 重挂不执行=排水循环永久死亡。"""
    body = _slice(_src("setlist_gui.py"), "def _run_call", "def _tick(")
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


# ---- 修复6：主线程停摆黑匣子（stallguard） ----

def test_watchdog_logs_stall_and_recovery(tmp_path):
    import stallguard
    hb = {"t": time.monotonic()}
    msgs = []
    log = tmp_path / "stall.log"
    wd = stallguard.StallWatchdog(
        heartbeat=lambda: hb["t"],
        snapshot=lambda: {"tag": "_tick_body", "calls": 3},
        path=log, notify=msgs.append, threshold=0.2, period=0.05)
    wd.start()
    time.sleep(0.5)                     # 心跳冻结=停摆窗口
    hb["t"] = time.monotonic()          # 恢复
    time.sleep(0.4)
    wd.stop()
    text = log.read_text(encoding="utf-8")
    assert "停摆" in text and "_tick_body" in text and "恢复" in text
    assert any("主线程曾停摆" in m for m in msgs)


def test_watchdog_silent_when_healthy(tmp_path):
    import stallguard
    hb = {"t": time.monotonic()}
    log = tmp_path / "stall.log"
    wd = stallguard.StallWatchdog(
        heartbeat=lambda: hb["t"],
        snapshot=lambda: {}, path=log, threshold=0.2, period=0.05)
    wd.start()
    for _ in range(8):                  # 健康心跳：每 50ms 喂一次
        time.sleep(0.05)
        hb["t"] = time.monotonic()
    wd.stop()
    assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""


def test_bounded_call_queue_drops_oldest():
    import stallguard
    q = stallguard.BoundedCallQueue(3)
    for i in range(4):
        q.put(i)
    assert q.dropped == 1
    assert [q.get_nowait() for _ in range(3)] == [1, 2, 3]


def test_coalesce_keeps_last_occurrence():
    import stallguard

    a = lambda: 0  # noqa: E731
    b = lambda: 1  # noqa: E731
    c = lambda: 2  # noqa: E731
    assert stallguard.coalesce([a, b, a, c]) == [b, a, c]
    assert stallguard.coalesce([a]) == [a]
    x, y = (lambda: 1), (lambda: 1)     # 不同 lambda 对象永不相等，不合流
    assert stallguard.coalesce([x, y]) == [x, y]

    class A:
        def m(self):
            pass

    o = A()
    assert stallguard.coalesce([o.m, o.m]) == [o.m]   # 同绑定方法合流保尾


def test_watchdog_wired_both_guis():
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        assert "StallWatchdog(" in src, name
        assert 'self._hb = time.monotonic()' in src, name
        assert '_hb_tag = getattr(fn, "__name__", "<lambda>")' in src, name


# ---- 修复7：winmm 专线（open/close 收敛专职线程，主线程限时等待） ----

import midi_bridge as mb  # noqa: E402
import kbd_auto  # noqa: E402


class _FakeWinmm:
    """可编排 winmm 桩：记录调用序列，midiInOpen 可脚本化延迟/失败。"""

    def __init__(self, ndevs=1, name=b"FakePort", open_delay=0.0,
                 open_ret=0):
        self.log = []
        self.ndevs = ndevs
        self.name = name
        self.open_delay = open_delay
        self.open_ret = open_ret
        self._lock = __import__("threading").Lock()

    def midiInGetNumDevs(self):
        self.log.append("numdevs")
        return self.ndevs

    def midiInGetDevCapsA(self, _i, caps, _n):
        # 裸桩无 argtypes：byref 传入的是 CArgObject，_obj 才是结构体
        getattr(caps, "_obj", caps).szPname = self.name.ljust(32, b"\0")
        return 0

    def midiInOpen(self, ph, _idx, _proc, _inst, _flags):
        if self.open_delay:
            time.sleep(self.open_delay)
        with self._lock:
            self.log.append("open")
        if not self.open_ret:
            getattr(ph, "_obj", ph).value = 42
        return self.open_ret

    def midiInStart(self, _h):
        self.log.append("start")

    def midiInStop(self, _h):
        self.log.append("stop")

    def midiInReset(self, _h):
        self.log.append("reset")

    def midiInClose(self, _h):
        self.log.append("close")


def test_open_in_success_and_double_close(monkeypatch):
    fake = _FakeWinmm()
    monkeypatch.setattr(mb, "_winmm", fake)
    h, err = mb.open_in(0, None)
    assert err is None and h.value == 42
    assert fake.log[:2] == ["numdevs", "open"] or "open" in fake.log
    mb.close_in(h)
    mb.close_in(h)                      # 幂等：二次 close 无操作
    assert fake.log.count("close") == 1
    assert fake.log[-3:] == ["stop", "reset", "close"]


def test_open_in_timeout_aborts_late(monkeypatch):
    """超时弃单：调用方 (None, err) 即返；迟到的 open 完成后自查弃单标志
    即开即回收，句柄绝不泄漏。"""
    fake = _FakeWinmm(open_delay=0.4)
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    t0 = time.monotonic()
    h, err = mb.open_in(0, None)
    elapsed = time.monotonic() - t0
    assert h is None and "超时" in err
    assert elapsed < 2.0                # 主线程限时返回
    deadline = time.monotonic() + 2.0   # 弃单迟到回收
    while time.monotonic() < deadline and "close" not in fake.log:
        time.sleep(0.05)
    assert "close" in fake.log


def test_open_in_hard_hang_bounded(monkeypatch):
    """midiInOpen 无限挂死（Win11 进程级锁场景）：调用方仍限时返回。"""
    fake = _FakeWinmm(open_delay=1.2)   # 远超 timeout+宽限，模拟挂死
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    t0 = time.monotonic()
    h, err = mb.open_in(0, None)
    assert h is None and "超时" in err
    assert time.monotonic() - t0 < 2.0
    time.sleep(1.0)                     # 迟到 job 收尾（排空专线）


def test_midiin_missing_port_exits(monkeypatch):
    """缺端口=SystemExit（调用方按既有降级语义捕获），口径不变。"""
    fake = _FakeWinmm(ndevs=0)
    monkeypatch.setattr(mb, "_winmm", fake)
    try:
        mb.MidiIn("NoSuchPort", lambda n, v: None)
        assert False, "must SystemExit"
    except SystemExit as e:
        assert "没找到" in str(e)


def test_midiin_open_and_idempotent_close(monkeypatch):
    fake = _FakeWinmm()
    monkeypatch.setattr(mb, "_winmm", fake)
    port = mb.MidiIn("FakePort", lambda n, v: None)
    assert port.name == "FakePort"
    port.close()
    port.close()
    assert fake.log.count("close") == 1
    assert port._h is None


def test_rawmidiin_timeout_raises_portnotfound(monkeypatch):
    """kbd_auto.RawMidiIn 走同一专线：超时=PortNotFound（调用方既有
    降级分支直接消化），迟到弃单同样回收。"""
    fake = _FakeWinmm(open_delay=0.4)
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    try:
        kbd_auto.RawMidiIn("FakePort", lambda s, a, b: None)
        assert False, "must PortNotFound"
    except kbd_auto.PortNotFound as e:
        assert "超时" in str(e)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and "close" not in fake.log:
        time.sleep(0.05)
    assert "close" in fake.log


def test_winmm_io_thread_wiring():
    """双 GUI 枚举降频 + kbd_auto 走专线（源码锚）。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        assert "_enum_n" in src, name               # 枚举降频 ~2s
        assert 'mb._pick(mb._in_devices(), self.vj_hint)' in src, name
    ksrc = _src("kbd_auto.py")
    assert "mb.open_in(idx, self._cb)" in ksrc
    assert "mb.close_in(h)" in ksrc
    assert "midiInOpen(ctypes.byref(self._h)" not in ksrc  # 旧直连不回归


# ---- 修复8：calls 队列治理接线（有界/合流/紧急通道） ----

def test_queue_governance_wiring():
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        assert "stallguard.BoundedCallQueue(200)" in src, name
        assert "self.calls_urgent = collections.deque()" in src, name
        assert "def urgent(self, fn)" in src, name
        assert "stallguard.coalesce(batch)" in src, name
    src = _src("web_remote.py")
    assert "app.urgent(app._panic)" in src           # 全停走紧急通道
    assert "app.calls.put(app._panic)" not in src    # 旧同队写法不回归


# ---- 修复9：LL 钩子运行期自愈（pedal） ----

def test_hook_selfheal_probe_and_rehook(monkeypatch):
    """raw 见键而钩子探针滞后=钩子被系统静默摘除（LowLevelHooksTimeout）：
    换独立常驻线程重装+拆旧钩，限频 15 秒，_live=False 不自愈。"""
    import pedal
    calls = []
    br = pedal.RawInputBridge(on_action=lambda a: None,
                              on_event=calls.append)
    br._live = True
    br._hook = "old-hook"
    br._href = "trampoline-stub"    # 真桥由 _run 生成，测试桩不需要真回调
    monkeypatch.setattr(pedal.u32, "SetWindowsHookExW",
                        lambda *a: (calls.append("install"), 77)[1])
    monkeypatch.setattr(pedal.u32, "UnhookWindowsHookEx",
                        lambda h: calls.append("unhook"))
    monkeypatch.setattr(pedal.k32, "GetCurrentThreadId", lambda: 99)
    monkeypatch.setattr(pedal.u32, "GetMessageW", lambda *a: 0)  # 泵即退
    br._probe(time.monotonic())          # 探针 None=钩子从未见键 → 自愈
    br._probe(time.monotonic())          # 在途/限频：不二次触发
    br._rehook(time.monotonic())         # 显式调用同样受限频
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and "install" not in calls:
        time.sleep(0.02)
    assert "install" in calls
    assert calls.count("install") == 1   # 15s 限频内只装一次
    assert "unhook" in calls             # 旧钩子被拆
    assert any(isinstance(c, str) and "已自动重装" in c for c in calls)
    br._live = False
    br._probe(time.monotonic())          # 非 live 不自愈
    assert calls.count("install") == 1


def test_hook_selfheal_wiring():
    src = _src("pedal.py")
    assert "_hook_seen" in src and "_probe(" in src and "_rehook(" in src
    assert "self._rehook_tid" in src     # stop 时收尾自愈线程
    src = _src("setlist_gui.py")
    assert 'on_event=lambda m: self.q.put("踩钉桥：%s" % m)' in src
