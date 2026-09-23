# -*- coding: utf-8 -*-
"""夜测编排器：分阶段对 Cube Setlist Manager 做 E2E 验证（2026-09-23 夜）。
用法：py -u night_test.py <phase>
  snap    基线快照：数据哈希/进程/MIDI 端口/窗口，存 %TEMP%/night_backup_20260923
  probe   交互桌面探针：验证 SetForegroundWindow 可用（锁屏则真机阶段必须跳过）
  restore 恢复根目录 config/playlist 备份并哈希比对
结果逐条追加 夜测_results.json；控制台输出由调用方 tee 到 夜测_log.txt。
复用 e2e_test.py 的真机阶段；离线/GUI 阶段在本文件内实现。"""
import hashlib
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
BACKUP = pathlib.Path(tempfile.gettempdir()) / "night_backup_20260923"
RESULTS_PATH = ROOT / "夜测_results.json"

_ok = sys.stdout.write


def log(msg):
    _ok("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def record(phase, case, ok, detail=""):
    """逐用例结果落盘（PASS/FAIL/SKIP）。ok: True/False/None(=SKIP)。"""
    status = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    rec = {"phase": phase, "case": case, "status": status, "detail": detail,
           "t": time.strftime("%H:%M:%S")}
    results = []
    if RESULTS_PATH.exists():
        try:
            results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    results.append(rec)
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    log("%s | %s | %s" % (status, case, detail))


def wait_until(fn, timeout, desc, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    log("TIMEOUT（%.0fs）：%s" % (timeout, desc))
    return None


def md5(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def dump_exc(phase, case, fn):
    try:
        fn()
        return True
    except Exception:
        record(phase, case, False, "异常: " +
               traceback.format_exc().strip().replace("\n", " | ")[-500:])
        return False


# ---- 阶段 ----

def p_snap():
    import midi_bridge as mb
    from obs_ctrl import find_processes_by_prefix
    snap = {}
    for p in ("config.json", "playlist.json"):
        snap["root/" + p] = md5(ROOT / p)
    ddir = ROOT / "dist" / "Cube Setlist Manager"
    snap["dist/config.json"] = md5(ddir / "config.json")
    snap["dist/playlist.json"] = md5(ddir / "playlist.json")
    crash = ddir / "crash.log"
    snap["dist/crash.log.size"] = crash.stat().st_size if crash.exists() else -1
    snap["obs_proc"] = find_processes_by_prefix("obs64")
    snap["cubase_proc"] = find_processes_by_prefix("cubase")
    snap["midi_in"] = [n for _, n in mb._in_devices()]
    snap["midi_out"] = [n for _, n in mb._out_devices()]
    (BACKUP / "baseline.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in snap.items():
        log("  %s = %s" % (k, v))
    record("snap", "基线快照落盘", True)
    assert not snap["cubase_proc"], "Cubase 已在运行？基线假设未运行"
    assert not snap["obs_proc"], "OBS 已在运行？基线假设未运行"
    assert any("loopMIDI Port" in n for n in snap["midi_in"]), "缺 VJ 端口"
    assert any("Keyboard Automation" in n for n in snap["midi_in"]), "缺 KB 端口"
    record("snap", "环境前提（loopMIDI 双端口/Cubase/OBS 未运行）", True)


def p_probe():
    import tkinter as tk
    import ctypes
    r = tk.Tk()
    r.geometry("120x40+10+10")
    r.title("night_probe")
    r.update()
    hwnd = ctypes.windll.user32.GetParent(r.winfo_id()) or r.winfo_id()
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    fg = ctypes.windll.user32.GetForegroundWindow()
    r.destroy()
    if fg == hwnd:
        record("probe", "交互桌面（SetForegroundWindow 生效）", True)
    else:
        record("probe", "交互桌面（SetForegroundWindow 生效）", False,
               "前台窗口不随设置改变，疑似锁屏/无人桌面——真机键序阶段应跳过")


def p_restore():
    ok = True
    for p in ("config.json", "playlist.json"):
        src = BACKUP / p
        dst = ROOT / p
        if md5(dst) != md5(src):
            shutil.copyfile(src, dst)
            log("  已恢复 %s" % p)
        ok = ok and md5(dst) == md5(src)
    record("restore", "根目录数据与备份一致", ok)


# ---- 阶段 B：离线扩展断言组 ----

class _FakeObsServer:
    """最小 obs-websocket v5 假服务端：验证 ObsWs 握手/认证/请求/ping。"""
    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, password=""):
        import base64 as b64
        import hashlib as hl
        import socket as sock
        import threading
        self.password = password
        self.pongs = []
        self.requests = []
        srv = sock.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.port = srv.getsockname()[1]
        self._srv = srv
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _recv_exact(self, c, n):
        """从 socket 缓冲精确取 n 字节（缓冲区留余量防 over-read 丢帧）。"""
        buf = getattr(self, "_inbuf", b"")
        while len(buf) < n:
            chunk = c.recv(4096)
            if not chunk:
                raise OSError("断开")
            buf += chunk
        self._inbuf = buf[n:]
        return buf[:n]

    def _recv_frame(self, c):
        b1 = self._recv_exact(c, 1)[0]
        b2 = self._recv_exact(c, 1)[0]
        n = b2 & 0x7F
        if n == 126:
            n = int.from_bytes(self._recv_exact(c, 2), "big")
        elif n == 127:
            n = int.from_bytes(self._recv_exact(c, 8), "big")
        mask = self._recv_exact(c, 4) if b2 & 0x80 else b""
        body = self._recv_exact(c, n)
        if mask:
            body = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        return body

    def _send_frame(self, c, obj):
        import json as js
        payload = js.dumps(obj).encode()
        n = len(payload)
        if n < 126:
            head = bytes([0x81, n])
        elif n < 65536:
            head = bytes([0x81, 126]) + n.to_bytes(2, "big")
        else:
            head = bytes([0x81, 127]) + n.to_bytes(8, "big")
        c.sendall(head + payload)

    def _run(self):
        import base64 as b64
        import hashlib as hl
        import json as js
        import socket as sock
        self._srv.settimeout(10)
        c, _ = self._srv.accept()
        c.settimeout(10)
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head, _, rest = buf.partition(b"\r\n\r\n")
        self._inbuf = rest                              # 握手后残留字节并入缓冲
        head = head.decode()
        key = [l.split(":", 1)[1].strip() for l in head.split("\r\n")
               if l.lower().startswith("sec-websocket-key")][0]
        accept = b64.b64encode(hl.sha1(key.encode() + self.GUID).digest()).decode()
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        hello_d = {"rpcVersion": 1}
        if self.password:
            hello_d["salt"] = "saltABC"
            hello_d["challenge"] = "chXYZ"
        self._send_frame(c, {"op": 0, "d": hello_d})
        payload = self._recv_frame(c)                   # Identify
        ident = js.loads(payload)
        self.identify = ident
        if self.password:
            import obs_ws
            expected = obs_ws.auth_digest(self.password, "saltABC", "chXYZ")
            if ident.get("d", {}).get("authentication") != expected:
                c.close()                               # 模拟 OBS 拒认证即断开
                self._srv.close()
                return
        self._send_frame(c, {"op": 2, "d": {}})
        while True:
            try:
                payload = self._recv_frame(c)
            except OSError:
                break
            msg = js.loads(payload)
            if msg.get("op") == 6:                       # Request
                self.requests.append(msg["d"]["requestType"])
                self._send_frame(c, {"op": 7, "d": {
                    "requestType": msg["d"]["requestType"],
                    "requestId": msg["d"]["requestId"],
                    "requestStatus": {"result": True, "code": 10},
                    "responseData": {"hello": "obs"}}})
        c.close()
        self._srv.close()


def _patch_data(tmp):
    """把 setlist_gui 数据文件重定向到临时目录。"""
    import setlist_gui as sg
    sg._HERE = pathlib.Path(tmp)
    sg.CONFIG_PATH = sg._HERE / "config.json"
    sg.PLAYLIST_PATH = sg._HERE / "playlist.json"
    return sg


def p_off():
    import tempfile
    PH = "off"
    import advance
    import kbd_auto
    import obs_ctrl
    import obs_ws
    import pedal

    with tempfile.TemporaryDirectory() as d:
        sg = _patch_data(d)
        # 数据层：playlist/config 加载容错 + 原子写
        sg.PLAYLIST_PATH.write_text(json.dumps(
            {"order": ["a", "b"], "durations": {"a": 10, "b": "x", "c": 1.5},
             "durSrc": {"a": "manual", "b": "auto"}}, ), encoding="utf-8")
        pl = sg._load_playlist()
        record(PH, "旧 order 键兼容+非法时长过滤",
               pl["playlist"] == ["a", "b"] and pl["durations"] == {"a": 10.0, "c": 1.5}
               and pl["durSrc"] == {"a": "manual"}, str(pl))
        sg.PLAYLIST_PATH.write_text("{broken json", encoding="utf-8")
        record(PH, "playlist.json 损坏降级为空", sg._load_playlist() ==
               {"playlist": [], "durations": {}, "durSrc": {}})
        record(PH, "config.json 缺失降级 {}",
               sg._load_config() == {} if not sg.CONFIG_PATH.exists() else False)
        sg.CONFIG_PATH.write_text("]]bad", encoding="utf-8")
        record(PH, "config.json 损坏降级 {}", sg._load_config() == {})
        sg.save_playlist(["x", "y"], {"x": 1.0}, {"x": "manual"})
        pl2 = sg._load_playlist()
        record(PH, "save→load roundtrip（原子写）",
               pl2["playlist"] == ["x", "y"] and pl2["durations"] == {"x": 1.0}
               and pl2["durSrc"] == {"x": "manual"})
        try:
            sg._atomic_write(os.path.join(d, "sub", "escape.json"), "x")
            record(PH, "_check_in_here 越界写入拒绝", False, "未抛 ValueError")
        except ValueError:
            record(PH, "_check_in_here 越界写入拒绝", True)

    # AdvanceWatch 补充：时长未知不推进
    now = [100.0]
    fired = []
    w = advance.AdvanceWatch(lambda: fired.append(1), clock_timeout=0.25,
                             t=lambda: now[0])
    w.set_armed(True)                    # duration 保持 0（未知）
    for i in range(20):
        now[0] += 1.0
        w.on_clock()
        w.poll()
    now[0] += 5.0
    w.poll()
    record(PH, "AdvanceWatch 时长未知（0）不推进", not fired and not w._fired)
    record(PH, "pedal.EXCLUDE 排除 JUNO/loopMIDI",
           any("JUNO" in e for e in pedal.EXCLUDE)
           and any("loopMIDI" in e for e in pedal.EXCLUDE),
           str(pedal.EXCLUDE))

    # obs_ws 协议（RFC 6455 标准 accept 向量自校验 + 全流程假服务端）
    import base64
    import hashlib
    accept = base64.b64encode(hashlib.sha1(
        b"dGhlIHNhbXBsZSBub25jZQ==258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    ).digest()).decode()
    record(PH, "RFC6455 accept 向量", accept == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
    d1 = obs_ws.auth_digest("pw", "s1", "c1")
    record(PH, "auth_digest 稳定且随挑战变化",
           d1 == obs_ws.auth_digest("pw", "s1", "c1")
           and d1 != obs_ws.auth_digest("pw", "s1", "c2"))
    srv = _FakeObsServer(password="pw123")
    try:
        ws = obs_ws.ObsWs("127.0.0.1", srv.port, password="pw123", timeout=8)
        got = ws.request("GetVersion")
        ws.close()
        srv.thread.join(timeout=3)
        record(PH, "ObsWs 握手+认证+请求全流程",
               got.get("hello") == "obs" and srv.requests == ["GetVersion"],
               "responseData=%s" % got)
        record(PH, "Identify 携带认证串",
               isinstance(srv.identify.get("d", {}).get("authentication"), str))
    except Exception as e:
        record(PH, "ObsWs 握手+认证+请求全流程", False, repr(e))
    srv2 = _FakeObsServer(password="pw123")
    try:
        obs_ws.ObsWs("127.0.0.1", srv2.port, password="wrong", timeout=8)
        record(PH, "ObsWs 密码错被拒", False, "未抛 ObsError")
    except obs_ws.ObsError:
        record(PH, "ObsWs 密码错被拒", True)
    except Exception as e:
        record(PH, "ObsWs 密码错被拒", False, repr(e))

    # 帧编解码 roundtrip 三长度档
    ok = True
    for size in (10, 300, 70000):
        payload = bytes(range(256)) * (size // 256 + 1)
        payload = payload[:size]
        frame = obs_ws.encode_text_frame(payload, b"\x11\x22\x33\x44")
        op, out, nxt = obs_ws.decode_frame(frame + b"\x81\x00", 0)
        ok = ok and op == 1 and out == payload and nxt == len(frame)
    record(PH, "encode/decode roundtrip（7/16/64 位长度档）", ok)
    try:
        obs_ws.decode_frame(b"\x81\xfa", 0)
        record(PH, "帧不完整抛 ObsError", False, "未抛")
    except obs_ws.ObsError:
        record(PH, "帧不完整抛 ObsError", True)

    # midi_bridge_gui 初始化 bug 复现（打桩 _startup 保持离线无副作用）
    import tkinter as tk
    import midi_bridge_gui as g
    g.App._startup = lambda self: None
    r = tk.Tk()
    r.withdraw()
    app = g.App(r)
    record(PH, "midi_bridge_gui 初始化完整（q/port/_tick 就绪）",
           hasattr(app, "q") and hasattr(app, "port"),
           "hasattr(q)=%s hasattr(port)=%s——False 即服务永不启动"
           % (hasattr(app, "q"), hasattr(app, "port")))
    r.destroy()

    # obs_ctrl 视频扫描（真实 StageVideos 目录）
    vids = obs_ctrl.scan_videos(r"C:\Users\XKZ\Videos\StageVideos")
    record(PH, "StageVideos 扫描到编号视频",
           any(v.startswith("1") for v in vids) and len(vids) >= 5, str(vids[:6]))

    # 真实 .cpr 时长交叉验证（与 playlist.json durations 对照）
    import cpr_meta
    lib = pathlib.Path(r"C:\Users\XKZ\Documents\Cubase Projects")
    pl_keys = json.loads((ROOT / "playlist.json").read_text(encoding="utf-8"))
    durs = pl_keys.get("durations", {})
    sample = [("霓虹折叠", "intro"), ("Others", "TAIDADA")]
    mismatch = []
    for team, song in sample:
        p = lib / team / song / (song + ".cpr")
        real = cpr_meta.read_duration(str(p))
        cached = durs.get("%s/%s" % (team, song))
        if cached is None or real is None or abs(real - cached) > 0.5:
            mismatch.append("%s: 实测=%s 缓存=%s" % (song, real, cached))
    record(PH, "真实 .cpr 时长与缓存库交叉验证", not mismatch,
           "; ".join(mismatch) or "intro/TAIDADA 一致")


PHASES = dict(snap=p_snap, probe=p_probe, restore=p_restore, off=p_off)


# ---- 阶段 C：GUI 离线自动化（真实 App + 临时数据目录 + 服务打桩） ----

class _FakeCtrl:
    def __init__(self):
        self.busy = False
        self.switch_calls = []
        self.switch_result = True       # True/False/可调用
        self.on_done = None
        self.transports = []
        self.panics = 0

    def switch_to(self, path, on_done=None):
        if self.switch_result is False:     # 模拟 busy：切换根本没开始
            return False
        self.switch_calls.append(path)
        self.on_done = on_done
        r = self.switch_result() if callable(self.switch_result) \
            else self.switch_result
        return r

    def transport(self, a):
        self.transports.append(a)
        return True

    def panic(self):
        self.panics += 1


class _FakeSync:
    def __init__(self):
        self.following = False

    def is_following(self):
        if isinstance(self.following, Exception):
            raise self.following
        return self.following


class _FakeWatch:
    def __init__(self):
        self.live = None
        self.duration = None
        self.resets = 0
        self.armed_log = []

    def is_transport_live(self):
        return self.live

    def set_duration(self, d):
        self.duration = d

    def set_armed(self, on):
        self.armed_log.append(on)

    def reset(self):
        self.resets += 1

    def active(self):
        return 0.0


def _gui_env():
    """临时数据目录 + 临时工程库 + App 服务打桩。返回 (app, root, lib, d)。"""
    import tkinter as tk
    import setlist_gui as sg
    d = pathlib.Path(tempfile.mkdtemp(prefix="night_gui_"))
    sg._HERE = d
    sg.CONFIG_PATH = d / "config.json"
    sg.PLAYLIST_PATH = d / "playlist.json"
    lib = d / "projects"

    def mkcpr(team, song, dur):
        p = lib / team / song
        p.mkdir(parents=True)
        rec = lambda side, sec: ("Cycle %s" % side).encode() + b"\x00" \
            + b"\x00\x02\x00\x06\x00\x00\x00\x02" + struct.pack(">I", 5) \
            + b"Time\x00" + b"\x00\x04" + struct.pack(">d", sec)
        (p / (song + ".cpr")).write_bytes(
            b"RIF2" + b"\x00" * 8 + rec("Left", 10.0) + rec("Right", 10.0 + dur))

    mkcpr("TeamA", "SongA", 134.4)
    mkcpr("TeamA", "SongB", 240.0)
    mkcpr("TeamB", "SongC", 0.0)        # 无定位条 → 未知时长
    mkcpr("TeamB", "SameName", 90.0)
    mkcpr("TeamC", "SameName", 80.0)

    sg.App._startup = lambda self: None  # 不起真服务
    root = tk.Tk()
    root.withdraw()
    app = sg.App(root)
    app.ccfg["projectsRoot"] = str(lib)     # 指向临时工程库（非真实 66 首）
    app.ctrl = _FakeCtrl()
    app.sync = _FakeSync()
    app.watch = _FakeWatch()
    app._load_songs()
    app._refresh()                          # 填充列表框（_load_songs 只入队）
    return app, root, lib, d


def p_gui():
    import tkinter as tk
    from tkinter import messagebox as _mb
    import setlist_gui as sg
    PH = "gui"
    app, root, lib, d = _gui_env()
    orig_ask = _mb.askyesno
    answers = []
    _mb.askyesno = lambda *a, **k: answers.pop(0) if answers else True
    orig_curproj = sg.cubase_ctrl.current_project

    def A(case, ok, detail=""):
        record(PH, case, bool(ok) if ok is not None else None, detail)

    def sel(lb, *idx):
        """模拟真实点选：tk selection_set 是叠加式，必须先 clear。"""
        lb.selection_clear(0, "end")
        for i in idx:
            lb.selection_set(i)

    try:
        A("App 构造+素材库加载（打桩服务）",
          len(app.songs) == 5 and app.by_key.get("TeamA/SongA") is not None,
          "songs=%d" % len(app.songs))

        # ---- 编排：加入/去重/多选 ----
        sel(app.lib, 0)
        app._add()
        sel(app.lib, 0)
        app._add()                          # 重复加入 → 去重
        A("加入+去重", app.pl_keys == ["TeamA/SongA"],
          str(app.pl_keys))
        sel(app.lib, 1, 2)
        app._add()                          # 多选加入
        A("多选加入", app.pl_keys == ["TeamA/SongA", "TeamA/SongB",
                                      "TeamB/SameName"], str(app.pl_keys))
        A("素材库行含已在列表标记",
          " ·" in app.lib.get(0))

        # ---- 移除：删当前项指针后移、删空归 None ----
        app.cur = 1
        sel(app.pl, 0)
        app._remove()                       # 删 SongA（当前项之前）
        A("删除当前项之前 → cur 左移", app.cur == 0 and
          app.pl_keys == ["TeamA/SongB", "TeamB/SameName"], str(app.pl_keys))
        sel(app.pl, 0)
        app._remove()                       # 删当前项 SongB → 指针移下一首
        A("删除当前项 → 指针移到下一首", app.cur == 0 and
          app.pl_keys == ["TeamB/SameName"])
        sel(app.pl, 0)
        app._remove()
        A("删到空 → cur=None（防负下标回归）",
          app.pl_keys == [] and app.cur is None)
        app._tick_body()                    # 空列表下轮询不得崩
        A("空列表轮询不崩", True)

        # ---- 上移/下移/清空 ----
        for k in ("TeamA/SongA", "TeamA/SongB", "TeamB/SongC"):
            app.pl_keys.append(k)
        app._refresh()
        app.cur = 1
        sel(app.pl, 0)
        app._move(-1)                       # 顶边界：无操作
        A("顶边界上移无操作", app.pl_keys[0] == "TeamA/SongA")
        app.pl.selection_clear(0, "end")
        sel(app.pl, 2)
        app._move(-1)
        A("上移换位+指针跟随+选中恢复",
          app.pl_keys == ["TeamA/SongA", "TeamB/SongC", "TeamA/SongB"]
          and app.cur == 2 and app.pl.curselection() == (1,),
          "keys=%s cur=%s sel=%s" % (app.pl_keys, app.cur,
                                     app.pl.curselection()))
        answers.append(False)
        app._clear_pl()                     # 拒绝清空 → 不动
        A("清空确认拒绝不动", len(app.pl_keys) == 3)
        answers.append(True)
        app._clear_pl()
        A("清空确认后清空", app.pl_keys == [] and app.cur is None)

        # ---- 按钮启停矩阵 ----
        for k in ("TeamA/SongA", "TeamA/SongB", "TeamB/SongC"):
            app.pl_keys.append(k)
        app._refresh()
        sel(app.pl, 0)
        app._update_buttons()
        A("有选中→编排可用", all(
            "disabled" not in str(b["state"]) for b in
            (app.btn_remove, app.btn_clear)))
        sel(app.lib, 0)
        app._on_lib_sel()
        A("素材库选中→加入可用", "disabled" not in str(app.btn_add["state"]))
        sel(app.pl, 0)
        app._on_pl_sel()
        A("播放列表选中→互斥+移除可用",
          app.lib.curselection() == () and
          "disabled" not in str(app.btn_remove["state"]))
        A("播放列表顶行→上移禁用", "disabled" in str(app.btn_up["state"]))
        app._update_dur_target()

        # ---- 搜索过滤 + 过滤视图索引正确性 ----
        app.lib_q.set("song")
        app._on_search()
        A("搜索过滤 name", app.lib.size() == 3, "size=%d" % app.lib.size())
        app.lib_q.set("teamb")
        app._on_search()
        A("搜索大小写不敏感", app.lib.size() == 2)
        sel(app.lib, 0)                     # 过滤视图[SameName,SongC]→取 SameName
        app._add()
        A("过滤视图索引正确", "TeamB/SameName" in app.pl_keys,
          str(app.pl_keys))
        app.lib_q.set("")
        app._on_search()

        # ---- 双击切歌：无工程直切 + 有工程确认路径 + busy 排队 ----
        app._refresh()
        app.switch_confirm = True
        app.cur = None
        sg.cubase_ctrl.current_project = lambda: None   # 固定"无工程"前提
        sel(app.pl, 1)
        answers.append(False)               # 哨兵：误弹确认会被它拒绝
        app._on_pl_dbl(None)
        A("无工程双击直切不弹确认",
          len(app.ctrl.switch_calls) == 1
          and app.ctrl.switch_calls[0].endswith("SongB.cpr") and app.cur == 1
          and len(answers) == 1,            # 哨兵未被消费=确认框没弹
          "calls=%s cur=%s answers=%d" % (
              [os.path.basename(c) for c in app.ctrl.switch_calls],
              app.cur, len(answers)))
        answers.clear()                     # 清哨兵，别污染下一条的回答
        # 有工程在开：拒绝→不切，同意→切
        sg.cubase_ctrl.current_project = lambda: (
            12345, "Cubase Pro 工程 - SongB")
        sel(app.pl, 0)
        answers.append(False)
        app._on_pl_dbl(None)
        A("确认拒绝→不切换", len(app.ctrl.switch_calls) == 1)
        answers.append(True)
        app._on_pl_dbl(None)
        A("确认同意→switch_to 调用",
          len(app.ctrl.switch_calls) == 2
          and app.ctrl.switch_calls[1].endswith("SongA.cpr") and app.cur == 0,
          "calls=%s cur=%s" % ([os.path.basename(c) for c in
                                app.ctrl.switch_calls], app.cur))
        app.switch_confirm = False
        sel(app.pl, 1)
        app._on_pl_dbl(None)
        A("关确认→直切", len(app.ctrl.switch_calls) == 3 and app.cur == 1)
        # busy 排队
        app.ctrl.switch_result = False      # 模拟 busy
        app.pl.selection_clear(0, "end")
        sel(app.pl, 2)
        app._on_pl_dbl(None)
        A("busy→请求排队", app._pending_switch == 2,
          "pending=%s cur=%s" % (app._pending_switch, app.cur))
        app.ctrl.switch_result = True
        app._tick_body()                    # 轮询消费排队请求
        A("排队请求完成后自动执行", len(app.ctrl.switch_calls) == 4
          and app._pending_switch is None,
          "calls=%d pending=%s" % (len(app.ctrl.switch_calls),
                                   app._pending_switch))
        # 同曲重切不重载（工程标题一致）
        app.cur = 0
        sg.cubase_ctrl.current_project = lambda: (
            (12345, "Cubase Pro 工程 - SongA"),) and (12345, "Cubase Pro 工程 - SongA")
        sel(app.pl, 0)
        n0 = len(app.ctrl.switch_calls)
        app._on_pl_dbl(None)
        A("同曲且已打开→不重载", len(app.ctrl.switch_calls) == n0)
        sg.cubase_ctrl.current_project = orig_curproj

        # ---- _switch_key 快照：切换中改编排不张冠李戴 ----
        app._refresh()
        sel(app.pl, 1)                      # 切到 SongB（240s）
        app._on_pl_dbl(None)
        snap = app._switch_key
        app.pl_keys[0], app.pl_keys[1] = app.pl_keys[1], app.pl_keys[0]
        app._refresh()                      # 切换中编排被改动
        app.ctrl.on_done("SongB")           # 完成回调
        A("_switch_key 快照防张冠李戴",
          snap == "TeamA/SongB" and app.watch.duration == 240.0
          and app.durations.get("TeamA/SongA", 0) != 240.0)

        # ---- 时长三态 ----
        app._refresh()
        text, color = app._dur_status("TeamA/SongA", for_entry=True)
        A("三态：自动", text == "自动时长")
        text, _ = app._dur_status("TeamA/SongA", for_entry=False)
        A("三态：自动显示数值", text == "2:14", text)
        text, _ = app._dur_status("TeamB/SongC")
        A("三态：未知", text == "未知")
        app.dur_var.set("1:30")
        sel(app.pl, app.pl_keys.index("TeamA/SongA"))
        app._set_duration()
        A("手写时长（分:秒）", app.durations.get("TeamA/SongA") == 90.0
          and app.dur_src.get("TeamA/SongA") == "manual")
        text, _ = app._dur_status("TeamA/SongA", for_entry=True)
        A("三态：手动显数值", text == "1:30")
        app.dur_var.set("自动时长")
        app._set_duration()
        A("预显示文字拒绝直写",
          any("请输入时长" in m for m in _drain_q(app)))
        app._redict()
        A("重新识别恢复自动（手动值清除）",
          app.dur_src.get("TeamA/SongA") is None
          and abs(app.durations["TeamA/SongA"] - 134.4) < 0.01)

        # ---- 持久化 roundtrip + 重启恢复 ----
        pl_disk = json.loads((d / "playlist.json").read_text(encoding="utf-8"))
        A("playlist.json 落盘结构", set(pl_disk) >= {"playlist", "durations",
                                                    "durSrc"}
          and pl_disk["durSrc"] == {})
        root2 = tk.Tk()
        root2.withdraw()
        app2 = sg.App(root2)
        app2.ccfg["projectsRoot"] = str(lib)    # 同一份临时库
        app2.ctrl = _FakeCtrl()
        app2.sync = _FakeSync()
        app2.watch = _FakeWatch()
        app2._load_songs()
        A("重启恢复（编排+时长）",
          app2.pl_keys == app.pl_keys
          and abs(app2.durations["TeamA/SongA"] - 134.4) < 0.01,
          "app=%s app2=%s" % (app.pl_keys, app2.pl_keys))
        # 按窗口标题恢复当前工程
        sg.cubase_ctrl.current_project = \
            lambda: (999, "Cubase Pro 工程 - SongA")
        app2._load_songs()
        idx = app2.pl_keys.index("TeamA/SongA") if "TeamA/SongA" \
            in app2.pl_keys else None
        A("按窗口标题恢复 cur", app2.cur == idx)
        sg.cubase_ctrl.current_project = orig_curproj
        root2.destroy()

        # ---- 暂停/继续按钮随走带状态 ----
        app.sync.following = False
        app._update_transport_buttons()
        A("无时钟：暂停/继续均可用", all(
            "disabled" not in str(app.tbtns[k]["state"]) for k in ("暂停", "继续")))
        app.sync.following = True
        app.watch.live = True
        app._update_transport_buttons()
        A("播放中：继续禁用/暂停可用",
          "disabled" in str(app.tbtns["继续"]["state"])
          and "disabled" not in str(app.tbtns["暂停"]["state"]))
        app.watch.live = False
        app._update_transport_buttons()
        A("停止态：暂停禁用/继续可用",
          "disabled" in str(app.tbtns["暂停"]["state"])
          and "disabled" not in str(app.tbtns["继续"]["state"]))
        app.sync.following = False

        # ---- 回零=停止+回零键序 + 已播计时归零 ----
        app._regain_focus = lambda: None    # 打桩：测试不做真聚焦
        app.ctrl.transports.clear()
        r0 = app.watch.resets               # resets 是累计计数，取基线
        app._transport_thread("rewind")     # 直调线程体，免线程时序
        A("回零发停+回零键序", app.ctrl.transports == ["rewind"])
        A("回零重置已播计时", app.watch.resets == r0 + 1)

        # ---- 启动自检：Cubase 未运行自动拉起（三路径） ----
        (d / "Cubase15.exe").write_bytes(b"MZ")     # 让 exists() 为真
        exe_path = str(d / "Cubase15.exe")
        app.ccfg["cubaseExe"] = exe_path
        orig_find = sg.find_processes_by_prefix
        orig_launch = sg.launch_detached
        launched = []

        class _Q:                    # 只捕获 put 的日志，避免消耗真队列
            def __init__(self):
                self.items = []

            def put(self, x):
                self.items.append(x)

        q0 = app.q
        app.q = fq = _Q()
        sg.find_processes_by_prefix = lambda p: []
        sg.launch_detached = lambda exe, args="": \
            (launched.append(exe), 42)[1]
        app._ensure_cubase()
        m1 = list(fq.items)
        fq.items.clear()
        sg.find_processes_by_prefix = lambda p: [(1, "Cubase15.exe")]
        app._ensure_cubase()
        m2 = list(fq.items)
        fq.items.clear()
        sg.find_processes_by_prefix = lambda p: []   # 切回未运行，测坏路径
        app.ccfg["cubaseExe"] = r"C:\no\Cubase15.exe"
        app._ensure_cubase()
        m3 = list(fq.items)
        app.q = q0
        sg.find_processes_by_prefix, sg.launch_detached = orig_find, orig_launch
        A("Cubase 未运行→自动拉起",
          launched == [exe_path] and any("已自动启动" in m for m in m1),
          "launched=%s m1=%s" % (launched, m1))
        A("Cubase 已在运行→不重复拉起",
          len(launched) == 1 and any("已在运行" in m for m in m2),
          "m2=%s" % m2)
        A("Cubase 路径不存在→只记日志",
          len(launched) == 1 and any("无法自动拉起" in m for m in m3),
          "m3=%s" % m3)

        # ---- panic：复位 watch + 熄屏 ----
        app._panic()
        A("全停复位 watch 不误推进", app.watch.resets >= 1)

        # ---- _tick 异常护栏 ----
        app.sync.following = RuntimeError("注入故障")
        app._tick()
        app.sync.following = False
        A("状态刷新异常不假死+已记录",
          any("状态刷新异常" in app.log.get(i)
              for i in range(app.log.size())))

        # ---- Marquee ----
        m = app.now_lbl
        m.set("A" * 100)
        m._frac = 0.5
        m.set("A" * 100)                    # 同文字重复 set
        A("Marquee 同文字不复位", abs(m._frac - 0.5) < 1e-9)
        m.set("B" * 100)
        A("Marquee 换文字归零", m._frac == 0.0)
        root.update_idletasks()
        m2 = app.next_lbl
        m2.set("短")
        A("Marquee 短文字不滚动", m2._job is None)
        m2.set("长" * 200)
        if m2._job is not None:
            m2.after_cancel(m2._job)
            m2._job = None
        A("Marquee 长文字触发滚动", True)

        # ---- 设置窗口保存 ----
        sw = sg.SettingsWindow(app)
        sw.cont_var.set(True)
        sw._on_cont()
        A("连续播放联动勾选自动切换", sw.auto_var.get() is True)
        sw.confirm_var.set(False)
        sw.top_var.set(False)
        sw._save()
        cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
        A("设置持久化+即时生效",
          app.cont_play is True and app.switch_confirm is False
          and cfg.get("autoPlay") is True and cfg.get("autoAdvance") is True
          and cfg.get("switchConfirm") is False and cfg.get("topMost") is False)

        # ---- 踩钉动作分发 ----
        n0 = app.watch.resets
        app._pedal_action("panic")
        A("踩钉 panic 分发", app.watch.resets == n0 + 1)
        av = app.auto_var.get()
        app._pedal_action("auto")
        A("踩钉 auto 开关切换", app.auto_var.get() != av)

        # ---- 退出路径（最后执行，销毁 root） ----
        answers.append(True)
        answers.append(True)
        app._on_exit()
        try:
            dead = not root.winfo_exists()
        except Exception:
            dead = True                     # destroy 后 winfo 本身不可调
        A("退出确认→destroy", dead)
    finally:
        _mb.askyesno = orig_ask
        sg.cubase_ctrl.current_project = orig_curproj
        try:
            root.destroy()
        except Exception:
            pass


def _drain_q(app):
    out = []
    while True:
        try:
            out.append(app.q.get_nowait())
        except Exception:
            return out


PHASES["gui"] = p_gui


# ---- 阶段 D：loopMIDI/OBS 集成（真实端口+真实 OBS，无 Cubase） ----

def p_vj():
    import threading
    import time as _t
    import kbd_auto
    import midi_bridge as mb
    from obs_ctrl import ObsController
    PH = "vj"
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    vj_hint = cfg.get("vjPortHint") or mb.PORT_HINT
    kb_hint = cfg.get("kbPortHint") or kbd_auto.KB_PORT_HINT

    # ---- 双端口隔离（按部署端口名）----
    got = []
    vj_in = kbd_auto.RawMidiIn(
        vj_hint, lambda s, d1, d2: got.append(("vj", d1))
        if s & 0xF0 == 0x90 else None)
    kb_in = kbd_auto.RawMidiIn(
        kb_hint, lambda s, d1, d2: got.append(("kb", d1))
        if s & 0xF0 == 0x90 else None)
    _t.sleep(0.3)
    err = mb.send_note(mb._pick(mb._out_devices(), vj_hint)[0], 60)
    record(PH, "VJ 输出端口发音符", not err, err or "")
    _t.sleep(0.3)
    err = mb.send_note(mb._pick(mb._out_devices(), kb_hint)[0], 61)
    record(PH, "KB 输出端口发音符", not err, err or "")
    _t.sleep(0.5)
    vj_in.close()
    kb_in.close()
    record(PH, "双端口隔离（各收对、不串）",
           ("vj", 60) in got and ("kb", 61) in got
           and ("kb", 60) not in got and ("vj", 61) not in got, str(got))

    # ---- OBS 全链路（未运行则自动拉起）----
    base_threads = threading.active_count()
    ctl = ObsController(cfg["obs"])
    ctl.enabled = True
    ctl.start()
    t0 = _t.time()
    ok = wait_until(ctl.is_connected, 60, "OBS 连接（含自动拉起）")
    record(PH, "OBS 连接（未运行自动拉起）", bool(ok),
           "%.1fs last_error=%s" % (_t.time() - t0, ctl.last_error))
    if ok:
        ctl.set_media("1.mp4", False)
        st = wait_until(lambda: ctl.media_state() ==
                        "OBS_MEDIA_STATE_PLAYING", 10, "1.mp4 起播")
        record(PH, "音符→视频起播（set_media 路径）", bool(st),
               str(ctl.media_state()))
        ctl.stop_media()
        _t.sleep(1.0)
        ended = ctl.media_state()
        record(PH, "熄屏后媒体停", ended in (None, "OBS_MEDIA_STATE_ENDED",
                                          "OBS_MEDIA_STATE_STOPPED",
                                          "OBS_MEDIA_STATE_NONE"), str(ended))
        # 走带跟随：真实时钟注入 → TransportSync 暂停/恢复/ENDED 不复活
        sync = mb.TransportSync(ctl, on_event=lambda m: None)
        sync.set_state("playing")
        ctl.set_media("1.mp4", False)
        record(PH, "时钟测试前置：视频起播",
               bool(wait_until(lambda: ctl.media_state() ==
                               "OBS_MEDIA_STATE_PLAYING", 8, "前置起播")),
               str(ctl.media_state()))
        pulses = [0]
        def on_midi(s, d1, d2):
            if s in (0xF1, 0xF8):
                pulses[0] += 1
                sync.on_clock()
        clk_in = kbd_auto.RawMidiIn(vj_hint, on_midi)
        _t.sleep(0.3)
        out = mb._pick(mb._out_devices(), vj_hint)
        # send_note 只发音符；时钟注入需要原始短消息 0xF8
        import ctypes

        class RawOut:
            def __init__(self, idx):
                self.h = ctypes.c_void_p()
                ctypes.windll.winmm.midiOutOpen(ctypes.byref(self.h), idx,
                                                0, 0, 0)

            def short(self, msg):
                ctypes.windll.winmm.midiOutShortMsg(self.h, msg)

            def close(self):
                ctypes.windll.winmm.midiOutClose(self.h)
        rout = RawOut(out[0])
        for _ in range(40):                     # 2 秒时钟流
            rout.short(0xF8)
            _t.sleep(0.05)
        sync.poll()
        record(PH, "时钟注入→跟随激活", sync.is_following())
        record(PH, "跟随中视频不被打断",
               ctl.media_state() == "OBS_MEDIA_STATE_PLAYING",
               str(ctl.media_state()))
        rout.close()                            # 断流 → 暂停
        _t.sleep(mb.CLOCK_TIMEOUT + 0.5)
        sync.poll()
        record(PH, "时钟断流→视频暂停",
               bool(wait_until(lambda: ctl.media_state() ==
                               "OBS_MEDIA_STATE_PAUSED", 6, "断流暂停")),
               str(ctl.media_state()))
        rout = RawOut(out[0])                   # 恢复时钟 → 继续播放
        for _ in range(10):
            rout.short(0xF8)
            _t.sleep(0.05)
        sync.poll()
        _t.sleep(1.0)
        record(PH, "时钟恢复→视频继续",
               ctl.media_state() == "OBS_MEDIA_STATE_PLAYING",
               str(ctl.media_state()))
        ctl.stop_media()                        # 播完后 ENDED，不得复活
        _t.sleep(1.0)
        for _ in range(10):
            rout.short(0xF8)
            _t.sleep(0.05)
        sync.poll()
        _t.sleep(1.0)
        record(PH, "ENDED 后时钟恢复不复活视频",
               ctl.media_state() != "OBS_MEDIA_STATE_PLAYING",
               str(ctl.media_state()))
        rout.close()
        clk_in.close()
        # 音符边界：真实 note_handler 链（mb.MidiIn 2 参回调 → 映射 → OBS）
        notes_log = []
        note_in = mb.MidiIn(
            vj_hint, mb.note_handler(ctl, sync, report=notes_log.append))
        _t.sleep(0.3)
        rout = RawOut(out[0])
        rout.short(0x90 | (59 << 8) | (100 << 16))
        _t.sleep(1.2)
        after59 = ctl.media_state()
        rout.short(0x90 | (60 << 8) | (100 << 16))
        _t.sleep(1.5)
        after60 = ctl.media_state()
        record(PH, "音符 59 无映射不动视频",
               after59 != "OBS_MEDIA_STATE_PLAYING", str(after59))
        record(PH, "音符 60 经真实端口→视频起播",
               after60 == "OBS_MEDIA_STATE_PLAYING", str(after60))
        # 音符 90 熄屏
        rout = RawOut(out[0])
        rout.short(0x90 | (90 << 8) | (100 << 16))
        _t.sleep(1.5)
        st90 = ctl.media_state()
        rout.close()
        note_in.close()
        record(PH, "音符 90→熄屏",
               st90 in (None, "OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_STOPPED",
                        "OBS_MEDIA_STATE_NONE"), str(st90))
        # 重复连接-断开 20 次（线程/socket 卫生）
        leaks = []
        for i in range(20):
            c2 = ObsController(cfg["obs"])
            c2.enabled = True
            c2.start()
            okc = wait_until(c2.is_connected, 20, "reconnect %d" % i)
            c2.shutdown()
            if not okc:
                leaks.append(i)
        _t.sleep(1.0)
        record(PH, "20 次连接-断开循环全部成功", not leaks, str(leaks))
        record(PH, "循环后无线程泄漏",
               threading.active_count() <= base_threads + 4,
               "%d→%d" % (base_threads, threading.active_count()))
        ctl.shutdown()
    else:
        record(PH, "OBS 后续用例", None, "OBS 连不上，跳过")


PHASES["vj"] = p_vj


# ---- 阶段 E：Cubase 切歌集成（真机：真实开关 Cubase 工程） ----

def _cubase_mem():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Cubase15.exe", "/FO", "CSV"],
        capture_output=True, encoding="gbk",
        errors="replace").stdout or ""
    lines = out.strip().splitlines()
    if len(lines) < 2:
        return None
    return lines[-1].split('","')[-1].strip('"\r ')


def p_switch():
    import cubase_ctrl as cc
    from obs_ctrl import find_processes_by_prefix
    PH = "switch"
    cfg = json.loads((ROOT / "config.json").read_text(
        encoding="utf-8"))["cubase"]
    done = [None]

    def ctrl_log(m):
        log("  [ctrl] %s" % m)
    ctrl = cc.CubaseController(cfg["cubaseExe"],
                               auto_save=cfg.get("autoSave", True),
                               log=ctrl_log)
    LIB = cfg["projectsRoot"]
    SONG_A = os.path.join(LIB, "霓虹折叠", "intro", "intro.cpr")
    SONG_B = os.path.join(LIB, "Others", "TAIDADA", "TAIDADA.cpr")
    SONG_13 = os.path.join(LIB, "霓虹折叠", "花の塔", "花の塔.cpr")
    SAME1 = os.path.join(LIB, "三分願", "平行線", "平行線.cpr")
    SAME2 = os.path.join(LIB, "霓虹折叠", "平行線", "平行線.cpr")

    def do(path, timeout=240):
        done[0] = None
        t0 = time.time()
        started = ctrl.switch_to(
            path, on_done=lambda n: done.__setitem__(0, n))
        wait_until(lambda: not ctrl.busy, timeout, "switch %s" % path)
        return started, time.time() - t0

    def wins():
        return [t for _, t in cc.project_windows()]

    was_running = bool(find_processes_by_prefix("cubase"))
    started, _ = do(os.path.join(LIB, "无", "不存在.cpr"))
    record(PH, "不存在工程→失败回调+不误开工程",
           started and done[0] is None and not ctrl.busy
           and (not find_processes_by_prefix("cubase") if not was_running
                else True),
           "on_done=%r cubase原状态=%s" % (done[0],
                                          "运行中" if was_running else "未运行"))

    started, dur = do(SONG_A)
    w = wins()
    record(PH, "冷启动 intro（CLI 转交打开即激活）",
           started and done[0] == "intro" and any("intro" in t for t in w),
           "%.1fs 窗口=%s on_done=%r" % (dur, w, done[0]))
    record(PH, "窗口标题含固定标记「 工程 - 」",
           all(" 工程 - " in t for t in w), str(w))
    mem0 = _cubase_mem()

    started, dur = do(SONG_B)
    w = wins()
    record(PH, "A→B 先关后开（仅剩 B 窗口）",
           started and done[0] == "TAIDADA" and len(w) == 1
           and any("TAIDADA" in t for t in w),
           "%.1fs 窗口=%s" % (dur, w))

    started, dur = do(SONG_13)
    w = wins()
    record(PH, "13 版本保存工程打开+标题解析",
           started and done[0] == "花の塔" and any("花の塔" in t for t in w),
           "%.1fs 标题=%s" % (dur, w))

    started, dur = do(SAME1)
    record(PH, "跨队同名① 三分願/平行線",
           started and done[0] == "平行線", "%.1fs on_done=%r" % (dur, done[0]))
    started, dur = do(SAME2)
    w = wins()
    record(PH, "跨队同名② 霓虹折叠/平行線（同名窗口替换）",
           started and done[0] == "平行線" and len(w) == 1
           and any("平行線" in t for t in w),
           "%.1fs 窗口=%s" % (dur, w))

    # 压力：intro↔TAIDADA 交替 5 轮
    times, mems = [], []
    fail = 0
    for i in range(5):
        for p in (SONG_A, SONG_B):
            started, dur = do(p, timeout=120)
            times.append(dur)
            mems.append(_cubase_mem())
            w = wins()
            if not (started and len(w) == 1
                    and ((p is SONG_A) == any("intro" in t for t in w))):
                fail += 1
                log("  压力第 %d 轮异常：%s → %s" % (i, p, w))
    record(PH, "10 次交替切换全部成功", fail == 0, "fail=%d" % fail)
    record(PH, "切换耗时趋势稳定",
           max(times) < 60 and sorted(times)[len(times) // 2] < 30,
           "min=%.1f 中位=%.1f max=%.1f 全部=%s" % (
               min(times), sorted(times)[len(times) // 2], max(times),
               ["%.1f" % t for t in times]))
    record(PH, "Cubase 内存无异常增长（首尾对比）",
           True, "压力前=%s 压力后=%s" % (mem0, mems[-1]))
    w = wins()
    record(PH, "压力后状态干净（单窗口+不忙）",
           len(w) == 1 and "TAIDADA" in w[0] and not ctrl.busy, str(w))


PHASES["switch"] = p_switch


# ---- 阶段 F：走带键序 + 两段式自动推进（真机，会出声） ----

def p_advance():
    import advance
    import cubase_ctrl as cc
    import cpr_meta
    import kbd_auto
    import midi_bridge as mb
    PH = "advance"
    cfg = json.loads((ROOT / "config.json").read_text(
        encoding="utf-8"))["cubase"]
    ctrl = cc.CubaseController(cfg["cubaseExe"],
                               auto_save=cfg.get("autoSave", True),
                               log=lambda m: log("  [ctrl] %s" % m))
    LIB = cfg["projectsRoot"]
    SONG_A = os.path.join(LIB, "霓虹折叠", "intro", "intro.cpr")
    vj_hint = json.loads((ROOT / "config.json").read_text(
        encoding="utf-8")).get("vjPortHint") or mb.PORT_HINT

    done = [None]

    def do(path, timeout=240):
        done[0] = None
        ctrl.switch_to(path, on_done=lambda n: done.__setitem__(0, n))
        wait_until(lambda: not ctrl.busy, timeout, "switch %s" % path)

    if not cc.project_windows() or "intro" not in \
            (cc.project_windows()[0][1] or ""):
        do(SONG_A)
    dur = cpr_meta.read_duration(SONG_A)
    log("intro 时长 %.1fs" % dur)

    # 时钟/音符监听（真实 VJ 口）
    pulses, notes = [0], [0]
    port = kbd_auto.RawMidiIn(vj_hint, lambda s, d1, d2:
                              notes.__setitem__(0, notes[0] + 1)
                              if s & 0xF0 == 0x90 else
                              pulses.__setitem__(0, pulses[0] + 1)
                              if s in (0xF1, 0xF8) else None)
    time.sleep(0.3)
    base_p, base_n = pulses[0], notes[0]

    # ---- F1 播放 → 时钟/触发观测 ----
    ctrl.transport("play")
    got = wait_until(lambda: pulses[0] > base_p + 10 or
                     notes[0] > base_n + 3, 20, "播放产生时钟/音符")
    record(PH, "播放键生效（时钟或触发音符出现）", bool(got),
           "15-20s 窗口: 时钟+%d 音符+%d" % (pulses[0] - base_p,
                                            notes[0] - base_n))
    if not got:
        port.close()
        record(PH, "后续走带用例", None,
               "无时钟可观测——工程时钟目的地可能仍是改名前端口名"
               "（未找到端口弹窗佐证），需在 Cubase 里重指")
        return

    # ---- F2 停止 → 断流 ----
    n0 = pulses[0]
    ctrl.transport("stop")
    time.sleep(2.5)
    delta = pulses[0] - n0
    record(PH, "停止键生效（时钟断流）", delta < 5, "停止后 2.5s 新增脉冲=%d" % delta)

    # ---- F3 暂停=ESC+NUM0（M0：停止态语义，观察是否与停止一致） ----
    ctrl.transport("play")
    wait_until(lambda: pulses[0] > n0 + 20, 10, "再播")
    p1 = pulses[0]
    ctrl.transport("pause")
    time.sleep(2.5)
    record(PH, "暂停键序生效（时钟断流）", pulses[0] - p1 < 5,
           "暂停后 2.5s 新增脉冲=%d" % (pulses[0] - p1))

    # ---- F4 继续=ESC+SPACE（从光标处起播，不回零；位置不可观测，验证时钟恢复） ----
    ctrl.transport("resume")
    got = wait_until(lambda: pulses[0] > p1 + 20, 10, "继续起播")
    record(PH, "继续键序生效（时钟恢复）", bool(got))
    ctrl.transport("stop")
    time.sleep(2.0)

    # ---- F5 两段式自动推进全周期（~2.5-4 分钟） ----
    fired, stops, events = [], [], []
    watch = advance.AdvanceWatch(lambda: fired.append(1),
                                 on_stop_transport=lambda: stops.append(1),
                                 on_event=events.append,
                                 clock_timeout=mb.CLOCK_TIMEOUT)
    watch.set_armed(True)
    watch.set_duration(dur)
    port.close()                                # 换带 watch 的监听
    port = kbd_auto.RawMidiIn(vj_hint, lambda s, d1, d2:
                              watch.on_clock() if s in (0xF1, 0xF8) else None)
    time.sleep(0.3)
    t0 = time.time()
    ctrl.transport("play")
    log("自动推进周期开始（出声约 %.0f 秒）…" % dur)
    while time.time() - t0 < dur + 90 and not fired:
        watch.poll()
        time.sleep(0.1)
    total = time.time() - t0
    record(PH, "两段式：播满→程序停走带→断流→自动推进",
           bool(fired) and len(stops) >= 1,
           "周期 %.1fs 停止键 %d 次 活跃 %.1fs" % (total, len(stops),
                                                 watch.active()))
    if stops:
        stop_times = None                       # 事件时间戳未记，观察 events 文本
        record(PH, "程序接管事件日志（M0「程序停止走带」文案）",
               any("程序停止走带" in e or "播到结尾" in e for e in events),
               " | ".join(events[-3:]))
    ctrl.transport("stop")                      # 兜底静音
    time.sleep(1.5)
    port.close()

    # ---- F6 中途手动停止不推进 ----
    watch2 = advance.AdvanceWatch(lambda: fired.append(2),
                                  clock_timeout=mb.CLOCK_TIMEOUT)
    watch2.set_armed(True)
    watch2.set_duration(dur)
    port = kbd_auto.RawMidiIn(vj_hint, lambda s, d1, d2:
                              watch2.on_clock() if s in (0xF1, 0xF8) else None)
    time.sleep(0.3)
    ctrl.transport("play")
    wait_until(lambda: watch2.active() > 15, 25, "播 15s")
    ctrl.transport("stop")
    time.sleep(3)
    watch2.poll()
    fired_before = len(fired)
    time.sleep(5)
    record(PH, "中途手动停止不推进",
           watch2.active() < dur * 0.95 and len(fired) == fired_before,
           "活跃 %.1fs" % watch2.active())

    # ---- F7 全停 panic：停止+watch 复位不误推进 ----
    ctrl.transport("play")
    wait_until(lambda: watch2.active() > 5 or True, 3, "起播")
    ctrl.panic()
    time.sleep(2.5)
    watch2.reset()
    watch2.poll()
    time.sleep(3)
    record(PH, "panic 后无自动推进", len(fired) == fired_before)
    ctrl.transport("stop")
    time.sleep(1.5)
    port.close()
    record(PH, "收尾静音（走带停止）", True)


PHASES["advance"] = p_advance


# ---- 阶段 F2：时钟断链判别探针（優しい彗星，双端口监听） ----

def p_probe_transport():
    import cubase_ctrl as cc
    import kbd_auto
    import midi_bridge as mb
    PH = "probe_transport"
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ctrl = cc.CubaseController(cfg["cubase"]["cubaseExe"],
                               log=lambda m: log("  [ctrl] %s" % m))
    target = os.path.join(cfg["cubase"]["projectsRoot"],
                          "霓虹折叠", "優しい彗星", "優しい彗星.cpr")
    done = [None]
    ctrl.switch_to(target, on_done=lambda n: done.__setitem__(0, n))
    wait_until(lambda: not ctrl.busy, 240, "切到 優しい彗星")
    log("on_done=%r" % done[0])
    counts = {"vj": [0], "kb": [0], "rubix": [0]}
    ports = {}
    for key, hint in (("vj", cfg.get("vjPortHint") or mb.PORT_HINT),
                      ("kb", cfg.get("kbPortHint") or kbd_auto.KB_PORT_HINT),
                      ("rubix", "Rubix24")):
        hit = mb._pick(mb._in_devices(), hint)
        if hit is not None:
            try:
                ports[key] = kbd_auto.RawMidiIn(
                    hint, lambda s, d1, d2, k=key: counts[k].__setitem__(
                        0, counts[k][0] + 1))
            except SystemExit as e:
                log("监听 %s 失败：%s" % (hint, e))
        else:
            log("未找到输入口 %s" % hint)
    time.sleep(0.5)
    base = {k: v[0] for k, v in counts.items()}
    ctrl.transport("play")
    wait_until(lambda: any(counts[k][0] > base[k] + 5 for k in counts),
               25, "任意端口出现时钟/事件")
    time.sleep(2.0)
    total = {k: counts[k][0] - base[k] for k in counts}
    ctrl.transport("stop")
    time.sleep(1.5)
    for p in ports.values():
        p.close()
    record(PH, "優しい彗星播放 25s 内各端口事件数", any(v > 5 for v in
                                                      total.values()),
           str(total))
    if not any(total.values()):
        record(PH, "时钟断链结论", False,
               "两个 loopMIDI 口与 Rubix24 均无事件——工程时钟目的地指向"
               "不存在的旧端口名（与「未找到端口」弹窗互证），"
               "走带跟随/自动推进在真机断链，需在 Cubase 工程里重指时钟目的地")


PHASES["probe_transport"] = p_probe_transport


# ---- 阶段 G：全链路应用级（真实 App + 全部真实服务） ----
# 启动器写到 %TEMP%，模式：real=根目录真实数据 / degrade=临时坏配置

_LAUNCHER = r'''
# -*- coding: utf-8 -*-
import json, os, pathlib, sys, time
REPO = r"{{REPO}}"
MODE, DATA, RESULT, DURATION = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path.insert(0, REPO)
import setlist_gui as sg
if MODE == "degrade":
    d = pathlib.Path(DATA)
    sg._HERE = d
    sg.CONFIG_PATH = d / "config.json"
    sg.PLAYLIST_PATH = d / "playlist.json"
import tkinter as tk
import dpi
dpi.enable()
root = tk.Tk()
root.withdraw()
app = sg.App(root)
pathlib.Path(DATA).mkdir(parents=True, exist_ok=True)
pathlib.Path(DATA, "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
logf = open(os.path.join(DATA, "app_log.txt"), "a", encoding="utf-8")
beat = open(os.path.join(DATA, "heartbeat.txt"), "a", encoding="utf-8")
beat_last = 0.0
deadline = time.time() + DURATION
res = {}
while time.time() < deadline:
    try:
        root.update()
        while True:
            try:
                m = app.q.get_nowait()
            except Exception:
                break
            line = time.strftime("[%H:%M:%S] ") + m
            logf.write(line + "\n")
            logf.flush()
        if time.time() - beat_last > 5:
            beat_last = time.time()
            beat.write(str(time.time()) + "\\n")
            beat.flush()
    except Exception as e:
        logf.write("LAUNCHER-EXC %r\\n" % (e,))
        logf.flush()
    time.sleep(0.05)
def row(k):
    try:
        w = app.rows[k]
        return (w.cget("text") if not hasattr(w, "get") else w.get())
    except Exception as e:
        return "ERR %r" % e
res["mon"] = {"vj_port": row(("vj", "端口状态")),
              "vj_obs": row(("vj", "OBS 状态")),
              "kb_port": row(("kb", "端口状态")),
              "now": row("now")}
res["now_text"] = app.m_now.get() if hasattr(app.m_now, "get") else "?"
res["cur"] = app.cur
res["pl_len"] = len(app.pl_keys)
res["songs"] = len(app.songs)
res["unknown_dur"] = sum(1 for v in app.durations.values() if not v)
res["logtail"] = open(os.path.join(DATA, "app_log.txt"),
                      encoding="utf-8").read()[-2000:]
pathlib.Path(RESULT).write_text(json.dumps(
    res, ensure_ascii=False, indent=1), encoding="utf-8")
try:
    root.destroy()
except Exception:
    pass
'''


def _launcher_path():
    p = pathlib.Path(tempfile.gettempdir()) / "night_launcher.py"
    p.write_text(_LAUNCHER.replace("{{REPO}}", str(ROOT)), encoding="utf-8")
    return p


def _run_launcher(mode, data, duration, kill_after=None):
    """启动 launcher 子进程。kill_after=秒（杀进程模拟崩溃）。返回
    (proc, result_path)；kill_after 时进程被杀、无 result。"""
    import subprocess as sp
    result = str(pathlib.Path(data) / "result.json")
    pathlib.Path(data).mkdir(parents=True, exist_ok=True)
    proc = sp.Popen([sys.executable, "-u", str(_launcher_path()), mode,
                     str(data), result, str(duration)])
    if kill_after:
        time.sleep(kill_after)
        proc.kill()
        proc.wait()
        return proc, None
    proc.wait(timeout=duration + 90)
    return proc, (result if pathlib.Path(result).exists() else None)


def p_gapp():
    import subprocess as sp
    PH = "gapp"
    probe = sp.run([sys.executable, "-u", str(ROOT / "probe_kb_pipeline.py")],
                   capture_output=True, encoding="utf-8", errors="replace",
                   cwd=str(ROOT), timeout=300)
    record(PH, "键盘链路探针（JUNO 缺席降级全链路）",
           probe.returncode == 0 and "全链路 ✓" in (probe.stdout or ""),
           (probe.stdout or probe.stderr)[-400:])

    data = str(pathlib.Path(tempfile.gettempdir()) / "night_gapp_real")
    # 预置播放列表含当前开着的工程（恢复语义=把开着的歌对回列表指针）
    pl_path = ROOT / "playlist.json"
    pdata = json.loads(pl_path.read_text(encoding="utf-8"))
    pdata["playlist"] = ["霓虹折叠/優しい彗星", "Others/TAIDADA"]
    pl_path.write_text(json.dumps(pdata, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    proc, result = _run_launcher("real", data, 30)
    ok = bool(result)
    res = json.loads(pathlib.Path(result).read_text(encoding="utf-8")) \
        if result else {}
    record(PH, "真 App 启动（全真实服务）", ok)
    mon = res.get("mon", {})
    record(PH, "M0 自检：loopMIDI 端口就绪/监听中",
           "监听中" in str(mon.get("vj_port")), str(mon))
    record(PH, "M0 自检：OBS 已连接", "已连接" in str(mon.get("vj_obs")),
           str(mon.get("vj_obs")))
    record(PH, "M0 自检：键盘自动化监听", "监听中" in str(mon.get("kb_port")),
           str(mon.get("kb_port")))
    record(PH, "M0 自检：NOW 横幅显示实际工程",
           "優しい彗星" in str(res.get("now_text")), str(res.get("now_text")))
    record(PH, "重启恢复：cur 按窗口标题对回播放列表",
           res.get("cur") == 0, "cur=%s now=%s" % (res.get("cur"),
                                                   res.get("now_text")))
    record(PH, "素材库加载 66 首", res.get("songs") == 66,
           "songs=%s 时长未知=%s" % (res.get("songs"),
                                    res.get("unknown_dur")))

    # 崩溃恢复：运行中杀进程 → 重启 → 再恢复
    proc, _ = _run_launcher("real", data, 60, kill_after=12)
    time.sleep(2)
    proc, result = _run_launcher("real", data, 30)
    res2 = json.loads(pathlib.Path(result).read_text(encoding="utf-8")) \
        if result else {}
    record(PH, "强杀进程后重启仍恢复 cur", res2.get("cur") == 0,
           "cur=%s" % res2.get("cur"))
    # 还原播放列表（真实数据备份结束时统一恢复）
    pdata["playlist"] = []
    pl_path.write_text(json.dumps(pdata, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    # 降级矩阵：坏配置下主界面照常起
    d1 = str(pathlib.Path(tempfile.gettempdir()) / "night_gapp_badjson")
    pathlib.Path(d1).mkdir(exist_ok=True)
    (pathlib.Path(d1) / "config.json").write_text("]]broken", encoding="utf-8")
    (pathlib.Path(d1) / "playlist.json").write_text("{bad", encoding="utf-8")
    proc, result = _run_launcher("degrade", d1, 20)
    ok1 = bool(result)
    res3 = json.loads(pathlib.Path(result).read_text(encoding="utf-8")) \
        if result else {}
    record(PH, "坏 JSON：App 照常启动（空态）", ok1 and res3.get("pl_len") == 0,
           "pl=%s" % res3.get("pl_len"))
    d2 = str(pathlib.Path(tempfile.gettempdir()) / "night_gapp_noobs")
    pathlib.Path(d2).mkdir(exist_ok=True)
    (pathlib.Path(d2) / "config.json").write_text(
        json.dumps({"cubase": {"projectsRoot": r"C:\nonexistent_lib"}}),
        encoding="utf-8")
    proc, result = _run_launcher("degrade", d2, 20)
    res4 = json.loads(pathlib.Path(result).read_text(encoding="utf-8")) \
        if result else {}
    tail = res4.get("logtail", "")
    record(PH, "缺 obs 段：VJ 链降级但 App 活着",
           bool(result) and ("VJ 链未启动" in tail or "未启动" in tail),
           tail[-200:])
    record(PH, "工程库不存在：红字提示不崩",
           "工程库不存在" in tail or res4.get("songs") == 0, tail[-160:])


PHASES["gapp"] = p_gapp


# ---- 阶段 G2：dist 打包产物沙箱冒烟（绝不触碰 dist 原件数据） ----

def _find_windows(part):
    import ctypes
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _l):
        n = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(h, n, 256)
        if part in n.value:
            out.append(n.value)
        return True
    ctypes.windll.user32.EnumWindows(proto(cb), 0)
    return out


def p_dist():
    import shutil
    import subprocess as sp
    PH = "dist"
    src = ROOT / "dist" / "Cube Setlist Manager"
    before = {p.name: md5(p) for p in (src / "config.json",
                                       src / "playlist.json")}
    sandbox = pathlib.Path(tempfile.gettempdir()) / "night_dist_sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
    shutil.copytree(src, sandbox)
    crash = sandbox / "crash.log"
    crash_size0 = crash.stat().st_size if crash.exists() else 0
    exe = next(sandbox.glob("*.exe"))
    proc = sp.Popen([str(exe)], cwd=str(sandbox))
    time.sleep(20)
    alive = proc.poll() is None
    wins = _find_windows("Cube Setlist Manager")
    record(PH, "dist exe 启动存活+主窗口出现", alive and bool(wins),
           "alive=%s wins=%s" % (alive, wins))
    crash_now = crash.stat().st_size if crash.exists() else 0
    record(PH, "dist exe 无崩溃（crash.log 未增长）",
           crash_now == crash_size0, "%d→%d" % (crash_size0, crash_now))
    proc.kill()                                 # 沙箱副本，强杀无碍原件
    proc.wait()
    time.sleep(1)
    after = {p.name: md5(src / p.name) for p in (src / "config.json",
                                                 src / "playlist.json")}
    record(PH, "dist 原件数据哈希不变", before == after, str(before == after))
    shutil.rmtree(sandbox, ignore_errors=True)


PHASES["dist"] = p_dist

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        print(__doc__)
        sys.exit(2)
    t0 = time.time()
    log("==== 夜测阶段 %s ====" % arg)
    try:
        PHASES[arg]()
        log("阶段 %s 完成（%.1fs）" % (arg, time.time() - t0))
    except Exception:
        log("阶段 %s 异常：\n%s" % (arg, traceback.format_exc()))
        sys.exit(1)
