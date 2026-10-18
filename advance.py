# -*- coding: utf-8 -*-
"""工程播完自动推进：跟随 MTC 时码绝对位置，到点由程序接管：
  ① 位置到达工程时长（仍播放中）→ 主动发停止键（实测 Cubase 播到头
     不会自己停，本程序必须接管；停止键 3 秒重试，至多 3 次）；
  ② 停止生效、时码断流 → 触发 on_finished（由 GUI 切下一首）。
判定量 = 时码位置 − 左定位条（与工程时长同坐标系：时长 = 右−左定位条）。
位置由 mtc.Decoder 解码（四分之一帧装配 / 全帧），回调只记「最后位置+时戳」
——QF 四分之一帧是唯一播放证据；全帧（停止/定位态补发）只更新位置不动
活跃时戳（on_full_position），防止停止期间补发的全帧把断流伪装成在播。
暂停/手动停止不推进（位置停在半途，断流时不足 95% 不切换）；未设工程时长
（0）不推进；工程切换须 set_duration()/reset() 重新对位（reset=纪元复位，
位置回未知）；全停用 suppress_advance() 抑制本曲推进（不碰位置/三态）。
on_position 在 winmm 回调线程，poll 在看门狗线程，与 TransportSync 同样
只做记账级操作。"""
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
        self.left = 0.0             # 左定位条偏移（位置→歌内秒换算）
        self._pos = None            # 最近时码位置（工程绝对秒）；None=未知
        self._last = 0.0            # 最近 QF 活跃时戳
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

    def set_duration(self, sec, left=None):
        """装载工程时长与左定位条。不复位进度：时码是绝对值，播放中改
        时长/重识别不清已播。left=None=保留现值（手动时长不知定位条）。"""
        with self._lock:
            self.duration = float(sec or 0)
            if left is not None:
                self.left = float(left or 0)

    def _reset(self):
        self._pos = None
        self._live = False
        self._fired = False
        self._dead_reported = False
        self._stop_tries = 0
        self._last_stop = 0.0

    def reset(self):
        """纪元复位（切歌/换口/回零）：位置回未知、三态回未播放。"""
        with self._lock:
            self._reset()

    def suppress_advance(self):
        """全停用：本曲不再自动推进。位置/活跃/三态都不动——显示保持
        真值（旧累计模型的 reset 清零显示是失步源）。"""
        with self._lock:
            self._fired = True
            self._dead_reported = False

    def on_position(self, sec):
        """QF 装配出完整位置（winmm 回调线程，~15Hz）——播放证据。"""
        now = self._t()
        with self._lock:
            self._pos = sec
            self._last = now
            self._live = True

    def on_full_position(self, sec):
        """全帧位置（停止/定位态）：只更新位置，不动活跃时戳。"""
        with self._lock:
            self._pos = sec

    def is_transport_live(self):
        """时码 QF 仍在流动（工程真的在播放）；从未收到过返回 False。"""
        with self._lock:
            return self._live and self._t() - self._last <= self.clock_timeout

    def ever_live(self):
        """本工程是否收到过 QF（区分「未播放/已暂停」：
        收过但断流=暂停；从未收到=未播放，未发时码工程无从判断）。"""
        with self._lock:
            return self._live

    def played(self):
        """歌内已播秒 = 位置−左定位条（钳 0）；位置未知=0（GUI 显示用）。"""
        with self._lock:
            return max(0.0, self._pos - self.left) \
                if self._pos is not None else 0.0

    def poll(self):
        finished = False
        with self._lock:
            if not (self.armed and self.duration > 0) or self._fired \
                    or not self._live:
                return
            now = self._t()
            playing = now - self._last <= self.clock_timeout
            played = max(0.0, self._pos - self.left) \
                if self._pos is not None else 0.0
            if playing:
                # 仍在播：位置到时长就由程序停走带（重试间隔 3s，至多 3 次）
                self._dead_reported = False
                if played >= self.duration and self._stop_tries < 3 \
                        and now - self._last_stop >= 3.0:
                    self._stop_tries += 1
                    self._last_stop = now
                    self.on_event("工程播到结尾（已播 %.0f 秒 / 时长 %.0f 秒）"
                                  "→ 自动停止走带%s" % (
                                      played, self.duration,
                                      "" if self._stop_tries == 1
                                      else "（重试 %d/2）" % (self._stop_tries - 1)))
                    self.on_stop_transport()
                return
            if played >= self.duration * 0.95:
                self._fired = True
                self.on_event("走带已停（已播 %.0f 秒 / 时长 %.0f 秒）"
                              "→ 自动切换下一首" % (played, self.duration))
                finished = True
            else:
                if not self._dead_reported:
                    self._dead_reported = True
                    self.on_event("走带停止（已播 %.0f 秒，未满时长 %.0f 秒，"
                                  "不切换）" % (played, self.duration))
                return
        self.on_finished()              # 锁外触发，避免回调死锁
