# -*- coding: utf-8 -*-
"""稳定性修复回归：主线程停摆家族（UI 黑面+APP 指令积压+点击恢复集中执行）。
每条用例锚定一个已清除的主线程阻塞点或一个新增的韧性机制；源码锚断言沿用
test_clock_port 的既有风格（防回归复学回旧写法）。"""
import pathlib
import sys
import threading
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
    双端同改；旧的主线程直调写法不得回归。连续保存的热应用有锁串行。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _slice(_src(name), "def _save(self)", "_MUTEX = None")
        assert "def apply_obs" in src, name
        assert "threading.Thread(target=apply_obs" in src, name
        assert "not app.ctl.apply_mute()" not in src, name
        assert "not app.ctl.apply_projector()" not in src, name
        assert "with app._obs_apply_lock" in src, name


# ---- 修复3：简化版独立 _drain_calls + 两端排水循环 BaseException 韧性 ----

def test_automator_independent_drain():
    """简化版补独立 50ms _drain_calls（原搭 400ms tick 且逐条无兜底）。
    BaseException 必须就地吞掉：SystemExit 在 py3.14 实测会冲出 mainloop
    终止进程（report_callback_exception 都不经过）。"""
    src = _src("automator_gui.py")
    assert "root.after(50, self._drain_calls)" in src
    body = _slice(src, "def _run_call", "def _tick_body")
    assert "except BaseException" in body
    tick = _slice(src, "def _tick_body", "def _transport_state")
    assert "self.calls.get_nowait()" not in tick   # tick 不再排水


def test_setlist_drain_catches_baseexception():
    """同 automator：SystemExit 不在 Exception 之列，漏过去=进程闪退。"""
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


def test_coalesce_respects_allowlist():
    """有状态指令（_next/_prev）不在白名单：同批连发两次就是两次。"""
    import stallguard

    nxt = lambda: "next"  # noqa: E731
    r = lambda: "r"  # noqa: E731
    batch = [nxt, r, nxt, r]
    assert stallguard.coalesce(batch, {"_refresh"}) == [nxt, r, nxt, r]
    assert stallguard.coalesce(batch, {"<lambda>"}) == [nxt, r]


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
    """可编排 winmm 桩：记录调用序列，midiInOpen 可脚本化延迟（仅首次，
    供毒化-恢复测试）/失败。"""

    def __init__(self, ndevs=1, name=b"FakePort", open_delay=0.0,
                 open_ret=0):
        self.log = []
        self.ndevs = ndevs
        self.name = name
        self.open_delay = open_delay
        self.open_ret = open_ret
        self._opens = 0
        self._lock = __import__("threading").Lock()

    def midiInGetNumDevs(self):
        self.log.append("numdevs")
        return self.ndevs

    def midiInGetDevCapsA(self, _i, caps, _n):
        # 裸桩无 argtypes：byref 传入的是 CArgObject，_obj 才是结构体
        getattr(caps, "_obj", caps).szPname = self.name.ljust(32, b"\0")
        return 0

    def midiInOpen(self, ph, _idx, _proc, _inst, _flags):
        with self._lock:
            self._opens += 1
            delay = self.open_delay if self._opens == 1 else 0.0
        if delay:
            time.sleep(delay)
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
    deadline = time.monotonic() + 3.0   # 熔断标志了结清零（不污染后续用例）
    while time.monotonic() < deadline:
        with mb._io_stuck_lock:
            if not mb._io_stuck:
                break
        time.sleep(0.05)


def test_open_in_hard_hang_bounded(monkeypatch):
    """midiInOpen 无限挂死（Win11 进程级锁场景）：调用方仍限时返回。"""
    fake = _FakeWinmm(open_delay=1.2)   # 远超 timeout+宽限，模拟挂死
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    t0 = time.monotonic()
    h, err = mb.open_in(0, None)
    assert h is None and "超时" in err
    assert time.monotonic() - t0 < 2.0
    deadline = time.monotonic() + 3.0   # 毒化语义：迟到 job 了结才解除熔断
    while time.monotonic() < deadline:
        with mb._io_stuck_lock:
            if not mb._io_stuck:
                break
        time.sleep(0.05)


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
        assert 'stallguard.coalesce(\n                batch' in src, name
        assert '"_persist_web_remote"}' in src, name
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
    assert "self._rehook_tids" in src    # stop 逐个收尾在途自愈泵
    src = _src("setlist_gui.py")
    assert 'on_event=lambda m: self.q.put("踩钉桥：%s" % m)' in src


# ---- R1 复审 FAIL 清单（A-I）回归 ----

def test_load_slots_rejects_bad_types(tmp_path):
    """手改 JSON 的 pc/msb/lsb 类型洞（null/字符串）按条丢弃不再外漏——
    旧过滤只验 "pc" in v，describe_slot 做 501+pc 才炸（曾可毒杀发送线程）。"""
    f = tmp_path / "keyboard_automation.json"
    f.write_text('{"slots": {"60": {"pc": 3, "msb": 87, "lsb": 0},'
                 ' "61": {"pc": null}, "62": {"pc": "3"},'
                 ' "63": {"pc": 1, "msb": null}, "64": {"pc": true}}}',
                 encoding="utf-8")
    import kbd_auto
    slots = kbd_auto.load_slots(str(tmp_path / "x.cpr"))
    # pc 类型洞全弃；msb:null 视为未提供（保留条目，映射语义=无 MSB）
    assert set(slots) == {60, 63}
    assert slots[60] == {"pc": 3, "msb": 87, "lsb": 0}
    assert slots[63] == {"pc": 1}


def test_tone_switcher_loop_survives_exception(monkeypatch):
    """发送线程裸 while True 曾被单次异常静默毒杀（音色/延音/移调全场
    失效零日志）：现异常就地吞掉进日志，线程存活继续消费。"""
    import kbd_auto
    results = []
    sw = kbd_auto.ToneSwitcher(
        dict(kbd_auto.DEFAULT_JUNO), lambda m: None,
        on_result=lambda d, e: results.append((d, e)))
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("boom")
        return None

    monkeypatch.setattr(kbd_auto, "send_slot", flaky)
    sw.submit({"pc": 3}, why="t1")
    sw.submit({"pc": 4}, why="t2")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(results) < 2:
        time.sleep(0.02)
    assert len(results) >= 2             # 第一次异常后线程仍活着
    sw._q.put(((0.0, None), "quit-marker"))  # 不会退出也无妨（daemon）


def test_winmm_poison_fastfail(monkeypatch):
    """专线毒化（挂死 job 占住串行队列）时后续 run_io 立即快速失败，
    主线程不再每次都等满超时（踩钉 10s 重试曾致周期性 3.5s 冻结）。"""
    fake = _FakeWinmm(open_delay=0.8)
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    h1, err1 = mb.open_in(0, None)       # 第一次：等满超时并置毒化标志
    assert h1 is None and "超时" in err1
    t0 = time.monotonic()
    h2, err2 = mb.open_in(0, None)       # 第二次：快速失败
    assert time.monotonic() - t0 < 0.5
    assert h2 is None and "超时" in err2
    deadline = time.monotonic() + 3.0    # 迟到 job 了结→熔断解除
    while time.monotonic() < deadline:
        with mb._io_stuck_lock:
            if not mb._io_stuck:
                break
        time.sleep(0.05)
    h3, err3 = mb.open_in(0, None)       # 熔断解除后恢复正常
    assert h3 is not None and err3 is None


def test_mainloop_daemon_wiring():
    """后台线程静默死亡家族的兜底锚：OBS 重连/音符/翻谱/音色发送四循环
    异常均就地吞掉并上报，不带走线程。"""
    assert "连接循环异常（已恢复）" in _src("obs_ctrl.py")
    assert "音符处理异常（已恢复）" in _src("midi_bridge.py")
    assert "翻谱处理异常（已恢复）" in _src("web_remote.py")
    assert "音色发送异常（已恢复）" in _src("kbd_auto.py")


def test_title_load_off_main_thread():
    """_adopt/_redict/_apply_title 的工程文件 IO 挪后台（网络盘无界卡顿）+
    代际守卫；kbd 窗热同步经 calls 回主线程。"""
    src = _src("setlist_gui.py")
    assert "def _adopted_load" in src
    body = _slice(src, "def _adopt", "def _tick_banner")
    assert "_load_gen" in body
    assert "load_slots(path)" in body
    redict = _slice(src, "def _redict", "def _persist(self)")
    assert "threading.Thread(target=run" in redict
    assert "        d = _read_duration(song[\"path\"])" not in redict
    src = _src("automator_gui.py")
    assert "_title_gen" in src
    assert "def _kbd_sync" in src
    assert "self.calls.put(lambda: self._kbd_sync(song))" in src


def test_settings_probe_off_marshalled_after():
    """设置页探测 worker 改走 calls 队列（self.after 封送阻塞 worker），
    复探走 Timer 线程（网络不占主线程）。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        assert "self.app.calls.put(apply)" in src, name
        assert "threading.Timer(1.2" in src, name
        assert "self.after(1200" not in src, name   # 旧主线程复探不回归


def test_marquee_step_guard():
    """跑马灯单步异常不再断 after 链（TclError 退场、其余下个 set 重挂）。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        assert "def _step_body" in src, name
        body = _slice(src, "def _step(self)", "def _step_body")
        assert "except tk.TclError" in body, name
        assert "except Exception" in body, name


def test_tick_baseexception_aligned():
    """_tick 与排水循环同为 BaseException 防御（SystemExit 闪退风险对齐）。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _src(name)
        body = _slice(src, "def _tick(self)", "def _tick_body")
        assert "except BaseException as e" in body, name


def test_pedal_hid_retry_gated():
    """HID 桥泵死亡而 MIDI 口在连时，10s 重试门控也覆盖 HID 通道。"""
    src = _slice(_src("setlist_gui.py"), "ped = self.pedal",
                 "self._update_transport_buttons")
    assert "not ped.bridge.running" in src


# ---- R2 复审清单（H1/M1/M3/TOCTOU/P1-1/P1-2/P2-4/L1/L2/L4）回归 ----

def test_winmm_poison_causal_guard(monkeypatch):
    """假毒化防：置位以票号为因果锚——job 在超时唤醒延迟窗口内迟到完成
    时，后到的置位被拒绝（复审 H1：否则永久熔断至重启）。"""
    fake = _FakeWinmm()
    monkeypatch.setattr(mb, "_winmm", fake)
    deadline = time.monotonic() + 3.0   # 先等前序用例的毒化标志清零
    while time.monotonic() < deadline:
        with mb._io_stuck_lock:
            if not mb._io_stuck:
                break
        time.sleep(0.05)
    done = threading.Event()
    holder = {}

    def slow_fn():
        done.wait(1.0)                  # 占住专线，超时后仍会完成
        return "ok"

    t = threading.Thread(target=lambda: holder.update(
        r=mb.run_io(slow_fn, 0.05)), daemon=True)
    t.start()
    time.sleep(0.3)                     # 等超时分支走完（job 尚未完成）
    with mb._io_stuck_lock:
        stuck_after = mb._io_stuck
    assert stuck_after                  # 确曾置位（job 明确未了结）
    done.set()                          # 迟到完成
    t.join(2.0)
    time.sleep(0.2)
    with mb._io_stuck_lock:
        final_stuck = mb._io_stuck
        done_ticket = mb._io_done_ticket
    assert not final_stuck              # 了结后标志必为 False（假毒化被拒）
    assert done_ticket > 0              # 票号已回填
    ok, val = mb.run_io(lambda: "next", 1.0)
    assert ok and val == "next"         # 专线未被假毒化锁死


def test_close_in_enqueued_under_poison(monkeypatch):
    """毒化期 close_in 的收尾 job 必须入队（force 豁免快速失败）——否则
    句柄永久泄漏+幽灵口继续派发回调（复审 M1）。"""
    fake = _FakeWinmm(open_delay=0.6)   # 首次 open 慢→制造毒化窗口
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    box = {}
    t = threading.Thread(target=lambda: box.update(r=mb.open_in(0, None)))
    t.start()
    deadline = time.monotonic() + 2.0   # 等首次超时置位毒化
    while time.monotonic() < deadline:
        with mb._io_stuck_lock:
            if mb._io_stuck:
                break
        time.sleep(0.02)
    with mb._io_stuck_lock:
        assert mb._io_stuck             # 毒化窗口内
    mb.close_in(42)                     # 毒化期 close：force 必须仍入队
    t.join(3.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and fake.log.count("close") < 2:
        time.sleep(0.05)
    # 弃单回收 + close_in 收尾都执行了（毒化期 close 不再被快速失败吞掉）
    assert fake.log.count("close") >= 2


def test_out_devices_via_io_thread():
    """输出方向同走专线（send_slot 的 midiOutOpen/Close 曾直调 winmm，
    进程级锁挂死时发送线程无限挂死零日志，复审 P1-2）。"""
    ksrc = _src("kbd_auto.py")
    assert "mb.open_out(hits[0][0])" in ksrc
    assert "mb.close_out(h)" in ksrc
    assert "_winmm.midiOutOpen(ctypes.byref(h)" not in ksrc
    src = _src("midi_bridge.py")
    assert "def open_out" in src and "def close_out" in src
    assert "run_io(job, force=True)" in src      # close 类 force 入队


def test_enum_devices_bounded():
    """枚举走专线+last-good 缓存：毒化/挂死时主线程拿缓存不陪葬（M3）。"""
    src = _src("midi_bridge.py")
    assert "_enum_devices" in src and "_in_cache_holder" in src
    body = _slice(src, "def _in_devices", "def _out_devices")
    assert "_enum_devices(job, _in_cache_holder)" in body
    assert "_winmm.midiInGetNumDevs" in body


def test_cpr_read_duration_catches_oserror():
    """工程文件不可读（OSError）返回 None 不外抛——三个装载后台线程的
    静默死亡源（复审 P2-4）。"""
    import cpr_meta
    assert cpr_meta.read_duration(str(pathlib.Path("Z:/nope/x.cpr"))) is None


def test_rehook_hang_watchdog(monkeypatch):
    """重装 SetWindowsHookExW 挂死：看门狗放行 _rehooking（自愈能力不得
    单点失效，复审 L1；时长常量化供测试缩时）。"""
    import pedal

    calls = []
    br = pedal.RawInputBridge(on_action=lambda a: None,
                              on_event=calls.append)
    br._live = True
    br._hook = "old"
    br._href = "trampoline-stub"

    def hang(*a):                       # 永不返回（挂死模拟）
        calls.append("hang")
        time.sleep(30)

    monkeypatch.setattr(pedal.u32, "SetWindowsHookExW", hang)
    monkeypatch.setattr(pedal.k32, "GetCurrentThreadId", lambda: 99)
    monkeypatch.setattr(pedal, "REHOOK_UNSTICK_SEC", 0.3)
    br._rehook(time.monotonic())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and "hang" not in calls:
        time.sleep(0.02)
    assert br._rehooking                # 在途
    deadline = time.monotonic() + 3.0   # 看门狗放行
    while time.monotonic() < deadline and br._rehooking:
        time.sleep(0.05)
    assert not br._rehooking            # 自愈能力恢复（可再试）
    assert any("超时未返回" in c for c in calls if isinstance(c, str))


def test_bridge_start_gate_released():
    """HID 桥成功启动后看门狗引用必须清空：泵线程日后自行死亡时 10s
    重拉不被 start() 门槛永久拒绝（复审 P1-1）。"""
    src = _slice(_src("pedal.py"), "def _start_watchdog", "def _retire")
    assert "self._watchdog = None   # 成功即放行 start()" in src
    body = _slice(_src("pedal.py"), "def _run(self)", "def _run_inner")
    assert "self._retire()" in body     # 未捕异常死亡也清登记


def test_generation_guard_locked():
    """代际守卫的校验+写原子化（TOCTOU 闭环，复审 M3）。"""
    src = _src("setlist_gui.py")
    assert "self._load_lock = threading.Lock()" in src
    body = _slice(src, "def _adopted_load", "def _tick_banner")
    assert "with self._load_lock:" in body
    sw = _slice(src, "def _switch", "def _switch_done")
    assert "with self._load_lock:" in sw
    src = _src("automator_gui.py")
    assert "self._title_lock = threading.Lock()" in src
    body = _slice(src, "def _apply_title", "def _kbd_sync")
    assert "with self._title_lock:" in body


def test_exit_stops_watchdog():
    """退出确认后停黑匣子：root.destroy 后心跳冻结不算停摆（复审 L4）。"""
    for name in ("setlist_gui.py", "automator_gui.py"):
        src = _slice(_src(name), "def _on_exit", "def _exit_worker")
        assert "self._stall_wd.stop()" in src, name


# ---- R3 复审清单（R3-1/R3-2/R3-4/R3-5）回归 ----

def test_kbd_window_io_off_main_thread():
    """键盘自动化窗口的映射装载/落盘出主线程（网络盘部署下的停摆残留，
    复审 R3 全量 ISSUE-1）：set_song 后台装载+代际守卫、保存走后台。"""
    src = _src("kbd_auto.py")
    body = _slice(src, "def set_song", "def _refresh(self, note)")
    assert "threading.Thread(target=run" in body
    assert "_song_gen" in body
    assert "def _persist_async" in src
    cap = _slice(src, "def _finish_capture", "def _trigger(self, note)")
    assert "_persist_async(store, self.song" in cap
    assert "save_slots(self.song[\"path\"]" not in cap   # 旧同步写不回归
    clr = _slice(src, "def _clear(self", "    def _tick(self)")
    assert "_persist_async" in clr


def test_open_out_dead_flag_recycle(monkeypatch):
    """open_out 超时弃单同款即开即回收（复审 R3 全量 ISSUE-2，M1 的
    输出方向对偶项）：迟到的 open 成功后自查 dead 标志自行 close。"""
    class _OutFake(_FakeWinmm):
        def midiOutOpen(self, ph, _idx, _proc, _inst, _flags):
            time.sleep(0.4)             # 首次慢开制造弃单窗口
            with self._lock:
                self.log.append("outopen")
            getattr(ph, "_obj", ph).value = 7
            return 0

        def midiOutClose(self, _h):
            with self._lock:
                self.log.append("outclose")

    fake = _OutFake()
    monkeypatch.setattr(mb, "_winmm", fake)
    monkeypatch.setattr(mb, "IO_TIMEOUT", 0.1)
    h, err = mb.open_out(0)
    assert h is None and "超时" in err
    deadline = time.monotonic() + 2.0   # 迟到 open 完成后自查回收
    while time.monotonic() < deadline and "outclose" not in fake.log:
        time.sleep(0.05)
    assert "outclose" in fake.log


def test_switch_done_third_writer_locked():
    """_switch_done 写回纳入 _load_lock；daw_ctrl 改为 on_done 完成后才清
    busy（完成回调的 IO 尾巴期间门控不再失效，复审 R3 对抗 M 项）。"""
    src = _src("setlist_gui.py")
    body = _slice(src, "def _switch_done", "def _auto_play")
    assert "with self._load_lock:" in body
    dsrc = _src("daw_ctrl.py")
    fin = _slice(dsrc, "def _switch(self, path, on_done)",
                 "    def _close(self, hwnd)")
    tail = fin[fin.index("        finally:"):]
    assert tail.index("on_done(") < tail.index("self.busy = False")


def test_grace_noop_removed():
    """open_in 的 0.5s 宽限 no-op 已删（复审 R3-4 死重+毒化期队列增长源）：
    弃单 job 本就在队列里会迟到自查回收。"""
    src = _slice(_src("midi_bridge.py"), "def open_in", "def close_in")
    assert "run_io(lambda: None" not in src
