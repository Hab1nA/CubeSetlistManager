# -*- coding: utf-8 -*-
"""MIDI→OBS 触发桥：监听 loopMIDI 虚拟端口，音符→播放/停止指定视频。

链路：Cubase MIDI 轨(指定位置画音符) → loopMIDI 虚拟端口 → 本脚本
     → 复用 obs_ctrl.ObsController → obs-websocket → OBS 媒体源「舞台视频」
纯标准库：MIDI 收发走 win32 winmm(ctypes) 短消息，不依赖 mido/rtmidi
（python-rtmidi 1.5.8 在本机 Python 3.14 下 import 即崩，实测）。
用法：
  python midi_bridge.py               # 监听（自动拉起 OBS、断线重连）
  python midi_bridge.py --send 60     # 不经 Cubase，向 loopMIDI 发测试音符
Cubase 侧：新建 MIDI 轨（非乐器轨），输出端口选 loopMIDI Port，
在触发位置画音符：C4→1.mp4、C#4→2.mp4 …（只认 Note On，音符长度无所谓）。
视频文件名支持「编号 空格 自定义名」如 `4 开场钢琴.mp4`，按编号匹配；
纯编号 `4.mp4` 优先。
暂停跟随（可选）：走带 → 工程同步设置 → 传输，勾选“发送 MIDI 时钟”，
目的地选 loopMIDI Port（建议存进工程模板）。收到时钟脉冲=在播，
脉冲断流=暂停视频（定格）；时钟恢复=继续播放。暂停态磁吸对齐到
触发音符上起播时，以音符切换为准（短暂抑制恢复，旧视频不闪现）。
不发送时钟则此功能不启用。
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import winreg
from ctypes import wintypes

from obs_ctrl import ObsController, launch_detached

PORT_HINT = "loopMIDI Port"   # 默认端口全名；不能宽匹配 loopMIDI，否则会误开「Keyboard Automation」端口
CLOCK_TIMEOUT = 0.25   # 这么久没有走带脉冲就视为暂停/停止
POLL_SEC = 0.1         # 看门狗轮询间隔
NOTE_SUPPRESS = 0.2    # 音符到达后此窗口内不恢复旧视频（磁吸对齐音符起播时，切换归音符管）
# C4=60 → 1.mp4，向上每个音顺延：C#4=61 → 2.mp4 … D6=89 → 30.mp4
NOTE_MAP = {n: "%d.mp4" % (n - 59) for n in range(60, 90)}
NOTE_MAP[90] = None  # F#6 → 熄屏

_winmm = ctypes.WinDLL("winmm")
_MIM_DATA = 0x3C3         # 短消息到达（本机驱动实测：回调模式也走 MM_MIM_* 常量）
_CALLBACK_FUNC = 0x30000
_Proc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, ctypes.c_uint,
                           ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)
_winmm.midiInOpen.argtypes = (ctypes.POINTER(wintypes.HANDLE), ctypes.c_uint,
                              _Proc, ctypes.c_size_t, wintypes.DWORD)
_winmm.midiOutOpen.argtypes = (ctypes.POINTER(wintypes.HANDLE), ctypes.c_uint,
                               _Proc, ctypes.c_size_t, wintypes.DWORD)


class _InCaps(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.UINT),
                ("szPname", ctypes.c_char * 32), ("dwSupport", wintypes.DWORD)]


class _OutCaps(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.UINT),
                ("szPname", ctypes.c_char * 32),
                ("wTechnology", wintypes.WORD), ("wReserved1", wintypes.WORD),
                ("dwSupport", wintypes.DWORD)]


def _pick(pairs, hint=PORT_HINT):
    hits = [(i, n) for i, n in pairs if hint in n]
    return hits[0] if hits else None


# ---- winmm 专线：open/close 统一走专职线程 ----
# winmm 的 close 在 Win11 走进程级锁（见 MidiIn.close 注释），回调密集时
# 最坏无限挂死，open 同锁。这些调用此前大量落在 GUI 主线程（端口热切换/
# 退出/每 10s 踩钉重连/录制开关），一次挂死=主线程停摆=黑面+指令积压。
# 专职线程串行执行所有 winmm open/close，调用方限时等待：超时即放弃等待，
# 迟到结果由内部弃单回收（不漏句柄），主线程从此不被 winmm 挂死。
IO_TIMEOUT = 3.0
_io_q = queue.Queue()
_io_stuck = False             # 专线毒化标志：某条 job 超过等待时限仍在跑
_io_stuck_lock = threading.Lock()


def _io_worker():
    global _io_stuck
    while True:
        job = _io_q.get()
        with _io_stuck_lock:
            _io_stuck = False    # 上一条已了结（正常完成/迟到完成/异常）
        try:
            job()
        except BaseException:
            pass                # 专线绝不死；成功/失败语义由 job 自行记账
        with _io_stuck_lock:
            _io_stuck = False


_io_thread = threading.Thread(target=_io_worker, daemon=True,
                              name="winmm-io")
_io_thread.start()


def run_io(fn, timeout=None):
    """fn 在 winmm 专线上串行执行，调用方至多等 timeout 秒（None=模块默认
    IO_TIMEOUT，调用时求值便于测试桩改写）。返回 (done, value)：
    done=False=超时（fn 仍会迟到执行，返回值弃置——需要「迟到自知」的
    操作在 fn 里读共享 state，见 open_in）。等到了异常则原样抛出。
    专线线程内重入直接执行（否则自己等自己=必然超时假象）。"""
    if timeout is None:
        timeout = IO_TIMEOUT
    if threading.current_thread() is _io_thread:
        return True, fn()
    with _io_stuck_lock:
        stuck = _io_stuck
    if stuck:
        # 毒化熔断：一次真硬挂死的 midiInClose（Win11 进程级锁）会永久占住
        # 专线，此后每个操作都注定等满超时——主线程侧（踩钉 10s 重试/
        # _apply_ports/_on_exit）改为立即快速失败，直到挂死 job 迟到了结
        # 自动解除。锁被占住期间 winmm 本就无可成功操作，语义不变
        return False, None
    done = threading.Event()
    box = {}

    def job():
        try:
            box["r"] = fn()
        except BaseException as e:
            box["e"] = e
        finally:
            done.set()

    _io_q.put(job)
    if not done.wait(timeout):
        with _io_stuck_lock:
            globals()["_io_stuck"] = True
        return False, None
    if "e" in box:
        raise box["e"]
    return True, box.get("r")


def open_in(idx, proc, timeout=None):
    """在专线 midiInOpen+Start（限时等待）。返回 (HANDLE, None) 或
    (None, 错误文案)。超时弃单：job 迟到执行后自查弃单标志，即开即回收，
    绝不泄漏已打开的句柄。"""
    if timeout is None:
        timeout = IO_TIMEOUT
    h = wintypes.HANDLE()
    state = {"dead": False}
    result = {}

    def job():
        r = _winmm.midiInOpen(ctypes.byref(h), idx, proc, 0, _CALLBACK_FUNC)
        if r:
            result["err"] = "midiInOpen 失败（code %d，端口被占用？）" % r
            return
        _winmm.midiInStart(h)
        if state["dead"]:       # 等待方已放弃：即开即回收
            _winmm.midiInStop(h)
            _winmm.midiInReset(h)
            _winmm.midiInClose(h)
            result["err"] = "打开超时（%.0f 秒，弃单已回收）" % timeout
            return
        result["ok"] = True

    done, _ = run_io(job, timeout)
    if not done:
        state["dead"] = True
        run_io(lambda: None, 0.5)   # 宽限弃单回收（job 迟到完成时置 err）
    if result.get("ok"):
        return h, None
    return None, result.get("err") or "打开超时（%.0f 秒）" % timeout


_close_guard = []           # 已收尾的句柄对象：close_in 幂等守卫。存对象
_close_guard_lock = threading.Lock()   # 本身（c_void_p 不可哈希），顺带持
# 引用防 id 复用误判——句柄数值会被系统回收复用，绝不能按数值判重


def close_in(h):
    """在专线按官方收尾序 Stop→Reset→Close 关输入口（限时）。超时弃置：
    闭包持句柄，专线迟到后照常收尾；调用方（含主线程）即刻返回。
    幂等守卫：对象层 close() 虽有 _h 置空，但两线程并发 close 可同时读
    到同一句柄；句柄值被系统回收复用后二次 close 会误关无关新口。"""
    with _close_guard_lock:
        if any(g is h for g in _close_guard):
            return
        _close_guard.append(h)

    def job():
        _winmm.midiInStop(h)
        _winmm.midiInReset(h)
        _winmm.midiInClose(h)

    run_io(job)


def _in_devices():
    # 用 A 版查询：W 版对 teVirtualMIDI/Rubix 等驱动返回 INVALPARAM，名字拿不到
    out = []
    for i in range(_winmm.midiInGetNumDevs()):
        c = _InCaps()
        if _winmm.midiInGetDevCapsA(ctypes.c_uint(i), ctypes.byref(c),
                                    ctypes.sizeof(c)) == 0:
            out.append((i, c.szPname.decode("mbcs")))
    return out


def _out_devices():
    out = []
    for i in range(_winmm.midiOutGetNumDevs()):
        c = _OutCaps()
        if _winmm.midiOutGetDevCapsA(ctypes.c_uint(i), ctypes.byref(c),
                                     ctypes.sizeof(c)) == 0:
            out.append((i, c.szPname.decode("mbcs")))
    return out


class MidiIn:
    """winmm MIDI 输入口；on_note(note, vel) 只收 Note On(vel>0)，
    on_clock() 收走带脉冲（MIDI Clock F8 / MTC 四分之一帧 F1）。"""

    def __init__(self, name_sub, on_note, on_clock=None):
        found = _pick(_in_devices(), name_sub)
        if found is None:
            sys.exit("没找到 %s 端口：请先安装并运行 loopMIDI 建一个虚拟端口"
                     % name_sub)
        idx, self.name = found
        self._on_note = on_note
        self._on_clock = on_clock
        self._cb = _Proc(self._dispatch)   # 必须持有引用，防止回调被 GC
        h, err = open_in(idx, self._cb)    # 专线上限时打开（主线程不陪葬）
        if h is None:
            sys.exit(err)
        self._h = h

    def _dispatch(self, h, msg, inst, p1, p2):
        if msg != _MIM_DATA:
            return
        status = p1 & 0xFF
        if status in (0xF1, 0xF8):
            if self._on_clock:
                self._on_clock()
        elif status & 0xF0 == 0x90 and (p1 >> 16) & 0xFF:
            self._on_note((p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF)

    def close(self):
        """官方收尾序（微软/JUCE 口径）：Stop 切断本句柄投递 → Reset 兜底
        → Close。裸 Close 在回调密集时（F8 流 ~20ms 一个）与回调执行存在
        竞态窗口，且 Win11 的 close 走进程级锁，最坏挂死后续 open/close。
        在 winmm 专线上限时执行：挂死时调用方（含主线程）3 秒即返，专线
        迟到完成收尾；幂等（二次 close 无操作）。"""
        h = self._h
        self._h = None
        if h:
            close_in(h)


class TransportSync:
    """跟随 Cubase 走带同步视频：收到走带脉冲=在播放；脉冲断流=暂停/停止。
    首个脉冲到来才启用——没开时钟发送的工程行为与旧版完全一致（不会误暂停）。
    video_state: playing(在播) / paused(被走带暂停) / stopped(未触发或已熄屏)。
    回调线程只记账，所有 OBS 操作都在看门狗线程做（winmm 回调里禁网络）。"""

    def __init__(self, ctl, on_event=None):
        self.ctl = ctl
        self.on_event = on_event or (lambda msg: None)
        self._enabled = False
        self._last_pulse = 0.0
        self._last_note = 0.0
        self.video_state = "stopped"
        self.current_video = None   # 最近成功触发的视频文件名（仅展示用）
        self._lock = threading.Lock()

    def is_following(self):
        return self._enabled

    def set_state(self, state):
        with self._lock:
            self.video_state = state

    def note_seen(self):
        with self._lock:
            self._last_note = time.time()

    def on_clock(self):
        with self._lock:
            self._enabled = True
            self._last_pulse = time.time()

    def reset(self):
        """跟随基线复位（时钟端口热切换时调用）：跟随态与脉冲计时清零，
        三态回「未播放」——旧口断流不再卡「已暂停」误导互锁/全停。
        video_state 不动：OBS 视频保持现场（在播不误停、暂停不被误拉起），
        新口时钟到来后按实况对齐。"""
        with self._lock:
            self._enabled = False
            self._last_pulse = 0.0

    def poll(self):
        with self._lock:
            enabled, live = self._enabled, (
                time.time() - self._last_pulse < CLOCK_TIMEOUT)
        if not enabled:
            return
        if not live and self.video_state == "playing":
            if self.ctl.pause_media():
                self.set_state("paused")
                self.on_event("走带暂停 → 视频已暂停")
        elif live and self.video_state == "paused":
            self._resume_if_worth()

    def _resume_if_worth(self):
        """恢复前先问 OBS 实况：已播完/已停/出错的视频不复活——PLAY 会把
        播完的视频从头再放一遍盖在画面上；只有真在暂停态才继续。
        刚收到触发音符（磁吸对齐音符起播的瞬间）则不恢复，切换归音符管。
        OBS 查不到（重启中）就等下一轮轮询。"""
        with self._lock:
            if time.time() - self._last_note < NOTE_SUPPRESS:
                return
        st = self.ctl.media_state()
        if st == "OBS_MEDIA_STATE_PAUSED":
            if self.ctl.resume_media():
                self.set_state("playing")
                self.on_event("走带恢复 → 视频继续")
        elif st == "OBS_MEDIA_STATE_PLAYING":
            self.set_state("playing")   # 实况在播，对齐本地标记
        elif st is not None:
            self.set_state("stopped")
            self.on_event("视频已播完或停止，不随走带恢复")


def note_handler(ctl, sync, report=print):
    """音符→OBS 动作的标准处理器；report 输出事件行（print 或 GUI 日志）。
    winmm 回调线程只记带入队，OBS 网络动作由内部串行工作线程执行——
    回调里做网络会拖住同一端口的时钟投递（winmm 按序回调），时钟停
    超 CLOCK_TIMEOUT 就误判暂停；连发积压时只执行最新的一个触发。"""
    q = queue.Queue()

    def on_note(note, vel):
        if note in NOTE_MAP:
            sync.note_seen()
            q.put(note)

    def worker():
        while True:
            note = q.get()
            try:
                while True:             # 倒掉积压：切换归最新音符管
                    try:
                        note = q.get_nowait()
                    except queue.Empty:
                        break
                target = NOTE_MAP[note]
                if target is None:
                    ok = ctl.stop_media()
                    if ok:
                        sync.set_state("stopped")
                        sync.current_video = None
                else:
                    ok = ctl.set_media(target, False)
                    if ok:
                        sync.set_state("playing")
                        sync.current_video = target
                report("音符 %d → %s%s" % (
                    note, "熄屏" if target is None else target,
                    "" if ok else " 失败：%s" % ctl.last_error))
            except Exception as e:
                # 工作线程绝不允许静默死亡（死=VJ 触发全场失效且零日志）
                try:
                    report("音符处理异常（已恢复）：%s: %s"
                           % (type(e).__name__, e))
                except Exception:
                    pass
    threading.Thread(target=worker, daemon=True).start()
    return on_note


def load_obs_cfg():
    if getattr(sys, "frozen", False):   # PyInstaller exe：配置放 exe 同目录
        here = os.path.dirname(sys.executable)
    else:
        here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "config.json")
    if not os.path.exists(path):
        sys.exit("找不到 %s：请把 config.json 和程序放在一起" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["obs"]


def send_note(idx, note, vel=127):
    h = wintypes.HANDLE()
    r = _winmm.midiOutOpen(ctypes.byref(h), idx, _Proc(), 0, 0)
    if r:
        sys.exit("midiOutOpen 失败（code %d）" % r)
    _winmm.midiOutShortMsg(h, 0x90 | (note << 8) | (vel << 16))
    _winmm.midiOutClose(h)


def _loopmidi_exe():
    """安装路径：优先开机自启 Run 键（=本机真实位置），退回约定位置。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            exe = winreg.QueryValueEx(k, "loopMIDI")[0]
        if os.path.exists(exe):
            return exe
    except OSError:
        pass
    for p in (r"C:\Program Files (x86)\Tobias Erichsen\loopMIDI\loopMIDI.exe",
              r"C:\Program Files\Tobias Erichsen\loopMIDI\loopMIDI.exe"):
        if os.path.exists(p):
            return p
    return None


def loopmidi_ports():
    """loopMIDI 已配置的全部虚拟端口名（读它自己的注册表名单，建口/改名
    即时反映；loopMIDI 未装或名单读不到返回空集）。名单是 Ports 键下的
    值（值名=端口名），不是子键。"""
    names = set()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Tobias Erichsen\loopMIDI\Ports") as k:
            i = 0
            while True:
                try:
                    names.add(winreg.EnumValue(k, i)[0])
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return names


def ensure_loopmidi(hint=PORT_HINT):
    """端口不存在时拉起 loopMIDI 程序，等虚拟端口出现。启动方式与
    obs_ctrl 拉起 OBS 一致：launch_detached（ShellExecuteW），路径来自
    本机注册表/约定位置，不含外部输入。"""
    if _pick(_in_devices(), hint):
        return
    exe = _loopmidi_exe()
    if exe is None:
        sys.exit("没有 %s 端口也找不到 loopMIDI.exe："
                 "请先安装 loopMIDI（tobias-erichsen.de/software/loopmidi.html）"
                 % hint)
    print("loopMIDI 未运行，正在拉起…", flush=True)
    r = launch_detached(exe)
    if r <= 32:
        sys.exit("拉起 loopMIDI 失败（ShellExecute 代码 %s）" % r)
    deadline = time.time() + 15
    while time.time() < deadline:
        if _pick(_in_devices(), hint):
            return
        time.sleep(0.5)
    sys.exit("loopMIDI 已拉起但 15 秒内没出现 %s 端口；"
             "请打开 loopMIDI 检查端口列表是否为空" % hint)


def main():
    ensure_loopmidi()
    if len(sys.argv) == 3 and sys.argv[1] == "--send":
        note = int(sys.argv[2])
        hit = _pick(_out_devices())
        if hit is None:
            sys.exit("没找到 %s 输出端口" % PORT_HINT)
        send_note(hit[0], note)
        print("已发 note %d（映射 %s）" % (note, NOTE_MAP.get(note)), flush=True)
        return
    ctl = ObsController(load_obs_cfg())
    sync = TransportSync(ctl, on_event=lambda m: print(m, flush=True))
    port = MidiIn(PORT_HINT, note_handler(ctl, sync), on_clock=sync.on_clock)
    ctl.enabled = True
    ctl.start()
    threading.Thread(target=_watchdog, args=(sync,), daemon=True).start()
    print("监听 %s：%s（Ctrl+C 退出）" % (port.name, NOTE_MAP), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        port.close()


def _watchdog(sync):
    while True:
        sync.poll()
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
