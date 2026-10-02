# -*- coding: utf-8 -*-
"""平板端踩钉转发模拟器（黑盒测试专用）：PedalForwarder.kt + PedalBatcher.kt
的逐行为 Python 镜像，经真实 HTTP 线缆驱动 PC 端 /pedal/event。

与 Kotlin 的对应关系（按组件名；两侧改动须同步）：
  DEVICE/FLUSH_MS/GUARD_MS/MAX_AGE_MS/HB_MS/RETRY_MS → 同名常量
  PedalBatcher（@Synchronized 打包/切块/guard）      → _buf/_pending_down + _flush
  onKey（白名单双域：keyCode 或 scanCode）            → on_key
  scheduleFlush/flush（40ms 静默窗；≤32 条/跨度≤5000ms 分块；
  dt=距块内最大时戳偏移；et=块内最大时戳）             → _schedule_flush/_flush
  armGuard/cancelGuard（DOWN 1s 无 UP 补合成 up）     → _guard_cb
  hbTask（5s 心跳；403 期间照发——电脑端开回即自愈）   → _hb_loop
  ensureWorker（单 worker 串行；finally 自清 running） → _ensure_worker/_worker_loop
  send（200 清 403 标志；403=停发+透传等待；4xx 且≠429 丢弃；
  429/网络/5xx 重试；年龄>2s 丢弃）                    → _send
  postOnce（POST /pedal/event，1.5s 超时，异常→None）  → _post_once
  setEnabled（True 清 403 标志+重启心跳；False 清在途） → set_enabled
  APP 进程死亡/重启（seq 归 1、队列/guard 全灭）        → app_restart

协议：{device, seq, et, events:[{vk,kc,down,dt}]} / {device, seq, hb:true}。
vk=scanCode（Linux 键码=getScanCode 实况）、kc=keyCode（Android 键码兜底）、
dt=距块内最大时戳毫秒偏移、et=块内最大时戳（APP 单调毫秒，PC 跨包重定基准）。

键码命名空间（AOSP《Keyboard devices》
source.android.com/docs/core/interaction/input/keyboard-devices）：
内核 hid-input 把 HID usage 映射成 linux/input.h 的 KEY_* 码，Android 把该
Linux 码原样作为 KeyEvent.getScanCode() 上报，HID usage 本身不进框架。
因此真实踏板在 wire 上的 vk 是 LKC（如 Scan Next=163），PC 端按 LKC 表
映射 VK；kc 兜底覆盖个别 scanCode 报 0 的栈。
"""
import http.client
import json
import queue
import random
import socket
import threading
import time
import uuid

DEVICE = "pedal-usb"
FLUSH_MS = 40            # UP 后 40ms 静默窗把并走带突发打成一包
GUARD_MS = 1000          # DOWN 后 1s 未见 UP 补合成 up，防 PC 端悬挂
MAX_AGE_MS = 2000        # 包龄超 2s 丢弃——陈旧走带动作重放比丢弃危险
HB_MS = 5000             # 5s 心跳（PC 健康显示数据源，link 判据 15s）
RETRY_MS = 200           # 重试间隔（> per-device 20ms 限流窗，留裕度）

# 键位白名单（Android keycode 域）：媒体键/翻页键/方向键/编辑键/F1-12。
# 不含音量键（24/25/164）——转发开启时平板自身硬件音量必须保留；
# 不含字母数字/小键盘（131-142 只到 F12；143-154 是 NumLock/小键盘，
# 真 F13-24 键码 326-337 自 API 30/Android 11 起）；不含 89/90/23（PC 表无
# 映射，吞了=两端全灭）；不含退格 67（防真实键盘打字被吞）。
PEDAL_KEYS = frozenset(
    {85, 86, 87, 88, 126, 127,
     92, 93, 61, 66, 111, 62, 122, 123, 121,
     19, 20, 21, 22, *range(131, 143)})
# LKC 183-194（F13-F24）：无默认 Android 键码映射（keyCode=0），按 scanCode 放行
PEDAL_SCANS = range(183, 195)

# AOSP keyboard-devices 码表节选：踏板真实会发的键。
# 每项 (HID usage, LKC=KeyEvent.getScanCode(), AKC=KeyEvent.getKeyCode())。
# LKC 一列即 linux/input-event-codes.h：KEY_NEXTSONG=163、KEY_PLAYPAUSE=164、
# KEY_PREVIOUSSONG=165、KEY_STOPCD=166、KEY_PAGEUP=104、KEY_PAGEDOWN=109、
# KEY_ENTER=28、KEY_TAB=15、KEY_SPACE=57、KEY_F1=59、KEY_F13=183。
KEYS = {
    "play_pause": (0xCD, 164, 85),   # Consumer Play/Pause → MEDIA_PLAY_PAUSE
    "next":       (0xB5, 163, 87),   # Consumer Scan Next → MEDIA_NEXT
    "prev":       (0xB6, 165, 88),   # Consumer Scan Prev → MEDIA_PREVIOUS
    "stop":       (0xB7, 166, 86),   # Consumer Stop → MEDIA_STOP
    "play":       (0xB0, 207, 126),  # Consumer Play → MEDIA_PLAY（LKC 207 无
                                      #   VK 对应 → 走 AKC 126 兜底，测兜底表）
    "pause":      (0xB1, 119, 121),  # Consumer Pause → KEY_PAUSE(119) →
                                      #   KEYCODE_BREAK(121)（Generic.kl 实况）
    "mute":       (0xE2, 113, 164),  # Consumer Mute → VOLUME_MUTE（白名单外）
    "vol_up":     (0xE9, 115, 24),   # Consumer Vol+ → VOLUME_UP（白名单外）
    "vol_down":   (0xEA, 114, 25),   # Consumer Vol- → VOLUME_DOWN（白名单外）
    "pageup":     (0x4B, 104, 92),   # Keyboard PageUp → PAGE_UP
    "pagedown":   (0x4E, 109, 93),   # Keyboard PageDown → PAGE_DOWN
    "enter":      (0x28, 28, 66),    # Keyboard Enter → ENTER
    "tab":        (0x2B, 15, 61),    # Keyboard Tab → TAB
    "space":      (0x2C, 57, 62),    # Keyboard Space → SPACE
    "esc":        (0x29, 1, 111),    # Keyboard Esc → ESCAPE
    "f1":         (0x3A, 59, 131),   # Keyboard F1 → F1
    "f13":        (0x68, 183, 0),    # Keyboard F13 → keyCode=0（按 scanCode 放行）
}


class Ev:
    """PedalBatcher.Ev：缓冲条目 (vk=scanCode, kc=keyCode, down, t=毫秒)。"""

    def __init__(self, vk, kc, down, t):
        self.vk, self.kc, self.down, self.t = vk, kc, down, t


class Pkt:
    """发送队列条目 (t=入队时刻毫秒, body=JSON 串, seq)。"""

    def __init__(self, t, body, seq):
        self.t, self.body, self.seq = t, body, seq


class TabletPedalSim:
    """PedalForwarder+PedalBatcher 的进程级镜像。捕获侧（on_key）对应
    Kotlin 无障碍主线程——Python 无主线程约定，用 _cap_lock 串行（语义
    等价：边沿不丢、顺序不变）。发送侧单 worker 串行，与 Kotlin 相同。"""

    def __init__(self, host, port, device=DEVICE, jitter=None, seed=None):
        self._host, self._port = host, port
        self._device = device
        self._jitter = jitter            # (lo, hi) 秒：每包首发前随机延迟
        self._rng = random.Random(seed)
        # ---- 捕获侧（PedalBatcher 状态 + guard/flush 调度）----
        self._cap_lock = threading.Lock()
        self._buf = []                   # buf: ArrayList<Ev>
        self._pending_down = {}          # pendingDown: HashMap<Int, Ev>
        self._guards = {}                # guard 句柄（可取消）
        self._flush_pending = False
        self._flush_timer = None
        # ---- 发送侧 ----
        self._q = queue.Queue()          # q: LinkedBlockingQueue<Pkt>
        self._seq = 0                    # seq: AtomicLong（app_restart 归零）
        self._sid = str(uuid.uuid4())[:8]  # 会话标识（app_restart 换新；
                                           #   Kotlin UUID 字符串前 8 位同风格）
        self._seq_lock = threading.Lock()
        self._worker = None
        self._running = False
        # ---- 开关 ----
        self.enabled = True
        self.disabled_by_pc = False
        self.ready = True
        # ---- 心跳 ----
        self._hb_gen = 0
        self._hb_thread = None
        # ---- 故障注入（测试专用，非 Kotlin 行为）----
        self._blackhole = 0        # 接下来 N 次 POST 连接层丢包（包未达 PC）
        self._drop_resp = 0        # 接下来 N 次 POST 响应在途丢失（PC 已处理）
        self._stall_once = 0.0     # 下一次 POST 前停顿（模拟到达延迟/积压突发）
        # ---- 观测 ----
        self.attempts = []         # 每次 HTTP 尝试 {seq, code, resp, exc, t}
        self.drops = []            # report() 镜像：客户端侧丢弃/停发原因
        self._stopped = False

    @staticmethod
    def _now_ms():
        """SystemClock.uptimeMillis 镜像（boot 单调毫秒；APP 重启不回跳）。"""
        return time.monotonic() * 1000.0

    # ---- 捕获侧（PedalForwarder.onKey 镜像）----

    def on_key(self, key_code, scan_code, down, event_time_ms=None,
               virtual_device=False):
        """无障碍 onKeyEvent。key_code=Android keyCode（白名单域之一），
        scan_code=上线缆的 vk（Linux 键码）。白名单双域：keyCode 命中或
        scanCode 命中（F13-F24 走后者）皆放行；scanCode=0 时靠 kc 兜底。"""
        if event_time_ms is None:
            event_time_ms = self._now_ms()
        with self._cap_lock:
            if not self.enabled or self.disabled_by_pc or not self.ready:
                return False                  # 电脑端关闭 → 透传给前台 App
            if virtual_device:
                return False                  # 输入法/屏幕按键
            if key_code not in PEDAL_KEYS and scan_code not in PEDAL_SCANS:
                return False                  # 白名单双域
            if down:
                ev = Ev(scan_code, key_code, True, event_time_ms)
                self._buf.append(ev)
                self._pending_down[scan_code] = ev
                self._guards[scan_code] = True
                t = threading.Timer(GUARD_MS / 1000.0,
                                    lambda: self._guard_cb(scan_code))
                t.daemon = True
                t.start()
            else:
                self._pending_down.pop(scan_code, None)
                self._buf.append(Ev(scan_code, key_code, False, event_time_ms))
            self._schedule_flush()
        return True                           # 拦截：键不下发给前台 App

    def _guard_cb(self, scan_code):
        """guardFire 镜像：1s 无 UP 补合成 up（t=当下，kc 沿用在途 down 的
        值——与 Kotlin PedalBatcher.guardFire 的 ev.kc 一致）。"""
        with self._cap_lock:
            if self._stopped or self._guards.get(scan_code) is None:
                return
            del self._guards[scan_code]
            ev = self._pending_down.pop(scan_code, None)
            if ev is not None:
                self._buf.append(Ev(ev.vk, ev.kc, False, self._now_ms()))
                self._schedule_flush()

    # ---- 打包（PedalBatcher.flush 镜像：≤32 条/跨度≤5000ms 分块）----

    def _schedule_flush(self):
        if self._flush_pending:
            return
        self._flush_pending = True
        self._flush_timer = threading.Timer(FLUSH_MS / 1000.0, self._flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush(self):
        with self._cap_lock:
            if self._stopped:
                return
            self._flush_pending = False
            if not self._buf:
                return
            events_all = list(self._buf)
            self._buf.clear()
            blocks = []                       # [(events, et=块内 max 时戳)]
            chunk, min_t = [], float("inf")
            for e in events_all:
                if len(chunk) >= 32 or (chunk and e.t - min_t > 5000):
                    blocks.append((chunk, max(x.t for x in chunk)))
                    chunk, min_t = [], float("inf")
                chunk.append(e)
                min_t = min(min_t, e.t)
            if chunk:
                blocks.append((chunk, max(x.t for x in chunk)))
            for evs, et in blocks:
                events = [{"vk": e.vk, "kc": e.kc, "down": e.down,
                           "dt": int(et - e.t)} for e in evs]
                with self._seq_lock:
                    self._seq += 1
                    seq = self._seq
                body = json.dumps({"device": self._device, "seq": seq,
                                   "sid": self._sid, "et": int(et),
                                   "events": events})
                self._q.put(Pkt(self._now_ms(), body, seq))
        self._ensure_worker()

    # ---- 心跳（hbTask 镜像：403 期间照发——电脑端开回即自愈）----

    def start_heartbeat(self):
        self._start_hb()

    def _start_hb(self):
        self._hb_gen += 1
        gen = self._hb_gen
        if self._hb_thread is None or not self._hb_thread.is_alive():
            self._hb_thread = threading.Thread(target=self._hb_loop,
                                               args=(gen,), daemon=True)
            self._hb_thread.start()

    def _hb_loop(self, gen):
        next_t = time.monotonic() + HB_MS / 1000.0   # 首发延后 HB_MS
        while gen == self._hb_gen and not self._stopped:
            if time.monotonic() >= next_t:
                if not self.enabled:
                    return                # 开关关闭：心跳停（setEnabled 重启）
                self._enqueue_hb()
                next_t = time.monotonic() + HB_MS / 1000.0
            time.sleep(0.02)

    def _enqueue_hb(self):
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        body = json.dumps({"device": self._device, "seq": seq,
                           "sid": self._sid, "hb": True})
        self._q.put(Pkt(self._now_ms(), body, seq))
        self._ensure_worker()

    # ---- 开关（setEnabled 镜像）----

    def set_enabled(self, v):
        self.enabled = v
        if v:
            self.disabled_by_pc = False                 # 重新开启即清停发
            self._start_hb()
            self._ensure_worker()
        else:
            with self._cap_lock:
                self._buf.clear()
                self._pending_down.clear()
                self._guards.clear()
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                    self._flush_timer = None
                self._flush_pending = False
            with self._q.mutex:
                self._q.queue.clear()

    def reenable(self):
        """APP 端重新打开开关（setEnabled(True)）。"""
        self.set_enabled(True)

    # ---- APP 重启（进程死亡 → 服务重建；非 Kotlin 单行，对应生命周期）----

    def app_restart(self):
        """进程被杀重启：内存态归零——seq 从 1 重新计数、未发队列/guard/
        403 停发标志全部消失。PC 端对此不可见（无任何重启信令）。"""
        self._hb_gen += 1
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=0.5)
            self._worker = None
        with self._cap_lock:
            self._buf.clear()
            self._pending_down.clear()
            self._guards.clear()
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            self._flush_pending = False
        with self._q.mutex:
            self._q.queue.clear()
        with self._seq_lock:
            self._seq = 0                    # AtomicLong 随进程消失
        self._sid = str(uuid.uuid4())[:8]    # 新进程新会话标识
        self.disabled_by_pc = False
        self.enabled = True
        self.ready = True
        self._start_hb()

    # ---- 发送侧 ----

    def _ensure_worker(self):
        if self._stopped:
            return
        if self._worker is not None and self._worker.is_alive():
            return                           # 死亡自清后可重建（finally）
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop,
                                        name="pedal-fwd-sim", daemon=True)
        self._worker.start()

    def _worker_loop(self):
        try:
            while self._running and not self._stopped:
                try:
                    p = self._q.get(timeout=1)
                except queue.Empty:
                    continue
                self._send(p)
        finally:
            self._running = False            # 意外死亡自清：下次可重建

    def _send(self, p):
        """send 镜像。注入点：网络抖动在每包首发前应用一次（单 worker 串行
        ⇒ 包顺序不变、只整体延迟）。跨包手势窗由 et 锚定保护，到达抖动
        不再影响 PC 端判定。"""
        if self._jitter:
            time.sleep(self._rng.uniform(*self._jitter))
        while True:
            code, resp, exc = self._post_once(p.body)
            if code == 200:
                if self.disabled_by_pc:      # 403 期间心跳探到电脑端已开启
                    self.disabled_by_pc = False
                    self._report("电脑端已开启，转发恢复")
                return
            if code == 403:
                if not self.disabled_by_pc:  # 状态沿：只提示一次
                    self._report("电脑端未勾选「允许平板转发」，"
                                 "停发等待（按键透传给前台 App）")
                self.disabled_by_pc = True
                return
            if code is not None and code < 500 and code != 429:
                self._report("转发被拒（HTTP %s），本包丢弃" % code)
                return
            if (time.monotonic() * 1000.0 - p.t) > MAX_AGE_MS:
                self._report("链路不通超 2s，本包丢弃")
                return
            time.sleep(RETRY_MS / 1000.0)

    def _post_once(self, body):
        """POST /pedal/event。返回 (code|None, resp|None, exc|None)。
        故障注入：_blackhole=连接层丢包（PC 未收到）；_drop_resp=响应在途
        丢失（PC 已处理，客户端视为网络失败——seq 去重的真实考题）。"""
        seq = json.loads(body).get("seq")
        if self._stall_once > 0:
            time.sleep(self._stall_once)
            self._stall_once = 0.0
        try:
            if self._blackhole > 0:
                self._blackhole -= 1
                raise ConnectionError("blackholed (injected)")
            conn = http.client.HTTPConnection(self._host, self._port,
                                              timeout=1.5)
            try:
                conn.request("POST", "/pedal/event", body=body,
                             headers={"Content-Type": "application/json"})
                if self._drop_resp > 0:
                    self._drop_resp -= 1
                    try:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    raise ConnectionError("response dropped (injected)")
                r = conn.getresponse()
                data = r.read()
                try:
                    resp = json.loads(data.decode("utf-8"))
                except ValueError:
                    resp = {"raw": data[:120].decode("utf-8", "replace")}
                code = r.status
            finally:
                conn.close()
        except OSError as e:
            self._rec_attempt(seq, None, None, "%s: %s"
                              % (type(e).__name__, e))
            return None, None, e
        self._rec_attempt(seq, code, resp, None)
        return code, resp, None

    def _rec_attempt(self, seq, code, resp, exc):
        self.attempts.append({"seq": seq, "code": code, "resp": resp,
                              "exc": exc, "t": time.monotonic()})

    def _report(self, msg):
        self.drops.append(msg)           # Kotlin report() → 诊断通道

    # ---- 测试便捷面 ----

    def pulse(self, name, width_ms=10):
        """一次真实按压脉冲：down 边沿 → width_ms 后 up 边沿（真实踏板
        脉冲宽 7-15ms）。wire vk=AOSP LKC（getScanCode 实况）、kc=keyCode，
        与真机一致。返回 (akc, vk, t_down_ms, t_up_ms)。"""
        usage, lkc, akc = KEYS[name]
        t0 = self._now_ms()
        self.on_key(akc, lkc, True, t0)
        time.sleep(width_ms / 1000.0)
        t1 = self._now_ms()
        self.on_key(akc, lkc, False, t1)
        return akc, lkc, t0, t1

    def raw_post(self, body, path="/pedal/event"):
        """裸 HTTP POST（绕过客户端状态机——模拟垃圾包/风暴工具）。"""
        conn = http.client.HTTPConnection(self._host, self._port, timeout=3)
        try:
            conn.request("POST", path, body=body,
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            data = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(data)
            except ValueError:
                return r.status, {"raw": data}
        finally:
            conn.close()

    def stop(self):
        """收尾：停 worker/心跳/未决定时器（测试 teardown，非 Kotlin）。"""
        self._stopped = True
        self._running = False
        self._hb_gen += 1
        with self._cap_lock:
            self._guards.clear()
            self._pending_down.clear()
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
        if self._hb_thread is not None:
            self._hb_thread.join(timeout=0.5)
