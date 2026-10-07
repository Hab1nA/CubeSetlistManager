# -*- coding: utf-8 -*-
"""踩钉冗余路端到端黑盒测试：真实 HTTP 线缆层 + 真实 WebServer +
真实 PedalListener/DeviceBridge/GestureEngine 栈 + 真实时间轴。

与既有 tests/test_bridge.py::test_pedal_remote 的区别：那是白盒桩测
（直接调 DeviceBridge.inject / 手工构造 HTTP 包、_pedal_rate 手工清白）；
这里由 tools/pedal_wire_sim.py（PedalForwarder.kt+PedalBatcher.kt 的逐行
为 Python 镜像）从网络线缆驱动整条生产链路：

  模拟器(真实 HTTP POST) → web_remote._Handler._h_pedal（真实）
    → setlist_gui.App.pedal_remote_inject（真实未绑定方法）
    → pedal.remote_key_to_vk（真实 LKC/AKC 双表）→ DeviceBridge.inject（真实）
    → GestureEngine / hid_fire 快路径（真实）→ 动作记录器（替身）

App 角色构造说明（import setlist_gui 安全性已验证）：setlist_gui 模块级
只有 import/常量/类定义——Tk 窗口、命名互斥体、dpi.enable() 全部只在
main() 里发生（模块尾 `if __name__ == "__main__"`），import 不创建任何
窗口、不依赖显示环境；既有 tests/test_bridge.py 早已 import 它。
因此本文件直接用真实 `setlist_gui.App.pedal_remote_inject` 未绑定方法
（挂到鸭子类型 WireApp 上），执行的是 100% 生产代码路径；唯一替身是
on_action 记录器（App._pedal_action 下游的 _next/_transport 属走带域，
与本功能无关）和 q 队列消费（App 用它转发主线程，测试里只读）。

时序纪律（防 flaky）：
- 所有「等动作」用 wait_until 轮询（deadline 兜底），不用裸 sleep 赌时序；
- 单击快路径在 HTTP handler 线程内同步触发，天然确定性；
- 引擎单踩结算走真 Timer(DOUBLE_WINDOW)，等窗用 1s 宽限 deadline；
- 网络抖动由单 worker 串行应用 ⇒ 包序不变、只整体延迟（keep-alive 语义）；
- 跨包事件由 et 锚定（APP 单调时戳差值外推）：窗内间距不被到达抖动拉伸、
  事件序/弹跳闸/去抖窗按真实间距判定；**能力边界**（S17 钉死）：包到达
  晚于手势窗剩余时间时，窗内定时器已触发——任何接收端无法回溯补救，
  与蓝牙直连同款 RF 延迟劣化路径完全一致；
- 20ms 包率限流的确定性触发：服务端状态注入（rate 锚点拨到未来）或
  「停 worker 令队列积压成突发」的真实客户端时序，不靠裸连发赌调度器。
"""
import json
import pathlib
import queue
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # conftest 已做，双保险

import http.client
import pedal
import setlist_gui                        # 见模块头：import 安全（零窗口）
import web_remote
from tools.pedal_wire_sim import TabletPedalSim, DEVICE

_LOOPBACK = "127.0.0.1"
LKC_NEXT = 163               # AOSP：Consumer Scan Next → getScanCode 实况值
VK_NEXT = 0xB0               # remote_key_to_vk(163, 87) → 蓝牙路径同款 VK
VK_PRIOR = 0x21              # PageUp（LKC 104 / AKC 92）
VK_RETURN = 0x0D             # Enter（LKC 28 / AKC 66）
VK_F13 = 0x7C                # F13（LKC 183，keyCode=0 → scanCode 白名单域）


def wait_until(cond, timeout=2.0, interval=0.01):
    """轮询等待（防 flaky 的唯一等待方式）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


class WireApp:
    """web_remote._Handler._h_pedal 的 App 侧鸭子替身。pedal_remote_inject
    用真实 setlist_gui.App.pedal_remote_inject（类属性挂载，未绑定方法）。"""

    def __init__(self, hid_binds, gestures=None, double_window=None):
        self.lite = False
        self.pedal_remote_enabled = False
        self._pedal_seq = {}
        self._pedal_rate = {}
        self._pedal_gwin = [0.0, 0]
        self._pedal_gwin_lock = threading.Lock()
        self.q = queue.Queue()
        self.actions = []            # [(动作名, 时刻)]（HTTP 线程写入，GIL 安全）
        self.pedal = pedal.PedalListener(
            on_action=lambda a: self.actions.append((a, time.monotonic())),
            on_event=lambda m: self.q.put(str(m)))
        self.pedal.apply("", {}, hid_binds, "wire-sim", False,
                         gestures=gestures, double_window=double_window)
        # 不调 try_open()/bridge.start()：注入路径不依赖 raw 窗口线程

    # 真实生产方法（未绑定）——覆盖 LKC/AKC 映射、et 锚定、未知键计数、
    # bridge.inject 全链路
    pedal_remote_inject = setlist_gui.App.pedal_remote_inject

    def names(self):
        return [a for a, _t in self.actions]


class Harness:
    """每个场景独立一套：真实 WebServer（回环随机端口）+ 真实栈 + 模拟器。"""

    def __init__(self, hid_binds=None, gestures=None, double_window=None):
        self.app = WireApp(hid_binds if hid_binds is not None
                           else {"next": VK_NEXT},
                           gestures=gestures, double_window=double_window)
        reg = web_remote.DeviceRegistry([], self.app.q.put)
        self.srv = web_remote.WebServer((_LOOPBACK, 0), self.app, reg,
                                        lambda: 8766)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.sim = TabletPedalSim(_LOOPBACK, self.port, seed=20261002)

    def close(self):
        self.sim.stop()
        self.srv.shutdown()
        self.srv.server_close()

    # ---- 常用断言小工具 ----

    def enable_remote(self):
        self.app.pedal_remote_enabled = True

    def wait_actions(self, n, timeout=2.0):
        wait_until(lambda: len(self.app.actions) >= n, timeout)

    def settle(self, quiet=0.25):
        """等待链路安静且动作数稳定（多等一轮防迟到的 Timer 动作）。"""
        time.sleep(quiet)
        n = len(self.app.actions)
        wait_until(lambda: len(self.app.actions) == n, quiet + 0.5)
        return self.app.names()


# ======================================================================
# 场景矩阵
# ======================================================================

def test_s1_single_next():
    """单踩 next：10ms 脉冲（真实 LKC 键码）→ 恰好一次 "next"（快路径在
    down 沿同步触发），无多余动作。"""
    h = Harness({"next": VK_NEXT})          # 纯单踩 → 桥快路径
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(1, 2.0)
        assert h.settle(0.3) == ["next"], h.app.names()
        # 客户端视角：一包一发，200
        assert len(h.sim.attempts) == 1 and h.sim.attempts[0]["code"] == 200
        assert h.sim.attempts[0]["seq"] == 1
    finally:
        h.close()


def test_s2_double_next():
    """双踩 next：两脉冲间隔 200ms → 触发一次 "next"（双踩），第一踩
    绝不先发单踩（单踩哨兵 "play" 绑同键，触发即失败）。"""
    h = Harness({"next": VK_NEXT, "play": VK_NEXT},   # 同键单+双共存（生产形态）
                gestures={"next": "double", "play": "single"})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)               # 第一踩
        time.sleep(0.2)                                # 踩间隔 200ms
        h.sim.pulse("next", width_ms=10)               # 第二踩
        h.wait_actions(1, 2.0)
        assert h.settle(0.5) == ["next"], h.app.names()
        # 双踩第二踩包号=2（每脉冲独立一包：40ms 静默窗短于踩间隔）
        assert [a["seq"] for a in h.sim.attempts] == [1, 2]
    finally:
        h.close()


def test_s3_triple_fast():
    """快速三连踩（150ms 间隔）：按引擎语义 = 前两踩成双踩 "next"，
    第三踩窗平静过期结算单踩 "play"。动作顺序与数量精确。"""
    h = Harness({"next": VK_NEXT, "play": VK_NEXT},
                gestures={"next": "double", "play": "single"})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)
        time.sleep(0.15)
        h.sim.pulse("next", width_ms=10)
        time.sleep(0.15)
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(2, 2.5)          # 双踩即时 + 单踩等 0.35s 窗
        assert h.settle(0.3) == ["next", "play"], h.app.names()
    finally:
        h.close()


def test_s4_pulse_widths_7_15ms():
    """连接脉冲抖动：7ms 与 15ms 按压宽混合，两脚都不丢（各自恰好一次）。"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=7)
        time.sleep(0.3)                 # > DEBOUNCE 0.15s：两脚独立
        h.sim.pulse("next", width_ms=15)
        h.wait_actions(2, 2.0)
        assert h.settle(0.2) == ["next", "next"], h.app.names()
    finally:
        h.close()


def test_s5_network_jitter():
    """网络抖动 10-80ms/包：三脚（500ms 间隔）仍各触发一次，无重复触发。
    送达断言按 seq 分组看终态：负载下服务端响应慢于客户端超时会触发
    合法同 seq 重试（生产语义），中间多出的尝试不算失败——「恰好一次
    动作」由 settle 钉死。"""
    h = Harness({"next": VK_NEXT})
    h.sim._jitter = (0.01, 0.08)
    h.enable_remote()
    try:
        for _ in range(3):
            h.sim.pulse("next", width_ms=10)
            time.sleep(0.5)
        h.wait_actions(3, 5.0)
        assert h.settle(0.3) == ["next"] * 3, h.app.names()
        last_by_seq = {}
        for a in h.sim.attempts:
            last_by_seq[a["seq"]] = a
        assert len(last_by_seq) == 3, h.sim.attempts
        assert all(a["code"] == 200 for a in last_by_seq.values()), \
            h.sim.attempts
    finally:
        h.close()


def test_s6_loss_and_retry():
    """丢包+重试：(a) 首发连接层丢失 → 同 seq 200ms 重发 → 恰好一次动作；
    (b) 响应在途丢失（PC 已处理）→ 同 seq 重发被 PC 会话去重收下（dup）
    → 仍恰好一次动作。两条都是客户端真实重试状态机跑出来的。"""
    # (a) 包未达 PC
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        h.sim._blackhole = 1
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(1, 3.0)
        assert h.settle(0.3) == ["next"], h.app.names()
        assert len(h.sim.attempts) == 2, h.sim.attempts
        assert h.sim.attempts[0]["exc"] and h.sim.attempts[0]["code"] is None
        assert h.sim.attempts[0]["seq"] == h.sim.attempts[1]["seq"] == 1
        assert h.sim.attempts[1]["code"] == 200
        # 重试间隔 ≈ RETRY_MS（下界排除立即重发；上界放宽——负载下
        # worker 线程 sleep 醒来后被调度延迟属正常，不改变重试语义）
        gap = h.sim.attempts[1]["t"] - h.sim.attempts[0]["t"]
        assert 0.12 <= gap <= 1.5, gap
    finally:
        h.close()
    # (b) PC 已处理但响应丢了
    h2 = Harness({"next": VK_NEXT})
    h2.enable_remote()
    try:
        h2.sim._drop_resp = 1
        h2.sim.pulse("next", width_ms=10)
        h2.wait_actions(1, 3.0)
        assert h2.settle(0.4) == ["next"], h2.app.names()   # 无重复动作
        assert len(h2.sim.attempts) == 2
        assert h2.sim.attempts[1]["code"] == 200
        assert h2.sim.attempts[1]["resp"].get("dup") is True  # PC 幂等收下
    finally:
        h2.close()


def test_s7_learning_capture():
    """学习模式：bridge.learning+capture 桩 → 事件进 capture 且 down/up
    间距保留（12ms 脉冲不被 40-80ms 网络抖动拉平）；不触发动作。"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    br = h.app.pedal.bridge
    cap = []
    br.learning = True
    br.capture = lambda vk, d, t: cap.append((vk, d, t))
    h.sim._jitter = (0.04, 0.08)
    try:
        _akc, _vk, t0, t1 = h.sim.pulse("next", width_ms=12)
        assert wait_until(lambda: len(cap) >= 2, 3.0)
        h.settle(0.3)
        assert h.app.names() == [], h.app.names()       # 学习期不触发
        assert [(v, d) for v, d, _t in cap] == \
            [(VK_NEXT, True), (VK_NEXT, False)]
        gap = cap[1][2] - cap[0][2]
        width = (t1 - t0) / 1000.0                       # 模拟器实测脉宽
        # 间距保留：|捕获间距 − 实发脉宽| ≤3ms，且远小于网络抖动下限
        assert abs(gap - width) <= 0.003, (gap, width)
        assert gap < 0.020 <= 0.5 * 0.04, (gap,)         # 未被抖动拉平
    finally:
        br.learning = False
        br.capture = None
        h.close()


def test_s8_silent_page():
    """静音：bridge.silent → 无动作；健康显示照常更新（remote_event_t）。"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    h.app.pedal.bridge.silent = True
    try:
        h.sim.pulse("next", width_ms=10)
        wait_until(lambda: h.app.pedal.bridge.remote_event_t > 0, 2.0)
        assert h.settle(0.3) == [], h.app.names()
        assert h.sim.attempts[0]["code"] == 200           # 线缆层正常收包
        h.app.pedal.bridge.silent = False                 # 解除静音恢复
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(1, 2.0)
        assert h.settle(0.2) == ["next"]
    finally:
        h.close()


def test_s9_heartbeat_5s():
    """心跳：5s 一发，两条 → PC 端 bridge.remote_hb_t 两次更新、全程零动作。
    （Kotlin startHb 首发延后 HB_MS：本场景 ~10.3s，全矩阵最长项。）"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        h.sim.start_heartbeat()
        assert wait_until(lambda: h.app.pedal.bridge.remote_hb_t > 0, 7.0)
        hb1 = h.app.pedal.bridge.remote_hb_t
        assert h.settle(0.2) == []
        assert wait_until(lambda: h.app.pedal.bridge.remote_hb_t > hb1 + 4.0,
                          7.0)
        assert h.settle(0.2) == []
        # 两条心跳都走了线缆且被 200 收下（间隔 ≈5s > 20ms 限流窗）
        hbs = [a for a in h.sim.attempts if a["code"] == 200
               and a["resp"] and a["resp"].get("ok")]
        assert len(hbs) == 2, h.sim.attempts
        gap = hbs[1]["t"] - hbs[0]["t"]
        assert 4.8 <= gap <= 5.6, gap
        assert h.app.names() == []
    finally:
        h.close()


def test_s10_app_restart_seq_reset():
    """APP 重启 seq 归 1：会话标识（sid 每进程随机）→ 重启后第一脚照常
    触发。旧实现按 device 域单调判重会把新 seq=1 误判 dup 静默吞掉——
    演出中途 APP 崩溃重启的第一脚往往是救场动作，此用例钉死回归。"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)                       # 旧会话 seq=1
        h.wait_actions(1, 2.0)
        assert h.app.names() == ["next"]
        h.sim.app_restart()            # 进程死亡重启：seq 归零 + sid 换新
        h.sim.pulse("next", width_ms=10)                       # 新会话 seq=1
        h.wait_actions(2, 2.0)
        assert h.settle(0.3) == ["next", "next"], h.app.names()
        # 新会话送达断言放宽到「重启后存在一次 200 且未被吞」（负载下同
        # seq 合法重试会让 attempts 错位）；「新 sid 不被旧基准吞」这一
        # 核心回归由 dup is not True 钉死
        ok = [a for a in h.sim.attempts[1:] if a["code"] == 200]
        assert ok and ok[0]["resp"].get("dup") is not True, h.sim.attempts
    finally:
        h.close()


def test_s11_pc_switch_403():
    """403：PC 开关关 → 403 后客户端停发（后续踩踏零 HTTP 尝试、按键
    透传给前台 App）；APP 端重新开启后恢复触发。403 当包按 Kotlin 语义
    丢弃（不补发）。"""
    h = Harness({"next": VK_NEXT})
    try:
        h.sim.pulse("next", width_ms=10)                       # PC 关 → 403
        wait_until(lambda: h.sim.disabled_by_pc, 2.0)
        assert h.sim.attempts[0]["code"] == 403
        assert any("停发" in d for d in h.sim.drops)
        assert h.app.names() == []
        h.sim.pulse("next", width_ms=10)                       # 停发期踩踏
        time.sleep(0.3)
        assert len(h.sim.attempts) == 1          # 无新 HTTP 尝试（客户端侧吞）
        assert h.sim._q.qsize() == 0             # 包被丢，不排队不补发
        h.enable_remote()                        # PC 开关打开（服务端）
        h.sim.reenable()                         # APP 端重新开启（清停发标志）
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(1, 2.0)
        assert h.settle(0.2) == ["next"], h.app.names()
        assert len(h.sim.attempts) == 2 and h.sim.attempts[1]["code"] == 200
    finally:
        h.close()


def test_s12_garbage_packets():
    """垃圾包/结构错包 → 4xx，不影响后续正常包（动作照常触发）；
    非法 seq/事件均不烧号（400 不记录去重表）；未知键=200 但事件被 PC
    丢弃、计入未识别计数并进诊断队列（scanCode/keyCode 双值可见）。"""
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        port = h.port

        def post(body):
            conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=3)
            try:
                conn.request("POST", "/pedal/event", body=body,
                             headers={"Content-Type": "application/json"})
                r = conn.getresponse()
                return r.status, json.loads(r.read().decode("utf-8"))
            finally:
                conn.close()

        # 非 JSON / 错路径 / 结构错包（全部黑盒直发；垃圾包用独立 device，
        # 避免占用被测客户端 pedal-usb 的去重空间——顺带验证按 device 隔离）
        G = "garbage-dev"
        assert post("not json{")[0] == 400
        bad = [
            {"device": G, "events": []},                              # seq 缺失
            {"device": G, "seq": True, "events": []},                 # bool seq
            {"device": G, "seq": 1, "events": "nope"},                # 非列表
            {"device": G, "seq": 1, "events": []},                    # 空
            {"device": G, "seq": 1,
             "events": [{"vk": True, "kc": 87, "down": True}]},       # bool vk
            {"device": G, "seq": 1,
             "events": [{"vk": LKC_NEXT, "kc": 87, "down": 1}]},      # 非 bool down
            {"device": G, "seq": 1,
             "events": [{"vk": LKC_NEXT, "kc": 87, "down": True,
                         "dt": -1}]},                                 # dt 越界
            {"device": G, "seq": 1,
             "events": [{"vk": LKC_NEXT, "kc": 87, "down": True,
                         "dt": 5001}]},
            {"device": G, "seq": 1, "et": True,                      # bool et
             "events": [{"vk": LKC_NEXT, "kc": 87, "down": True, "dt": 0}]},
        ]
        for b in bad:
            code, _j = post(json.dumps(b))
            assert code == 400, (code, b)
        # 垃圾不烧号：同 device 的合法包 seq=1 照常受理。信封合法但键不
        # 认识 → 200、事件被 PC 丢弃并进诊断队列（不触发动作）
        code, j = post(json.dumps(
            {"device": G, "seq": 1, "et": 1000,
             "events": [{"vk": 0x999, "kc": 555, "down": True, "dt": 0}]}))
        assert code == 200 and j.get("ok") and j.get("dup") is not True, j
        time.sleep(0.03)                 # > 20ms 包率闸
        code, j = post(json.dumps({"device": G, "seq": 2, "sid": "g1",
                                   "hb": True}))
        assert code == 200 and j.get("ok"), j           # 心跳信封合法
        # 错路径 404
        conn = http.client.HTTPConnection(_LOOPBACK, port, timeout=3)
        conn.request("POST", "/pedal/evnt", body="{}")
        assert conn.getresponse().status == 404
        conn.close()
        assert h.settle(0.2) == []       # 垃圾全程零动作
        # 正常包不受垃圾影响（pedal-usb 独立去重/限流/seq）
        h.sim.pulse("next", width_ms=10)                       # seq=1
        h.wait_actions(1, 2.0)
        assert h.settle(0.2) == ["next"], h.app.names()
        # 未知键诊断：scanCode/keyCode 双值进队列（排查换键位/表缺键）
        assert h.app.pedal.bridge.remote_unknown >= 1
        diag = [m for m in list(h.app.q.queue) if "未识别" in str(m)]
        assert diag and "scanCode=2457" in diag[0] \
            and "keyCode=555" in diag[0], diag
    finally:
        h.close()


def test_s13_rate_limit_storm():
    """限流风暴 → 429 且无动作/恢复：(a) 裸 HTTP 连发风暴全部 429
    （确定性：服务端 rate 锚点拨到未来），风暴平息后链路恢复；(b) 客户端
    真实突发——首发停顿 0.7s 令第二包积压，恢复后连发撞 20ms 包率闸，
    429 后按 RETRY_MS 重试送达；三脚各触发恰好一次。"""
    # (a) 裸风暴
    h = Harness({"next": VK_NEXT})
    h.enable_remote()
    try:
        codes = []
        # 确定性限流：把上包时刻锚到 +150ms 未来（服务端 now − rate < 20ms
        # 恒成立 → 6 连发全 429）。服务端 429 不刷新锚点，从测试线程预置
        # 「当下」值会跟回环 connect 的偶发慢启（>20ms）赛跑——已实测翻车；
        # 未来锚点属 fixture 式服务端状态注入（与 tests/test_bridge.py 手工
        # 清 _pedal_rate 同族），不改动被测代码。
        h.app._pedal_rate[DEVICE] = time.monotonic() + 0.15
        for i in range(1, 7):
            code, _j = h.sim.raw_post(json.dumps(
                {"device": DEVICE, "seq": i, "sid": "storm",
                 "et": 1000 + i,
                 "events": [{"vk": LKC_NEXT, "kc": 87, "down": True, "dt": 0},
                            {"vk": LKC_NEXT, "kc": 87, "down": False,
                             "dt": 10}]}))
            codes.append(code)
        assert codes == [429] * 6, codes
        assert h.settle(0.3) == []                     # 风暴零动作
        # 风暴平息后链路恢复：锚点过期，合法心跳 200（仍零动作）
        time.sleep(0.25)
        code, j = h.sim.raw_post(json.dumps({"device": DEVICE, "seq": 7,
                                             "sid": "storm", "hb": True}))
        assert code == 200 and j.get("ok"), (code, j)
        assert h.settle(0.2) == []
    finally:
        h.close()
    # (b) 客户端真实突发 + 429 恢复：首发 POST 停顿 0.7s 令第二包积压，
    # 恢复后连发撞 20ms 包率闸（429）→ 按 RETRY_MS 重试送达。三脚各触发
    # 恰好一次（第三脚远离突发窗，不受恢复期节拍影响）。
    # 【脚间距纪律】前两脚 0.3s（>DEBOUNCE 0.15 留倍余量）——本场景考的是
    # 限流恢复，不赌 DEBOUNCE 边缘（0.15 sleep 与 DEBOUNCE 等值的零余量
    # 曾致翻车；真实踏板双踩语义由 S2/S3 专项覆盖）。
    h2 = Harness({"next": VK_NEXT})
    h2.enable_remote()
    try:
        h2.sim._stall_once = 0.7        # 首发 POST 停顿 ⇒ 第二包积压成突发
        h2.sim.pulse("next", width_ms=10)
        time.sleep(0.3)                 # 第二包在停顿窗内入队（余量 >400ms）
        h2.sim.pulse("next", width_ms=10)
        time.sleep(1.6)                 # 第三脚远离突发恢复期
        h2.sim.pulse("next", width_ms=10)
        h2.wait_actions(3, 8.0)
        assert h2.settle(0.3) == ["next"] * 3, h2.app.names()
        codes = [a["code"] for a in h2.sim.attempts]
        assert 429 in codes, codes      # 突发确实撞了限流闸（确定性）
        assert all(c in (200, 429) for c in codes), codes
        # 200 计数 >=3：负载下同 seq 合法重试（响应迟到被 dup 收下也是
        # 200）可产生额外 200；「各恰好一次动作」由上方 settle 钉死，
        # dup 收下不触发第二动作正是会话去重的语义（不在此断言无 dup）
        assert len([c for c in codes if c == 200]) >= 3   # 三脚各送达

    finally:
        h2.close()


def test_s14_mapping_and_whitelist():
    """真实键码映射全链路（AOSP scanCode=LKC 命名空间）：媒体键/翻页键/
    F13（keyCode=0 走 scanCode 白名单域）各自落到正确 VK 动作；音量键在
    白名单外——客户端直接放行（零 HTTP 尝试，平板音量控制不被吞）。"""
    h = Harness(hid_binds={"next": VK_NEXT, "pgup": VK_PRIOR,
                           "enter": VK_RETURN, "f13": VK_F13})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)        # LKC 163 → VK_NEXT
        h.sim.pulse("pageup", width_ms=10)      # LKC 104 → VK_PRIOR
        h.sim.pulse("enter", width_ms=10)       # LKC 28 → VK_RETURN
        h.sim.pulse("f13", width_ms=10)         # LKC 183, keyCode=0 → VK_F13
        h.wait_actions(4, 3.0)
        assert h.settle(0.3) == ["next", "pgup", "enter", "f13"], \
            h.app.names()
        assert all(a["code"] == 200 for a in h.sim.attempts)
        # 音量键：白名单外 → 客户端侧放行，无 HTTP 尝试、无动作
        n = len(h.sim.attempts)
        h.sim.pulse("vol_up", width_ms=10)
        h.sim.pulse("mute", width_ms=10)
        h.settle(0.3)
        assert len(h.sim.attempts) == n, h.sim.attempts
        assert h.settle(0.2) == ["next", "pgup", "enter", "f13"]
    finally:
        h.close()


def test_s15_guard_synth_up():
    """卡死保险：DOWN 后 1s 无 UP → guard 合成 up → 单踩在双踩窗平静
    过期后结算（真实 Timer）。按键不悬挂、恰好一次动作。"""
    h = Harness({"play": VK_NEXT})
    h.enable_remote()
    try:
        akc, _vk = 87, LKC_NEXT
        h.sim.on_key(akc, _vk, True, h.sim._now_ms())   # 踩下不松开
        # guard 1s + flush 40ms + 双踩窗 0.35s + Timer 调度余量
        h.wait_actions(1, 4.0)
        assert h.settle(0.4) == ["play"], h.app.names()
        # 电脑端视角：down/up 成对送达（up 为 guard 合成）
        assert len(h.sim.attempts) == 1 and h.sim.attempts[0]["code"] == 200
    finally:
        h.close()


def test_s16_heartbeat_auto_recovery():
    """403 自愈：心跳在停发期间照发——PC 端把开关打开后，下一个心跳
    探到 200 → 客户端自动恢复转发（无需 APP 端人工重开开关）。"""
    h = Harness({"next": VK_NEXT})
    h.sim.start_heartbeat()
    try:
        h.sim.pulse("next", width_ms=10)                # PC 关 → 403 停发
        wait_until(lambda: h.sim.disabled_by_pc, 2.0)
        assert h.sim.attempts[0]["code"] == 403
        h.enable_remote()                               # 电脑端此时才打开
        # 下一跳心跳 ≤5s：探到 200 → 清停发标志 → 恢复
        assert wait_until(lambda: not h.sim.disabled_by_pc, 8.0)
        assert any("恢复" in d for d in h.sim.drops), h.sim.drops
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(1, 2.0)
        assert h.settle(0.2) == ["next"], h.app.names()
    finally:
        h.close()


def test_s17_et_anchor_cross_packet_double():
    """跨包 et 锚定的能力边界（诚实语义）：第二踩的包被人为迟到 0.5s——
    到达间距 ≫ 双踩窗 0.35s，第一踩的窗内定时器在包到达前已触发结算。
    【任何接收端都无法回溯补救此形态——蓝牙直连同款 RF 延迟同样劣化为
    两次单踩；et 锚定的价值是窗内间距不被到达抖动拉伸（白盒
    test_pedal_remote 的 RemoteClock 段已证）+ 事件序/弹跳/去抖保护，
    不是拯救超窗迟到包。】本用例钉死该边界：两脚零丢失、零重复、
    劣化为两次单踩而非吞脚。"""
    h = Harness({"next": VK_NEXT, "play": VK_NEXT},
                gestures={"next": "double", "play": "single"})
    h.enable_remote()
    try:
        h.sim.pulse("next", width_ms=10)                # 第一踩（立即送达）
        time.sleep(0.15)                                # 真实踩间隔 ~200ms
        h.sim._stall_once = 0.5                         # 第二踩的包迟到 0.5s
        h.sim.pulse("next", width_ms=10)
        h.wait_actions(2, 6.0)          # 单踩1（窗到期）+ 单踩2（新序列结算）
        assert h.settle(0.5) == ["play", "play"], h.app.names()
        assert len(h.sim.attempts) == 2                 # 两包零丢失
        assert all(a["code"] == 200 for a in h.sim.attempts)
        assert all(not (a["resp"] or {}).get("dup") for a in h.sim.attempts)
        # 到达间距确实 >0.4s：锚定不是时序侥幸，是真超窗
        arr_gap = h.sim.attempts[1]["t"] - h.sim.attempts[0]["t"]
        assert arr_gap > 0.4, arr_gap
    finally:
        h.close()
