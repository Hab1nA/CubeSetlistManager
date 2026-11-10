# -*- coding: utf-8 -*-
"""Cubase MIDI Remote 直控（CC → driver → runCommand）。
哑协议：一条 CC=一条命令，动作序列语义在本模块。driver=mr_driver/Local/
CubeSetlistManager/CubeSetlistRemote/（部署 docs/MIDIRemote直控部署.md），
端口=loopMIDI「CubeSetlist Remote」单口双向。完整迁移：Cubase 无键盘
回落，口不在、发送失败或 Cubase 不在线=动作失败报错；效果核验沿用
MTC 三态。存活探针（Phase 2，11-11 落地）：纯回声方案被实证作废——
loopMIDI 同缆自听使回声无法区分 driver 反馈，且实测 driver 对外来 CC
不回发 feedback（活/死 Cubase 收到的字节完全一致）。改为 send 前查
Cubase 进程+界面在场（daw_ctrl 注入探针）：Cubase 已退出/退出空壳时
报错弃口进冷却，不再假成功；残留边界（Cubase 运行中驱动脚本被删）由
tools/probe_mr_echo.py 部署核验兜住。"""
import threading
import time

import midi_bridge as mb

PORT_HINT_DEFAULT = "CubeSetlist Remote"
CC_GAP = 0.03        # 序列逐条间隔（与 daw_ctrl.KEY_GAP 同量级）
RETRY_AFTER = 10.0   # 开口失败后重试间隔（loopMIDI 迟到自愈）

# 原始命令 CC（与 driver JS 头 CC-TABLE 行一致，tests/test_mr_ctrl.py 校验）
CC_START, CC_STOP, CC_RTZ, CC_SAVE, CC_CLOSE = 102, 103, 104, 105, 106

# 动作→CC 序列（键=daw_ctrl.ACTION_NAMES 五动作+close；每命令发
# press127+release0）。语义：play=从头播（先停防续播）→回零→播；
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
_alive_probe = None           # Cubase 存活探针（daw_ctrl 注入：进程+界面在场）

def set_alive_probe(fn):
    """注入 Cubase 存活探针（进程+界面在场）。send 发送前先查探针，
    不在线=动作失败报错弃口——「口在而 DAW 死」的静默失效根修；
    available() 语义=口可开，不查探针（探针 False 时仍 True）。"""
    global _alive_probe
    _alive_probe = fn


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
    """口未开且过了失败冷却则试开。返回 (句柄, 错误文案)；句柄为 None 时
    错误必非空（口缺失/打开失败/冷却中）。"""
    global _out, _next_try
    if _out is not None:
        return _out, None
    if time.time() < _next_try:
        return None, "输出口暂不可用，稍后自动重试"
    dev = mb._pick(mb._out_devices(), _port_hint)
    if dev is None:
        _next_try = time.time() + RETRY_AFTER
        return None, "找不到含「%s」的输出口" % _port_hint
    h, err = mb.open_out(dev[0])
    if h is None:
        _next_try = time.time() + RETRY_AFTER
        return None, err
    _out = h
    return h, None


def available():
    """直控是否就绪（探针/诊断用）：主动试开一次。"""
    with _lock:
        h, _err = _ensure_locked()
        return h is not None


def send(action):
    """发一个动作的 CC 序列。成功=None；失败=错误文案（口缺失/发送失败，
    已弃口并进入失败冷却）。线程安全：整段序列持锁，动作间不交错。"""
    seq = SEQ.get(action)
    if seq is None:
        return "未知动作 %r" % action
    global _next_try   # 失败路径写冷却截止；此前缺 global=写局部，冷却从未生效
    with _lock:
        if _alive_probe is not None and not _alive_probe():
            _drop_locked()
            _next_try = time.time() + RETRY_AFTER
            return "Cubase 不在线：进程或界面不在（口在而 DAW 死，拒绝发送）"
        h, err = _ensure_locked()
        if h is None:
            return err
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
