# -*- coding: utf-8 -*-
r"""BLE MIDI（WinRT）后端：键盘自动化的无线收发。全仓库唯一 import winrt 的
地方（源码锚测试钉住）；winrt 包缺失时 _HAS_WINRT=False，所有入口返回空
清单/明确错误文案，kbd_auto 行为与纯 winmm 版完全一致。

Windows 只把 BLE MIDI 端点暴露给 WinRT API——winmm 看不到（2026-10-09 本机
实测：winmm 4进5出全无 BLE 端点，WinRT 20进21出含 BLE 对；端点 id 形如
\\?\SWD#MMDEVAPI#MIDII_*.BLE10#{接口类GUID}，pywinrt 3.2.1 未投影
find_all_async 的 AQS 选择器重载，只有 0 参全量枚举，GUID 过滤在本地做）。

稳定性三支柱（PoC 实测约束，勿"简化"掉）：
1. from_id_async 连不上设备会无限期挂起且 asyncio 取消不传导——打开一律
   跑一次性 daemon 线程，调用方限时等待，超时弃单；迟到的打开自查弃单
   标志即关即回收（midi_bridge.open_in 的 state["dead"] 合同同款）。
   不用共享串行队列：一个挂起会毒化整个后端，这里坏设备只废自己的线程，
   重试永远可用。
   ponytail: 弃单的线程泄漏到 GATT 自行放弃或进程退出；_PEND_CAP 限并发
   挂起数，防对睡眠设备反复重试把线程堆起来。
2. 枚举走后台刷新线程 + TTL 缓存（镜像 midi_bridge._CacheHolder 的
   last-good 思想）：UI 300ms 轮询只读缓存绝不阻塞，扫描失败保旧清单。
3. MessageReceived 回调在 WinRT 线程池线程只做字节解析并直调 on_msg
   （消费方 SlotCapture.feed 只写普通属性）——与 winmm 回调线程同款纪律。
"""
import asyncio
import threading
import time

try:
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.devices.midi import (
        MidiChannelPressureMessage, MidiControlChangeMessage,
        MidiNoteOffMessage, MidiNoteOnMessage, MidiPitchBendChangeMessage,
        MidiPolyphonicKeyPressureMessage, MidiProgramChangeMessage,
        MidiSystemExclusiveMessage, MidiInPort, MidiOutPort)
    from winrt.windows.storage.streams import DataReader, DataWriter
    _HAS_WINRT = True
except ImportError:
    _HAS_WINRT = False

IO_TIMEOUT = 3.0        # 打开限时（调用方至多等这么久，同 midi_bridge.IO_TIMEOUT）
BLE_MARK = "（BLE）"     # 合成显示名后缀：hint 匹配/下拉/状态行与 winmm 共用
IN_GUID = "504be32c-ccf6-4d2c-b73f-6f8b3747e22b"    # MIDI IN 接口类 GUID
OUT_GUID = "6dc23320-ab33-4ce4-80d4-bbb3ebbf2814"   # MIDI OUT 接口类 GUID
SCAN_TTL = 5.0          # 缓存寿命：断连可见性滞后 ≤ TTL+0.5s，人机尺度可接受
_PEND_CAP = 2           # 并发挂起打开上限


def parse_short(raw):
    """原始 MIDI 事件字节 → (status, d1, d2) 或 None（纯函数）。
    WinRT 已把 BLE 包重组为完整事件：三字节类 8/9/A/B/E、两字节类 C/D、
    实时单字节 F8-FF（d1=d2=0）、MTC/曲目选择 F1/F3（1 数据字节）；
    SysEx F0/F7 与系统专用 F2/F4-F7 不收——与 winmm 路径只收短消息口径
    对齐（winmm 侧这些走 MIM_LONGDATA，RawMidiIn 本就不派发）。"""
    if not raw:
        return None
    st = raw[0]
    if st < 0x80:
        return None                    # 运行状态分片：WinRT 不应出现，防御
    if st >= 0xF0:
        if st in (0xF1, 0xF3):
            return (st, raw[1], 0) if len(raw) > 1 else None
        return (st, 0, 0) if st >= 0xF8 else None
    hi = st & 0xF0
    if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
        return (st, raw[1], raw[2]) if len(raw) > 2 else None
    return (st, raw[1], 0) if len(raw) > 1 else None


def _mark(name):
    """合成显示名：加（BLE）后缀（已带不重复）——后缀即后端标识，此后
    kbd_auto 的 _pick_hit/_device_entries/状态行零改动。纯函数。"""
    return name if name.endswith(BLE_MARK) else name + BLE_MARK


# ---- 枚举：后台刷新 + TTL 缓存（主线程只读，绝不等待） ----

_cache_lock = threading.Lock()
_cache = {"in": [], "out": []}      # [(dev_id, 合成名)]，按 (名, id) 稳定排序
_cache_at = {"in": 0.0, "out": 0.0}  # monotonic 时间戳；0=从未刷过
_scanning = {"in": False, "out": False}


def _scan_raw(guuid):
    """WinRT 全量枚举（0 参重载，见模块 docstring）。测试桩替换点。"""
    return asyncio.run(DeviceInformation.find_all_async())


def scan(side):
    """同步枚举一个方向，返回 [(dev_id, 合成名)]。阻塞数秒——只给后台
    刷新线程与探针用，UI 一律走 in_devices/out_devices 读缓存。
    过滤双条件：接口类 GUID（IN/OUT 各一）+ `.BLE10` 后缀。后者必须保留：
    Windows MIDI 2.0 给每个 winmm 口同时发布 KSA 字节桥接口（同带 GUID，
    但 WinRT 打开会无限挂起，PoC 实测）——只有 .BLE10 是 in-box BLE MIDI
    传输发布的真·BLE 端点。"""
    guuid = IN_GUID if side == "in" else OUT_GUID
    out = []
    for d in _scan_raw(guuid):
        low = d.id.lower()
        if guuid in low and ".ble10" in low:
            out.append((d.id, _mark(d.name or "（未命名设备）")))
    out.sort(key=lambda p: (p[1], p[0]))
    return out


def _refresh_once(side):
    """同步扫描一个方向并入缓存；异常保旧清单（last-good）。后台刷新线程
    与测试共用；_scanning 位防同方向重入。"""
    with _cache_lock:
        if _scanning[side]:
            return
        _scanning[side] = True
    try:
        lst = scan(side)
    except Exception:
        return
    finally:
        with _cache_lock:
            _scanning[side] = False
    with _cache_lock:
        _cache[side] = lst
        _cache_at[side] = time.monotonic()


def _refresher():
    while True:
        time.sleep(0.5)
        for side in ("in", "out"):
            with _cache_lock:
                stale = (time.monotonic() - _cache_at[side] >= SCAN_TTL
                         and not _scanning[side])
            if stale:
                _refresh_once(side)


def invalidate():
    """强制两个方向尽快后台重扫（设备行「刷新」钮用）：只清时间戳不动
    清单——刷新期间 UI 拿旧值好过拿空值。"""
    with _cache_lock:
        _cache_at["in"] = _cache_at["out"] = 0.0


def in_devices():
    """BLE 输入端点清单 [(dev_id, 合成名)]。未装 winrt 或首扫未完成 → []
    （调用方与 winmm 清单合并后匹配，空清单=该后端无候选）。"""
    if not _HAS_WINRT:
        return []
    with _cache_lock:
        return list(_cache["in"])


def out_devices():
    """BLE 输出端点清单，同 in_devices。"""
    if not _HAS_WINRT:
        return []
    with _cache_lock:
        return list(_cache["out"])


# ---- 打开：一次性线程 + 限时 + 弃单自回收 ----
# from_id_async 无法取消（稳定性支柱 1），所以打开不进任何共享队列——
# 每次打开自己的 daemon 线程，挂起只废自己，_PEND_CAP 防重试堆积。

_pend_lock = threading.Lock()
_pend = 0


def _open_raw_in(dev_id):
    """阻塞打开输入端点，返回原始 MidiInPort。只在一次性线程内调用；
    测试桩替换点（可编排延迟/挂起/失败）。"""
    return asyncio.run(MidiInPort.from_id_async(dev_id))


def _open_raw_out(dev_id):
    """阻塞打开输出端点，同 _open_raw_in。"""
    return asyncio.run(MidiOutPort.from_id_async(dev_id))


def open_in(dev_id, on_msg, timeout=None):
    """限时打开 BLE 输入端点。返回 (BleIn, None) 或 (None, 错误文案)。"""
    return _open(_open_raw_in, dev_id, timeout,
                 lambda port: BleIn(port, on_msg))


def open_out(dev_id, timeout=None):
    """限时打开 BLE 输出端点。返回 (BleOut, None) 或 (None, 错误文案)。"""
    return _open(_open_raw_out, dev_id, timeout, BleOut)


def _open(raw_open, dev_id, timeout, wrap):
    """open_in/open_out 共用骨架：一次性 daemon 线程执行 raw_open，调用方
    限时等待；超时置弃单标志，迟到打开自查即关即回收（绝不泄漏已打开的
    端口）。winrt 缺失与挂起过多快速失败，错误文案给人话。"""
    global _pend
    if not _HAS_WINRT:
        return None, "未安装 winrt 包，BLE MIDI 不可用"
    timeout = IO_TIMEOUT if timeout is None else timeout
    with _pend_lock:
        if _pend >= _PEND_CAP:
            return None, "BLE 打开挂起过多（%d 个），请稍后再试" % _pend
        _pend += 1
    state = {"dead": False}
    box = {}
    done = threading.Event()

    def run():
        global _pend               # 嵌套函数须自行声明：_open 的 global
        try:                       #   不传导到闭包内层（run_io 同坑同注）
            port = raw_open(dev_id)
            if state["dead"]:       # 等待方已弃单：即开即回收
                port.close()
                return
            box["port"] = port
        except Exception as e:
            box["err"] = "BLE 打开失败：%s: %s" % (type(e).__name__, e)
        finally:
            with _pend_lock:
                _pend -= 1
            done.set()

    threading.Thread(target=run, daemon=True, name="ble-open").start()
    if not done.wait(timeout):
        state["dead"] = True
        return None, "BLE 打开超时（%.0f 秒，设备无应答？弃单将自回收）" % timeout
    if "port" in box:
        return wrap(box["port"]), None
    return None, box.get("err") or "BLE 打开超时（%.0f 秒）" % timeout


def _buf_bytes(buf):
    """IBuffer → bytes。测试桩替换点（假事件直接给 bytes）。"""
    r = DataReader.from_buffer(buf)
    return bytes(r.read_bytes(buf.length))


class BleIn:
    """BLE 输入口：MessageReceived → parse_short → on_msg(status,d1,d2)。
    回调线程纪律见模块 docstring 支柱 3；token 持引用防 GC（=mb._Proc 纪律），
    close 幂等（_port 置空守卫，同 MidiIn.close 口径）。"""

    def __init__(self, port, on_msg):
        self._port = port
        self._on_msg = on_msg
        self._token = port.add_message_received(self._on_event)

    def _on_event(self, sender, args):
        m = parse_short(_buf_bytes(args.message.raw_data))
        if m is not None:
            self._on_msg(*m)

    def close(self):
        p, self._port = self._port, None
        if p is not None:
            p.remove_message_received(self._token)
            p.close()       # 本地对象释放，非阻塞（PoC 实测）


if _HAS_WINRT:
    _SHORT_MSG = {
        0x80: (MidiNoteOffMessage, 2), 0x90: (MidiNoteOnMessage, 2),
        0xA0: (MidiPolyphonicKeyPressureMessage, 2),
        0xB0: (MidiControlChangeMessage, 2),
        0xC0: (MidiProgramChangeMessage, 1),
        0xD0: (MidiChannelPressureMessage, 1),
    }
else:
    _SHORT_MSG = {}     # 测试给桩类；运行期该分支不可达（open_out 已拦截）


class BleOut:
    """BLE 输出口：短消息按状态半字节映射 v1 消息类（实际只发 B0/C0，全表
    兜底防真机报错断链）；pitch bend 14 位值=LSB|MSB<<7；SysEx 经
    MidiSystemExclusiveMessage，免 winmm 的 HDR 准备/DONE 轮询。
    send_long 返回 True=失败（同 kbd_auto._send_long 口径）。"""

    def __init__(self, port):
        self._port = port

    def send_short(self, payload):
        st = payload & 0xFF
        d1, d2 = (payload >> 8) & 0xFF, (payload >> 16) & 0xFF
        hi, ch = st & 0xF0, st & 0x0F
        try:
            if hi == 0xE0:
                self._port.send_message(
                    MidiPitchBendChangeMessage(ch, d1 | d2 << 7))
            elif hi in _SHORT_MSG:
                cls, n = _SHORT_MSG[hi]
                self._port.send_message(
                    cls(ch, d1, d2) if n == 2 else cls(ch, d1))
            else:
                return "BLE 输出不支持状态字节 %02X" % st
        except Exception as e:
            return "BLE 发送失败：%s: %s" % (type(e).__name__, e)
        return None

    def send_long(self, data):
        try:
            w = DataWriter()
            w.write_bytes(bytes(data))
            self._port.send_message(MidiSystemExclusiveMessage(w.detach_buffer()))
        except Exception:
            return True
        return False

    def close(self):
        p, self._port = self._port, None
        if p is not None:
            p.close()


if _HAS_WINRT:
    threading.Thread(target=_refresher, daemon=True, name="ble-scan").start()


if __name__ == "__main__":
    # 离线自检：无需 winrt 在场（CI 口径）
    assert parse_short(b"\x90\x3c\x40") == (0x90, 0x3C, 0x40)
    assert parse_short(b"\x80\x3c\x00") == (0x80, 0x3C, 0x00)
    assert parse_short(b"\xB0\x40\x7F") == (0xB0, 0x40, 0x7F)
    assert parse_short(b"\xE0\x00\x40") == (0xE0, 0x00, 0x40)
    assert parse_short(b"\xC5\x07") == (0xC5, 0x07, 0)          # PC 两字节
    assert parse_short(b"\xD0\x33") == (0xD0, 0x33, 0)
    assert parse_short(b"\xF8") == (0xF8, 0, 0)                 # 实时单字节
    assert parse_short(b"\xFE") == (0xFE, 0, 0)
    assert parse_short(b"\xF1\x27") == (0xF1, 0x27, 0)          # MTC 四分之一帧
    assert parse_short(b"\xF0\x41\xF7") is None                 # SysEx 不收
    assert parse_short(b"\xF7") is None and parse_short(b"\xF2\x00\x00") is None
    assert parse_short(b"\x3c\x40") is None                     # <0x80 防御
    assert parse_short(b"\x90\x3c") is None                     # 截断防御
    assert parse_short(b"\xC5") is None
    assert parse_short(b"") is None
    assert _mark("JUNO-DS") == "JUNO-DS（BLE）"
    assert _mark("JUNO-DS（BLE）") == "JUNO-DS（BLE）"           # 防重后缀
    # GUID 过滤 + 稳定排序 + 空名兜底（桩枚举，winrt 不必在场）
    class _D:
        def __init__(self, i, n):
            self.id, self.name = i, n
    orig = _scan_raw
    globals()["_scan_raw"] = lambda g: [
        _D(r"\\?\SWD#MMDEVAPI#MIDII_2.BLE10#{" + g + "}", "AX-09"),
        _D(r"\\?\SWD#MMDEVAPI#MIDII_1.BLE10#{" + g.upper() + "}", "JUNO-DS"),
        _D(r"\\?\SWD#MMDEVAPI#MIDIU_x#{ffffffff-0000-0000-0000-000000000000}",
          "非MIDI口"),
        # KSA 字节桥：带同款 GUID 但无 .BLE10——winmm 口的双发布，必须排除
        # （WinRT 打开这类口会无限挂起，PoC 实测）
        _D(r"\\?\SWD#MMDEVAPI#MIDIU_KSA_x_0_0#{" + g + "}", "Keyboard Auto"),
        _D(r"\\?\SWD#MMDEVAPI#MIDII_3.BLE10#{" + g + "}", "")]
    ins = scan("in")
    # 排序按合成名：全角括号（U+FF08）大于 ASCII，未命名兜底排最后
    assert [n for _, n in ins] == ["AX-09（BLE）", "JUNO-DS（BLE）",
                                   "（未命名设备）（BLE）"]
    assert all(BLE_MARK in n for _, n in ins)
    globals()["_scan_raw"] = orig
    # 短消息映射（桩消息类记录构造参数；_SHORT_MSG 桩化不依赖 winrt）
    rec = []
    class _Msg:
        def __init__(self, *a):
            rec.append(a)
    orig_map = dict(_SHORT_MSG)
    _SHORT_MSG.clear()
    _SHORT_MSG.update({0xB0: (_Msg, 2), 0xC0: (_Msg, 1)})
    class _P:
        def __init__(self):
            self.sent = []
        def send_message(self, m):
            self.sent.append(m)
    p = _P()
    out = BleOut(p)
    out._port = p
    assert out.send_short(0xB0 | 0 | 0 << 8 | 85 << 16) is None and rec[-1] == (0, 0, 85)
    assert out.send_short(0xC1 | 2 << 8) is None and rec[-1] == (1, 2)
    assert "不支持" in out.send_short(0xF0)                     # 表外状态拒绝
    _SHORT_MSG.clear()
    _SHORT_MSG.update(orig_map)
    # 降级口径（置 False 强制走无 winrt 分支，任何机器上确定性成立）
    real_flag = _HAS_WINRT
    globals()["_HAS_WINRT"] = False
    try:
        assert in_devices() == [] and out_devices() == []
        port, err = open_in("x", lambda *a: None)
        assert port is None and "winrt" in err
    finally:
        globals()["_HAS_WINRT"] = real_flag
    print("midi_ble self-check OK")
