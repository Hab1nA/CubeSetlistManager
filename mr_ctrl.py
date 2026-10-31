# -*- coding: utf-8 -*-
"""Cubase MIDI Remote 直控（CC → driver → runCommand）。
哑协议：一条 CC=一条命令，动作序列语义在本模块，对齐 daw_ctrl facts 键序
语义（先停防播放中定位续播）。driver=mr_driver/Local/CubeSetlistManager/
CubeSetlistRemote/（部署 docs/MIDIRemote直控部署.md），端口=loopMIDI 建
「CubeSetlist Remote」单口双向。v1 driver 无回读：可用性=口开成功，效果
核验沿用既有 MTC 三态/全停核验；不可用（口缺失/发送失败）→ 调用方
（daw_ctrl）动作级回落键盘模拟，全程不改焦点。
ponytail: v1 无在线心跳——driver 死而口在=CC 无人受理且不报错，Phase 2
加 feedback 绑定（driver 死→口开成功但无心跳→动作级回落）。"""
import threading
import time

import midi_bridge as mb

PORT_HINT_DEFAULT = "CubeSetlist Remote"
CC_GAP = 0.03        # 序列逐条间隔（与 daw_ctrl.KEY_GAP 同量级）
RETRY_AFTER = 10.0   # 开口失败后重试间隔（loopMIDI 迟到自愈）

# 原始命令 CC（与 driver JS 头 CC-TABLE 行一致，tests/test_mr_ctrl.py 校验）
CC_START, CC_STOP, CC_RTZ, CC_SAVE, CC_CLOSE = 102, 103, 104, 105, 106

# 动作→CC 序列（键=daw_ctrl facts["transport"] 五动作+close；每命令发
# press127+release0）。语义对齐：play=从头播（先停防续播）→回零→播；
# resume=原地续播（不发回零）；rewind=停止+回零；close=先停+保存再关。
SEQ = {
    "play": (CC_STOP, CC_RTZ, CC_START),
    "stop": (CC_STOP,),
    "pause": (CC_STOP,),
    "resume": (CC_START,),
    "rewind": (CC_STOP, CC_RTZ),
    "close": (CC_STOP, CC_SAVE, CC_CLOSE),
}

_lock = threading.Lock()      # send/口管理互斥（GUI 主线与切歌工作线程两路进）
_port_hint = PORT_HINT_DEFAULT
_out = None                   # mb.open_out 常驻句柄（None=未开/已弃）
_next_try = 0.0               # 开口失败冷却截止（RETRY_AFTER 内不再枚举）


def set_port_hint(hint):
    """config 载入后由 GUI 调用；换名弃现口（下次 send 重开）。"""
    global _port_hint
    with _lock:
        if hint and hint != _port_hint:
            _drop_locked()
            _port_hint = hint


def _drop_locked():
    # 弃口=fire-and-forget 专线关，句柄不在此线程收
    global _out
    if _out is not None:
        mb.close_out(_out)
        _out = None


def _ensure_locked():
    """口未开且过了失败冷却则试开。返回句柄（None=暂不可用）。"""
    global _out, _next_try
    if _out is not None or time.time() < _next_try:
        return _out
    dev = mb._pick(mb._out_devices(), _port_hint)
    if dev is None:
        _next_try = time.time() + RETRY_AFTER
        return None
    h, err = mb.open_out(dev[0])
    if h is None:
        _next_try = time.time() + RETRY_AFTER
        return None
    _out = h
    return h


def available():
    """直控是否就绪（探针/诊断用）：主动试开一次。"""
    with _lock:
        return _ensure_locked() is not None


def send(action):
    """发一个动作的 CC 序列。成功=None；失败=错误文案（口缺失/发送失败，
    已弃口并进入失败冷却）。线程安全：整段序列持锁，动作间不交错。"""
    seq = SEQ.get(action)
    if seq is None:
        return "未知动作 %r" % action
    with _lock:
        h = _ensure_locked()
        if h is None:
            return "MR 口未就绪（含「%s」的输出口缺失或打不开）" % _port_hint
        for i, cc in enumerate(seq):
            if i:
                time.sleep(CC_GAP)
            for val in (127, 0):        # press→release（命令在按下沿触发）
                err = mb.send_cc_h(h, 0, cc, val)
                if err:
                    _drop_locked()
                    _next_try = time.time() + RETRY_AFTER
                    return err
                time.sleep(CC_GAP)
        return None
