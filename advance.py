# -*- coding: utf-8 -*-
"""工程播完自动推进：旁观走带时钟，累计「活跃时长」，到点由程序接管：
  ① 活跃 ≥ 工程时长 → 主动发停止键（on_stop_transport，实测 Cubase 播到头
     不会自己停，本程序必须接管；停止键 3 秒重试，至多 3 次）；
  ② 停止生效、时钟断流 → 触发 on_finished（由 GUI 切下一首）。
与已验证的 TransportSync 并联接同一时钟源（不改动它的内部逻辑）。
暂停/手动停止不累计也不推进（活跃不足）；未设工程时长（0）不推进；
工程切换须 set_duration()/reset() 重新累计。on_clock 在 winmm 回调线程，
poll 在看门狗线程，与 TransportSync 同样只做记账级操作。"""
import threading
import time


class AdvanceWatch:
    def __init__(self, on_finished, on_stop_transport=None, on_event=None,
                 clock_timeout=0.25, t=time.time):
        self.on_finished = on_finished
        self.on_stop_transport = on_stop_transport or (lambda: None)
        self.on_event = on_event or (lambda msg: None)
        self.clock_timeout = clock_timeout
        self._t = t
        self.armed = False          # GUI「自动推进」勾选框
        self.duration = 0.0         # 当前工程时长秒；0=未知（不推进）
        self._active = 0.0          # 累计走带脉冲活跃时长
        self._last = 0.0
        self._live = False
        self._fired = False
        self._dead_reported = False
        self._stop_tries = 0        # 已发停止键次数（含重试）
        self._last_stop = 0.0
        self._lock = threading.Lock()

    def set_armed(self, on):
        with self._lock:
            self.armed = on
            self._fired = False
            self._dead_reported = False

    def set_duration(self, sec):
        with self._lock:
            self.duration = float(sec or 0)
            self._reset()

    def _reset(self):
        self._active = 0.0
        self._live = False
        self._fired = False
        self._dead_reported = False
        self._stop_tries = 0
        self._last_stop = 0.0

    def reset(self):
        with self._lock:
            self._reset()

    def is_transport_live(self):
        """时钟仍在跳（工程真的在播放）；从未收到过时钟返回 False。"""
        with self._lock:
            return self._live and self._t() - self._last <= self.clock_timeout

    def active(self):
        """已累计的走带活跃秒数（GUI 显示剩余用，不摸私有字段）。"""
        with self._lock:
            return self._active

    def on_clock(self):
        now = self._t()
        with self._lock:
            # 断流后的首个脉冲不累计（工程切换/长停顿会拉开大间隔）
            if self._live and now - self._last <= self.clock_timeout * 4:
                self._active += now - self._last
            self._last = now
            self._live = True

    def poll(self):
        with self._lock:
            if not (self.armed and self.duration > 0) or self._fired \
                    or not self._live:
                return
            now = self._t()
            if now - self._last <= self.clock_timeout:
                self._dead_reported = False
                # 仍在播：到时长就由程序停走带（重试间隔 3s，至多 3 次）
                if self._active >= self.duration and self._stop_tries < 3 \
                        and now - self._last_stop >= 3.0:
                    self._stop_tries += 1
                    self._last_stop = now
                    self.on_event("工程播到结尾（已播 %.0f 秒 / 时长 %.0f 秒）"
                                  "→ 自动停止走带%s" % (
                                      self._active, self.duration,
                                      "" if self._stop_tries == 1
                                      else "（重试 %d/2）" % (self._stop_tries - 1)))
                    self.on_stop_transport()
                return
            if self._active >= self.duration * 0.95:
                self._fired = True
                self.on_event("走带已停（已播 %.0f 秒 / 时长 %.0f 秒）"
                              "→ 自动切换下一首" % (self._active, self.duration))
            else:
                if not self._dead_reported:
                    self._dead_reported = True
                    self.on_event("走带停止（已播 %.0f 秒，未满时长 %.0f 秒，"
                                  "不切换）" % (self._active, self.duration))
                return
        self.on_finished()              # 锁外触发，避免回调死锁
