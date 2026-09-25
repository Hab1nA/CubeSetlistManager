# -*- coding: utf-8 -*-
"""移动端遥控与翻谱推送：内嵌 HTTP 服务（只绑热点网卡 IP）+ 翻谱 MIDI 组合
判定 + 逐设备 HTTP POST 到平板 MacroDroid（Gesture 动作点击翻页）。
翻谱链路全程不经过浏览器——网页被冻结/杀掉翻谱照常（设计第八节架构前提）。
推送安全收口（设计第七节）：目标仅限已配对设备的 私网IPv4:固定端口 固定路径，
不收网页传来的任意 URL；协议/路径常量化、禁跟重定向（防 302 借道）。
配置在 config.json「webRemote」段（DEFAULT_WEB_REMOTE）。
所有控制动作经 app.calls 队列回 Tk 主线程执行，HTTP 线程不碰 Tk。"""
import json
import os
import queue
import sys
import threading
import time
import http.client
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cpr_meta
import hotspot
import kbd_auto

CMD_PREV = 36                # C2 → 上一页命令族（屏幕左半区）
CMD_NEXT = 37                # C#2 → 下一页命令族（屏幕右半区）
DEV_NOTES = tuple(range(48, 58))   # C3–A3 → 设备槽位 1–10
_WATCHED = frozenset((CMD_PREV, CMD_NEXT) + DEV_NOTES)
COMBO_WINDOW = 0.1           # 归并窗口：从窗口首音符起算（M0 实测校准点）
PUSH_TIMEOUT = 1.5           # 推送短超时：连接池省掉了握手，但平板 Wi-Fi
                             # 省电唤醒仍可达秒级；失败只记日志不拖累别的设备
TURN_PATH = "/turn"          # 翻谱设备固定接收路径（与配置说明一致）
APP_PORT = 8767              # APP 版页面端口（浏览器版=serverPort 8765）

DEFAULT_WEB_REMOTE = {
    "enabled": False,
    "serverPort": 8765,
    "appPort": 8767,           # APP 版页面端口（WebView 专用，浏览器不提供）
    "taskerPort": 8766,        # 翻谱接收端口：所有设备统一，APP 内可改需两端同步
    "midiIn": "",              # 翻谱信号 loopMIDI 端口名（设置页下拉选择）
    "devices": [],             # {slot,name,ip,enabled,screen:{w,h}（仅诊断）}
}


def _err(e):
    return str(e).strip() or type(e).__name__


def _port(v, default):
    try:
        n = int(v)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


# ---- 翻谱组合判定（纯函数，离线可测） ----

def combo_evaluate(notes):
    """窗口内音符集合 → (命令音符, 设备音符列表)；非法组合抛 ValueError
    （判定矩阵见设计第五节：恰一命令 + ≥1 设备 = 合法）。"""
    s = set(notes)
    cmd = s & {CMD_PREV, CMD_NEXT}
    devs = sorted(s & set(DEV_NOTES))
    if len(cmd) > 1:
        raise ValueError("上一页与下一页命令音符（C2/C#2）同时出现")
    if not cmd:
        raise ValueError("只有设备音符、没有命令音符（C2/C#2）")
    if not devs:
        raise ValueError("命令音符没有配对设备音符（C3–A3）")
    return cmd.pop(), devs


def valid_push_ip(ip):
    """推送目标只认点分 IPv4 的私网段（热点网段=RFC1918）与环回（本机自测
    假翻谱设备用）；公网与链路本地（169.254，含云元数据地址）一律拒发。"""
    parts = str(ip or "").split(".")
    if len(parts) != 4:
        return False
    try:
        o = [int(p) for p in parts]
    except ValueError:
        return False
    if any(p < 0 or p > 255 for p in o):
        return False
    a, b = o[0], o[1]
    return (a == 10 or (a == 192 and b == 168)
            or (a == 172 and 16 <= b <= 31) or a == 127)


# ---- 翻谱推送：语义指令 + 持久连接 ----

# 推送连接池：PC→APP 持久 HTTP 连接，每次翻页省一次 TCP 握手（热点上这是
# 往返耗时的大头）。http.client 原生不走系统代理（私网直连，urllib 会被
# Clash 等代理劫持），目标又受 valid_push_ip 限定。任何异常弃旧连接、新
# 连接重试一次——NanoHTTPD 空闲断开或 keep-alive 退化时自动落到每请求
# 新连接，正确性不受影响；新连接即失败说明设备不可达，不重试（翻页要快）。
_push_pool = {}                  # (ip, port) → [threading.Lock(), 连接或 None]


def _push_post(ip, port, body):
    """POST /turn 并读完应答（不读完无法复用连接）。返回错误文本或 None。"""
    key = (ip, port)
    entry = _push_pool.setdefault(key, [threading.Lock(), None])

    def once(conn):
        conn.request("POST", TURN_PATH, body=body,
                     headers={"Content-Type": "application/json"})
        conn.getresponse().read()

    with entry[0]:
        conn = entry[1]
        reused = conn is not None
        if conn is None:
            conn = http.client.HTTPConnection(ip, port, timeout=PUSH_TIMEOUT)
        try:
            once(conn)
            entry[1] = conn
            return None
        except Exception as e:
            try:
                conn.close()
            except OSError:
                pass
            entry[1] = None
            if not reused:
                return _err(e)
        try:
            conn = http.client.HTTPConnection(ip, port, timeout=PUSH_TIMEOUT)
            once(conn)
            entry[1] = conn
            return None
        except Exception as e:
            try:
                conn.close()
            except OSError:
                pass
            entry[1] = None
            return _err(e)


def push_turn(dev, dir_, tasker_port, test=False):
    """同步向单台设备推一页（语义协议：dir=next/prev；翻页方法与坐标
    组装在 APP 端按其本机设置执行，PC 不再关心方法与分辨率）。
    返回 (ok, 日志行)。到达即算成功——应答状态码不归链路管。
    test=True：APP 端先切后台再执行手势（测试按钮在前台是 APP 自身）。"""
    name = dev.get("name") or "设备%d" % dev.get("slot")
    ip = dev.get("ip")
    if not valid_push_ip(ip):
        return False, "翻谱推送失败（%s）：目标 IP 非法（仅限热点私网地址）" % name
    body = json.dumps({"dir": dir_, **({"test": 1} if test else {})})
    t0 = time.monotonic()
    err = _push_post(ip, tasker_port, body)
    if err:
        return False, "翻谱推送失败（%s %s → %s）：%s" % (name, dir_, ip, err)
    return True, "翻谱已推送（%s %s %dms）" % (
        name, dir_, int((time.monotonic() - t0) * 1000))


def _bind_web(app, registry, tport, ip, port, page, report=None):
    """热点刚（重）开时适配器 IPv4 可能尚未就位（PowerShell 拿到 IP 早于
    系统把地址配到网卡），立刻 bind 报 WinError 10049：限期重试兜底。"""
    deadline = time.monotonic() + 10.0
    while True:
        try:
            return WebServer((ip, port), app, registry, tport, page=page)
        except OSError as e:
            if time.monotonic() >= deadline:
                raise
            if report:
                report("绑定 %s:%d 未就绪（%s），重试…" % (ip, port, _err(e)))
            time.sleep(1.0)


def push_async(dev, dir_, tasker_port, report, test=False):
    """单设备推送放独立线程：一台离线不吃 0.5s 超时拖累其余设备。
    test=True 来自「测试上一页/下一页」按钮——APP 收到后先切后台再执行。"""
    threading.Thread(target=lambda: report(
        push_turn(dev, dir_, tasker_port, test=test)[1]),
        daemon=True).start()


# ---- 翻谱 MIDI：回调入队 → worker 归并窗口判组合 → 并行推送 ----

class ScoreTurnHub:
    """沿用 note_handler 的稳定性模式：winmm 回调线程只入队，daemon worker
    按 COMBO_WINDOW 收集音符判组合（设计第五节），推送每设备独立线程。"""

    def __init__(self, registry, tasker_port, report, clock=None):
        self.registry = registry
        self._tasker_port = tasker_port        # 函数：取当前翻谱接收端口
        self._report = report
        self._clock = clock or time.monotonic
        self.q = queue.Queue()
        self._alive = True
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, note):
        """winmm 回调线程调用：只入队，禁止任何重活。"""
        if note in _WATCHED:
            self.q.put((self._clock(), note))

    def close(self):
        self._alive = False
        self.q.put((0.0, None))                # 哨兵唤醒阻塞中的 worker

    def _loop(self):
        while self._alive:
            first = self.q.get()
            if first[1] is None:
                break
            batch = {first[1]}
            deadline = first[0] + COMBO_WINDOW
            while True:                        # 固定窗口收满即判；窗口外=新组合
                remain = deadline - self._clock()
                if remain <= 0:
                    break
                try:
                    _t, n = self.q.get(timeout=remain)
                except queue.Empty:
                    break
                if n is None:
                    self._alive = False
                    break
                batch.add(n)
            if not self._alive:
                break
            self._fire(batch)

    def _fire(self, batch):
        try:
            cmd, devs = combo_evaluate(batch)
        except ValueError as e:
            self._report("翻谱组合非法：%s（已忽略）" % e)
            return
        dir_ = "prev" if cmd == CMD_PREV else "next"
        for dev in self.registry.targets(devs):
            push_async(dev, dir_, self._tasker_port(), self._report)


# ---- 翻谱设备表（槽位 1–10） ----

def _clean_dev(d):
    dev = dict(d)
    dev["slot"] = int(d["slot"])
    if not 1 <= dev["slot"] <= 10:
        raise ValueError("slot")
    dev.setdefault("name", "设备%d" % dev["slot"])
    dev.setdefault("enabled", True)
    if dev.get("ip"):
        dev["ip"] = str(dev["ip"])
    return dev


class DeviceRegistry:
    """设备表：网页 HTTP 线程认领/修改，主线程持久化读取，锁串行化。
    IP 一律取自 HTTP 连接的 client_address（不收网页报文里的 IP）。
    变更经 on_change 回调（App 注入，回 Tk 主线程写 config）。"""

    def __init__(self, devices, report):
        self._lock = threading.Lock()
        self._devices = []
        for d in list(devices)[:10]:
            try:
                self._devices.append(_clean_dev(d))
            except (KeyError, TypeError, ValueError):
                continue
        self._report = report
        self.on_change = None

    def _changed(self):
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    def snapshot(self):
        with self._lock:
            return [dict(d) for d in self._devices]

    def by_ip(self, ip):
        with self._lock:
            return next((dict(d) for d in self._devices
                         if d.get("ip") == ip), None)

    def unregister(self, ip):
        """本机（按 IP 识别）取消登记：从设备表删除。返回 (device, err)。"""
        with self._lock:
            dev = next((d for d in self._devices if d.get("ip") == ip), None)
            if dev is None:
                return None, "本机尚未认领设备"
            self._devices.remove(dev)
            self._changed()
            self._report("设备取消登记：槽位 %d「%s」"
                         % (dev["slot"], dev["name"]))
            return dev, None

    def targets(self, notes):
        """设备音符 → 已配对且启用的设备列表；未配对/停用跳过并报告（不算
        非法，其余设备照发——设计第五节）。"""
        out, skips = [], []
        with self._lock:
            for n in notes:
                slot = n - DEV_NOTES[0] + 1
                d = next((x for x in self._devices
                          if x.get("slot") == slot), None)
                if d is None:
                    skips.append("槽位 %d 未配对（音符 %d），跳过" % (slot, n))
                elif not d.get("enabled", True):
                    skips.append("设备「%s」已停用，跳过" % d.get("name"))
                elif not d.get("ip"):
                    skips.append("设备「%s」没有 IP，跳过" % d.get("name"))
                else:
                    out.append(dict(d))
        for s in skips:
            self._report(s)
        return out

    def claim(self, ip, slot=None, name=None, screen=None):
        """认领：指定槽位（可接管）或自动取最小空槽；同 IP 重复认领=刷新
        信息（DHCP 换 IP 后重新认领即自愈）。返回 (device, err)。"""
        moved = None
        with self._lock:
            dev = next((d for d in self._devices if d.get("ip") == ip), None)
            if slot is not None and (dev is None or dev["slot"] != slot):
                if not 1 <= slot <= 10:
                    return None, "槽位号须在 1–10"
                other = next((d for d in self._devices
                              if d["slot"] == slot), None)
                if other is not None:
                    self._devices.remove(other)
                    moved = other.get("name")
            if dev is None and slot is None:
                used = {d["slot"] for d in self._devices}
                slot = next((n for n in range(1, 11) if n not in used), None)
                if slot is None:
                    return None, "槽位 1–10 已满"
            if dev is None:
                dev = {"slot": slot}
                self._devices.append(dev)
            elif slot is not None:
                dev["slot"] = slot
            if name:
                dev["name"] = str(name)[:20]
            dev.setdefault("name", "设备%d" % dev["slot"])
            dev.setdefault("enabled", True)
            if screen:
                dev["screen"] = screen
            dev["ip"] = ip
            out = dict(dev)
        if moved:
            self._report("槽位 %d 被「%s」接管（原「%s」）"
                         % (out["slot"], out["name"], moved))
        self._changed()
        self._report("设备认领：槽位 %d「%s」（%s）"
                     % (out["slot"], out["name"], ip))
        return out, None

    def update(self, ip, name=None, enabled=None):
        """本机（按 IP 识别）改名字/启停。返回 (device, err)。
        翻页方法归 APP 端本机设置（语义协议，PC 不再管）。"""
        with self._lock:
            dev = next((d for d in self._devices if d.get("ip") == ip), None)
            if dev is None:
                return None, "本机尚未认领设备"
            if name:
                dev["name"] = str(name)[:20]
            if enabled is not None:
                dev["enabled"] = bool(enabled)
            out = dict(dev)
        self._changed()
        return out, None


# ---- /state 快照（App._tick_banner 每 400ms 在 Tk 主线程重建） ----

def _fmt_dur(d):
    return cpr_meta.fmt_mmss(d) if d else ""


def _apk_file():
    """翻谱 APP 的 APK（程序目录下 CubeTurn.apk，构建后由 build.bat 拷入）。
    返回字节或 None。"""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.getcwd())
    try:
        with open(os.path.join(base, "CubeTurn.apk"), "rb") as f:
            return f.read()
    except OSError:
        return None


def build_snapshot(app, has_project, proj_name=None):
    """攒 /state 快照纯字典：整体引用替换，HTTP 线程整读（GIL 原子），
    不碰 Tk、不做 win32 枚举（has_project/proj_name 由 _tick_banner 顺路
    带出：open=有打开的工程，projName=工程标题里的真实名字，工程不一定
    在播放列表里——网页 NOW 与确认弹窗「从」名都要用它对齐 PC 横幅）。
    tstate/pos/dur：走带三态与当前工程进度（移动端状态行+进度条）。"""
    songs = []
    for key in app.pl_keys:
        s = app.by_key.get(key)
        if s is None:                       # 列表有而素材库缺：还能切回，保留显示
            songs.append({"name": key.split("/")[-1], "dur": ""})
            continue
        songs.append({"name": s["name"], "dur": _fmt_dur(app.durations.get(key))})
    cur = app.cur
    w = getattr(app, "watch", None)
    ctrl = getattr(app, "ctrl", None)
    if w is None or ctrl is None:
        tstate = "stopped"
    elif w.is_transport_live():
        tstate = "playing"
    else:
        tstate = "paused" if w.ever_live() else "stopped"
    key = (app.pl_keys[cur]
           if isinstance(cur, int) and 0 <= cur < len(app.pl_keys) else None)
    dur = float(app.durations.get(key) or 0.0) if key else 0.0
    pos = min(dur, max(0.0, w.active())) if (w is not None and dur > 0) else 0.0
    return {
        "ready": ctrl is not None,
        "busy": bool(ctrl is not None and ctrl.busy),
        "open": bool(has_project),
        "projName": proj_name,
        "confirm": bool(app.switch_confirm),
        "live": bool(app.watch is not None and app.watch.is_transport_live()),
        "tstate": tstate,
        "pos": round(pos, 1),
        "dur": round(dur, 1),
        "cur": cur,
        "songs": songs,
        # APP 端自动纠正用：APP 若误连 serverPort，从快照得知正确 APP 端口
        "appPort": getattr(getattr(app, "web", None), "app_port", None),
    }


# ---- 网页（单文件内嵌；ReaSetlistManager 范式：轮询+大按钮+断线徽标） ----

PAGE_COMMON = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="theme-color" content="#14161a">
<title>Cube 遥控</title>
<style>
:root{--bg:#14161a;--panel:#1e2127;--panel2:#262a32;--line:#2e323b;
  --fg:#e9ebef;--mut:#8b909a;--ok:#4ade80;--warn:#fbbf24;--err:#f87171;
  --acc:#7fe896}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--fg);min-height:100vh;
  font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei UI",sans-serif;
  user-select:none;-webkit-user-select:none;
  padding-bottom:calc(158px + env(safe-area-inset-bottom))}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}

header{display:flex;align-items:center;gap:10px;padding:12px 16px 0}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--err)}
.badge .dot{width:8px;height:8px;border-radius:50%;background:var(--err)}
.badge.on{color:var(--ok)}
.badge.on .dot{background:var(--ok)}
.spacer{flex:1}
.ghost{font-size:13px;color:var(--mut);border:1px solid var(--line);
  border-radius:8px;padding:7px 12px;background:var(--panel)}
.ghost:active{background:var(--panel2)}

.hero{padding:18px 20px 8px}
.lbl{font-size:11px;letter-spacing:.18em;color:var(--mut);font-weight:700}
.strow{display:flex;align-items:baseline;justify-content:space-between}
.tstate{font-weight:700}
.tstate.playing{color:var(--ok)}
.tstate.paused{color:var(--warn)}
.tstate.stopped{color:var(--mut)}
.nowrow{display:flex;align-items:baseline;gap:12px}
.nowrow .now{flex:1;min-width:0}
/* 尺寸壳在外（断点显隐）、状态字在内（JS 只改状态类）——字号走
   「壳→字」继承，JS 重建内层类名也不会把尺寸抹掉 */
.tstate-mini .tstate{font-size:12px}
.tstate-big .tstate{font-size:30px;font-weight:800}
/* 设备适配断点（CSS px）：手机竖屏 360–480、平板 ≥600，取 640 分界。
   手机：状态用小字（NOW 标签行右端，tstate-mini）、设置面板单列；
   平板：状态升为 NOW 歌名同规格同行（tstate-big）、面板保持两列 */
.tstate-mini{display:inline-block}
/* 设置面板双列：左列（连接/翻谱两组设置）自适应、右列（所有设备）固定
   30% 等高；手机断点下 devcols 退化单列。此前只写了断点覆盖规则漏了
   这组基础规则，所有设备都退化成了单列 */
.devcols{display:flex;gap:14px;align-items:stretch}
.devcols-l{flex:1;min-width:0}
.devcols-r{width:30%;flex:none;display:flex;flex-direction:column}
/* 翻谱地址行：平板与原版同构（addr-ctl=display:contents，子元素直接
   参与父行 flex）；手机竖屏一行摆不下 → addr-ctl 整体折为第二行
   （第二行端口贴左、指示器 margin-left:auto 与「应用」贴右） */
.addr-ctl{display:contents}
@media (max-width:640px){
  .tstate-big{display:none}
  .devcols{display:block}
  .devcols-r{width:auto;margin-top:12px}
  .fld-addr{flex-wrap:wrap}
  .fld-addr .addr-ctl{display:flex;flex:1 1 100%;align-items:center;gap:8px}
  .fld-addr #t-ip{white-space:nowrap}
  /* 第二行：端口贴左，指示器 margin-left:auto 把它与「应用」推到贴右 */
}
@media (min-width:641px){
  .tstate-mini{display:none}
}
.now{font-size:30px;font-weight:800;line-height:1.25;margin:4px 0 10px;
  word-break:break-all}
.prow{display:flex;align-items:center;gap:10px;margin:-4px 0 10px}
.pbar{flex:1;height:4px;background:var(--panel2);border-radius:2px;
  overflow:hidden}
.pbar i{display:block;height:100%;width:0;background:var(--acc)}
.ptime{flex:none;font-size:12px;color:var(--mut);
  font-variant-numeric:tabular-nums}
.nextrow{display:flex;align-items:baseline;gap:10px;font-size:16px;min-height:24px}
.next{color:var(--mut);word-break:break-all}

.list{list-style:none;margin:14px 12px 0;border:1px solid var(--line);
  border-radius:14px;overflow:hidden;background:var(--panel)}
.list li{display:flex;align-items:center;gap:12px;padding:13px 14px;
  border-bottom:1px solid var(--line);font-size:16px}
.list li:last-child{border-bottom:0}
.list li:active{background:var(--panel2)}
.list .no{width:22px;color:var(--mut);font-size:13px;flex:none;
  font-variant-numeric:tabular-nums}
.list .nm{flex:1;min-width:0;line-height:1.35}
.list .du{color:var(--mut);font-size:13px;flex:none;
  font-variant-numeric:tabular-nums}
.list li.cur{background:rgba(127,232,150,.10)}
.list li.cur .no,.list li.cur .du{color:var(--acc)}
.list li.cur .nm{color:var(--acc);font-weight:700}
.empty{padding:44px 16px;text-align:center;color:var(--mut);font-size:14px}

.bar{position:fixed;left:0;right:0;bottom:0;background:var(--panel);
  border-top:1px solid var(--line);
  padding:10px 12px calc(10px + env(safe-area-inset-bottom))}
.bar .row{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.bar .row.row3{grid-template-columns:repeat(3,1fr)}
.bar .row+.row{margin-top:8px}
.tbtn{height:52px;border-radius:12px;background:var(--panel2);
  border:1px solid var(--line);font-size:15px;font-weight:600}
.tbtn:active{background:#333845}
.tbtn[disabled]{opacity:.35}
.tbtn.panic{color:var(--err);border-color:rgba(248,113,113,.45)}
.tbtn.go{background:var(--acc);border-color:var(--acc);color:#10130f}
.tbtn.go:active{background:#8ff0a2}

.mask{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;
  align-items:flex-end;z-index:20}
.mask.center{align-items:center}
.mask.show{display:flex}
.sheet{width:100%;background:var(--panel);border-radius:18px 18px 0 0;
  padding:16px 16px calc(20px + env(safe-area-inset-bottom));
  max-height:88vh;overflow-y:auto}
.sheet h2,.dlg h2{font-size:17px;margin-bottom:4px}
.sub{font-size:12px;color:var(--mut);line-height:1.6}
.grp{border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:12px}
.fld{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:14px}
.fld:first-of-type{margin-top:0}
.fld label{width:60px;color:var(--mut);font-size:13px;flex:none}
.fld input[type=text],.fld select{flex:1;min-width:0;background:var(--panel2);
  color:var(--fg);border:1px solid var(--line);border-radius:8px;
  padding:9px 10px;font:inherit}
.fld input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc)}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
  padding:10px 14px;font-size:14px;font-weight:600;color:inherit;
  text-decoration:none}
.btn.pri{background:var(--acc);color:#10130f;border-color:var(--acc)}
.btn.on{background:var(--acc);color:#10130f;border-color:var(--acc);
  font-weight:700}
.btn:active{filter:brightness(1.12)}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.dlg .btn,.dlg .btns .btn{flex:1;text-align:center}
.dv{display:flex;align-items:center;gap:8px;padding:8px 0;font-size:14px;
  border-top:1px solid var(--line)}
.dv:first-of-type{border-top:0}
.dv .no{color:var(--mut);font-size:12px;width:34px;flex:none}
.dv .st{margin-left:auto;font-size:12px;color:var(--mut);flex:none}
.dv .st.on{color:var(--ok)}
.notice{margin-top:12px;margin-bottom:2px;padding:10px 16px;font-size:13px;color:var(--warn);
  background:rgba(251,191,36,.10);border-bottom:1px solid var(--line)}
.ind{flex:none;font-size:12px;padding:4px 10px;border-radius:99px;
  border:1px solid var(--line);color:var(--mut)}
.ind.ok{color:var(--ok);border-color:rgba(74,222,128,.45)}
.ind.bad{color:var(--err);border-color:rgba(248,113,113,.45)}
.mono{color:var(--fg);font-variant-numeric:tabular-nums}
.dlg{margin:auto;width:min(92vw,380px);background:var(--panel);
  border:1px solid var(--line);border-radius:16px;padding:18px}
.dlg p{font-size:15px;line-height:1.7;margin:8px 0 4px}
#toast{position:fixed;left:50%;bottom:calc(180px + env(safe-area-inset-bottom));
  transform:translateX(-50%);background:rgba(0,0,0,.75);color:#fff;
  font-size:13px;border-radius:99px;padding:9px 16px;opacity:0;
  transition:opacity .25s;pointer-events:none;z-index:40;max-width:80vw}
#toast.show{opacity:1}
</style>
</head>
<body>
<header>
  <span class="badge" id="badge"><span class="dot"></span>
    <span id="bt">连接中</span></span>
  <span class="spacer"></span>
  __ACC_IND__
  __RIGHT_BTN__
</header>

<div class="notice" id="env-notice" data-page="__PAGE_TYPE__" hidden></div>

<section class="hero">
  <div class="strow"><span class="lbl">NOW</span>
    <span class="tstate-mini"><span class="tstate stopped" id="tstate">未在播放</span></span></div>
  <div class="nowrow"><span class="now" id="now">—</span>
    <span class="tstate-big"><span class="tstate stopped">未在播放</span></span></div>
  <div class="prow" id="prow" hidden>
    <div class="pbar"><i id="pfill"></i></div>
    <span class="ptime" id="ptime"></span></div>
  <div class="nextrow"><span class="lbl">NEXT</span>
    <span class="next" id="next">—</span></div>
</section>

<ol class="list" id="list"></ol>
<div class="empty" id="empty" hidden>播放列表为空</div>

<footer class="bar">
  <div class="row row3">
    <button class="tbtn go" data-cmd="play">▶ 开始</button>
    <button class="tbtn" data-cmd="pause" id="b-pause">⏸ 暂停</button>
    <button class="tbtn" data-cmd="resume">▷ 继续</button>
  </div>
  <div class="row">
    <button class="tbtn" data-cmd="prev">⏮ 上一首</button>
    <button class="tbtn" data-cmd="rewind">⟲ 回零</button>
    <button class="tbtn" data-cmd="next">⏭ 下一首</button>
    <button class="tbtn panic" data-cmd="panic">■ 全停</button>
  </div>
</footer>

<div class="mask" id="m-confirm">
  <div class="dlg">
    <h2>切换工程</h2>
    <p id="c-text"></p>
    <div class="btns">
      <button class="btn" id="c-no">取消</button>
      <button class="btn pri" id="c-yes">确定切换</button>
    </div>
  </div>
</div>

__DEV_PANEL__

<div id="toast"></div>

<script>
"use strict";
var st=null,conn=null,pend=-1,toastT=null,lastSig=null;

function $(id){return document.getElementById(id)}
function el(tag,cls,txt){var e=document.createElement(tag);
  if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}

function toast(m){var t=$("toast");t.textContent=m;t.classList.add("show");
  clearTimeout(toastT);toastT=setTimeout(function(){
    t.classList.remove("show")},1800)}

/* 安卓返回手势/返回键经桥调 uiBack：关闭最上层浮层（.mask.show，
 * DOM 靠后=视觉在上，倒序找第一个）——设置面板、「选择谱面 App」及
 * 未来新增的二级/三级页面只要用 .mask 结构就自动被覆盖；
 * 无浮层可关返回 false，原生侧走默认行为 */
function uiBack(){
  var ms=document.querySelectorAll(".mask");
  for(var i=ms.length-1;i>=0;i--)
    if(ms[i].classList.contains("show")){
      ms[i].classList.remove("show");
      return "true";
    }
  return "false";
}

function post(path,body){return fetch(path,{method:"POST",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify(body||{})}).then(function(r){
    return r.json().then(function(j){return{code:r.status,j:j}})})}

function setConn(on){if(on===conn)return;conn=on;
  var b=$("badge");b.classList.toggle("on",on);
  $("bt").textContent=on?"已连接":"连接断开，重试中"}

function render(){
  if(!st)return;
  var cur=typeof st.cur==="number"?st.cur:-1;
  var sig=[st.busy,st.ready,st.live,cur,st.projName,
    JSON.stringify(st.songs)].join("|");
  if(sig===lastSig)return;      // 无变化不动 DOM：切歌期间每次重绘都是
  lastSig=sig;                  // 一次帧提交（Chromium 合成过渡会闪白）
  var s=st.songs?st.songs[cur]:null;
  // NOW/NEXT 对齐 PC 横幅：显示真实打开的工程名（工程可能不在播放列表
  // 里，cur 为空时按歌名索引查不到）。切歌期间保持切歌前的歌名不动
  // （当前状态行已提示「切换中…」，这里不再重复），切完刷成新值
  if(!st.busy){
    $("now").textContent=st.projName?st.projName:
      (st.ready?"（无打开的工程）":"主程序启动中…");
    var n=st.songs?st.songs[cur+1]:null;
    $("next").textContent=n?n.name:(s?"（末尾）":"—");
  }
  var L=$("list");L.textContent="";
  for(var i=0;i<(st.songs||[]).length;i++){
    var li=el("li");if(i===cur)li.className="cur";
    li.appendChild(el("span","no",String(i+1)));
    li.appendChild(el("span","nm",st.songs[i].name));
    li.appendChild(el("span","du",st.songs[i].dur||""));
    li.addEventListener("click",onRow.bind(null,i));
    L.appendChild(li);
  }
  $("empty").hidden=st.songs?st.songs.length>0:true;
  var bs=document.querySelectorAll(".tbtn");
  for(var k=0;k<bs.length;k++){var b=bs[k];
    b.disabled=!!st.busy&&b.dataset.cmd!=="panic";
    if(!st.ready&&b.dataset.cmd!=="panic")b.disabled=true;}
}

function fmtT(s){s=Math.max(0,Math.floor(s));return Math.floor(s/60)+":"+
  ("0"+(s%60)).slice(-2)}

/* 播放状态行 + 进度条：pos 每 tick 都变，独立于 render 的大 sig 门
   （render 会重建整个列表 DOM，跟着 pos 刷会一秒两抖），只动这几个节点。
   状态两副本（手机小字/平板大字，断点显隐），一并更新 */
function renderLive(){
  if(!st)return;
  var ts=st.busy?"busy":(st.tstate||"stopped");
  var txt={playing:"播放中",paused:"已暂停",stopped:"未在播放",
    busy:"切换中…"}[ts];
  var cls="tstate "+(ts==="busy"?"paused":ts);
  var tes=document.querySelectorAll(".tstate");
  for(var i=0;i<tes.length;i++){
    tes[i].textContent=txt;
    tes[i].className=cls;
  }
  var prow=$("prow");
  var cur=typeof st.cur==="number"?st.cur:-1;
  var d=typeof st.dur==="number"?st.dur:0;
  if(cur<0||!d){prow.hidden=true;return}
  prow.hidden=false;
  var pos=typeof st.pos==="number"?st.pos:0;
  $("pfill").style.width=Math.min(100,pos/d*100)+"%";
  $("ptime").textContent=fmtT(pos)+" | "+fmtT(d-pos);
}

function onRow(i){
  if(!st||st.busy||!st.ready)return;
  if(st.confirm&&st.open&&i!==st.cur){
    pend=i;
    // 「从」名与 PC 弹窗同源：真实工程名优先（cur 为空也拿得到）
    var a=st.projName||(st.songs[st.cur]&&st.songs[st.cur].name),
        b=st.songs[i];
    $("c-text").textContent="从《"+(a||"？")+"》切换到《"+
      b.name+"》？当前工程将被关闭（自动保存）。";
    $("m-confirm").classList.add("show");
  }else cmd("switch",i);
}
$("c-no").addEventListener("click",function(){
  $("m-confirm").classList.remove("show")});
$("c-yes").addEventListener("click",function(){
  $("m-confirm").classList.remove("show");
  if(pend>=0)cmd("switch",pend);pend=-1});

var tbs=document.querySelectorAll(".tbtn");
for(var ti=0;ti<tbs.length;ti++){
  tbs[ti].addEventListener("click",function(){cmd(this.dataset.cmd)});
}

function cmd(action,index){
  post("/cmd",{action:action,index:index}).then(function(r){
    if(!r.j||!r.j.ok)toast((r.j&&r.j.error)||"操作失败");
    refresh();
  },function(){setConn(false);toast("发送失败")});
}

function refresh(){
  fetch("/state",{cache:"no-store"}).then(function(r){return r.json()})
    .then(function(j){st=j;setConn(true);render();renderLive()},
          function(e){setConn(false);
            try{toast("刷新失败:"+e)}catch(_){}});
  try{if(window.updateAcc)updateAcc()}catch(e){}
  try{if(window.refreshUsageTip)refreshUsageTip()}catch(e){}
  try{if(window.refreshTarget)refreshTarget()}catch(e){}
  try{refreshEnv()}catch(e){}
  // APP 内误连网页端口时自动纠正（端口从 /state 动态获取，不写死）
  try{if(window.CubeApp&&st&&typeof st.appPort==="number"&&st.appPort>0
      &&(location.port||"80")!==String(st.appPort))
    CubeApp.switchToAppPort(String(st.appPort))}catch(e){}
}

function refreshEnv(){
  var n=$("env-notice");if(!n)return;
  var page=n.getAttribute("data-page");
  var inApp=!!window.CubeApp;         // 桥存在=运行在 APP 的 WebView 内
  var msg=null;
  if(page==="app"&&!inApp)
    msg="本页在浏览器中打开——翻谱功能请在 Cube 翻谱 APP 内使用";
  if(page==="browser"&&inApp){
    var ap=(st&&typeof st.appPort==="number")?st.appPort:null;
    msg=ap===null?"正在获取 APP 连接端口…"
      :"检测到 APP 连接端口为 "+ap+"——正在自动切换…";
  }
  if(msg){n.hidden=false;n.textContent=msg}
  else n.hidden=true;
}
try{refreshEnv()}catch(e){}

__DEV_JS__
setInterval(refresh,1000);
document.addEventListener("visibilitychange",function(){
  if(!document.hidden)refresh()});
refresh();
</script>
</body>
</html>
"""

# ---- 双版页面：浏览器端无翻谱功能（右上角下载 APP，留苹果端占位）；
# APP 端（8767）承载全部翻谱设置 ----

BTN_BROWSER = """<a class="ghost" href="/app.apk" style="color:var(--acc);
  text-decoration:none">下载 APP</a><!-- __RIGHT_BTN2__ 预留：苹果端按钮 -->"""

BTN_APP = """<button class="ghost" id="b-dev">设置</button>"""

DEV_PANEL_APP = """
<div class="mask" id="m-dev">
  <div class="sheet">
    <h2>设置</h2>
    <div class="devcols">
      <!-- 左列：连接设置 + 翻谱设置（手机断点下 devcols 变单列） -->
      <div class="devcols-l">
        <div class="grp">
          <div class="sub">连接设置</div>
          <div class="fld" style="margin-top:12px"><label>地址</label>
            <input type="text" id="c-addr"></div>
          <div class="btns" style="margin-top:12px">
            <button class="btn pri" id="c-save" style="flex:1;text-align:center">保存并重启</button>
          </div>
          <div class="sub" style="margin-top:10px">修改地址后需重启本 APP 生效；
            取消则保持原地址不变。</div>
        </div>
        <div class="grp">
          <div class="sub">翻谱设置</div>
      <div class="sub" style="margin-top:8px">认领本机并按谱面 App 支持情况
        选择翻页方法（存在本机，立即生效）。无障碍未开启时点按/滑动不可用
        （仅媒体键可用）。</div>
          <div class="sub" style="margin-top:6px">提高服务存活：建议开启系统
            「无障碍快捷方式」，并允许本 APP 的电池优化豁免（首次启动会请求）。</div>
      <div id="dev-own" style="margin-top:10px"></div>
      <div class="fld" style="margin-top:10px"><label>翻页方法</label>
        <div id="m-method" class="btns" style="flex:1;margin-top:0;gap:6px">
          <button class="btn m-opt" data-v="tap"
            style="flex:1;text-align:center;padding:9px 0">点按</button>
          <button class="btn m-opt" data-v="double"
            style="flex:1;text-align:center;padding:9px 0">双击</button>
          <button class="btn m-opt" data-v="swipe"
            style="flex:1;text-align:center;padding:9px 0">滑动</button>
          <button class="btn m-opt" data-v="media"
            style="flex:1;text-align:center;padding:9px 0">媒体键</button>
        </div>
      </div>
      <div class="fld" style="margin-top:10px"><label>谱面 App</label>
            <span id="t-target" class="mono">自动检测</span>
            <button class="btn" id="t-pick" style="margin-left:auto;flex:none;padding:9px 12px">选择</button>
          </div>
          <div class="sub" style="margin-top:8px">翻页测试会自动切回指定的谱面
            APP，并执行翻页手势。</div>
          <div class="sub" id="usage-tip" style="margin-top:6px;color:var(--warn)"
            >未授权「使用情况访问」——无法自动切回谱面 App，将退回切到桌面。</div>
          <div class="btns" id="usage-btns" style="margin-top:8px">
            <button class="btn" id="usage-grant">去授权使用情况访问</button>
          </div>
          <div class="fld fld-addr" style="margin-top:10px"><label>翻谱地址</label>
            <span id="t-ip" class="mono"></span>
            <span class="addr-ctl">
              <input type="text" id="t-port" style="width:70px;flex:none">
              <span class="ind" id="t-ind" style="margin-left:auto;flex:none">…</span>
              <button class="btn" id="t-apply" style="flex:none;padding:9px 12px">应用</button>
            </span>
          </div>
          <div class="sub" style="margin-top:8px">电脑端向乐队所有设备推送翻谱信号
            统一使用此端口——请确保电脑端与所有移动设备的此端口设置一致。</div>
        </div>
      </div>
      <!-- 右列：所有设备（等高，宽度显著小于左列；手机断点堆叠在下方） -->
      <div class="devcols-r">
        <div class="grp" style="flex:1">
          <div class="sub">所有设备</div>
          <div id="dev-all" style="margin-top:6px"></div>
        </div>
      </div>
    </div>
    <div class="sub" style="margin-top:14px;text-align:center">APP 版本 <span id="app-ver"></span></div>
  </div>
</div>

<div class="mask" id="m-apps">
  <div class="sheet">
    <h2>选择谱面 App</h2>
    <div class="sub">测试翻页与自动切回的目标应用</div>
    <div id="apps-list" style="margin-top:10px;max-height:50vh;overflow-y:auto"></div>
    <div class="btns" style="margin-top:12px">
      <button class="btn" id="apps-clear">清除（自动检测）</button>
      <button class="btn" id="apps-close" style="flex:1;text-align:center">关闭</button>
    </div>
  </div>
</div>
"""

DEV_JS_APP = """
/* ---- 设置面板（仅 APP 版页面）：连接设置 + 翻谱设置 ---- */
function updateAcc(){
  if(!window.CubeApp)return;
  var i=$("acc-ind");if(!i)return;
  var ok=CubeApp.accEnabled()===true;
  i.textContent=ok?"无障碍开":"无障碍关";
  i.className="ind"+(ok?" ok":" bad");
}
function refreshUsageTip(){
  if(!window.CubeApp)return;
  var tip=$("usage-tip"),btns=$("usage-btns");if(!tip)return;
  if(CubeApp.usageAccess()===true){tip.style.display="none";btns.style.display="none"}
  else{tip.style.display="block";btns.style.display="flex"}
}
function refreshTarget(){
  if(!window.CubeApp)return;
  var t=$("t-target");if(!t)return;
  var name=CubeApp.turnTarget();
  t.textContent=name||"自动检测（最近使用的第三方应用）";
}
$("t-pick").addEventListener("click",function(){
  if(!window.CubeApp)return;
  var apps=JSON.parse(CubeApp.listApps());
  var box=$("apps-list");box.textContent="";
  for(var i=0;i<apps.length;i++){(function(app){
    var row=el("div","dv");row.style.cursor="pointer";
    row.appendChild(el("span",null,app.name));
    row.addEventListener("click",function(){
      CubeApp.setTurnTarget(app.pkg,app.name);
      $("m-apps").classList.remove("show");
      refreshTarget();toast("谱面 App："+app.name);
    });
    box.appendChild(row);
  })(apps[i])}
  $("m-apps").classList.add("show");
});
$("apps-clear").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.clearTurnTarget();
  $("m-apps").classList.remove("show");
  refreshTarget();toast("已清除，使用自动检测");
});
$("apps-close").addEventListener("click",function(){
  $("m-apps").classList.remove("show")});
$("usage-grant").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.openUsageAccess()});
$("b-acc").addEventListener("click",function(){
  if(window.CubeApp)CubeApp.openAccSettings()});
$("b-dev").addEventListener("click",openDev);
/* 翻页方法：四选一分段按钮，选中态存 APP 本机（语义协议由 APP 组装动作） */
function renderMethod(){
  if(!window.CubeApp)return;
  var cur=null;try{cur=CubeApp.turnMethod()}catch(e){}
  var bs=document.querySelectorAll(".m-opt");
  for(var i=0;i<bs.length;i++)
    bs[i].classList.toggle("on",bs[i].getAttribute("data-v")===cur);
}
var _mbs=document.querySelectorAll(".m-opt");
for(var _mi=0;_mi<_mbs.length;_mi++)(function(b){
  b.addEventListener("click",function(){
    if(!window.CubeApp)return;
    if(CubeApp.setTurnMethod(b.getAttribute("data-v"))){
      renderMethod();toast("翻页方法："+b.textContent);
    }else toast("设置失败");
  });
})(_mbs[_mi]);
// 触摸遮罩（面板区域以外）关闭：移动端标准交互
$("m-dev").addEventListener("click",function(e){
  if(e.target===this)$("m-dev").classList.remove("show")});
$("m-apps").addEventListener("click",function(e){
  if(e.target===this)$("m-apps").classList.remove("show")});

function openDev(){
  $("m-dev").classList.add("show");
  $("c-addr").value=location.href;
  refreshInd();
  refreshUsageTip();
  refreshTarget();
  renderMethod();
  try{$("app-ver").textContent=CubeApp?CubeApp.version():"?"}catch(e){}
  fetch("/devices",{cache:"no-store"}).then(function(r){return r.json()})
    .then(function(d){renderDev(d);
      $("t-ip").textContent="http://"+d.you+":";},
      function(){toast("取设备列表失败")});
}

function setInd(ok,text){var i=$("t-ind");i.textContent=text;
  i.className="ind"+(ok?" ok":" bad")}

function refreshInd(){
  if(!window.CubeApp)return;
  var p=CubeApp.turnPort();
  $("t-port").value=p;
  setInd(!!p,p?p+" 端口监听中":"未监听");
}

$("t-apply").addEventListener("click",function(){
  if(!window.CubeApp)return;
  var p=$("t-port").value.trim();
  if(!/^[0-9]{2,5}$/.test(p)||parseInt(p,10)<1024||parseInt(p,10)>65535){
    setInd(false,"端口非法");return}
  var ok=CubeApp.setTurnPort(p);
  setInd(ok,ok?p+" 端口监听中":"端口不可用");
  toast(ok?"端口已应用：请同步电脑端「APP 翻谱地址」":"端口被占用或无法监听");
});

$("c-save").addEventListener("click",function(){
  if(!window.CubeApp){toast("桥未就绪");return}
  var url=$("c-addr").value.trim();
  if(!url){toast("地址不能为空");return}
  var r=CubeApp.requestAddressChange(url);
  if(r==="restart"){toast("地址已保存，重启中…")}
  else if(r==="same"){toast("地址未变化");$("c-addr").value=location.href}
  else{toast("已取消：地址保持原值");$("c-addr").value=location.href}
});

function dpr(){return window.devicePixelRatio||1}
function phys(){return{w:Math.round(screen.width*dpr()),
  h:Math.round(screen.height*dpr())}}

function devTest(dir){
  post("/device/test",{dir:dir}).then(function(r){
    toast(r.j&&r.j.ok?"测试已发送：已切到后台":((r.j&&r.j.error)||"发送失败"));
  },function(){toast("发送失败")});
}

function renderDev(d){
  var all=d.devices||[],own=null;
  for(var i=0;i<all.length;i++)if(all[i].ip===d.you)own=all[i];
  var g=el("div","grp");
  if(!own){
    g.appendChild(el("div","sub","本机未登记"));
    var f1=el("div","fld");f1.appendChild(el("label",null,"名字"));
    var inp=el("input");inp.type="text";inp.value="谱台";inp.id="d-name";
    f1.appendChild(inp);g.appendChild(f1);
    var f2=el("div","fld");f2.appendChild(el("label",null,"槽位"));
    var sel=el("select");sel.id="d-slot";
    sel.appendChild(new Option("自动",""));
    for(var s=1;s<=10;s++)sel.appendChild(new Option(String(s),String(s)));
    f2.appendChild(sel);g.appendChild(f2);
  var bts=el("div","btns");
  var bb=el("button","btn pri","认领本机");
  bb.addEventListener("click",function(){
    var body={name:$("d-name").value.trim()||"谱台",screen:phys()};
    var sv=$("d-slot").value;if(sv)body.slot=parseInt(sv,10);
      post("/device/claim",body).then(function(r){
        if(r.j&&r.j.ok){toast("已认领槽位 "+r.j.device.slot);openDev();}
        else toast((r.j&&r.j.error)||"认领失败");
      },function(){toast("认领失败")});
    });
    bts.appendChild(bb);g.appendChild(bts);
  }else{
    g.appendChild(el("div","sub",
      "本机：槽位 "+own.slot+"（"+(own.ip||"")+"）"));
    var f1=el("div","fld");f1.appendChild(el("label",null,"名字"));
    var inp=el("input");inp.type="text";inp.value=own.name;inp.id="d-name";
    f1.appendChild(inp);g.appendChild(f1);
    var f2=el("div","fld");f2.appendChild(el("label",null,"启用"));
    var ck=el("input");ck.type="checkbox";ck.id="d-en";
    ck.checked=own.enabled!==false;
    f2.appendChild(ck);g.appendChild(f2);
    var bts=el("div","btns");
    var b1=el("button","btn","保存修改");
    b1.addEventListener("click",function(){
      post("/device/update",{name:$("d-name").value.trim(),
        enabled:$("d-en").checked})
        .then(function(r){toast(r.j&&r.j.ok?"已保存":
          ((r.j&&r.j.error)||"保存失败"));if(r.j&&r.j.ok)openDev();},
          function(){toast("保存失败")});
    });
    var b0=el("button","btn","取消登记");
    b0.addEventListener("click",function(){
      post("/device/unregister",{}).then(function(r){
        toast(r.j&&r.j.ok?"已取消登记":((r.j&&r.j.error)||"操作失败"));
        if(r.j&&r.j.ok)openDev();
      },function(){toast("操作失败")});
    });
    var b2=el("button","btn","测试上一页");
    b2.addEventListener("click",function(){devTest("prev")});
    var b3=el("button","btn","测试下一页");
    b3.addEventListener("click",function(){devTest("next")});
    bts.appendChild(b1);bts.appendChild(b0);
    bts.appendChild(b2);bts.appendChild(b3);
    g.appendChild(bts);
  }
  var box=$("dev-own");box.textContent="";box.appendChild(g);
  var allbox=$("dev-all");allbox.textContent="";
  if(!all.length)allbox.appendChild(el("div","sub","还没有任何设备登记"));
  for(var j=0;j<all.length;j++){
    var dd=all[j],row=el("div","dv");
    row.appendChild(el("span","no","#"+dd.slot));
    row.appendChild(el("span",null,dd.name+(dd.ip===d.you?"（本机）":"")));
    row.appendChild(el("span","st"+(dd.enabled!==false?" on":""),
      dd.enabled!==false?"启用":"停用"));
    allbox.appendChild(row);
  }
}
"""

PAGE_BROWSER = (PAGE_COMMON
                .replace("__PAGE_TYPE__", "browser")
                .replace("__RIGHT_BTN__", BTN_BROWSER)
                .replace("__ACC_IND__", "")
                .replace("__DEV_PANEL__", "")
                .replace("__DEV_JS__", ""))
PAGE_APP = (PAGE_COMMON
            .replace("__PAGE_TYPE__", "app")
            .replace("__RIGHT_BTN__", BTN_APP)
            .replace("__ACC_IND__",
                     '<span class="ind" id="acc-ind">无障碍…</span>\n'
                     '  <button class="ghost" id="b-acc" style="margin-left:8px">无障碍</button>')
            .replace("__DEV_PANEL__", DEV_PANEL_APP)
            .replace("__DEV_JS__", DEV_JS_APP))

# ---- HTTP 服务 ----

class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, app, registry, tasker_port, page=PAGE_BROWSER):
        self.app = app
        self.registry = registry
        self.tasker_port = tasker_port      # 函数：取当前翻谱接收端口
        self.page = page
        super().__init__(addr, _Handler)

    def handle_error(self, request, client_address):
        # 平板切后台/刷新会硬断 keep-alive 连接：常态，不刷 stderr
        import sys
        if isinstance(sys.exc_info()[1],
                      (ConnectionError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CubeSetlist/1.0"

    def log_message(self, fmt, *args):
        pass                                # /state 每秒轮询，不能刷 GUI 日志

    # -- 应答小工具 --

    def _send(self, code, ctype, data, dispo=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if dispo:
            self.send_header("Content-Disposition", dispo)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionError, BrokenPipeError):
            pass                            # 平板切后台轮询断开：常态，不报

    def _json(self, code, obj):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # -- GET --

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, "text/html; charset=utf-8",
                       self.server.page.encode("utf-8"))
        elif path == "/state":
            snap = getattr(self.server.app, "_web_snap", None) or {}
            self._json(200, snap)
        elif path == "/devices":
            self._json(200, {"devices": self.server.registry.snapshot(),
                             "you": self.client_address[0]})
        elif path == "/app.apk":
            apk = _apk_file()
            if apk is None:
                self._json(404, {"error": "APK 未找到（先在 mobile/ 跑 "
                                          "gradlew assembleDebug 并拷贝）"})
                return
            self._send(200, "application/vnd.android.package-archive", apk,
                       dispo="attachment; filename=CubeTurn.apk")
        else:
            self._json(404, {"error": "未知路径"})

    # -- POST --

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "请求体不是合法 JSON"})
            return
        ip = self.client_address[0]
        try:
            for fn, p in ((self._h_cmd, "/cmd"),
                          (self._h_claim, "/device/claim"),
                          (self._h_update, "/device/update"),
                          (self._h_unregister, "/device/unregister"),
                          (self._h_test, "/device/test"),
                          (self._h_diag, "/diag")):
                if path == p:
                    fn(body, ip)
                    return
            self._json(404, {"error": "未知路径"})
        except Exception as e:
            self._json(500, {"error": _err(e)})

    def _ok(self, **kw):
        self._json(200, dict(ok=True, **kw))

    def _h_cmd(self, body, _ip):
        app = self.server.app
        action = body.get("action")
        # 全停永远可用（切换卡住时的安全阀），其余动作切换中禁用（设计第四节）
        if action == "panic":
            app.calls.put(app._panic)
        elif getattr(app, "_web_snap", {}).get("busy"):
            self._json(409, {"error": "切换中，稍候再操作"})
            return
        elif action in ("play", "pause", "resume", "stop", "rewind"):
            app.calls.put(lambda a=action: app._transport(a))
        elif action == "next":
            app.calls.put(app._next)
        elif action == "prev":
            app.calls.put(app._prev)
        elif action == "switch":
            i = body.get("index")
            if not isinstance(i, int) or isinstance(i, bool) \
                    or not 0 <= i < len(app.pl_keys):
                self._json(400, {"error": "index 越界"})
                return
            app.calls.put(lambda i=i: app._switch(i, "手动"))
        else:
            self._json(400, {"error": "未知 action"})
            return
        self._ok()

    def _h_claim(self, body, ip):
        screen = body.get("screen") or {}
        try:
            screen = {"w": int(screen["w"]), "h": int(screen["h"])}
        except (KeyError, TypeError, ValueError):
            screen = None
        slot = body.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int):
            slot = None
        dev, err = self.server.registry.claim(
            ip, slot=slot, name=body.get("name"), screen=screen)
        if err:
            self._json(400, {"error": err})
            return
        self._ok(device=dev)

    def _h_update(self, body, ip):
        dev, err = self.server.registry.update(
            ip, name=body.get("name"), enabled=body.get("enabled"))
        if err:
            self._json(400, {"error": err})
            return
        self._ok(device=dev)

    def _h_unregister(self, body, ip):
        dev, err = self.server.registry.unregister(ip)
        if err:
            self._json(400, {"error": err})
            return
        self._ok(device=dev)

    def _h_diag(self, body, ip):
        """APP 侧诊断回传：把翻谱推送处理时的内部状态直接打进电脑日志。"""
        msg = str(body.get("msg") or "")[:500]
        if msg:
            self.server.app.q.put("APP 诊断：%s" % msg)
        self._ok()

    def _h_test(self, body, ip):
        dir_ = body.get("dir")
        if dir_ not in ("prev", "next"):
            self._json(400, {"error": "dir 只能是 prev 或 next"})
            return
        dev = self.server.registry.by_ip(ip)
        if dev is None:
            self._json(400, {"error": "本机尚未认领设备"})
            return
        push_async(dev, dir_, self.server.tasker_port(),
                   self.server.app.q.put, test=True)
        self._ok()


# ---- 生命周期管理（App 持有一个实例） ----

_ATTR = {"enabled": "enabled", "serverPort": "server_port",
         "appPort": "app_port",
         "taskerPort": "tasker_port", "midiIn": "midi_hint"}


class WebRemote:
    """遥控栈：热点 → HTTP 服务 → 翻谱 MIDI 监听。设置页保存/程序启动时
    apply()；总开关关=服务+端口停（热点按所有权决定关不关）。apply 总在
    后台线程跑（热点 PowerShell 调用要数秒），锁保证同一时刻只做一份。"""

    def __init__(self, app, cfg):
        self.app = app
        self.enabled = bool(cfg.get("enabled"))
        self.server_port = _port(cfg.get("serverPort"), 8765)
        self.app_port = _port(cfg.get("appPort"), 8767)
        self.tasker_port = _port(cfg.get("taskerPort"), 8766)
        self.midi_hint = str(cfg.get("midiIn") or "")
        self.registry = DeviceRegistry(cfg.get("devices") or [], app.q.put)
        self.registry.on_change = self._persist
        self.server = None
        self.app_server = None
        self.midi_port = None
        self.hub = None
        self.hotspot_owner = False       # 热点是否本程序开的（退出时决定关不关）
        self._lock = threading.Lock()

    def _report(self, msg):
        self.app.q.put(msg)

    def _persist(self):
        # 注册表变更来自 HTTP 线程：回 Tk 主线程写 config（保证单写者）
        self.app.calls.put(self.app._persist_web_remote)

    def startup(self):
        """程序启动（后台线程）：按总开关决定整套起不起。"""
        self.apply()

    def apply(self, **fields):
        with self._lock:
            for k, v in fields.items():
                if v is not None and k in _ATTR:
                    setattr(self, _ATTR[k], v)
            self._rebuild()

    def shutdown(self):
        """退出收尾（后台线程）：停服务/翻谱口，按所有权关热点。"""
        with self._lock:
            self._stop_server()
            self._stop_midi()
            self._hotspot_off()

    # -- 内部：_rebuild 起止整套；各 _stop 幂等 --

    def _rebuild(self):
        self._stop_server()
        self._stop_midi()
        if not self.enabled:
            self._hotspot_off()
            self._report("移动端遥控已停用（设置页可开启）")
            return
        self._report("移动端遥控：开启热点…")
        st = hotspot.ensure_on()
        if not st.get("ok"):
            self._report("移动端遥控不可用：%s" % st.get("err", "未知错误"))
            return
        self.hotspot_owner = not st.get("was_on", False)
        ip = st.get("ip")
        self._report("热点已开（%s）%s" % (
            st.get("ssid") or "无 SSID",
            "，本机 IP %s" % ip if ip else "，未取到本机热点 IP"))
        if not ip:
            self._report("网页服务未启动：没有热点网卡 IP")
            return
        self._start_server(ip)
        self._start_midi()

    def _hotspot_off(self):
        if not self.hotspot_owner:
            return
        self.hotspot_owner = False
        r = hotspot.stop()
        self._report("热点已关闭" if r.get("ok")
                     else "热点关闭失败：%s" % r.get("err"))

    def _start_server(self, ip):
        try:
            self.server = _bind_web(self.app, self.registry,
                                    lambda: self.tasker_port, ip,
                                    self.server_port, PAGE_BROWSER,
                                    self._report)
        except OSError as e:
            self._report("网页服务启动失败（%s:%d）：%s"
                         % (ip, self.server_port, _err(e)))
            return
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.5},
                         daemon=True).start()
        self._report("网页遥控已就绪：http://%s:%d/（平板连热点后访问）"
                     % (ip, self.server_port))
        # APP 版页面（appPort）：承载翻谱设置；浏览器版（serverPort）无翻谱功能
        try:
            self.app_server = _bind_web(self.app, self.registry,
                                        lambda: self.tasker_port, ip,
                                        self.app_port, PAGE_APP, self._report)
        except OSError as e:
            self._report("APP 页面服务启动失败（%s:%d）：%s"
                         % (ip, self.app_port, _err(e)))
            return
        threading.Thread(target=self.app_server.serve_forever,
                         kwargs={"poll_interval": 0.5},
                         daemon=True).start()
        self._report("APP 页面已就绪：http://%s:%d/（在翻谱 APP 内访问）"
                     % (ip, self.app_port))

    def _stop_server(self):
        for attr in ("server", "app_server"):
            srv = getattr(self, attr, None)
            if srv is None:
                continue
            try:
                srv.shutdown()          # serve_forever 循环退出
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
            setattr(self, attr, None)

    def _start_midi(self):
        if not self.midi_hint:
            self._report("翻谱信号监听未配置端口（设置页「翻谱端口名称」）")
            return
        if self.hub is None:
            self.hub = ScoreTurnHub(self.registry, lambda: self.tasker_port,
                                    self._report)
        try:
            self.midi_port = kbd_auto.RawMidiIn(self.midi_hint, self._on_midi)
        except kbd_auto.PortNotFound as e:
            self.midi_port = None
            self._report("翻谱信号监听未启动：%s" % e)
        else:
            self._report("翻谱信号监听已启动（%s）" % self.midi_port.name)

    def _stop_midi(self):
        if self.midi_port is not None:
            try:
                self.midi_port.close()
            except Exception:
                pass
        self.midi_port = None
        if self.hub is not None:
            self.hub.close()
            self.hub = None

    def _on_midi(self, status, d1, d2):
        # winmm 回调线程：只滤 note-on 入队，禁止重活（note_handler 铁律）
        if self.hub is not None and status & 0xF0 == 0x90 and d2 > 0:
            self.hub.submit(d1)
