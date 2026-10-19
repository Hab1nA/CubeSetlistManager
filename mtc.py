# -*- coding: utf-8 -*-
"""MIDI 时码（MTC）解码器：走带绝对位置的唯一来源。

两种携带位置的形态（本模块是它们唯一的解析点）：
  四分之一帧 F1 0nnn dddd：8 条一轮转编码完整时码（类型 0/1=帧、2/3=秒、
    4/5=分、6/7=时+帧率），只在播放中流动，每 SMPTE 帧 4 条（30fps≈120 条/秒，
    完整位置 ~67ms 一更新）——播放中的位置与活跃证据（唯一播放证据）。
  全帧 F0 7F 7F 01 01 hh mm ss ff F7（Universal Real Time SysEx）：停止/
    定位态由主控补发——只更新位置，不算活跃（部分主控停止期间也补发）。
换算：位置秒 = 时:分:秒 + 帧/帧率；速率取小时字段高 2 位（24/25/29.97df/30），
29.97df 的帧分数按 30 算（fraction 误差 <0.1%，秒级判定无感）。

线程纪律：feed/full_frame/note_f8 在 winmm 回调或专线线程上跑，锁内只做
记账；对外回调（on_qf/on_position/on_full）整体吞异常——winmm 回调线程
绝不允许异常逸出（异常零痕迹吞噬区，签名错位类事故的防线）。
纪元（epoch）：reset() 把位置清为未知、弃装配组——切歌/换口/回零时调用，
三态与进度随之回「未播放/0」；qf/f8 计数不清（端口内容统计与 F8-only 提示
用，跟口不跟歌）。"""
import threading

# 速率码 → 帧率（小时字段 bit5-6：0=24fps 1=25 2=30 drop-frame 3=30）
_FPS = (24.0, 25.0, 30.0, 30.0)

# 全帧头（Universal Real Time SysEx：通用广播 7F、MTC 01、全帧 01）
_FULL_HEAD = b"\xf0\x7f\x7f\x01\x01"


def parse_full_frame(data):
    """字节流 → 全帧位置秒 | None。在缓冲里扫描全帧（容忍前后杂字节、
    一次投递多条），取最后一条完整帧=最新位置；字段越界的候选丢弃。"""
    pos = None
    i = data.find(_FULL_HEAD)
    while i >= 0:
        if i + 9 <= len(data):
            hh, mm, ss, ff = data[i + 5], data[i + 6], data[i + 7], data[i + 8]
            fps = _FPS[(hh >> 5) & 0x03]
            if (hh & 0x1F) < 24 and mm < 60 and ss < 60 and ff < fps:
                pos = ((hh & 0x1F) * 3600) + mm * 60 + ss + ff / fps
        i = data.find(_FULL_HEAD, i + 1)
    return pos


def qf_bytes(h, m, s, f, rate=3):
    """时码 → 8 条四分之一帧数据字节（构造测试/探针自检用；rate:
    0=24 1=25 2=30df 3=30）。"""
    return bytes([
        (0 << 4) | (f & 0x0F),
        (1 << 4) | ((f >> 4) & 0x0F),
        (2 << 4) | (s & 0x0F),
        (3 << 4) | ((s >> 4) & 0x0F),
        (4 << 4) | (m & 0x0F),
        (5 << 4) | ((m >> 4) & 0x0F),
        (6 << 4) | (h & 0x0F),
        (7 << 4) | (((h >> 4) & 0x01) | ((rate & 0x03) << 1)),
    ])


class Decoder:
    """四分之一帧装配 + 全帧解析 → 绝对位置秒。

    on_qf()           每条 QF 到达即调（活跃证据喂给 TransportSync，粒度同旧时钟脉冲）
    on_position(sec)  QF 装配出完整位置（播放中，~15Hz）
    on_full(sec)      全帧位置（停止/定位态；缺省=并入 on_position）"""

    def __init__(self, on_qf, on_position, on_full=None):
        self._on_qf = on_qf or (lambda: None)
        self._on_position = on_position or (lambda sec: None)
        self._on_full = on_full or on_position or (lambda sec: None)
        self._lock = threading.Lock()
        self.qf_seen = 0            # 累计计数（跟口不跟歌，reset 不清）
        self.f8_seen = 0
        self.full_seen = 0          # 收到的全帧数——「时码已配置」的铁证：
        #   正确配置的工程停止/定位时必补发全帧；只开时钟的工程永远没有。
        #   F8-only 诊断提示用它把「停止模式时钟的 F8」与「没配时码」区分开
        self._pos = None
        self._parts = None          # 装配中的 8 半字节；None=等类型 0 起组
        self._expect = 0            # 下一个允许的类型
        self._epoch = 0             # 纪元号：reset 递增；落位/回调前校验，
                                    #   防「复位插在 assemble 与落位之间」
                                    #   把旧纪元位置复活（双临界区教训）

    # ---- 对消费方的只读面 ----

    def position(self):
        """最近解码位置（工程绝对秒）；一次都没见过 = None。"""
        with self._lock:
            return self._pos

    def reset(self):
        """纪元复位：位置未知、弃装配组（切歌/换口/回零时调用）。"""
        with self._lock:
            self._epoch += 1
            self._pos = None
            self._parts = None
            self._expect = 0

    # ---- 输入面（winmm 回调/专线线程） ----

    def feed(self, data1):
        """四分之一帧数据字节（0nnn dddd）。严格 (上一条+1)%8 顺序装配，
        乱序/重复/孤条一律弃组、等下一条类型 0 重起——中途加入最多等一轮
        （~67ms）拿到首个完整位置；字段越界的整组作废（防杂字节伪位置）。"""
        try:
            typ, nib = (data1 >> 4) & 0x07, data1 & 0x0F
            done = None
            with self._lock:
                self.qf_seen += 1
                if typ == 0:
                    self._parts = [nib] + [None] * 7
                    self._expect = 1
                elif self._parts is not None and typ == self._expect:
                    self._parts[typ] = nib
                    self._expect = typ + 1
                    if self._expect == 8:
                        done = self._assemble(self._parts)
                        self._parts = None
                        self._expect = 0
                        if done is not None:
                            # 与 assemble 同临界区落位：reset 不可能插在中间
                            self._pos = done
                else:
                    self._parts = None
                    self._expect = 0
            self._emit(self._on_qf)
            if done is not None:
                self._emit(self._on_position, done)
        except Exception:
            pass

    def full_frame(self, data):
        """longdata 缓冲原始字节：扫全帧，命中即更新位置（不动活跃）。
        parse 在锁外（纯函数）：先取纪元号，落位前复核——parse 期间发生
        reset 则本次位置属于旧纪元，弃。"""
        try:
            with self._lock:
                epoch = self._epoch
            pos = parse_full_frame(bytes(data))
            if pos is not None:
                with self._lock:
                    if self._epoch != epoch:
                        return
                    self._pos = pos
                    self.full_seen += 1
                self._emit(self._on_full, pos)
        except Exception:
            pass

    def note_f8(self):
        """MIDI Clock 脉冲（F8）：只计数——迁移后 F8 不是位置来源，计数
        仅供「有 F8 无 QF」一次性诊断提示（工程只开了时钟输出没开时码）。"""
        with self._lock:
            self.f8_seen += 1

    # ---- 内部 ----

    @staticmethod
    def _assemble(p):
        ff = p[0] | (p[1] << 4)
        ss = p[2] | (p[3] << 4)
        mm = p[4] | (p[5] << 4)
        hh = p[6] | ((p[7] & 0x01) << 4)
        fps = _FPS[(p[7] >> 1) & 0x03]
        if hh < 24 and mm < 60 and ss < 60 and ff < fps:
            return hh * 3600 + mm * 60 + ss + ff / fps
        return None

    @staticmethod
    def _emit(fn, *a):
        try:
            fn(*a)
        except Exception:
            pass
