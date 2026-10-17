# -*- coding: utf-8 -*-
"""主线程停摆黑匣子 + 跨线程指令队列治理（两套 GUI 共用）。

停摆家族机理（审计定案）：Tk 主线程被单个操作卡住 → 消息泵停转（不收
WM_PAINT=深色窗黑面、不泵 after=50ms 指令排水停）→ APP 指令全部积压于
无界队列 → 阻塞超时到期后一次性排空（黑面恢复+积压集中执行）。此前
出了事只有用户肉眼，没有日志能回答「当时卡在哪个调用、队列里压了多少」——
黑匣子把每次停摆的现场（时长/正在执行的回调/队列深度/丢弃数）落盘。
"""
import ctypes
import faulthandler
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes


class StallWatchdog:
    """心跳由主线程轮询循环喂（_tick/_drain_calls 每轮更新时间戳）；守护
    线程发现停摆超阈值即写现场到文件，停摆期间每 5 秒追一条，恢复补总结行
    并回调 notify（进 GUI 日志，恢复后可见）。文件按 crash.log 同款轮转。"""

    def __init__(self, heartbeat, snapshot, path, notify=None,
                 threshold=3.0, period=1.0, clock=time.monotonic):
        self._hb = heartbeat            # fn() → 主线程最近心跳 monotonic
        self._snap = snapshot           # fn() → dict 现场证据
        self._path = str(path)
        self._notify = notify or (lambda msg: None)
        self._threshold = threshold
        self._period = period
        self._clock = clock
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="stall-watchdog")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _write(self, line):
        # 轮转：>512KB 换 .1（crash.log 同款）；任何异常都不能杀死守护线程
        try:
            p = pathlib.Path(self._path)
            try:
                if p.exists() and p.stat().st_size > 512 * 1024:
                    p.replace(p.with_name(p.name + ".1"))
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _loop(self):
        in_stall = False
        last_write = 0.0
        stall_start = 0.0
        while not self._stop.is_set():
            self._stop.wait(self._period)
            if self._stop.is_set():
                return
            now = self._clock()
            try:
                hb = self._hb()
                snap = self._snap()
            except Exception:
                continue        # 心跳/快照查询失败：绝不杀死黑匣子线程
            stall = now - hb
            if stall > self._threshold:
                if not in_stall:
                    in_stall = True
                    stall_start = hb
                    last_write = now
                    self._write("%s 停摆 %.1fs：正在执行=%s 现场=%s"
                                % (time.strftime("%m-%d %H:%M:%S"), stall,
                                   snap.get("tag") or "（循环间隙）", snap))
                elif now - last_write >= 5.0:
                    last_write = now
                    self._write("%s 仍在停摆（已 %.1fs）：正在执行=%s 现场=%s"
                                % (time.strftime("%m-%d %H:%M:%S"),
                                   now - stall_start,
                                   snap.get("tag") or "（循环间隙）", snap))
            elif in_stall:
                in_stall = False
                total = hb - stall_start
                self._write("%s 恢复：本次停摆共 %.1fs"
                            % (time.strftime("%m-%d %H:%M:%S"), total))
                try:
                    self._notify("主线程曾停摆 %.1f 秒（详见 stall.log）"
                                 % total)
                except Exception:
                    pass


class StackSentinel:
    """C 级主线程栈哨兵（faulthandler 计时窗）：主线程每次喂心跳即取消旧
    窗重装新窗；真停摆=不再喂，C 级看门狗线程到窗即把全部线程栈落盘并按
    窗连拍，恢复喂心跳即断。与 StallWatchdog 互补：后者出墙钟时间线与
    队列现场，但它是 Python 线程——主线程若持 GIL 卡死会一并饿死且拿不
    到栈；本哨兵转储不取 GIL，恰补该盲区（一次停摆即裁决卡点）。窗宽与
    watchdog 阈值对齐，两份日志时间线互照。"""

    def __init__(self, path, window=3.0):
        self._window = window
        self._f = None
        try:
            # 启动期轮转（crash.log 同款）：运行期句柄常驻，Windows 下
            # replace 会失败，故只在本构造点轮转；停摆连拍为 KB 级，够用
            p = pathlib.Path(path)
            if p.exists() and p.stat().st_size > 512 * 1024:
                p.replace(p.with_name(p.name + ".1"))
            self._f = open(p, "a", encoding="utf-8")
        except OSError:
            self._f = None            # 与 watchdog 同哲学：黑匣子自坏不伤主程序

    def feed(self):
        if self._f is None:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(self._window, file=self._f,
                                              repeat=True)
        except Exception:
            pass

    def stop(self):
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        f, self._f = self._f, None    # 置空在前：stop 后 feed 恒 no-op，
        try:                          # 防重装已关闭 fd（fd 号会被系统复用）
            if f is not None:
                f.close()
        except Exception:
            pass


class StallNativeProbe:
    """停摆自愈哨兵 + 原生取证（StallWatchdog 之外的独立守护线程）。

    机理（10-17 实验室复现定案）：停摆中主线程原生栈停在
    NtUserMsgWaitForMultipleObjectsEx → Tcl_WaitForEvent ——它空闲等在
    消息泵里，Tcl 定时器（after 50ms 排水链）到期却不醒；点击窗口=注入
    消息=唯一可靠唤醒源，这就是「点一下本软件才恢复」的根因。消息投递
    通道与定时器通道正交且从未失效，故本哨兵双管齐下：

    ① 自愈：心跳停滞 >poke 阈值即向主窗 PostMessageW(WM_NULL)（惰性消息，
       忙/闲皆无害），每周期补投直到心跳恢复——把用户可感的停摆从 5-10s
       压到 ~1s；「首针即愈」同时就是根因指纹（定时唤醒缺失，非卡调用）。
    ② 取证：停滞超取证阈值（与 StallWatchdog 对齐）才升级——记录前台窗口
       PID+标题（客观锚定「失焦才停摆」）+ py-spy --native 全线程原生栈
       （faulthandler 只有 Python 帧）。证据落 stall_native.log。

    py-spy 路径：CSM_PYSPY 环境变量 > PATH > 解释器旁 Scripts；缺失则只
    自愈不取证。全部采样自坏不伤主程序（try/except 全包）。"""

    def __init__(self, heartbeat, main_hwnd, path,
                 threshold=3.0, poke=0.7, period=0.25, clock=time.monotonic):
        self._hb = heartbeat
        self._hwnd = main_hwnd
        self._path = str(path)
        self._threshold = max(threshold, poke)   # 取证永不低于自愈阈值
        self._poke = poke
        self._period = period
        self._clock = clock
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True,
                         name="stall-native-probe").start()
        return self

    def stop(self):
        self._stop.set()

    def _write(self, text):
        try:
            p = pathlib.Path(self._path)
            try:
                if p.exists() and p.stat().st_size > 512 * 1024:
                    p.replace(p.with_name(p.name + ".1"))
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(text.rstrip("\n") + "\n")
        except OSError:
            pass

    @staticmethod
    def _find_pyspy():
        exe = os.environ.get("CSM_PYSPY")
        if exe and os.path.isfile(exe):
            return exe
        exe = shutil.which("py-spy")
        if exe:
            return exe
        cand = pathlib.Path(sys.executable).with_name("Scripts") / "py-spy.exe"
        return str(cand) if cand.is_file() else None

    @staticmethod
    def _foreground():
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetForegroundWindow()
            if not hwnd:
                return "（无前台窗口）"
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, buf, 256)
            return "PID %s「%s」hwnd=0x%X" % (pid.value, buf.value or "?", hwnd)
        except Exception as e:
            return "查询失败 %s" % type(e).__name__

    def _pyspy_dump(self):
        exe = self._find_pyspy()
        if not exe:
            return "（py-spy 不可用：设 CSM_PYSPY 或 pip install py-spy）"
        try:
            r = subprocess.run(
                [exe, "dump", "--pid", str(os.getpid()), "--native"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20)
            out = (r.stdout or "").rstrip("\n")
            if (r.stderr or "").strip():
                out += "\n[stderr] " + r.stderr.strip()
            return out
        except Exception as e:
            return "（py-spy 失败：%s: %s）" % (type(e).__name__, e)

    def _loop(self):
        u32 = ctypes.windll.user32
        in_stall = False
        dumped = False
        pokes = 0
        poke_t = None
        stall_start = 0.0
        while not self._stop.is_set():
            self._stop.wait(self._period)
            if self._stop.is_set():
                return
            try:
                hb = self._hb()
            except Exception:
                continue
            now = self._clock()
            stall = now - hb
            if stall <= self._poke:
                if in_stall:
                    in_stall = False
                    since = ("，WM_NULL×%d 后 %.2fs 恢复"
                             % (pokes, hb - poke_t)) if poke_t else ""
                    self._write("%s 恢复：本次停摆约 %.1fs%s"
                                % (time.strftime("%m-%d %H:%M:%S"),
                                   hb - stall_start, since))
                dumped = False
                pokes = 0
                poke_t = None
                continue
            if not in_stall:
                in_stall = True
                stall_start = hb
            if stall > self._threshold and not dumped:
                dumped = True
                self._write("==== %s 停摆 %.1fs：前台=%s"
                            % (time.strftime("%m-%d %H:%M:%S"), stall,
                               self._foreground()))
                self._write(self._pyspy_dump())
            if stall > self._poke and now - (poke_t or 0.0) >= self._period:
                try:
                    hwnd = self._hwnd() or 0
                except Exception:
                    hwnd = 0
                if hwnd:
                    u32.PostMessageW(hwnd, 0, 0, 0)     # WM_NULL：惰性探针
                    pokes += 1
                    poke_t = self._clock()


class BoundedCallQueue(queue.Queue):
    """有界跨线程指令队列：满了丢最旧并计数。主线程停摆期间无界积压会在
    恢复后一口气全执行（APP 连点 N 下「下一首」=连切 N 首）；演出语义下
    执行陈旧指令比丢弃更伤。丢弃计数经 snapshot 进黑匣子。
    只重载 put 一个漏斗：queue.Queue.put_nowait 内部就是 self.put(item,
    block=False)，两个入口天然都走这里；恒不阻塞（block/timeout 仅保
    签名兼容，语义=丢最旧换位）。"""

    def __init__(self, cap=200):
        super().__init__(maxsize=cap)
        self.dropped = 0

    def put(self, item, block=True, timeout=None):
        while True:
            try:
                return queue.Queue.put(self, item, block=False)
            except queue.Full:
                try:
                    self.get_nowait()       # 丢最旧
                    self.dropped += 1
                except queue.Empty:
                    pass                    # 消费侧刚拿走：重试即成


def coalesce(batch, names=None):
    """一次排空批次内合流重复回调：保留最后一次出现，顺序不变。
    names=None=全部合流（单元测试用）；GUI 传幂等回调名集合（{"_refresh",
    "_persist_web_remote"}）——有状态指令（_next/_prev 等）不在名单内，
    同批连发两次就是两次，永不丢（复审 L2：绑定方法按引用相等，无差别
    合流会吞掉 50ms 内的合法连发）。不同 lambda 对象永不相等，不误伤。"""
    if len(batch) < 2:
        return batch
    last = {fn: i for i, fn in enumerate(batch)}
    keep = []
    for i, fn in enumerate(batch):
        coalescable = names is None or getattr(fn, "__name__", "") in names
        keep.append(last[fn] == i or not coalescable)
    return [fn for i, fn in enumerate(batch) if keep[i]]
