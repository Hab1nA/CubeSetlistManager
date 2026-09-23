# -*- coding: utf-8 -*-
"""OBS 控制器：后台连接线程（断线重连）+ 媒体源动作。

安全原则：视频联动绝不阻塞歌曲流程——所有动作失败只记 last_error，
不抛进 Cubase 任务线程。
"""
import ctypes
import glob
import os
import re
import socket
import threading
import time
from ctypes import wintypes

from obs_ws import ObsWs, ObsError

_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32
_TH32CS_SNAPPROCESS = 0x2


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
        self.connected_at = 0.0

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

    def is_connected(self):
        return bool(self._ws)

    # ---- 对外动作（永不抛） ----

    def set_media(self, file_rel, loop):
        """切媒体源文件并从头播放；loop=True 即幕间循环模式。"""
        path = self._abs_path(file_rel)
        if path is None:
            self.last_error = "视频文件不存在：%s" % file_rel
            return False
        def do(ws):
            ws.request("SetInputSettings", {
                "inputName": self.cfg.get("mediaInput", "舞台视频"),
                "inputSettings": {"local_file": path, "is_local_file": True,
                                  "looping": bool(loop)},
                "overlay": True})
            ws.request("TriggerMediaInputAction", {
                "inputName": self.cfg.get("mediaInput", "舞台视频"),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"})
        return self._act(file_rel + ("(循环)" if loop else ""), do)

    def stop_media(self):
        """熄屏：停止媒体源（源区域显示空/黑）。"""
        return self._act("熄屏", lambda ws: ws.request(
            "TriggerMediaInputAction", {
                "inputName": self.cfg.get("mediaInput", "舞台视频"),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"}))

    def pause_media(self):
        """暂停媒体源（画面定格在当前位置）。"""
        return self._act("暂停", lambda ws: ws.request(
            "TriggerMediaInputAction", {
                "inputName": self.cfg.get("mediaInput", "舞台视频"),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"}))

    def resume_media(self):
        """恢复媒体源（从暂停位置继续播）。"""
        return self._act("恢复", lambda ws: ws.request(
            "TriggerMediaInputAction", {
                "inputName": self.cfg.get("mediaInput", "舞台视频"),
                "mediaAction": "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"}))

    def file_exists(self, file_rel):
        """预加载核对：cue 里的文件名/路径能否落到真实文件。"""
        return self._abs_path(file_rel) is not None

    def _ensure_media_input(self, ws):
        """OBS 里缺配置的媒体源时，在当前活动场景自动补建（等比铺满画布），
        连接后即可用，无需手动摆源。幂等：已存在直接返回。"""
        name = self.cfg.get("mediaInput", "舞台视频")
        if any(i["inputName"] == name
               for i in ws.request("GetInputList")["inputs"]):
            return
        scene = ws.request("GetCurrentProgramScene")["currentProgramSceneName"]
        vid = ws.request("GetVideoSettings")
        item = ws.request("CreateInput", {
            "sceneName": scene, "inputName": name,
            "inputKind": "ffmpeg_source",
            "inputSettings": {"is_local_file": True},
            "sceneItemEnabled": True})
        ws.request("SetSceneItemTransform", {
            "sceneName": scene, "sceneItemId": item["sceneItemId"],
            "sceneItemTransform": {
                "position": {"x": 0, "y": 0}, "alignment": 5,
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "bounds": {"x": vid["baseWidth"], "y": vid["baseHeight"]}}})

    def prime_videos(self, files, on_progress=lambda f, i, n: None):
        """预热：把每个视频快速过一遍媒体源（各 ~0.6s），暖 OS 文件缓存，
        结束恢复调用方指定的幕间。必须在线程里跑（阻塞式）。"""
        n = len(files)
        for i, f in enumerate(files):
            on_progress(f, i, n)
            if not self.set_media(f, loop=False):
                continue  # 失败（含未连接）直接跳过，不阻塞预加载
            self._stop.wait(0.6)
        return True

    def media_state(self):
        """查媒体源实况（OBS_MEDIA_STATE_* 字符串），连不上返回 None。"""
        with self._lock:
            if not self._ws:
                return None
            try:
                return self._ws.request("GetMediaInputStatus", {
                    "inputName": self.cfg.get("mediaInput", "舞台视频")
                }).get("mediaState")
            except (ObsError, OSError):
                return None

    def _act(self, label, fn):
        with self._lock:
            if not self._ws:
                self.last_error = "未连接 OBS（启动中或断线）"
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
                return False

    def _abs_path(self, file_rel):
        root = self.cfg.get("videoRoot", "")
        cand = os.path.join(root, file_rel)
        if os.path.exists(cand):
            return os.path.abspath(cand)
        # 允许只填文件名：在视频库里按 basename 找
        name = os.path.basename(file_rel)
        for rel in scan_videos(root):
            if os.path.basename(rel) == name:
                return os.path.abspath(os.path.join(root, rel))
        # 宽松匹配：文件名以「编号+空格」开头（`4 开场钢琴.mp4` ↔ 4.mp4）；
        # 同编号多个文件时取自然排序第一个
        m = re.match(r"(\d+)", name)
        if m:
            for rel in scan_videos(root):
                bm = re.match(r"(\d+)\s", os.path.basename(rel))
                if bm and bm.group(1) == m.group(1):
                    return os.path.abspath(os.path.join(root, rel))
        return None

    # ---- 连接循环（RESYNC） ----

    def _connect_loop(self):
        was_connected = False          # 只在「断开→连上」跃迁时通知（探活周期
        while not self._stop.is_set():  # 会绕回顶部，不能每次都当全新连接）
            if not self.enabled:
                time.sleep(1)
                continue
            try:
                self._ensure_obs_running()
                ws = ObsWs(self.cfg.get("host", "127.0.0.1"),
                           int(self.cfg.get("port", 4455)),
                           self.cfg.get("password", ""), timeout=5)
                ws.request("GetVersion")  # 探活
                self._ensure_media_input(ws)  # 缺「舞台视频」源就在活动场景补建
                with self._lock:
                    self._ws = ws
                    self.connected_at = time.time()
                    self.last_error = ""
                if self.on_connected and not was_connected:
                    try:
                        self.on_connected()
                    except Exception:
                        pass
                was_connected = True
                # 挂起等断线/停用：socket 读超时也不重连，靠动作失败触发
                self._stop.wait(self.cfg.get("healthSec", 30))
                if self._stop.is_set():
                    break
                if not self.enabled:  # 被关掉：主动断开，下轮睡眠
                    with self._lock:
                        if self._ws:
                            self._ws.close()
                            self._ws = None
                    continue
                # 周期探活，断了就清理进入重连（下次连上才重新通知）
                with self._lock:
                    if self._ws:
                        try:
                            self._ws.request("GetVersion", timeout=3)
                        except (ObsError, OSError):
                            try:
                                self._ws.close()
                            except OSError:
                                pass
                            self._ws = None
                            self.last_error = "连接已断开，正在重连…"
                            was_connected = False
            except (ObsError, OSError) as e:
                self.last_error = str(e)
                with self._lock:
                    self._ws = None
                self._stop.wait(self.cfg.get("retrySec", 5))

    def _ensure_obs_running(self):
        if find_processes_by_prefix("obs64"):
            return
        exe = self.cfg.get("obsExe") or autodetect_obs()
        if not exe or not os.path.exists(exe):
            raise ObsError("找不到 obs64.exe，请在 config.json obs.obsExe 指定")
        if not self.cfg.get("autoStart", True):
            raise ObsError("OBS 未运行（autoStart=false 不自动拉起）")
        r = launch_detached(exe, "--disable-shutdown-check --minimize")
        if r <= 32:
            raise ObsError("启动 OBS 失败（代码 %s）" % r)
        # 等端口就绪（OBS 起来要几秒）
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(
                        (self.cfg.get("host", "127.0.0.1"),
                         int(self.cfg.get("port", 4455))), timeout=1):
                    return
            except OSError:
                time.sleep(1)
        raise ObsError("OBS 已启动但 30 秒内 4455 端口没就绪"
                       "（检查 OBS 的 WebSocket 服务器设置是否启用）")
