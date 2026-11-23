# -*- coding: utf-8 -*-
"""OBS 控制器：后台连接线程（断线重连）+ 媒体源动作。

安全原则：视频联动绝不阻塞歌曲流程——所有动作失败只记 last_error，
不抛进 Cubase 任务线程。
"""
import ctypes
import glob
import os
import pathlib
import re
import socket
import sys
import threading
import time
import traceback
from ctypes import wintypes

from obs_ws import ObsWs, ObsError

_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32
_TH32CS_SNAPPROCESS = 0x2
_WM_CLOSE = 0x0010


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def find_processes_by_prefix(prefix):
    """[(pid, exe_name)]，进程名以 prefix 开头（如 obs64）。"""
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower().startswith(prefix):
                out.append((entry.th32ProcessID, entry.szExeFile))
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snap)
    return out


def launch_detached(exe, args=""):
    """ShellExecuteW 拉起 GUI 程序（OBS / loopMIDI 共用）；返回 <=32 为失败。
    参数独立传递，不经 shell 字符串拼接。"""
    return _shell32.ShellExecuteW(None, "open", exe, args,
                                  os.path.dirname(exe) or None, 1)


def process_windows(prefix):
    """[(hwnd, 标题)]：进程名以 prefix 开头（小写）进程的可见顶层窗口。"""
    pids = {pid for pid, _ in find_processes_by_prefix(prefix)}
    out = []
    if not pids:
        return out
    buf = ctypes.create_unicode_buffer(256)
    pid = wintypes.DWORD()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_win(hwnd, _lp):
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and _user32.IsWindowVisible(hwnd):
            if 0 < _user32.GetWindowTextLengthW(hwnd) < 255 \
                    and _user32.GetWindowTextW(hwnd, buf, 256):
                out.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(on_win, 0)
    return out


def obs_projector_windows():
    """obs64 的顶层投影窗口。obs-websocket 5.x 没有「关闭投影器」请求，
    只能按窗口标题（含「投影」/Projector）匹配后发消息。"""
    return [(h, t) for h, t in process_windows("obs64")
            if "投影" in t or "Projector" in t]


def close_obs_projectors():
    """给所有 OBS 投影窗口发 WM_CLOSE（异步，不阻塞调用方）。返回关掉的标题。"""
    closed = []
    for hwnd, t in obs_projector_windows():
        if _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
            closed.append(t)
    return closed


def close_process_windows(prefix):
    """给进程名以 prefix 开头进程的所有可见顶层窗口发 WM_CLOSE（优雅关闭，
    异步不阻塞）。返回发过的窗口标题。"""
    return [t for h, t in process_windows(prefix)
            if _user32.PostMessageW(h, _WM_CLOSE, 0, 0)]


def wait_process_gone(prefix, wait_sec):
    """等前缀进程全部退出；到点仍在返回 False。"""
    deadline = time.time() + wait_sec
    while find_processes_by_prefix(prefix):
        if time.time() >= deadline:
            return False
        time.sleep(0.5)
    return True


def read_obs_websocket_password(ini_path=None):
    r"""OBS 的 WebSocket 密码自动读取：obs-websocket 5（OBS 28+ 内置）把
    密码明文存 %APPDATA%\obs-studio\global.ini 的 [WebSocketServerSettings]
    ServerPassword。config.json obs.password 留空时连接即读此处——启用鉴权
    的 OBS 免手填，未启用鉴权则无该键返回空串空密码直连；读不到（OBS 未
    装/IO 错/非默认位置）一律返回 ""，不阻塞连接循环。"""
    if ini_path is None:
        ini_path = os.path.join(os.environ.get("APPDATA", ""), "obs-studio",
                                "global.ini")
    try:
        sec = ""
        for line in pathlib.Path(ini_path).read_text(
                encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1].strip()
            elif sec == "WebSocketServerSettings" \
                    and line.startswith("ServerPassword="):
                return line.split("=", 1)[1]
    except (OSError, ValueError):
        return ""
    return ""


def close_obs_app():
    """优雅退出 OBS（窗口收 WM_CLOSE 即保存场景配置后退出；强杀会留崩溃
    标记，禁用）。返回是否已退出。"""
    close_process_windows("obs64")
    return wait_process_gone("obs64", 15)


def close_loopmidi():
    """直接终止 loopMIDI——真机实测（2026-09-24，用户确认）：WM_CLOSE 它
    只缩托盘不退出，优雅关闭无意义；虚拟 MIDI 口无用户数据，可安全强杀。
    返回是否已退出。"""
    for pid, _ in find_processes_by_prefix("loopmidi"):
        h = _kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            _kernel32.TerminateProcess(h, 0)
            _kernel32.CloseHandle(h)
    return wait_process_gone("loopmidi", 5)


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128),
                ("DeviceKey", wintypes.WCHAR * 128)]


def list_screens():
    """本机显示器 [(名称, (x, y, 宽, 高))]，按 (y,x) 排序；不依赖 OBS 在线。
    名称=EDID 型号名（EnumDisplayDevices 纯配置查询），同名多屏加序号。
    ⚠ 别改用 dxva2 GetPhysicalMonitors* 取名——本机实测整屏黑死两次
    （2026-09-23，驱动监视器路径被查僵连带桌面会话黑屏）。"""
    names = {}
    dd = _DISPLAY_DEVICEW()
    dd.cb = ctypes.sizeof(dd)
    i = 0
    while _user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
        if dd.StateFlags & 1:            # ATTACHED_TO_DESKTOP=活动输出
            mon = _DISPLAY_DEVICEW()
            mon.cb = ctypes.sizeof(mon)
            if _user32.EnumDisplayDevicesW(dd.DeviceName, 0,
                                           ctypes.byref(mon), 0):
                names[dd.DeviceName] = mon.DeviceString or dd.DeviceName
        i += 1

    def _on_mon(hmon, _hdc, _rect, _data):
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append((names.get(mi.szDevice, mi.szDevice),
                        (r.left, r.top, r.right - r.left,
                         r.bottom - r.top)))
        return True

    out = []
    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _user32.EnumDisplayMonitors(None, None, cb(_on_mon), 0)
    out.sort(key=lambda m: (m[1][1], m[1][0]))
    seen = {}
    named = []
    for name, rect in out:
        if name in seen:            # 同型号多屏：显示名加序号，投影走排名兜底
            seen[name] += 1
            name = "%s (%d)" % (name, seen[name])
        else:
            seen[name] = 1
        named.append((name, rect))
    return named


def _screen_match(obs_name, want):
    """OBS 屏名 ↔ 本机屏名：OBS 在 EDID 名后追加 (N) 序号（如 NZ5(0)），
    我们的同名序号是「 (N)」，两者都算同屏。"""
    return (obs_name == want or obs_name.startswith(want + "(")
            or obs_name == want.rsplit(" (", 1)[0])


_num = re.compile(r"(\d+)")


def natural_key(s):
    """自然排序：'S2' 排在 'S10' 前，数字段按数值比。"""
    return tuple((0, int(t)) if t.isdigit() else (1, t) for t in _num.split(s))


VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".avi")


def autodetect_obs():
    hits = glob.glob(os.path.join(
        r"C:\Program Files\obs-studio", "bin", "64bit", "obs64.exe"))
    return hits[0] if hits else ""


def scan_videos(root):
    """递归扫视频文件，返回相对路径（正斜杠），自然排序。"""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(VIDEO_EXTS):
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                out.append(rel.replace("\\", "/"))
    return sorted(out, key=natural_key)


class ObsController:
    """一条到本机 obs-websocket 的常驻连接；enabled 由设置开关控制。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = False          # Bridge 按设置拨动
        self.on_connected = None      # 连上（含重连）后回调，Bridge 用来恢复幕间
        self._ws = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.last_error = ""
        # 连接纪元标志：只在「断开→连上」跃迁时通知（全部断开路径都要复位，
        # 动作失败/探活失败/停用/循环异常漏复位=重连后投影不恢复，11-13 C2）
        self._connected_notified = False
        # A/B 双源（无黑闪切换）：A=mediaInput、B=mediaInput+" B"，_active=
        # 当前可见侧；_item_ids=输入名→(scene, sceneItemId)（连接期校准，
        # 断链作废，值带 scene 防跨场景用旧 id）；_hidden_file=隐藏侧当前
        # 装载文件（预载态展示，触发路径不依赖它）
        self._active = "A"
        self._item_ids = {}
        self._hidden_file = None

    # ---- 生命周期 ----

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        with self._lock:
            if self._ws:
                self._ws.close()
                self._ws = None
            self._connected_notified = False

    def is_connected(self):
        return bool(self._ws)

    # ---- 对外动作（永不抛） ----

    def set_media(self, file_rel, loop):
        """切到 file_rel 从头播放；loop=True 即幕间循环模式。A/B 双源换切
        （无黑闪）：文件先装载到隐藏侧（装载不可见；同值合并不重载——
        预载过的槽位零装载开销），显式 RESTART 从头起播（不赌
        restart_on_activate 默认值，装载时已统一关掉该隐式重播），开新侧、
        等真正起播、再关旧侧——旧画面持续在屏直到新画面就绪，消除单源
        换文件的重载空窗（黑闪根因，2026-11-22 改双源；11-23 加预载）。"""
        path = self._abs_path(file_rel)
        if path is None:
            self.last_error = "视频文件不存在：%s" % file_rel
            return False
        def do(ws):
            new_name = self._hidden_name()
            self._load_hidden(ws, new_name, path, loop)
            ws.request("TriggerMediaInputAction", {
                "inputName": new_name,
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"})
            self._swap(ws, new_name)
        return self._act(file_rel + ("（循环）" if loop else ""), do)

    def preload(self, file_rel):
        """预载：把 file_rel 装载到隐藏侧（disabled 场景项不播不响=纯装载），
        触发该槽位时同值合并零重载、RESTART 毫秒级起播=准点切换。装载耗时
        （大头）提前到预载时花掉。幂等：重复预载/换预载目标都是覆盖。
        装载前强制隐藏侧归位（SetSceneItemEnabled=False）——用户在 OBS 里
        手动点开过 B 侧可见时，装载即自动播出叠画（审计 P2-7）。"""
        path = self._abs_path(file_rel)
        if path is None:
            self.last_error = "视频文件不存在：%s" % file_rel
            return False
        def do(ws):
            name = self._hidden_name()
            scene = ws.request("GetCurrentProgramScene")[
                "currentProgramSceneName"]
            ws.request("SetSceneItemEnabled", {
                "sceneName": scene,
                "sceneItemId": self._item_id(ws, scene, name),
                "sceneItemEnabled": False})
            self._load_hidden(ws, name, path, False)
        return self._act("预载%s" % file_rel, do)

    def hidden_file(self):
        """隐藏侧当前装载的文件名（预载态展示；None=未装载/未连接）。"""
        return self._hidden_file

    # ---- A/B 双源辅助 ----

    def _b_name(self):
        return self.cfg.get("mediaInput", "舞台视频") + " B"

    def _active_name(self):
        return self._b_name() if self._active == "B" else \
            self.cfg.get("mediaInput", "舞台视频")

    def _hidden_name(self):
        return self.cfg.get("mediaInput", "舞台视频") \
            if self._active == "B" else self._b_name()

    def _load_hidden(self, ws, name, path, loop):
        """装载（或清空）隐藏侧文件：统一合并 restart_on_activate=False
        （上屏重播由显式 RESTART 管，不赌 OBS 默认值——enable 隐式重播会
        与 RESTART 叠一次开头重闪）与 clear_on_media_end=True（一遍停播完
        清屏=真黑，默认定格末帧与「播完黑屏等回落/触发」的设计口径不符，
        审计 A-P2-6）。同值合并不触发重载=预载的核心机制。成功后记账
        _hidden_file（swap 换侧后由调用方刷新）。"""
        ws.request("SetInputSettings", {
            "inputName": name,
            "inputSettings": {"local_file": path, "is_local_file": True,
                              "looping": bool(loop),
                              "restart_on_activate": False,
                              "clear_on_media_end": True},
            "overlay": True})
        self._hidden_file = os.path.basename(path) if path else None

    def _swap(self, ws, new_name):
        """可见性换切到 new_name：先开新侧（旧侧还压着画面，装载/起播都
        不可见），等新侧 PLAYING 再关旧侧——装载期旧画面持续在屏，无空窗。
        开新侧即翻活动侧记账：断链恰在换切半途（新侧已开旧侧未关）时，
        重连归一按记账侧=换切意图的落点侧走，不会把关到一半的新画面关掉。
        等不到 PLAYING（大文件超时/静图/错误态）2 秒后照常收尾：最坏退化
        为旧画面多停 2 秒，不卡死切换。z 序不管理：新侧在上=装载期透明透
        出旧画面，在下=关旧侧时新侧已在播，两种摆位都无黑帧。"""
        scene = ws.request("GetCurrentProgramScene")["currentProgramSceneName"]
        old_name = self._active_name() if new_name != self._active_name() \
            else None
        new_id = self._item_id(ws, scene, new_name)
        old_id = self._item_id(ws, scene, old_name) if old_name else None
        ws.request("SetSceneItemEnabled", {
            "sceneName": scene, "sceneItemId": new_id,
            "sceneItemEnabled": True})
        self._active = "B" if new_name == self._b_name() else "A"
        # 换侧后隐藏侧=旧视频文件，不是用户预载意图的落点；预载态展示
        # 归零（触发路径不依赖此记账——SetInputSettings 幂等装载）
        self._hidden_file = None
        self._wait_playing(ws, new_name)
        if old_id is not None:
            ws.request("SetSceneItemEnabled", {
                "sceneName": scene, "sceneItemId": old_id,
                "sceneItemEnabled": False})

    def _wait_playing(self, ws, name, timeout=2.0):
        """等新侧真正起播（装载+解码开）再关旧侧；拿不到 PLAYING（超时/
        静图/错误态）照常收尾（旧画面多停 timeout 秒，不卡死切换）。持
        _act 锁最长约 timeout 秒——切换期间其它 OBS 动作排队等（切换优先
        于走带暂停）；单请求 timeout 收紧到 0.5s，防 OBS 假死时单次请求
        5s 阻塞越出 deadline（审计 P2-6）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = ws.request("GetMediaInputStatus", {"inputName": name},
                           timeout=0.5)
            if r.get("mediaState") == "OBS_MEDIA_STATE_PLAYING":
                return
            time.sleep(0.02)

    def _item_id(self, ws, scene, name):
        """输入 name 在 scene 的场景项 id（缓存键=输入名、缓存值带 scene
        ——sceneItemId 是 (scene, item) 二元标识，用户切节目场景后同名项
        id 不同，值里记 scene 不符即重建，防旧场景 id 打新场景被拒断链；
        审计 P2-2）。"""
        hit = self._item_ids.get(name)
        if hit is None or hit[0] != scene:
            hit = (scene, self._ensure_side(ws, scene, name))
            self._item_ids[name] = hit
        return hit[1]

    def _ensure_side(self, ws, scene, name):
        """输入+场景项双确保在场，返回场景项 id（幂等）。新建的 B 侧隐藏
        （可见性由 swap 管，防残文件自动播到画面上），A 侧保持原行为可见
        建出（空源无画面，视觉等价）。"""
        items = ws.request("GetSceneItemList", {"sceneName": scene})
        hit = next((i for i in items["sceneItems"]
                    if i["sourceName"] == name), None)
        if hit is not None:
            return hit["sceneItemId"]
        have = any(i["inputName"] == name
                   for i in ws.request("GetInputList")["inputs"])
        if have:
            item = ws.request("CreateSceneItem", {
                "sceneName": scene, "sourceName": name,
                "sceneItemEnabled": name != self._b_name()})
        else:
            item = ws.request("CreateInput", {
                "sceneName": scene, "inputName": name,
                "inputKind": "ffmpeg_source",
                "inputSettings": {"is_local_file": True,
                                  "clear_on_media_end": True},
                "sceneItemEnabled": name != self._b_name()})
        ws.request("SetSceneItemTransform", {
            "sceneName": scene, "sceneItemId": item["sceneItemId"],
            "sceneItemTransform": {
                "position": {"x": 0, "y": 0}, "alignment": 5,
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "bounds": self._canvas(ws)}})
        return item["sceneItemId"]

    def _canvas(self, ws):
        vid = ws.request("GetVideoSettings")
        return {"x": vid["baseWidth"], "y": vid["baseHeight"]}

    def loop_for(self, file_rel):
        """槽位编号查循环标记：basename 前导数字（与 _abs_path 宽松匹配、
        /vjmap 行同编号口径）；无标记/无编号 = False（播一遍停）。"""
        m = re.match(r"(\d+)", os.path.basename(str(file_rel or "")))
        loops = self.cfg.get("videoLoops") or {}
        return bool(m and loops.get(str(int(m.group(1)))))

    def default_for(self, file_rel):
        """槽位编号查默认回落标记（全局单值 videoDefault，与 videoLoops
        同编号口径）：命中=该槽位是默认 VJ——其它触发视频播完（ENDED）后
        回落循环它，而不是黑屏等下一条触发。"""
        m = re.match(r"(\d+)", os.path.basename(str(file_rel or "")))
        d = self.cfg.get("videoDefault")
        return bool(m and d == str(int(m.group(1))))

    def set_looping(self, on):
        """热应用循环标记到在播媒体源（仅合并 looping，不重载——正在播
        的画面零中断）。真机实证 2026-11-17：在播源合并 looping 即时生效，
        越过视频末尾续播；ENDED 定格不因合并复活，该态语义=下次触发生效。"""
        def do(ws):
            ws.request("SetInputSettings", {
                "inputName": self._active_name(),
                "inputSettings": {"looping": bool(on)}, "overlay": True})
        return self._act("循环%s" % ("开" if on else "关"), do)

    def _stop_and_forget(self, ws):
        """停双侧媒体源并清空其文件。OBS 媒体源只要存着文件，随场景激活
        就会自动播（没有「启动不自动播放」开关），忘掉文件才能根治下次
        启动自动续播上次的视频；B 侧虽隐藏，残文件会在下次 swap 开可见时
        自动播——双侧都清。"""
        for name in (self.cfg.get("mediaInput", "舞台视频"),
                     self._b_name()):
            ws.request("SetInputSettings", {
                "inputName": name,
                "inputSettings": {"local_file": "", "is_local_file": True},
                "overlay": True})
            ws.request("TriggerMediaInputAction", {
                "inputName": name,
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"})
        self._hidden_file = None

    def stop_media(self):
        """熄屏：停掉双侧媒体源并忘掉文件（显示空/黑，OBS 下次启动不续播）。"""
        return self._act("熄屏", self._stop_and_forget)

    def pause_media(self):
        """暂停媒体源（画面定格在当前位置；只动可见侧）。"""
        return self._act("暂停", lambda ws: ws.request(
            "TriggerMediaInputAction", {
                "inputName": self._active_name(),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"}))

    def resume_media(self):
        """恢复媒体源（从暂停位置继续播；只动可见侧）。"""
        return self._act("恢复", lambda ws: ws.request(
            "TriggerMediaInputAction", {
                "inputName": self._active_name(),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"}))

    def _set_mute(self, ws):
        """勾选=静音播放：输入静音+关监听；不勾=取消静音+开监听（监视器并
        输出，声音进 OBS「设置→音频→高级→监视输出设备」，默认=系统默认
        播放设备）。媒体源默认监听是关的——声音只进混音器/录制，不进任何
        播放设备，所以听不听得到由监听决定，两者一起按勾选状态同步。"""
        # 缺省 True=静音：与三处 UI（Tk 两版/webapp）的 get("vjMute", True)
        # 同口径——原缺省 False 使「从未保存过 OBS 设置」的运行态与勾选态
        # 相反（UI 显示静音、实际每轮连接强写不静音+开监听）
        mute = bool(self.cfg.get("vjMute", True))
        for name in (self.cfg.get("mediaInput", "舞台视频"),
                     self._b_name()):
            ws.request("SetInputMute", {"inputName": name,
                                        "inputMuted": mute})
            ws.request("SetInputAudioMonitorType", {
                "inputName": name,
                "monitorType": "OBS_MONITORING_TYPE_NONE" if mute
                else "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"})

    def apply_mute(self):
        """按 cfg['vjMute'] 同步媒体源静音/监听（设置页勾选即时生效；连接
        时也会同步一次）。"""
        return self._act("VJ静音", self._set_mute)

    def _ensure_media_input(self, ws):
        """OBS 里缺配置的媒体源（A/B 双侧）时在当前活动场景自动补建（等比
        铺满画布），连接后即可用，无需手动摆源；并按 OBS 实况校准活动侧：
        单侧可见=认该侧；双显（断链断在换切半途）=按记账侧归一——_swap
        开新侧即翻账，记账侧=换切意图的落点侧，关掉另一侧；双侧隐藏只记
        账不动可见性——首次 swap 自会摆正。预载记账也按 OBS 实况对账
        （OBS 重启=源重建为空，防 UI 假显「已预载」）。幂等。"""
        scene = ws.request("GetCurrentProgramScene")["currentProgramSceneName"]
        a = self.cfg.get("mediaInput", "舞台视频")
        ids = {a: (scene, self._ensure_side(ws, scene, a)),
               self._b_name(): (scene,
                                self._ensure_side(ws, scene, self._b_name()))}
        self._item_ids = ids
        items = ws.request("GetSceneItemList", {"sceneName": scene})
        ena = {i["sourceName"]: i["sceneItemEnabled"]
               for i in items["sceneItems"] if i["sourceName"] in ids}
        ena_a, ena_b = bool(ena.get(a)), bool(ena.get(self._b_name()))
        if ena_a and not ena_b:
            self._active = "A"
        elif ena_b and not ena_a:
            self._active = "B"
        elif ena_a and ena_b:
            other = self._b_name() if self._active == "A" else a
            ws.request("SetSceneItemEnabled", {
                "sceneName": scene, "sceneItemId": ids[other][1],
                "sceneItemEnabled": False})
        try:
            st = ws.request("GetInputSettings", {
                "inputName": self._hidden_name()})
            f = st.get("inputSettings", {}).get("local_file") or ""
            # 对账只降级不升级：预载意图在场时按 OBS 实况验证（OBS 重启
            # 源重建为空→清意图，UI 不假显）；无意图不反推——换切后隐藏
            # 侧残留的上一条视频不得亮成「已预载」（与 _swap 归零口径
            # 一致，审计 P2-1）
            if self._hidden_file is not None and \
                    (os.path.basename(f) if f else None) != self._hidden_file:
                self._hidden_file = None
        except (ObsError, OSError, KeyError):
            self._hidden_file = None

    def retire_names(self, old_a):
        """mediaInput 改名后的旧源对清理（设置保存链调用，工作线程执行）：
        旧 A/旧 B 输入连同场景项删除——留着会在场景里成为孤儿源（B 侧若
        手动开过可见性还会叠画）。删除后就地重跑连接校准补建新名源对并
        摆正可见性——连接校准只在重连时自动跑，此处不补建的话改名后首次
        媒体动作会对不存在的输入报错断链（审计 P1-1）。"""
        if not old_a:
            return
        def do(ws):
            for name in (old_a, old_a + " B"):
                try:
                    ws.request("GetInputSettings", {"inputName": name})
                except (ObsError, OSError):
                    continue                       # 本来就不存在
                scene = ws.request("GetCurrentProgramScene")[
                    "currentProgramSceneName"]
                items = ws.request("GetSceneItemList", {"sceneName": scene})
                for it in items["sceneItems"]:
                    if it["sourceName"] == name:
                        try:
                            # 协议要完整场景项对象（GetSceneItemList 条目
                            # 即该形状），不是裸 id
                            ws.request("RemoveSceneItem", {"sceneItem": it})
                        except (ObsError, OSError):
                            pass
                ws.request("RemoveInput", {"inputName": name})
                self._item_ids.pop(name, None)
            self._hidden_file = None
            self._ensure_media_input(ws)           # 就地补建新名源对
        return self._act("清理旧媒体源", do)

    def media_state(self):
        """查媒体源实况（OBS_MEDIA_STATE_* 字符串，可见侧），连不上返回
        None。"""
        with self._lock:
            if not self._ws:
                return None
            try:
                return self._ws.request("GetMediaInputStatus", {
                    "inputName": self._active_name()
                }).get("mediaState")
            except (ObsError, OSError):
                return None

    def apply_projector(self):
        """按 cfg['projectorMonitor']（本机屏名，空=无）把节目画面全屏投影
        过去；开成新投影后才关旧投影（换屏不留双份；OBS 侧屏数不符开不成时
        旧投影保留不黑场）。屏名对 OBS 侧先按名匹配，取不到再按 (y,x) 排名
        兜底；配置的屏本机都不在了只报错不动手。"""
        val = self.cfg.get("projectorMonitor", "")
        if isinstance(val, int):        # 旧版语义（OBS 索引）：-1=无
            if val < 0:
                return self.close_projector()
            return self._act("投影", lambda ws: ws.request(
                "OpenVideoMixProjector", {
                    "videoMixType": "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM",
                    "monitorIndex": val}))
        name = (val or "").strip()
        if not name or name == "无":
            return self.close_projector()
        wins = list_screens()
        me = next((m for m in wins if m[0] == name), None)
        if me is None:
            self.last_error = "配置的显示器「%s」当前不在线" % name
            return False
        rank = wins.index(me)           # 屏名对不上 OBS 命名时按整序排名兜底

        def do(ws):
            obss = ws.request("GetMonitorList")["monitors"]
            obss.sort(key=lambda m: (m["monitorPositionY"],
                                     m["monitorPositionX"]))
            hits = [m for m in obss if _screen_match(m["monitorName"], name)]
            # 命中子集按 (y,x) 排序取首：本机同名屏已在 list_screens 加 (N)
            # 序号唯一化，配置名只对应唯一物理屏
            if hits:
                hits.sort(key=lambda m: (m["monitorPositionY"],
                                         m["monitorPositionX"]))
                idx = hits[0]["monitorIndex"]
            elif rank < len(obss):
                idx = obss[rank]["monitorIndex"]
            else:
                raise ObsError("OBS 只见到 %d 块屏" % len(obss))
            ws.request("OpenVideoMixProjector", {
                "videoMixType": "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM",
                "monitorIndex": idx})

        # 打开前快照旧投影窗句柄：新投影窗与旧窗同匹配器（标题含投影/
        # Projector），成功后全量扫射会把新窗一起误杀=全场无投影（复审
        # 攻击轮 Critical）——只对快照里的旧窗发 WM_CLOSE
        old_wins = obs_projector_windows()
        ok = self._act("投影", do)
        if ok:
            for hwnd, _t in old_wins:
                _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        return ok

    def close_projector(self):
        """关掉全部 OBS 投影窗口（协议无关闭请求，走 WM_CLOSE）。永不抛。"""
        try:
            close_obs_projectors()
            return True
        except Exception as e:          # EnumWindows/消息异常不进主线程
            self.last_error = "关闭投影窗口失败：%s" % e
            return False

    def _act(self, label, fn):
        with self._lock:
            if not self._ws:
                self.last_error = "未连接 OBS：启动中或断线"
                return False
            try:
                fn(self._ws)
                self.last_error = ""
                return True
            except (ObsError, OSError) as e:
                self.last_error = "%s：%s" % (label, e)
                try:
                    self._ws.close()
                except OSError:
                    pass
                self._ws = None
                self._connected_notified = False   # 断链=纪元复位，重连必再通知
                self._item_ids = {}    # 场景项 id 缓存作废：重连后按实况重建
                return False

    def _abs_path(self, file_rel):
        root = self.cfg.get("videoRoot", "")
        if not root:
            return None     # 根未设/已清空：不落到进程 CWD 探测
        cand = os.path.join(root, file_rel)
        if os.path.exists(cand):
            return os.path.abspath(cand)
        # 允许只填文件名：在视频库里按 basename 找（全库精确优先），宽松
        # 匹配「编号+空格」开头（`4 开场钢琴.mp4` ↔ 4.mp4）其次，同编号
        # 多个取自然排序第一个——库清单只扫一遍复用（原两次全量 os.walk，
        # 大库每切歌翻两遍树）
        name = os.path.basename(file_rel)
        m = re.match(r"(\d+)", name)
        files = scan_videos(root)       # 单扫复用
        for rel in files:
            if os.path.basename(rel) == name:
                return os.path.abspath(os.path.join(root, rel))
        if m is not None:
            for rel in files:
                bm = re.match(r"(\d+)\s", os.path.basename(rel))
                if bm and bm.group(1) == m.group(1):
                    return os.path.abspath(os.path.join(root, rel))
        return None

    # ---- 连接循环（RESYNC） ----

    def _connect_loop(self):
        while not self._stop.is_set():
            if not self.enabled:
                time.sleep(1)
                continue
            try:
                fresh = self._ensure_obs_running()
                ws = ObsWs(self.cfg.get("host", "127.0.0.1"),
                           int(self.cfg.get("port", 4455)),
                           (self.cfg.get("password", "")
                            or read_obs_websocket_password()), timeout=5)
                ws.request("GetVersion")  # 探活
                self._ensure_media_input(ws)  # 缺「舞台视频」源就在活动场景补建
                if fresh:                 # 本次拉起的 OBS：掐掉上次退出时
                    try:                  # 残留文件的自动续播
                        self._stop_and_forget(ws)
                    except (ObsError, OSError):
                        pass
                mute_err = ""
                try:                      # 静音偏好随每次连接同步（回调前，
                    self._set_mute(ws)    # 契约=Tk 回调不重放静音）
                except (ObsError, OSError) as e:
                    # 不断链（连接健康，仅该请求失败——老版 OBS 无
                    # SetInputAudioMonitorType 即此症状）：接管时浮出到
                    # last_error，不静默吞
                    mute_err = "VJ静音同步失败：%s" % e
                if self._stop.is_set():       # 退出竞态：握手完成也别再接管
                    try:
                        ws.close()
                    except OSError:
                        pass
                    break
                with self._lock:
                    self._ws = ws
                    self.last_error = mute_err
                    first_notify = not self._connected_notified
                    self._connected_notified = True
                if first_notify and self.on_connected:
                    try:
                        self.on_connected()
                    except Exception as e:
                        self.last_error = "连接通知回调异常: %s" % e
                # 挂起等断线/停用：探活健康=保持现有连接。探活成功若落回
                # 循环顶部会每 healthSec 整轮重建连接+重同步静音，OBS 混音器
                # 手动调整 ≤30s 被打回=「VJ 静音不稳定」根因（11-15 定案）
                while not self._stop.is_set():
                    self._stop.wait(self.cfg.get("healthSec", 30))
                    if self._stop.is_set():
                        break
                    if not self.enabled:  # 被关掉：主动断开，下轮睡眠
                        with self._lock:
                            if self._ws:
                                self._ws.close()
                                self._ws = None
                            self._connected_notified = False
                        break
                    with self._lock:
                        if not self._ws:
                            break         # 动作失败断链：回外循环重连
                        try:
                            self._ws.request("GetVersion", timeout=3)
                        except (ObsError, OSError):
                            try:
                                self._ws.close()
                            except OSError:
                                pass
                            self._ws = None
                            self.last_error = "连接已断开，正在重连…"
                            self._connected_notified = False
                            break
                if self._stop.is_set():
                    break
            except (ObsError, OSError) as e:
                self.last_error = str(e)
                with self._lock:
                    self._ws = None
                    self._connected_notified = False
                self._stop.wait(self.cfg.get("retrySec", 5))
            except Exception as e:
                # 重连线程绝不允许静默死亡（死=OBS 永不重连、VJ 链全灭且
                # 零日志）：捕获面以外的逃逸者（obs_ws json.loads 的
                # ValueError 族、_ensure_media_input 的 KeyError 等响应
                # 形状异常）同样按断线处理进重连；last_error 带异常源行，
                # 逃逸面至少能定位到行
                origin = ""
                tb = traceback.extract_tb(sys.exc_info()[2])
                if tb:
                    f = tb[-1]
                    origin = "%s:%d " % (os.path.basename(f.filename),
                                         f.lineno)
                self.last_error = "连接循环异常，已恢复：%s%s: %s" % (
                    origin, type(e).__name__, e)
                with self._lock:
                    self._ws = None
                    self._connected_notified = False
                try:
                    self._stop.wait(self.cfg.get("retrySec", 5))
                except Exception:
                    time.sleep(5)

    def _ensure_obs_running(self):
        """OBS 没跑就拉起并等端口就绪；返回是否由本次拉起（True=冷启动）。"""
        if find_processes_by_prefix("obs64"):
            return False
        exe = self.cfg.get("obsExe") or autodetect_obs()
        if not exe or not os.path.exists(exe):
            raise ObsError("找不到 obs64.exe，请在 config.json obs.obsExe 指定")
        if not self.cfg.get("autoStart", True):
            raise ObsError("OBS 未运行")
        r = launch_detached(exe, "--disable-shutdown-check --minimize")
        if r <= 32:
            raise ObsError("启动 OBS 失败，代码 %s" % r)
        # 等端口就绪（OBS 起来要几秒）
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(
                        (self.cfg.get("host", "127.0.0.1"),
                         int(self.cfg.get("port", 4455))), timeout=1):
                    return True
            except OSError:
                time.sleep(1)
        raise ObsError("OBS 已启动但 30 秒内 4455 端口没就绪，"
                       "请检查 OBS 的 WebSocket 服务器设置是否启用")

    # 公开别名：GUI/web 层冷启动拉起用，不直取私有段（评审 Minor）
    ensure_obs_running = _ensure_obs_running
