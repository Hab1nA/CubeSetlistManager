# -*- coding: utf-8 -*-
"""Cube Setlist Manager：素材库选歌编排播放列表 + Cubase 工程"先关后开"切换 +
播完自动推进 + 键盘自动化联动 + VJ 视频联动（桥文件零改动）。
界面唯一入口=webapp 前端（webui.py 起隐藏 Tk 根承载业务栈 + pywebview 窗加载
/app 六页）；本模块的 App 是无头业务宿主——Tk 根只跑 mainloop/after 双循环
（日志排水、/state 快照、端口自愈、看门狗心跳），全部人机交互走
web_remote.py 的 HTTP 端点。
切歌=DawController.switch_to（关闭当前工程[自动保存]→打开下一个），
只认播放列表里的歌；自动推进=MTC 时码位置−左定位条 ≥ 工程时长×99%（FINISH_RATIO）且
时码断流。工程时长：启动时后台全量重探（自动来源；手动设定不覆盖）+
打开工程时重测，持久化在 playlist.json，未设定位条的在编排页手填。
直接 `python setlist_gui.py` 运行（转投 webui.main()），或 PyInstaller 打包 exe
（config.json、playlist.json 与 exe 同目录）。"""
import ctypes
import faulthandler
import json
import os
import shutil
import pathlib
import socket
import queue
import collections
import re
import subprocess
import sys
import threading
import time
from tkinter import filedialog

import advance
import cpr_meta
import daw_ctrl
import dpi
import hotspot
import kbd_auto
import mr_ctrl
import mtc
import midi_bridge as mb
import pedal
import points
import song_meta
import stallguard
import ui_text
import web_remote
import app_common as _ac
from obs_ctrl import ObsController, find_processes_by_prefix, \
    launch_detached, list_screens, close_obs_app, close_loopmidi

FOLLOW = ui_text.FOLLOW
TX_NAMES = {"play": "播放", "pause": "暂停", "resume": "继续",
            "stop": "停止", "rewind": "回零"}


def panic_quiet_msgs(*cfgs):
    """全停对琴的静音序列：CC123 All Notes Off → CC64=0 抬延音踏板 →
    CC120 All Sound Off 兜底。每个配置发其全部 MIDI 通道（JUNO 走
    patchCh/perfCh 两通道，AX 走 ch）。幂等无害，重复发不产生副作用。"""
    out = []
    for cfg in cfgs:
        chs = ([cfg.get("patchCh", 1), cfg.get("perfCh", 16)]
               if "perfCh" in cfg else [cfg.get("ch", 1)])
        for ch in chs:
            b = (ch - 1) & 0x0F
            out.append(("short", 0xB0 | b | 123 << 8, 0))
            out.append(("short", 0xB0 | b | 64 << 8, 0))
            out.append(("short", 0xB0 | b | 120 << 8, 0))
    return out


def transport_blocked(action, tstate):
    """走带命令的状态互锁：S1 的 SPACE 系键序是播放⇄停止开关、Cubase 的
    「继续」也是裸 SPACE——不看当前状态盲发会反向作用（已暂停按「暂停」
    反而起播、播放中按「播放/继续」反停、已暂停/停止按「停止/全停」反起
    播——S1 无真暂停，暂停=停止）。返回 True=忽略该命令。rewind 不拦
    （回零在停止态也有意义，键序由 facts["rewind_stopped"] 按态选择）。"""
    if action == "play":
        return tstate == "playing"
    if action in ("pause", "stop"):
        return tstate != "playing"
    if action == "resume":
        return tstate == "playing"
    return False

# 日志级别（新前端级别体系的唯一来源，产生端定级）：err=真实故障
# （自检失败/启动失败/未恢复/不可用…）；「已停用/未启动」类有意停运
# =warn 不算错。UI 的行内级别标签/着色/三档筛选全部消费此定级。
_LOG_ERR = re.compile("失败|异常|错误|未恢复|不可用|不存在|无法|损坏")
_LOG_WARN = re.compile("未生效|超时|降级|未就绪|忽略|未启动|未选|已停用|停用")


def _log_lv(msg):
    if _LOG_ERR.search(msg):
        return "err"
    if _LOG_WARN.search(msg):
        return "warn"
    return ""


_find_cubase_exe = _ac.find_cubase_exe    # 上移 app_common（评审 I-DUP-24）


_find_s1_exe = _ac.find_s1_exe
DAW_DEFAULTS = _ac.DAW_DEFAULTS


def daw_settings(cfg, daw):
    """DAW 路径段归一（按底座子段）：dawSettings.cubase/.studioone 各自
    持 projectsRoot/dawExe，缺项落底座默认。兼容读取三层旧配置——本底座
    无子段时：旧平铺 dawSettings 键只迁给 config 活跃底座（daw 键所指，
    即平铺值实际服役的那个底座；迁给另一底座=Cubase exe 漏进 S1 段，
    启动自检拉错 DAW 的真机事故换皮）；旧 cubase 段与 cubaseExe 键仍仅
    对 cubase 生效；最后落 DAW_DEFAULTS。子段一旦写入即为权威，旧层忽略。"""
    sec = cfg.get("dawSettings") or {}
    sub = sec.get(daw)
    s = dict(sub) if isinstance(sub, dict) else {}
    if not s:
        if daw == str(cfg.get("daw") or "cubase"):
            s.update({k: v for k, v in sec.items() if isinstance(v, str)})
        if daw == "cubase":
            legacy_sec = cfg.get("cubase")
            if isinstance(legacy_sec, dict):   # 畸形旧段（手改）不崩
                s.update(legacy_sec)
            legacy = s.pop("cubaseExe", None)
            if legacy:
                s.setdefault("dawExe", legacy)
    s.setdefault("dawExe", DAW_DEFAULTS[daw]["exe"])
    s.setdefault("projectsRoot", DAW_DEFAULTS[daw]["root"])
    # 分隔符统一成本机反斜杠（tk 对话框/手输都出「/」，显示与落库一口径）
    s["dawExe"] = os.path.normpath(s["dawExe"])
    s["projectsRoot"] = os.path.normpath(s["projectsRoot"])
    return s

_HERE = _ac.HERE
CONFIG_PATH = _ac.CONFIG_PATH
PLAYLIST_PATH = _ac.HERE / "playlist.json"
_CONFIG_BAKS = _ac.CONFIG_BAKS
_CONFIG_CORRUPT = _ac.CONFIG_CORRUPT
_PLAYLIST_BAKS = (_ac.HERE / "playlist.json.bak",
                  _ac.HERE / "playlist.json.bak2",
                  _ac.HERE / "playlist.json.bak3")
_PLAYLIST_CORRUPT = _ac.HERE / "playlist.json.corrupt"
_CRASH_LOG = None                  # faulthandler 落盘句柄，防 GC
_check_in_here = _ac.check_in_here
_rotate = _ac.rotate
_err = _ac.err_str
scan_library = _ac.scan_library


def _load_config():
    """机制上移 app_common（.corrupt 隔离/fsync/滚动备份）；壳读本模块
    常量——测试 monkeypatch sg 属性换路径的调用面不变。"""
    return _ac.load_config_at(CONFIG_PATH, _CONFIG_CORRUPT)


def _save_config(cfg):
    _ac.save_config_at(cfg, CONFIG_PATH, _CONFIG_BAKS, _CONFIG_CORRUPT,
                       here=_HERE)


def _atomic_write(path, text):
    _ac.atomic_write(path, text, here=_HERE)


# ---- 文件日志（审计 #15：q 消费漏斗单点接入；专用线程异步落盘——
#     日志行已在 Tk 线程消费，磁盘 IO 再入专用线程，主线程零等待）----
_LOGDIR = pathlib.Path(os.environ.get("APPDATA") or str(_HERE)) / \
    "CubeSetlistManager" / "logs"
_LOG_KEEP_DAYS = 7


class _FileLog:
    """按日滚动日志写手：put() 由消费线程调用，写盘在专属线程排队完成；
    进程退出靠 daemon+行级 flush 尽力而为（黑匣子另有 crash/stall 链）。"""

    def __init__(self, directory):
        self._dir = pathlib.Path(directory)
        self._q = queue.Queue(maxsize=2000)
        self._fh = None
        self._day = None
        threading.Thread(target=self._run, daemon=True,
                         name="filelog").start()

    def put(self, msg, lv):
        try:
            self._q.put_nowait((time.time(), msg, lv))
        except queue.Full:
            pass                      # 洪峰丢行不阻塞消费线程

    def _open(self, day):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._dir / ("%s.log" % day), "a",
                        encoding="utf-8")
        self._day = day
        # 清 7 天前的旧日志
        cutoff = time.time() - _LOG_KEEP_DAYS * 86400
        try:
            for p in self._dir.glob("*.log"):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
        except OSError:
            pass

    def _run(self):
        while True:
            t, msg, lv = self._q.get()
            day = time.strftime("%Y%m%d", time.localtime(t))
            if self._fh is None or day != self._day:
                self._open(day)
            try:
                self._fh.write("%s [%s] %s\n" % (
                    time.strftime("%H:%M:%S", time.localtime(t)),
                    lv or "I", msg))
                self._fh.flush()
            except OSError:
                pass


_FILE_LOG = None    # App.__init__ 里惰性创建（避免 import 期副作用）


# ---- OBS WebSocket 密码 DPAPI 加密（审计 #12：明文落盘泄露面=文件本身；
#     CryptProtectData 绑当前 Windows 用户，拷贝到他机不可解）----
_DPAPI_PREFIX = "dpapi:"


def _dpapi_protect(plain):
    """UTF-8 明文 → "dpapi:"+base64(CryptProtectData)。失败返回原明文
    （加密不可用时不阻断启动）。"""
    try:
        import ctypes.wintypes as wt

        class _BLOB(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("pb", ctypes.c_void_p)]

        buf = ctypes.create_string_buffer(plain.encode("utf-8"))
        pin = _BLOB(len(buf), ctypes.cast(buf, ctypes.c_void_p))
        pout = _BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(pin), None, None, None, None, 0,
                ctypes.byref(pout)):
            try:
                import base64
                return _DPAPI_PREFIX + base64.b64encode(
                    ctypes.string_at(pout.pb, pout.cb)).decode("ascii")
            finally:
                ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(pout.pb))
    except Exception:
        pass
    return plain


def _dpapi_unprotect(stored):
    """"dpapi:"+base64 → 明文；非该前缀=旧明文原样返回（迁移兼容）。"""
    if not stored or not stored.startswith(_DPAPI_PREFIX):
        return stored
    try:
        import ctypes.wintypes as wt
        import base64

        class _BLOB(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("pb", ctypes.c_void_p)]

        raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
        buf = ctypes.create_string_buffer(raw, len(raw))
        pin = _BLOB(len(raw), ctypes.cast(buf, ctypes.c_void_p))
        pout = _BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(pin), None, None, None, None, 0,
                ctypes.byref(pout)):
            try:
                # CryptProtectData 输出含终止 NUL（原文明文不含 \x00，
                # 续着进 ObsWs 握手会炸）——decode 后去掉
                return ctypes.string_at(pout.pb, pout.cb).decode(
                    "utf-8").rstrip("\x00")
            finally:
                ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(pout.pb))
    except Exception:
        pass
    return ""   # 解密失败（跨机拷贝的密文）：按空密码对待，不让连接崩


def _protect_obs_password(cfg):
    """读取链统一入口：识别 dpapi: 前缀解密；遇旧明文则原值可用并标记
    待升级（写入链在下次 _save_config 时加密落盘）。"""
    pwd = (cfg or {}).get("password") or ""
    if pwd.startswith(_DPAPI_PREFIX):
        return _dpapi_unprotect(pwd)
    return pwd


def _load_playlist():
    """{"playlist": [key…], "durations": {key: 秒}, "durSrc": {key: manual}}
    durSrc 里登记的是手动设定；不在表里=自动识别；无时长=未知。
    兼容旧 schema 的 order 键。"""
    if PLAYLIST_PATH.exists():
        try:
            with open(PLAYLIST_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            # 损坏降级：空播放列表（durations 启动探测会重建）；坏文件
            # 改名 .corrupt 留证隔离，启动后 App 向用户红字提示
            data = {}
            try:
                os.replace(str(PLAYLIST_PATH), str(_PLAYLIST_CORRUPT))
            except OSError:
                pass
        keys = data.get("playlist") or data.get("order") or []
        durations = {}
        for k, v in data.get("durations", {}).items():
            d = cpr_meta.safe_dur(v)   # 历史毒化（NaN/Infinity/超界）归零
            if d > 0:
                durations[str(k)] = d
        return {"playlist": [str(k) for k in keys],
                "durations": durations,
                "durSrc": {k: "manual"
                           for k, v in data.get("durSrc", {}).items()
                           if v == "manual"}}
    return {"playlist": [], "durations": {}, "durSrc": {}}


def save_playlist(keys, durations, dur_src):
    data = json.dumps({"playlist": keys, "durations": durations,
                       "durSrc": dur_src},
                      ensure_ascii=False, indent=2)
    _rotate(_PLAYLIST_BAKS, PLAYLIST_PATH)
    _atomic_write(PLAYLIST_PATH, data)


def _read_duration(path):
    """按工程扩展名分发时长解析：.cpr→RIFF 定位条，.song→ZIP/XML 事件终点。
    值域钳制（safe_dur）：伪造工程文件里的 inf/nan 不进 durations。"""
    return cpr_meta.safe_dur(
        song_meta.read_duration(path)
        if path.lower().endswith(".song")
        else cpr_meta.read_duration(path))


def _read_span(path):
    """(时长, 左定位条偏移)：左定位条是时码位置→歌内已播秒的换算原点
    （.song 无定位条概念 → 左=0；.cpr 解析不出 → (0, 0) 手填兜底）。
    同 _read_duration 钳制（伪造定位条 inf 不得毒化 watch/时码）。"""
    if path.lower().endswith(".song"):
        return (cpr_meta.safe_dur(song_meta.read_duration(path)), 0.0)
    loc = cpr_meta.read_locators(path)
    if not loc:
        return (0.0, 0.0)
    return (cpr_meta.safe_dur(loc[1] - loc[0]) if loc[1] > loc[0] else 0.0,
            cpr_meta.safe_dur(loc[0]))


def _reprobe(songs, durations, dur_src):
    """自动来源的工程时长全量重读工程文件 → {key: 新秒}（只收值有变化的）。
    手动设定不参与（「写入时长」受保护，要回工程内原值用「重新识别」）。"""
    out = {}
    for s in songs:
        if dur_src.get(s["key"]) == "manual":
            continue
        d = _read_duration(s["path"]) or 0.0
        if d != durations.get(s["key"]):
            out[s["key"]] = d
    return out




class App:
    """业务栈根对象。界面唯一入口=webapp 前端（webui.py/pywebview）；
    App 在隐藏 Tk 根上构建的业务骨架承载轮询与业务态，web 业务面走
    HTTP 端点独立实现。"""

    def __init__(self, root):
        self.root = root
        cfg = _load_config()
        # 底座选择：config 顶层 "daw"（cubase | studioone），缺省 cubase；
        # 非法值（手改 config）启动根归一，否则 daw_settings KeyError 无声死
        daw = str(cfg.get("daw") or "cubase")
        self.daw = daw if daw in daw_ctrl.FACTS else "cubase"
        self.facts = daw_ctrl.FACTS[self.daw]
        daw_ctrl.set_active(self.facts)
        # 最小尺寸不在构造期写死：底栏一行（状态+三组按钮+退出）比任何
        # 预估都宽，写死值窄于内容需求时 grid 会压缩权重列（退出列被压
        # 没）——__init__ 尾按实际需求宽设置
        self.ccfg = daw_settings(cfg, self.daw)
        if self.ccfg["dawExe"] and not os.path.exists(self.ccfg["dawExe"]):
            # 配置钉的路径已不存在（升级 DAW/换机）→ 回退自动探测，防静默失效
            self.ccfg["dawExe"] = DAW_DEFAULTS[self.daw]["exe"]
        # 全新配置（无文件/掉配置）的默认值：端口联动一律停用（名称不再
        # 预设旧硬编码，用户在设置页按实际在线端口选）；勾选项按演出
        # 习惯给安全默认
        self.auto_advance = bool(cfg.get("autoAdvance", True))
        self.cont_play = bool(cfg.get("autoPlay", False))  # 连续播放：切完自动起播
        self.top_most = bool(cfg.get("topMost", True))     # 保持软件前台
        self.switch_confirm = bool(cfg.get("switchConfirm", True))  # 切歌需确认
        self.exit_close_apps = bool(cfg.get("exitCloseApps"))  # 退出连带关被控软件
        # 启动连带开被控软件（DAW/OBS，与退出关闭对等；loopMIDI 不受此闸
        # 管：启动无条件保活、退出随 DAW 定去留）；默认开=保持历史行为
        # （此前启动序列无条件拉起）
        self.launch_apps = bool(cfg.get("launchAppsOnStart", True))
        # OBS 自动拉起独立配置（config obs.autoStart，默认开）：生效值=
        # 它 ∧ 总闸 ∧ 已配投屏位置，见 _obs_autostart_effective
        self._obs_autostart_cfg = bool(
            (cfg.get("obs") or {}).get("autoStart", True))
        # 端口提示名：空串=停用该联动（设置页下拉「无」）；缺键=无（不再
        # 回退旧硬编码端口名）
        self.vj_hint = str(cfg.get("vjPortHint") or "")
        self.kb_hint = str(cfg.get("kbPortHint") or "")
        self.clock_hint = str(cfg.get("clockPortHint") or "")
        # MIDI Remote 直控口名：空=用默认口名（与其它 hint 空=停用不同：
        # 直控无需配置即启用，口缺=动作失败报错）
        self.mr_hint = str(cfg.get("mrPortHint") or "")
        self.jcfg = dict(kbd_auto.DEFAULT_JUNO)
        self.jcfg.update(cfg.get("juno") or {})
        self.jcfg["dev"] = kbd_auto.norm_dev(self.jcfg.get("dev"))
        self.axcfg = dict(kbd_auto.DEFAULT_AX)
        self.axcfg.update(cfg.get("ax09") or {})
        self.axcfg["dev"] = kbd_auto.norm_dev(self.axcfg.get("dev"))
        # 跨线程队列先于踩钉监听器创建：构造期就拉起设备桥（钩子须在 MIDI
        # 口打开前装），桥线程踩踏后立即入队——晚了就是 AttributeError 丢动作
        self.q = queue.Queue()          # 日志/事件
        global _FILE_LOG
        _FILE_LOG = _FileLog(_LOGDIR)   # 文件日志（审计 #15；惰性首建）
        # 数据文件损坏隔离提示（_load_config/_load_playlist 改名 .corrupt）
        for c in (_CONFIG_CORRUPT, _PLAYLIST_CORRUPT):
            if c.exists():
                self.q.put("界面异常：检测到损坏的数据文件 %s，已隔离；"
                           "本次按默认值启动，可从同名 .bak 恢复" % c.name)
        # 一次性后台线程（探针/装载/热应用…）的异常统一进日志：此前
        # daemon 线程死亡静默，只有 Tk 回调与排水链有兜底
        threading.excepthook = self._thread_exc
        self.calls = stallguard.BoundedCallQueue(200)   # 跨线程 GUI 调用
        self.calls_urgent = collections.deque()  # 紧急调用（全停插队）
        self.pedal_hint, self.pedal_binds = pedal.load_binding(cfg)
        self.pedal_hid = pedal.load_hid(cfg)     # 蓝牙键盘型踩钉（HID 按键）
        self.pedal_device_hint, self.pedal_intercept = pedal.load_device_cfg(cfg)
        self.pedal_gestures, self.pedal_double, self.pedal_long = \
            pedal.load_gestures(cfg)
        _ped = cfg.get("pedal")                  # 手改配置非 dict 时不炸启动
        _ped = _ped if isinstance(_ped, dict) else {}
        self.pedal_remote_enabled = bool(_ped.get("remoteEnabled"))
        self._pedal_seq = {}     # 转发包去重：(device, sid) → 该会话已见最大 seq
        self._pedal_rate = {}    # 转发包限流：device → 上包时刻
        # 两表无锁的并发边界：合法部署=单平板单 worker 串行发包（键冲突
        # 不可达）；对抗性并发最坏 dict 迭代 RuntimeError→500→客户端重试
        # 自愈——LAN 信任域（/cmd 同级）内不构成升级，不为它上锁
        self._pedal_gwin = [0.0, 0]   # 全局限流窗：[窗起点, 窗内包数]
        self._pedal_gwin_lock = threading.Lock()   # gwin 非原子读改写
        # seq/rate 同为线程池并发的非原子读改写（淘汰+写回交错会漏删/多删
        # 一格）：与 gwin 各持小锁，不共用以免限流窗被去重表拖住
        self._pedal_dedup_lock = threading.Lock()
        # 监听器在构造期立即创建：设备桥的 LL 钩子安装必须发生在进程内任何
        # MIDI 口打开之前（实测在 MIDI 口活动后安装有概率挂死）
        self.pedal = pedal.PedalListener(
            on_action=lambda a: self.calls.put(
                lambda: self._pedal_action(a)),
            on_event=lambda m: self.q.put("踩钉桥：%s" % m))
        self.pedal.apply(self.pedal_hint, self.pedal_binds, self.pedal_hid,
                         self.pedal_device_hint, self.pedal_intercept,
                         self.pedal_gestures, self.pedal_double,
                         self.pedal_long)
        self.pedal.try_open()
        self._pedal_retry = 0.0
        self._link_heal_at = 0.0    # 联动端口自愈下轮时间（_tick 挂钩，10s 一轮）
        # 移动端遥控（webRemote 段；web 实例在 _startup 里起）
        self.web_cfg = dict(web_remote.DEFAULT_WEB_REMOTE)
        self.web_cfg.update(cfg.get("webRemote") or {})
        self.web = None
        self._web_snap = {}     # /state 快照（_tick_banner 每 400ms 重建）
        pl = _load_playlist()
        self.pl_keys = pl["playlist"]   # 播放列表（用户编排，持久化）
        self.durations = pl["durations"]
        self.dur_src = pl["durSrc"]     # "manual"=手填；不在表=自动；无时长=未知
        self.songs = []                 # 素材库全量
        self.by_key = {}
        self.cur = None                 # 播放列表下标：已请求加载的项
        self._adopted_name = None       # 横幅已跟随的工程名（手动跟随去重）
        self._play_after_switch = False  # 切换完成后是否自动开始播放
        self._pending_switch = None     # 切换中用户点的下一首（完成后执行）
        self._switch_key = None         # 进行中切换的 key 快照
        self._kb_warned = set()         # 未配置音色音符的告警去重
        self.kb_err = ""                # 键盘自动化停用原因（监控栏显示）
        self._lib_query = ""        # 编排页搜索词（_lib_view 过滤）
        self._kb_last = "待机"      # 最近一次音色切换结果（web 键盘页显示）
        self.log_ring = collections.deque(maxlen=500)   # 新前端日志页数据源
        self.slots = {}                 # 已加载工程：JUNO 映射（音符 72-81）
        self.ax_slots = {}              # 已加载工程：AX-09 映射（音符 84-89）
        self.cur_point_id = None        # 已装载条目的定位点 id（裸条目=None）
        self._persist_lock = threading.Lock()   # 主/切歌/启动三线程共用写播放列表
        self._obs_apply_lock = threading.Lock()  # 设置保存的 OBS 热应用串行化
        self._load_lock = threading.Lock()  # 装载代际守卫的校验+写原子化
        root.report_callback_exception = self._on_ui_error
        self.ctl = self.sync = self.port = self.ctrl = self.watch = None
        self.clock_port = None
        self.mtc = None                 # 时码解码器（启动线程构造，_watch 读计数）
        self._startup_done = False       # 门控 _apply_ports：启动期禁热切换
        self._pending_ports = False      # 启动期内保存过端口（完成后补热切换）
        self.switcher = self.ax_switcher = self.kb_port = None
        self.juno_shift = 0             # JUNO 全局移调累计值（半音，±24）
        self.ax_shift = 0               # AX-09 全局移调累计值（半音，±12）
        self.points = {}                # lib_key → 定位点数据（_load_songs 装载）
        self._goto_ack = None           # (发送前位置, 时戳, 标签)：GOTO 无回执告警
        self.mmc_out = None             # MMC 输出口句柄（定位点 GOTO）
        self.cur_song_path = None       # 已装载音色映射的工程路径（web 键盘页装载判定用）
        self._pedal_learner = None   # web 学习会话 (action, Learner, deadline)
        self._pedal_learn_gen = 0    # 学习启动世代（取消/重启即作废在途构造）
        self._pedal_learn_starting = False   # 后台构造中（轮询回 starting 态）
        self._pedal_learn_start_err = None   # 构造失败一次性错误（轮询消费）
        self._pedal_learn_start_t = 0.0      # 构造起步时刻（卡死防护计时）
        self.start_err = ""
        self._web_status = {}        # 遥控网络状态缓存（_web_status_probe 刷新）
        # 主窗顶层 HWND 一次预取（主线程）：_regain_focus 在走带/全停/切歌
        # worker 线程调用，禁再摸 Tk API（见其 docstring）。置顶切换不改
        # HWND，窗口生命周期=进程生命周期，无需失效机制。GetParent 在窗口
        # 未 realize 时实测返回 0（图标缺失分支会跳过 update_idletasks），
        # 退回 widget 句柄本身兜底
        self._main_hwnd = ctypes.windll.user32.GetParent(
            self.root.winfo_id()) or self.root.winfo_id()
        # 主线程停摆黑匣子：心跳由 _tick/_drain_calls 每轮喂，专职守护线程
        # 发现停摆超 3 秒即把现场（正在执行的回调/队列深度）落盘 stall.log——
        # 黑面在用户侧是肉眼，日志侧必须有铁证（局限：卡在按钮 command 等
        # 非轮询回调里时 tag 显示「循环间隙」，队列深度证据仍在）
        self._hb = time.monotonic()     # 主线程最近心跳（monotonic）
        self._hb_tag = ""               # 正在执行的主线程回调名（取证）
        self._stall_wd = stallguard.StallWatchdog(
            heartbeat=lambda: self._hb,
            snapshot=lambda: {"calls": self.calls.qsize(),
                              "q": self.q.qsize(),
                              "dropped": getattr(self.calls, "dropped", 0),
                              "tag": self._hb_tag},
            path=_HERE / "stall.log",
            notify=self.q.put)
        self._stall_wd.start()
        self._stack_sent = stallguard.StackSentinel(_HERE / "stall_stack.log")
        self._stall_probe = stallguard.StallNativeProbe(
            heartbeat=lambda: self._hb,
            main_hwnd=lambda: self._main_hwnd,
            path=_HERE / "stall_native.log").start()
        threading.Thread(target=self._startup, daemon=True).start()
        root.after(400, self._tick)
        root.after(50, self._drain_calls)

    # ---- 退出与收尾 ----

    def _on_exit(self):
        """退出收尾 → 关 MIDI 口/停后台线程。退出确认由前端侧栏退出钮
        两段式承担（Tk 对话框跨窗口会沉底/触发 stallguard 误报，红队
        遗留教训，随旧前端一并移除）。"""
        self._stall_wd.stop()       # 退出期心跳冻结不算停摆（假条目噪声）
        self._stack_sent.stop()     # 同理断掉栈哨兵计时窗
        self._stall_probe.stop()
        for closer in ((lambda: self.port.close()),
                       (lambda: self.kb_port.close()),
                       (lambda: self.clock_port.close()),
                       (lambda: self.pedal.shutdown()),
                       (lambda: mb.close_out(self.mmc_out))):
            try:
                closer()
            except Exception:
                pass
        # 非守护线程：窗口先关、进程等收尾做完——网络动作不占主线程，
        # 不会出现点退出后窗口长时间"未响应"；先趁 OBS 在线熄屏清文件，
        # 再按需关被控软件（顺序反了熄屏会随 OBS 退出而发不出去）
        threading.Thread(target=self._exit_worker, daemon=False).start()
        self.root.destroy()

    def _exit_worker(self):
        """退出收尾（窗口已关）。先停网页遥控栈（服务/翻谱口，热点按所有权
        决定关不关——热点停止放在 daemon 线程：PS 调用卡住不拖住退出），
        熄屏并清媒体源文件，OBS 下次启动不续播。"""
        if self.web is not None:
            # 限时等待：热点 PS 调用一般 1–3 秒；卡死时最多等 8 秒就放行退出
            t = threading.Thread(target=self._web_shutdown, daemon=True)
            t.start()
            t.join(8)
        try:
            if self.ctl is not None:
                self.ctl.stop_media()
                self.ctl.enabled = False
                self.ctl.shutdown()
        except Exception:
            pass
        if self.exit_close_apps:
            self._close_controlled()
        # loopMIDI 退出判定（11-04 定案）：DAW 底座仍在运行=不关（如用户
        # 在 DAW 退出弹窗按了「取消」，不能把伺候它的 loopMIDI 抽走）；
        # 退净=关。必须放在「退出时关闭被控软件」行为之后——先关 DAW 再
        # 判定，否则 DAW 恒在运行，判定恒「留」无效。
        if not find_processes_by_prefix(self.facts["proc_prefix"]):
            close_loopmidi()

    def _close_controlled(self):
        """两个被控软件**同时**发出关闭请求（先前是串行等待，Cubase 退出
        本身要几十秒，OBS 白等）。Cubase 的保存弹窗仍由本线程照看到退完；
        OBS 在子线程等待/兜底。loopMIDI 不在此列：去留看 DAW 底座脸色，
        判定在 _exit_worker 关完 DAW 后进行。"""
        threading.Thread(target=close_obs_app, daemon=True).start()
        daw_ctrl.close_app(log=lambda *_: None)

    def _restart(self):
        """底座切换重启：脱管拉起继任实例后走标准退出链（含 exitCloseApps
        收尾——切底座本就该关掉旧底座的被控软件）。继任带 --wait-parent
        <pid> 等本进程退出释放单实例互斥体；等待上限 90s（对齐 close_app
        60s + web 8s 收尾），超时放行撞双开框自然退出。"""
        argv = ([sys.executable] if getattr(sys, "frozen", False)
                else [sys.executable, os.path.abspath(sys.argv[0])])
        try:
            subprocess.Popen(
                argv + ["--wait-parent", str(os.getpid())],
                creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                               | subprocess.CREATE_NEW_PROCESS_GROUP),
                close_fds=True)
        except OSError as e:
            self.q.put("重启失败：%s，请手动退出后重开" % e)
            return
        self._on_exit()

    # ---- 后台线程：起服务 + 扫素材库 + 探时长 ----

    def _on_obs_connected(self):
        """OBS 连上（含重连）后回调（连接线程）：报状态，按设置恢复投影。
        静音不由回调重放：连接循环在回调前已 _set_mute 同步过（obs_ctrl
        「静音偏好随每次连接同步」），回调再发=重复 RPC+失败级联。"""
        self.q.put("OBS 已连接")
        if self.ctl.cfg.get("projectorMonitor", "") not in ("", None, -1) \
                and not self.ctl.apply_projector():
            self.q.put("VJ显示位置未恢复：%s" % self.ctl.last_error)

    def _obs_autostart_effective(self):
        """OBS 自动拉起的生效值，三重门：obs.autoStart 独立配置 ∧
        「启动时自动打开被控软件」 ∧ 已配置 VJ 显示位置（「无」=不投屏，
        此时即使总闸开也不允许连接循环自动拉起 OBS）。"""
        if self.ctl is None:
            return False
        mon = self.ctl.cfg.get("projectorMonitor", "") or ""
        return bool(self._obs_autostart_cfg and self.launch_apps and mon)

    def _ensure_daw(self):
        """启动自检：DAW 未运行就自动拉起（Cubase 冷启动到 Hub 约 30 秒，
        之后切歌才能单实例转交）。进程在而界面全无=正在退出的空壳（上一局
        「退出关闭被控软件」的尾巴），等它退净再拉起，不算「已在运行」。
        尽力而为，失败只记日志不阻断启动。"""
        dn = self.facts["display_name"]
        try:
            if find_processes_by_prefix(self.facts["proc_prefix"]):
                if daw_ctrl.ui_alive():
                    self.q.put("%s 已在运行" % dn)
                    return
                self.q.put("%s 正在退出，退净后自动启动…" % dn)

                def launch():
                    if not daw_ctrl.wait_exit():
                        # 等待期间窗口出来了（如正处冷启动）：不算失败
                        self.q.put("%s 已在运行" % dn
                                   if daw_ctrl.ui_alive()
                                   else "%s 无界面进程迟迟未退净，未自动启动"
                                        % dn)
                        return
                    self._launch_daw("%s 已退净，已自动启动"
                                     "，冷启动约 30 秒" % dn)
                threading.Thread(target=launch, daemon=True).start()
                return
            self._launch_daw("%s 未运行，已自动启动，冷启动约 30 秒" % dn)
        except Exception as e:
            self.q.put("%s 自检失败：%s" % (dn, _err(e)))

    def _launch_daw(self, ok_msg):
        """拉起 DAW 本体（路径无效/启动失败只记日志，不抛出）。"""
        exe = self.ccfg.get("dawExe") or ""
        if not exe or not os.path.exists(exe):
            self.q.put("dawExe 路径不存在，无法自动拉起")
            return
        r = launch_detached(exe)
        if r > 32:
            self.q.put(ok_msg)
        else:
            self.q.put("%s 自动启动失败，ShellExecute 代码 %s"
                       % (self.facts["display_name"], r))

    def _startup(self):
        """后台启动：按依赖分组逐项降级——一组失败只废该组功能并记红字，
        其余服务照常起（缺什么补什么，不再一票否决）。DAW/OBS 未运行会在
        此阶段自动拉起，受「启动时自动打开被控软件」总闸控制；loopMIDI
        例外——无条件保活（11-04 定案）：Cubase 工程引用它的端口，不在场
        每次载入弹「未找到端口」，与总闸解耦。"""
        try:
            if not self.launch_apps:
                self.q.put("启动时不打开被控软件：DAW/OBS 未拉起"
                           "（loopMIDI 照常保活），需要时请手动打开")
            else:
                self._ensure_daw()   # 最慢的后端最先拉起（冷启动约 30 秒）
            # loopMIDI 无条件保活：未运行就拉起；配置了联动口名时再等端口
            # （VJ 触发/走带跟随依赖端口，同样与总闸解耦）
            try:
                hint = self.vj_hint or self.kb_hint or self.clock_hint
                if hint:
                    mb.ensure_loopmidi(hint)   # 端口缺时拉起+等端口（≤15s）
                    self.q.put("loopMIDI 端口就绪")
                elif not find_processes_by_prefix("loopmidi"):
                    exe = mb._loopmidi_exe()
                    if exe is None:
                        raise SystemExit("找不到 loopMIDI.exe")
                    if launch_detached(exe) <= 32:
                        raise SystemExit("拉起 loopMIDI 失败")
                    self.q.put("loopMIDI 已拉起")
            except SystemExit as e:  # 单项降级：缺 loopMIDI 不拖垮其余
                self.q.put("loopMIDI 未就绪：%s，VJ 触发/走带跟随不可用"
                           % e)
            try:                         # OBS 未运行时连接循环会自动拉起，
                if not find_processes_by_prefix("obs64"):   # 这里只补状态
                    # 与三重门口径一致（此处 ctl 未建，按同源配置组合判）
                    if self.launch_apps and self._obs_autostart_cfg and (
                            _load_config().get("obs") or {}).get(
                                "projectorMonitor", ""):
                        self.q.put("OBS 未运行，将自动拉起")
                    else:
                        self.q.put("OBS 未运行，不自动拉起：启动打开被控"
                                   "软件或 OBS 自动启动未开，或未配置投屏"
                                   "位置")
            except Exception:
                pass
            # VJ 链：OBS 客户端 → 走带同步/自动推进 → MIDI 监听（相互依赖，
            # 整链一组；链内 MIDI 口缺失仍单独降级）
            try:
                self.ctl = ObsController(_load_config().get("obs") or {})
                self.ctl.on_connected = self._on_obs_connected
                # 密码以 dpapi: 密文落盘：控制器拿解密后的明文去连接；
                # 旧明文原样通过（写入链在下次保存时升级为密文）
                self.ctl.cfg["password"] = _protect_obs_password(
                    self.ctl.cfg)
                # OBS 自动拉起生效值三重门落位（obs.autoStart ∧ 总闸 ∧
                # 已配投屏位置）：连接循环据此决定是否拉起 OBS
                self.ctl.cfg["autoStart"] = self._obs_autostart_effective()
                self.sync = mb.TransportSync(self.ctl, on_event=self.q.put)
                self.watch = advance.AdvanceWatch(
                    on_finished=lambda: self.calls.put(self._advance),
                    on_stop_transport=lambda: self.calls.put(
                        lambda: self._transport("stop")),
                    on_event=self.q.put,
                    clock_timeout=mb.CLOCK_TIMEOUT)
                self.watch.set_armed(self.auto_advance)   # 恢复持久化的勾选
                if self.auto_advance:
                    self.q.put("自动切换工程已恢复为开启")
                try:
                    if self.vj_hint:
                        self.port = mb.MidiIn(
                            self.vj_hint,
                            mb.note_handler(self.ctl, self.sync,
                                            report=self.q.put))
                    else:
                        self.q.put("VJ 触发监听已停用")
                except SystemExit as e:  # 没有端口/被占用：只废 VJ 触发这一项
                    self.port = None
                    self.q.put("MIDI 监听未启动：%s，每 10 秒自动重试" % e)
                # 时码监听：独立端口（与 VJ 音符口解耦，端口可各自指定）。
                # 解码器居中分髮：QF 逐条喂三态、装配位置喂推进；全帧只进位置
                self.mtc = mtc.Decoder(on_qf=self.sync.on_clock,
                                       on_position=self.watch.on_position,
                                       on_full=self.watch.on_full_position)
                try:
                    if self.clock_hint:
                        self.clock_port = mb.MidiIn(
                            self.clock_hint, lambda n, v: None,
                            on_mtc=self.mtc.feed, on_f8=self.mtc.note_f8,
                            on_sysex=self.mtc.full_frame)
                        self.q.put("时码监听已启动：%s" % self.clock_port.name)
                    else:
                        self.mtc = None
                        self.q.put("时码监听已停用：未设时码端口，走带三态/"
                                   "已播/自动推进与停止键不可用")
                except SystemExit as e:
                    self.clock_port = None
                    self.mtc = None
                    self.q.put("时码监听未启动：%s，走带三态/自动推进暂"
                               "不可用；每 10 秒自动重试" % e)
                self.ctl.enabled = True
                self.ctl.start()
                threading.Thread(target=self._watch, daemon=True).start()
                if self.port is not None:
                    self.q.put("MIDI 监听已启动：%s" % self.port.name)
            except Exception as e:
                self.start_err = "VJ 链未启动：%s" % _err(e)
                self.q.put(self.start_err)
            # 切歌/走带控制（独立于 VJ 链）
            try:
                self.ctrl = daw_ctrl.DawController(
                    self.facts, self.ccfg["dawExe"], log=self.q.put)
            except Exception as e:
                self.q.put("%s 控制未启动：%s，切歌/走带不可用"
                           % (self.facts["display_name"], _err(e)))
            # MIDI Remote 直控口（走带/全停/关工程，Cubase 唯一路径）：
            # 仅 Cubase；口名缺省=默认 loopMIDI 口
            if self.facts.get("name") == "cubase":
                mr_ctrl.set_port_hint(
                    self.mr_hint or mr_ctrl.PORT_HINT_DEFAULT)
            # MMC 输出口（定位点 GOTO）：仅 Cubase 底座，走与 MTC 收听同一
            # 虚拟口——外发 SysEx 经 loopMIDI 喂给 DAW；mtc.Decoder 不解析
            # MMC 帧（F0 7F 7F 06 …），回环进本程序无害
            self._open_mmc_out()
            # 键盘自动化：发送线程（JUNO + AX-09）+ 联动端口
            try:
                self.switcher = kbd_auto.ToneSwitcher(
                    self.jcfg, self.q.put, on_result=self._on_kb_result)
                self.ax_switcher = kbd_auto.ToneSwitcher(
                    self.axcfg, self.q.put, on_result=self._on_kb_result)
                try:
                    if self.kb_hint:
                        self.kb_port = kbd_auto.RawMidiIn(self.kb_hint,
                                                          self._on_kb_msg)
                        self.q.put("键盘自动化监听已启动：%s"
                                   % self.kb_port.name)
                    else:
                        self.q.put("键盘自动化监听已停用")
                except kbd_auto.PortNotFound as e:
                    self.kb_port = None
                    self.kb_err = str(e)
                    self.q.put("键盘自动化停用：%s，每 10 秒自动重试" % e)
            except Exception as e:
                self.kb_port = None
                self.kb_err = str(e)
                self.q.put("键盘自动化未启动：%s" % _err(e))
            # 踩钉（监听器已在构造期创建并启动设备桥，这里补试 MIDI 口）
            try:
                if self.pedal.try_open():
                    self.q.put("CC 踩钉监听已启动：%s" % self.pedal.name)
                elif self.pedal_hint:
                    self.q.put("未找到踩钉口「%s」，每 10 秒自动重试"
                               % self.pedal_hint)
                if self.pedal.hid_active:
                    disp = (pedal.device_display(self.pedal_device_hint)
                            if self.pedal_device_hint else "未选择设备")
                    self.q.put("键盘踩钉监听已启动：%s" % disp)
            except Exception as e:
                self.q.put("踩钉监听未启动：%s" % _err(e))
            # 移动端遥控 + 翻谱推送（整组独立降级：总开关关=只报停用）
            try:
                self.web = web_remote.WebRemote(self, self.web_cfg)
                self.web.startup()      # 内部按总开关决定起不起（热点要数秒）
            except Exception as e:
                self.q.put("移动端遥控未启动：%s" % _err(e))
            try:
                self._load_songs()
            except Exception as e:
                self.q.put("素材库加载失败：%s" % _err(e))
        except SystemExit as e:
            self.start_err = str(e)
            self.q.put("启动失败：%s" % e)
        except Exception as e:              # 后台线程兜底：任何异常都进日志
            self.start_err = self.start_err or "启动异常：%s" % _err(e)
            self.q.put(self.start_err)
        finally:
            self._startup_done = True
            if self._pending_ports:      # 消费启动期票据：补热切换（幂等清零）
                self._pending_ports = False
                self.calls.put(self._apply_ports)
        threading.Thread(target=self._web_status_loop, daemon=True,
                         name="web-status-probe").start()

    def _web_status_loop(self):
        """遥控网络状态缓存（/settings GET 的 webStatus 数据源）：热点态
        PS 查询与端口 connect 探测都是秒级慢操作，不能在 Tk 主线程做——
        专职线程每 5 秒刷新，GET 只读缓存（GIL 原子）。"""
        while True:
            try:
                self._web_status_probe()
            except Exception:      # 单轮故障不得杀死线程（死=状态永久陈旧）
                pass
            time.sleep(5.0)

    def _web_status_probe(self):
        out = {"net": "?"}
        try:
            if str(self.web_cfg.get("mode") or "hotspot") == "lan":
                cands = [ip for ip, _n in hotspot.lan_ips()
                         if web_remote.valid_push_ip(ip)
                         and not ip.startswith("127.")]
                if self.web_cfg.get("ip") or len(cands) == 1:
                    out["net"] = "lan-ready"
                elif not cands:
                    out["net"] = "lan-none"
                else:
                    out["net"] = "lan-multi"
            else:
                st = hotspot.state()
                out["net"] = ("hot-on" if st.get("on") else
                              "hot-off" if st.get("ok") else "hot-bad")
        except Exception:
            pass
        srv_ip = "127.0.0.1"
        try:
            web = self.web
            if web is not None and web.server is not None:
                srv_ip = web.server.server_address[0]
        except Exception:          # 服务重建竞态（_stop_server 置 None）
            pass
        out["listen"] = {}
        for key, port in (("serverPort",
                           self.web_cfg.get("serverPort") or 8765),
                          ("appPort", self.web_cfg.get("appPort") or 8767)):
            try:
                s = socket.create_connection((srv_ip, port), timeout=0.8)
                s.close()
                out["listen"][key] = True
            except OSError:
                out["listen"][key] = False
        # 网卡枚举同属秒级慢操作（PS 子进程）：与状态一起在专职线程刷新，
        # _settings_get_web 只读缓存——设置页 GET 不得冻结 Tk 主线程
        out["adapters"] = [{"name": name, "ip": ip} for ip, name in
                           hotspot.lan_ips() if web_remote.valid_push_ip(ip)
                           and not ip.startswith("127.")]
        self._web_status = out

    def _entry_alive(self, key):
        """播放列表条目存活判定：pt 条目 ⇔ 宿主在库且启用且点存在。"""
        pt = points.parse_pt_key(key)
        if pt:
            lib_key, pid = pt
            data = self.points.get(lib_key)
            return bool(data and data["enabled"]
                        and points.get_point(data, pid)
                        and lib_key in self.by_key)
        return key in self.by_key

    def _resolve_entry(self, key):
        """播放列表条目统一解析 → {display, path, lib_key, song_name,
        point|None}：显示名/切歌路径/装载时长全走这里，pt 与裸条目同一
        出口；库缺或点缺返回 None。"""
        pt = points.parse_pt_key(key)
        if pt:
            lib_key, pid = pt
            song = self.by_key.get(lib_key)
            p = points.get_point(self.points.get(lib_key)
                                 or points.empty_data(), pid)
            if song is None or p is None:
                return None
            return {"display": points.display_name(song["name"], p["name"]),
                    "path": song["path"], "lib_key": lib_key,
                    "song_name": song["name"], "point": p, "key": key}
        song = self.by_key.get(key)
        if song is None:
            return None
        return {"display": song["name"], "path": song["path"],
                "lib_key": key, "song_name": song["name"], "point": None,
                "key": key}

    def _open_mmc_out(self, quiet=False):
        """MMC 输出口启动（_startup 工作线程调用）：仅 Cubase 底座 + 时码口
        已配；找不到/打不开只降级定位点定位，不拦主流程。quiet=自愈轮静默
        重试（失败不记日志，10s 一轮防刷屏）。"""
        self.mmc_out = None
        if self.facts.get("name") != "cubase" or not self.clock_hint:
            return
        dev = mb._pick(mb._out_devices(), self.clock_hint)
        if dev is None:
            if not quiet:
                self.q.put("MMC 输出口未启动：找不到含「%s」的输出端口"
                           "，定位点定位暂不可用；每 10 秒自动重试"
                           % self.clock_hint)
            return
        h, err = mb.open_out(dev[0])
        if h is None:
            if not quiet:
                self.q.put("MMC 输出口未启动：%s" % err)
            return
        self.mmc_out = h
        self.q.put("MMC 输出已启动：%s" % dev[1])

    def _load_songs(self):
        root = self.ccfg["projectsRoot"]
        if not os.path.isdir(root):
            self.q.put("工程库不存在：%s，请改 config.json 的"
                       " dawSettings.projectsRoot" % root)
            return
        songs = scan_library(root, self.facts["song_ext"])
        self.by_key = {s["key"]: s for s in songs}
        self.songs = songs
        # 定位点 sidecar 装载（工程目录级）；损坏降级为停用空表并告警。
        # 逐键装载含逐目录磁盘 IO：先建本地表再整体重绑——HTTP 线程的
        # /points、/playlist 直接迭代 self.points，边建边填会 dict
        # changed size during iteration（单次重绑对读者原子）
        pts = {}
        for s in songs:
            data, err = points.load_points(os.path.dirname(s["path"]))
            if err:
                self.q.put("《%s》%s" % (s["name"], err))
            pts[s["key"]] = data
        self.points = pts
        # 播放列表清掉已失效的项（库缺项 / 定位点停用或点已删）
        self.pl_keys = [k for k in self.pl_keys if self._entry_alive(k)]
        # 重启恢复：Cubase 里还开着的工程按标题对回播放列表
        ws = daw_ctrl.current_project()
        name = daw_ctrl.project_name_from_title(ws[1]) if ws else None
        if name:
            for i, k in enumerate(self.pl_keys):
                e = self._resolve_entry(k)
                if e is None or e["song_name"] != name:
                    continue
                self.cur = i
                d_file, left = _read_span(e["path"])
                p = e["point"]
                if p is not None:   # 定位点条目：装载段区间，不动 durations
                    d, left = p["end"] - p["start"], p["start"]
                else:
                    d = self.durations.get(k) or d_file or 0.0
                    self.durations[k] = d
                if self.watch is not None:  # watch 缺席降级态同 _switch
                    self.watch.set_duration(d, left)
                self.slots = kbd_auto.load_slots(
                    e["path"], point_id=e["point"]["id"]
                    if e["point"] else None)
                self.ax_slots = kbd_auto.load_slots(
                    e["path"], "ax", point_id=e["point"]["id"]
                    if e["point"] else None)
                self.cur_song_path = e["path"]
                self.cur_point_id = e["point"]["id"] if e["point"] else None
                self.q.put("已恢复当前工程：《%s》" % name)
                break
        self._probe_durations(songs)
        self.q.put("素材库 %d 首，播放列表 %d 首：%s"
                   % (len(songs), len(self.pl_keys), root))

    def _lib_view(self):
        """素材库视图（编排页 /playlist 数据源）：启用定位点的工程原位
        展开为点条目（裸条目隐藏）。"""
        q = self._lib_query
        out = []
        for s in self.songs:
            data = self.points.get(s["key"])
            if data and data["enabled"] and data["points"]:
                for p in data["points"]:
                    entry = dict(s, key=points.pt_key(s["key"], p["id"]),
                                 name=points.display_name(s["name"], p["name"]))
                    if not q or q in entry["name"].lower() \
                            or q in s["team"].lower():
                        out.append(entry)
            elif not q or q in s["name"].lower() or q in s["team"].lower():
                out.append(s)
        return out

    def _probe_durations(self, songs):
        """全量重探自动来源的工程时长并持久化（后台线程；65 首/66MB 实测
        约 0.15s）：在 DAW 里改过定位条/事件终点的歌，重启即跟上。手动设定
        不被覆盖；值全没变就不落盘，安静返回。解析失败（如未设定位条）由
        手填兜底。"""
        if not self.facts["probe_duration"]:
            return
        fresh = _reprobe(songs, self.durations, self.dur_src)
        if not fresh:
            return
        self.durations.update(fresh)
        self._persist()
        self.q.put("已重探工程时长：%d 首有更新，%d 首未知，需手填"
                   % (len(fresh),
                      sum(1 for s in songs
                          if not self.durations.get(s["key"]))))

    def _watch(self):
        f8_mark = None                 # F8-only 诊断：起见时刻与一次性闩锁
        f8_hinted = False
        while True:
            try:
                self.sync.poll()
                self.watch.poll()
                m = self.mtc
                if m is not None and m.qf_seen:
                    f8_mark = None      # 时码已到：F8 计数是残留，撤销计窗
                if not f8_hinted and m is not None and not m.qf_seen \
                        and not m.full_seen and m.f8_seen >= 8:
                    # full_seen 也要求为 0：正确配置的工程停止/定位会补发
                    # 全帧（SysEx），「停止模式时钟」的 F8 不构成时码缺失证据
                    if f8_mark is None:
                        f8_mark = time.monotonic()
                    elif time.monotonic() - f8_mark > 5.0:
                        f8_hinted = True
                        self.q.put("检测到 MIDI 时钟 F8 但未收到时码："
                                   "请在工程同步设置勾选 MTC 时码输出到"
                                   "该端口，否则走带三态/进度/自动推进"
                                   "不可用")
            except Exception as e:
                # 看门狗线程绝不允许静默死亡
                try:
                    self.q.put("后台监控异常，已自动恢复：%s" % _err(e))
                except Exception:
                    pass
            time.sleep(mb.POLL_SEC)

    def _on_kb_msg(self, status, d1, d2):
        """Keyboard Automation 端口音符：48-51→JUNO 全局移调（连按累加）、
        53-56→AX-09 全局移调（弯音钉位，连按累加）、72-81→JUNO、
        84-89→AX-09 查映射切音色。"""
        st = status & 0xF0
        if st == 0x90 and d2 == 0:
            st = 0x80                   # Note On vel=0 等价 Note Off
        if st not in (0x90, 0x80):
            return
        down = st == 0x90
        if d1 in kbd_auto.SHIFT_NOTES:
            if not down:
                return
            if self.ctrl is not None and self.ctrl.busy:
                self.q.put("切歌中，忽略移调音符 %s" % kbd_auto.note_name(d1))
                return
            self.juno_shift = max(
                -kbd_auto.SHIFT_LIMIT,
                min(kbd_auto.SHIFT_LIMIT,
                    self.juno_shift + kbd_auto.SHIFT_NOTES[d1]))
            if self.switcher:
                self.switcher.submit_shift(
                    self.juno_shift,
                    why="JUNO 全局移调 %+d 半音" % self.juno_shift)
            else:
                self.q.put(ui_text.TRANSPOSE_NOT_READY
                           % self.juno_shift)
            return
        if d1 in kbd_auto.AX_SHIFT_NOTES:
            if not down:
                return
            if self.ctrl is not None and self.ctrl.busy:
                self.q.put("切歌中，忽略移调音符 %s" % kbd_auto.note_name(d1))
                return
            self.ax_shift = max(
                -kbd_auto.AX_SHIFT_LIMIT,
                min(kbd_auto.AX_SHIFT_LIMIT,
                    self.ax_shift + kbd_auto.AX_SHIFT_NOTES[d1]))
            if self.ax_switcher:
                self.ax_switcher.submit_msgs(
                    kbd_auto.ax_shift_msgs(self.ax_shift,
                                           self.axcfg["ch"]),
                    why="AX-09 全局移调 %+d 半音" % self.ax_shift)
            else:
                self.q.put(ui_text.AX_TRANSPOSE_NOT_READY
                           % self.ax_shift)
            return
        if not down:
            return
        if d1 in kbd_auto.SLOT_NOTES:
            slots, switcher = self.slots, self.switcher
        elif d1 in kbd_auto.AX_NOTES:
            slots, switcher = self.ax_slots, self.ax_switcher
        else:
            return
        if self.ctrl is not None and self.ctrl.busy:
            self.q.put("切歌中，忽略音色音符 %s" % kbd_auto.note_name(d1))
            return
        slot = slots.get(d1)
        if not slot:
            if d1 not in self._kb_warned:   # 循环播放时同一告警只记一次
                self._kb_warned.add(d1)
                self.q.put("音符 %s 未配置音色映射，在「编排」页录制"
                           % kbd_auto.note_name(d1))
            return
        switcher.submit(slot, why=kbd_auto.note_name(d1))

    def _on_kb_result(self, desc, err):
        """ToneSwitcher 线程回调：最近一次音色切换结果（只写属性）。
        失败也带动作名（音色描述/移调半音数），否则看不出哪个动作失败。"""
        self._kb_last = ("%s失败：%s" % (desc, err)) if err \
            else ("已发送：%s" % desc)

    # ---- 播放列表编排（主线程） ----

    def _switch(self, i, via, play_after=False):
        """切到播放列表第 i 项；返回 False=没开始（越界/正忙/库缺项）。"""
        if not (0 <= i < len(self.pl_keys)):
            if via == "advance":
                self.q.put("自动切换：已到播放列表末尾，不切换")
            elif not self.pl_keys:
                self.q.put("播放列表为空：先从素材库加入歌曲")
            else:
                self.q.put("已在播放列表末尾")
            return False
        key = self.pl_keys[i]
        entry = self._resolve_entry(key)
        if entry is None:
            self.q.put("歌单项不在素材库里：%s" % key)
            return False
        if via == "手动" and i == self.cur and entry["point"] is None:
            ws = daw_ctrl.current_project()
            if ws and daw_ctrl.project_name_from_title(ws[1]) \
                    == entry["song_name"]:
                self.q.put("《%s》已是当前工程，不重载" % entry["song_name"])
                return False
        # 同工程定位点：免开关工程，停走带→GOTO→按需播放（不占 ctrl.busy）
        if entry["point"] is not None and self.ctrl is not None \
                and not self.ctrl.busy \
                and self.cur_song_path == entry["path"] \
                and self.mmc_out is not None:
            self.q.put("切换到《%s》：同工程定位，无需重开" % entry["display"])
            threading.Thread(target=self._goto_worker,
                             args=(i, entry, play_after),
                             daemon=True).start()
            return True
        if self.ctrl is None or not self.ctrl.switch_to(
                entry["path"], on_done=self._switch_done):
            if via == "手动":       # 忙时保留最后一次用户请求，完成后自动执行
                # 记 key 不记下标：排队期间列表被它端增删时，执行时刻按
                # key 重定位（switch key 防线同款），不再切到漂移后的邻位
                self._pending_switch = key
                self.q.put("切换中：《%s》已排队，完成后自动执行"
                           % entry["song_name"])
            else:
                self.q.put("正忙，忽略%s切换请求：《%s》"
                           % (via, entry["song_name"]))
            return False
        self._switch_key = key      # 快照：完成时按 key 归属，防编排变动张冠李戴
        self._play_after_switch = play_after
        if self.watch is not None:  # watch 没起来（VJ 链构造失败）不得炸切歌，
            self.watch.reset()      #   与 _panic 同款守卫（审计实测可达）；但
                                    #   旧工程关窗期间尾流 QF 会重灌位置——
                                    #   真正的纪元闭合在 _switch_done（彼时旧
                                    #   工程已确保关闭），此处只是早零显示
        self.cur = i
        with self._load_lock:       # 清空+代际递增原子化：堵后台装载的
            self.slots = {}         # 「校验后写入」TOCTOU 窗（复审 M3）
            self.ax_slots = {}
            self.cur_song_path = None
            self.cur_point_id = None
            self._load_gen = getattr(self, "_load_gen", 0) + 1
        self._kb_warned = set()
        self.q.put("切换到《%s》…：%s"
                   % (entry["display"], "已排队" if via == "排队" else via))
        return True

    def _goto_worker(self, i, entry, play_after):
        """同工程定位点切换（工作线程）：停走带→GOTO→主线程装载段区间。
        停止键失焦偶发失败仅记日志——GOTO 在播放中落地光标仍会跳，自愈。"""
        self.ctrl.transport("stop")
        self._goto_send(entry["point"]["start"], entry["display"])

        def finish():
            if (not (0 <= i < len(self.pl_keys))
                    or self.pl_keys[i] != points.pt_key(
                        entry["lib_key"], entry["point"]["id"])
                    or self.cur_song_path != entry["path"]):
                return              # 编排已变/已切走他曲：弃本次装载
            self.cur = i
            if self.watch is not None:
                self.watch.reset()  # 清推进闩；位置由 GOTO 补发全帧重灌
                self.watch.set_duration(
                    entry["point"]["end"] - entry["point"]["start"],
                    entry["point"]["start"])
            if play_after:
                # resume 而非 play：光标已被上面的 GOTO 放在段起点，通用
                # play 的回零会打回工程头；play 也不走 _point_play 二次
                # GOTO（免重复定位与「未响应」误报）
                threading.Thread(target=self._transport_thread,
                                 args=("resume",), daemon=True).start()
        self.calls.put(finish)

    def _goto_send(self, start, label):
        """发 MMC GOTO 并登记 ACK（任意线程；1.2 秒内时码无变化则告警）。"""
        if self.mmc_out is None:
            self.q.put("MMC 输出口未就绪：无法定位《%s》" % label)
            return
        pre = self.mtc.position() if self.mtc is not None else None
        err = mb.send_sysex_h(self.mmc_out, points.mmc_goto_bytes(start))
        if err:
            self.q.put("定位发送失败：%s，输出口已弃，10 秒内自愈重开" % err)
            mb.close_out(self.mmc_out)   # 输出口无热拔通知，发送失败即弃口，
            self.mmc_out = None          #   自愈循环按「未打开」重开（11-11）
        else:
            self._goto_ack = (pre, time.time(), label)

    def _switch_done(self, name):
        key = self._switch_key
        self._switch_key = None
        play_after = self._play_after_switch
        self._play_after_switch = False
        if name is None or not key:
            return
        entry = self._resolve_entry(key)
        if entry is None:
            return
        if entry["point"] is not None:
            # 定位点条目：时长=段区间（不动 durations/dur_src）；打开工程
            # 后光标停在上次保存位，必须补 GOTO 到段起点
            if self.watch is not None:
                self.watch.reset()
                self.watch.set_duration(
                    entry["point"]["end"] - entry["point"]["start"],
                    entry["point"]["start"])
            self._goto_send(entry["point"]["start"], entry["display"])
            self._finish_switch_common(entry, play_after)
            return
        # 打开后再测一次时长；手动设定不被自动识别覆盖（需要时点「重新识别」）
        d, left = _read_span(entry["path"])
        if self.dur_src.get(key) == "manual":
            self.q.put("《%s》保留手动时长 %s：工程内为 %s，可点「重新识别」改用"
                       % (entry["song_name"], cpr_meta.fmt_mmss(self.durations.get(key)),
                          cpr_meta.fmt_mmss(d)))
        elif d and d != self.durations.get(key):
            self.durations[key] = d
            self._persist()
            self.q.put("《%s》时长更新为 %s" % (entry["song_name"],
                                                cpr_meta.fmt_mmss(d)))
        elif not d and not self.durations.get(key):
            self.q.put("《%s》时长未知：工程未设循环定位条，可用「写入时长」手填"
                       % entry["song_name"])
        d = self.durations.get(key) or d   # 手动时长优先：watch 与横幅/推进
        if self.watch is not None:  # 同 _switch：watch 缺席降级态不炸后台线程
            self.watch.reset()      #   同一坐标系（文件的 left 配手动秒数会错位）
            self.watch.set_duration(d, left)   # 纪元闭合同 _switch_done
        self._finish_switch_common(entry, play_after)

    def _finish_switch_common(self, entry, play_after):
        """切换收尾公共段（_switch_done 工作线程）：音色映射装载+焦点/自动播。"""
        pid = entry["point"]["id"] if entry["point"] else None
        slots = kbd_auto.load_slots(entry["path"], point_id=pid)
        ax_slots = kbd_auto.load_slots(entry["path"], "ax", point_id=pid)
        with self._load_lock:       # 写回与 _switch 清空/后台装载同锁原子化
            self.slots = slots      # （复审 R3-1：第三写者收编；IO 在锁外）
            self.ax_slots = ax_slots
            self.cur_song_path = entry["path"]
            self.cur_point_id = pid
        if self.slots or self.ax_slots:
            self.q.put(ui_text.MAP_LOADED_LOG
                       % (entry["song_name"], len(slots), len(ax_slots)))
        if play_after:
            # 等待加载的循环放后台线程，别堵主线程的状态轮询
            self.calls.put(lambda: threading.Thread(
                target=self._auto_play, daemon=True).start())
            self.q.put("加载完成，自动开始播放")
        # 非自动播放分支不再收回焦点：daw_ctrl._switch 已还原操作前前台
        # （对抗复核：此处再 _regain_focus 会二次翻转，踩钉/手机触发的
        # 切歌把用户从原窗口拽到本软件）

    def _toggle_points(self, lib_key, on, span=None):
        """启用/停用定位点（主线程，定位点设置窗调用）：sidecar 翻 enabled +
        播放列表原位替换/还原（可逆、数据无损，不弹确认）。启用且无点时
        建初始点「定位点1」=[0:00, 工程末尾]。span=HTTP 线程预取的
        (时长, 左偏移)，None=现读（Tk 窗路径；网络盘会阻塞，回退环境接受）。"""
        song = self.by_key.get(lib_key)
        if song is None:
            return
        data = self.points.get(lib_key) or points.empty_data()
        if on and not data["points"]:
            d_file, left = span if span is not None \
                else _read_span(song["path"])
            end = left + d_file if d_file else 0.0
            data = points.initial_data(end)
            if not end:
                self.q.put("《%s》工程末尾未知：请在定位点设置里补填结束点"
                           % song["name"])
        data["enabled"] = bool(on)
        self.points[lib_key] = data
        # 切歌在途的归属键跟随变换：正在打开的歌被启用=完成后落首点；
        # 被停用=还原裸条目归属
        if self._switch_key == lib_key and on:
            keys = points.pt_keys_for(lib_key, data)
            if keys:
                self._switch_key = keys[0]
        elif self._switch_key and points.parse_pt_key(self._switch_key) \
                and not on:
            self._switch_key = lib_key
        old_keys = list(self.pl_keys)
        if on:
            self.pl_keys = points.expand_replace(
                old_keys, lib_key, points.pt_keys_for(lib_key, data))
        else:
            self.pl_keys = points.collapse_restore(old_keys, lib_key)
        self.cur = points.remap_cur(old_keys, self.pl_keys, self.cur, lib_key)
        self._sync_watch_span()
        if self._pending_switch is not None:
            # 排队切换以 key 记账：宿主被 toggle 时跟随家族首键；它端删掉则弃
            k = self._pending_switch
            pt = points.parse_pt_key(k)
            if k == lib_key or (pt and pt[0] == lib_key):
                fam = (points.pt_keys_for(lib_key, data)
                       if data["enabled"] else [lib_key])
                self._pending_switch = fam[0] if fam else None
            elif k not in self.pl_keys:
                self._pending_switch = None
        try:
            points.save_points(os.path.dirname(song["path"]), data)
        except (OSError, ValueError) as e:
            # 回滚内存变换并重读磁盘：不留「列表已变而定义没存」的分叉
            data2, _ = points.load_points(os.path.dirname(song["path"]))
            self.points[lib_key] = data2
            self.pl_keys = old_keys
            self.cur = points.remap_cur(self.pl_keys, old_keys,
                                        self.cur, lib_key)
            self._sync_watch_span()
            self.q.put("定位点保存失败：%s" % e)
            return
        self._persist()
        self.q.put("《%s》定位点已%s"
                   % (song["name"], "启用" if on else "停用，条目已还原整曲"))
        # 当前曲在这家族里：装载粒度随开关变了（pt↔裸），重装载映射
        cur_key = (self.pl_keys[self.cur]
                   if self.cur is not None and self.cur < len(self.pl_keys)
                   else None)
        if cur_key and (cur_key == lib_key
                        or (points.parse_pt_key(cur_key)
                            or (None, None))[0] == lib_key):
            e = self._resolve_entry(cur_key)
            if e is not None and e["song_name"] == song["name"]:
                pid = e["point"]["id"] if e["point"] else None
                with self._load_lock:
                    self._load_gen = getattr(self, "_load_gen", 0) + 1
                self.slots = kbd_auto.load_slots(e["path"], point_id=pid)
                self.ax_slots = kbd_auto.load_slots(e["path"], "ax",
                                                    point_id=pid)
                self.cur_point_id = pid

    def _check_goto_ack(self):
        """GOTO 无回执告警：发送 1.2 秒后时码位置无变化 → MMC 链路存疑。
        时码从未在场（ever_live=False）的会话静默——未开时码输出无从判定。"""
        ack = self._goto_ack
        if ack is None:
            return
        pre, sent, label = ack
        if time.time() - sent <= 1.2:
            return
        self._goto_ack = None
        if pre is None or self.mtc is None or not self.mtc.qf_seen:
            # 「时码从未在场」判据用 qf_seen（reset 不清）：停止态 GOTO 前
            # watch 刚 reset、ever_live 恒 False，用它会把告警永远哑掉
            return
        if self.mtc.position() == pre:
            self.q.put("工程未响应定位：《%s》时码未变化，请检查 DAW 已勾 "
                       "MMC Slave 且输入口与 MMC 输出口为同一虚拟口" % label)

    # ---- 新前端（webui/平板 /app 页）命令面：主线程执行，经 calls 投递 ----

    def _sync_watch_span(self):
        """定位点定义变更/启停后的段坐标重锚：remap_cur 挪指针不改 watch
        坐标——旧段照旧计时（缩短→晚切、放长→早切、停用→段坐标当整曲用
        提前杀歌）。指针条目不属已装载工程时不动（坐标归装载路径负责）。
        ponytail: 还原整曲分支 left 取 0（左定位条未读）——自动切换最多
        提前左定位条秒数触发；要精确需 read_locators 预取。"""
        i = self.cur
        if i is None or not (0 <= i < len(self.pl_keys)) or self.watch is None:
            return
        entry = self._resolve_entry(self.pl_keys[i])
        if entry is None or self.cur_song_path != entry["path"]:
            return
        p = entry["point"]
        self.watch.reset()
        if p is not None:
            self.watch.set_duration(p["end"] - p["start"], p["start"])
        else:
            self.watch.set_duration(
                self.durations.get(entry["lib_key"], 0.0) or 0.0, 0.0)

    def _points_sync_list(self, lib_key):
        """定位点定义变更后同步：落盘 + 启用态重排播放列表点条目块 +
        持久化刷新（/ptcmd 同链）。"""
        song = self.by_key.get(lib_key)
        data = self.points.get(lib_key)
        if song is None or data is None:
            return "工程不存在"
        try:
            points.save_points(os.path.dirname(song["path"]), data)
        except (OSError, ValueError) as e:
            data2, _ = points.load_points(os.path.dirname(song["path"]))
            self.points[lib_key] = data2          # 回读磁盘：放弃本次变更
            return "保存失败：%s" % e
        if data["enabled"]:
            old = list(self.pl_keys)
            new = points.collapse_restore(old, lib_key)
            new = points.expand_replace(new, lib_key,
                                        points.pt_keys_for(lib_key, data))
            self.pl_keys = new
            self.cur = points.remap_cur(old, new, self.cur, lib_key)
            # 当前装载条目被重排掉（如删除正在播放的点）：粒度标签已死，
            # 按落点条目重装载映射——防滞留死点段（对位规则①会抑制自愈）、
            # song_key=None 的保存不再写孤儿段；落点未变（增删他点/改排）
            # 时元组相等，零动作
            cur_key = (self.pl_keys[self.cur]
                       if self.cur is not None and self.cur < len(self.pl_keys)
                       else None)
            e = self._resolve_entry(cur_key) if cur_key else None
            pid = e["point"]["id"] if e and e["point"] else None
            if (self.cur_song_path, self.cur_point_id) != \
                    (e["path"] if e else None, pid):
                with self._load_lock:   # 作废在途装载，防旧结果覆写
                    self._load_gen = getattr(self, "_load_gen", 0) + 1
                if e is not None and e["path"] == self.cur_song_path:
                    self.slots = kbd_auto.load_slots(e["path"], point_id=pid)
                    self.ax_slots = kbd_auto.load_slots(e["path"], "ax",
                                                        point_id=pid)
                    self.cur_point_id = pid
                else:
                    self.cur_point_id = None    # 落点非本工程/指针空：粒度
                    #   清零，映射由后续切换/对位接管（remap 不跨工程，守卫）
        self._persist()
        return None

    def span_for(self, key):
        """工程文件 (时长, 左定位条偏移)：供 HTTP 线程预取用——_read_span
        是整工程解析（网络盘秒级），不能在 Tk 线程现读；本方法无 Tk 依赖，
        任意线程可调。工程不在库返回 None。"""
        song = self.by_key.get(key)
        return _read_span(song["path"]) if song else None

    def _points_cmd_web(self, op, lib_key, payload, span=None):
        """/ptcmd：定位点增删改排（op: toggle/add/del/edit/move）。返回
        None=成功，str=错误文案（HTTP 线程经 result 队列取回）。
        span=HTTP 线程预取的 (时长, 左偏移)：_read_span 是整工程解析，
        网络盘秒级，不得在 Tk 线程现读。"""
        song = self.by_key.get(lib_key)
        if song is None:
            return "工程不存在"
        if op == "toggle":
            self._toggle_points(lib_key, bool(payload.get("on")), span=span)
            return None
        data = self.points.get(lib_key) or points.empty_data()
        if op == "add":
            d_file, left = span if span is not None \
                else _read_span(song["path"])
            start = payload.get("start")
            if start is None:
                start = self.mtc.position() if self.mtc is not None else 0.0
            else:
                start = points.finite_tc(start)
                if start is None:
                    return "开始时码必须为有限数字"
            end = payload.get("end")
            if end is None:
                end = left + d_file if d_file else 0.0
            else:
                end = points.finite_tc(end)
                if end is None:
                    return "结束时码必须为有限数字"
            pt, data = points.next_point(data, start, end)
            self.points[lib_key] = data
            return self._points_sync_list(lib_key)
        pid = payload.get("id")
        pt = points.get_point(data, int(pid or 0))
        if pt is None:
            return "定位点不存在"
        if op == "del":
            data["points"] = [q for q in data["points"]
                              if q["id"] != pt["id"]]
            try:                # 点删了它的槽位段一并清（点 id 不复用，留着
                kbd_auto.delete_point_slots(song["path"], pt["id"])
            except OSError:     # 是死数据；失败不拦删点
                pass
            self.points[lib_key] = data
            if not data["points"]:
                self._toggle_points(lib_key, False)   # 删光=自动停用
                return None
            return self._points_sync_list(lib_key)
        if op == "edit":
            start = float(payload.get("start") or 0.0)
            end = float(payload.get("end") or 0.0)
            err, _warn = points.validate_point(start, end)
            if err:
                return err
            pt["name"] = str(payload.get("name") or pt["name"]).strip() \
                or pt["name"]
            pt["start"], pt["end"] = start, end
            self.points[lib_key] = data
            return self._points_sync_list(lib_key)
        if op == "move":
            step = int(payload.get("step") or 0)
            idx = data["points"].index(pt)
            j = idx + (1 if step > 0 else -1)
            if not 0 <= j < len(data["points"]):
                return None
            data["points"][idx], data["points"][j] = \
                data["points"][j], data["points"][idx]
            self.points[lib_key] = data
            return self._points_sync_list(lib_key)
        return "未知操作"

    def _pickdir_web(self):
        """/pickdir：原生目录选择对话框（Tk 主线程执行；空串=取消）。"""
        try:
            # Tk 主窗全程 withdraw：对话框挂它名下会跟着沉底（弹了也看
            # 不见）——无父对话框独立居中置前
            path = filedialog.askdirectory(parent=None, title="选择目录")
        except Exception as e:
            return {"error": _err(e)}
        return {"path": os.path.normpath(path) if path else ""}

    def _pickfile_web(self):
        """/pickdir kind=file：原生文件选择对话框（如 Cubase 程序）。"""
        try:
            path = filedialog.askopenfilename(parent=None, title="选择程序")
        except Exception as e:
            return {"error": _err(e)}
        return {"path": os.path.normpath(path) if path else ""}

    def _settings_get_web(self):
        """设置页数据：当前生效值 + 可选列表（显示器/网卡/在线端口）。"""
        cfg = _load_config()
        obs = cfg.get("obs") or {}
        web = self._web_config_payload()
        # 服务网卡候选带 {name, ip}：前端显示名、提交 IP（后端 lan_ip 存的
        # 是 IP，只报名永远配不上）；排除环回——_rebuild_lan 不认它。
        # lan_ips 是 PS 秒级慢操作：只读专职线程 5s 刷新的缓存，不在本
        # （Tk）线程现算（首启缓存未热时给空表，用户展开下拉前必有值）
        adapters = (self._web_status or {}).get("adapters") or []
        # daw_saved=落盘底座（表单 select 显示的）：非法值回落生效底座
        daw_saved = str(cfg.get("daw") or self.daw)
        if daw_saved not in daw_ctrl.FACTS:
            daw_saved = self.daw
        shown = (self.ccfg if daw_saved == self.daw
                 else daw_settings(cfg, daw_saved))
        return {
            "clockHint": self.clock_hint, "vjHint": self.vj_hint,
            "kbHint": self.kb_hint,
            "autoAdvance": self.watch.armed if self.watch else False,
            "autoPlay": self.cont_play, "topMost": self.top_most,
            "switchConfirm": self.switch_confirm,
            "exitCloseApps": self.exit_close_apps,
            "launchAppsOnStart": self.launch_apps,
            # daw=落盘值（回填下拉），dawActive=生效值（落盘≠生效时前端
            # 提示重启）；root/exe 顶层键随「落盘底座」走（表单 select 显示
            # 的就是它），dawSettings=两底座各自的记忆（前端切换联动换显）
            "projectsRoot": shown.get("projectsRoot", ""),
            "dawExe": shown.get("dawExe", ""),
            "daw": daw_saved,
            "dawActive": self.daw,
            "dawSettings": {d: {k: daw_settings(cfg, d)[k]
                                for k in ("projectsRoot", "dawExe")}
                            for d in daw_ctrl.FACTS},
            "obs": {"projectorMonitor": obs.get("projectorMonitor", ""),
                    "vjMute": bool(obs.get("vjMute", True)),
                    "videoRoot": obs.get("videoRoot", ""),
                    "mediaInput": obs.get("mediaInput", "")},
            "web": {k: web.get(k) for k in
                    ("enabled", "serverPort", "appPort", "taskerPort",
                     "midiIn", "mode", "ip")},
            "screens": [n for n, _r in list_screens()],
            "adapters": adapters,
            "ports": [n for _i, n in mb._in_devices()],
            "webStatus": dict(self._web_status),
        }

    def _apply_obs_web(self, vid, media, mon, mute):
        """OBS 设置热应用（后台线程执行；vid/media/mon/mute 为 None=该键
        不在场不动）。VJ 显示位置从「无」变为投屏且「启动时自动打开被控
        软件」开着时，OBS 未运行则拉起——连接循环连上后 on_connected 会
        按新位置恢复投影，此处无需（也无法，ws 未连）立即 apply_projector。"""
        ctl = self.ctl
        if ctl is None:
            return
        with self._obs_apply_lock:
            if mute is not None:
                ctl.cfg["vjMute"] = mute
            if vid is not None:      # 空串=显式清空（与 mediaInput 同口径）
                ctl.cfg["videoRoot"] = vid
            if media is not None:
                ctl.cfg["mediaInput"] = media
            mon_prev = None
            if mon is not None:
                mon_prev = ctl.cfg.get("projectorMonitor", "") or ""
                ctl.cfg["projectorMonitor"] = mon
                # 位置变化改变「允许自动拉起」的判定，即时重算生效值
                ctl.cfg["autoStart"] = self._obs_autostart_effective()
            if mute is not None and ctl.is_connected() \
                    and not ctl.apply_mute():
                self.q.put("VJ静音未生效：%s" % ctl.last_error)
            if mon is None:
                return
            if not mon:              # 显式改「无」：停投屏（关闭投影窗）
                ctl.close_projector()
                return
            fresh = False
            if mon and not mon_prev and self.launch_apps \
                    and not find_processes_by_prefix("obs64"):
                try:                 # 冷启动 OBS 并等端口就绪（≤30s）
                    fresh = ctl._ensure_obs_running()
                except Exception as e:
                    self.q.put("OBS 自动拉起失败：%s" % _err(e))
            if fresh:
                return               # 投影恢复交给 on_connected（ws 连上后）
            if not ctl.apply_projector():
                self.q.put("VJ显示位置未生效：%s" % ctl.last_error)
            # 配置了显示器=保持投影（仅显式改「无」才关，上方分支已覆盖）；
            # 此处不得 close——否则保存任意 VJ 配置都会打灭投影

    def _set_video_loop(self, note, on):
        """VJ 槽位循环开关（Tk 线程执行，/vjloop 端点经 _run_on_app 投递）：
        改运行时 cfg（琴键触发热路径即时读）→ obs 段单键合并落盘（不整段
        替换，防洗掉 host/port 等）→ 在播槽位热应用（OBS 网络动作走工作
        线程，不占主线程——与 panic/settings 同纪律）。None=成功（_run_on_app
        约定），str=错误文案。"""
        if self.ctl is None:
            return "OBS 控制器未就绪"
        loops = dict(self.ctl.cfg.get("videoLoops") or {})
        n = str(note - 71)
        if on:
            loops[n] = True
        else:
            loops.pop(n, None)
        self.ctl.cfg["videoLoops"] = loops
        try:
            cfg = _load_config()
            obs = cfg.get("obs") or {}
            obs["videoLoops"] = loops
            cfg["obs"] = obs
            _save_config(cfg)
        except (ValueError, OSError) as e:
            self.q.put("循环设置保存失败：%s" % e)
            return "保存失败：%s" % e
        cur = getattr(self.sync, "current_video", None) \
            if self.sync is not None else None
        if cur and self.sync.video_state in ("playing", "paused"):
            m = re.match(r"(\d+)", os.path.basename(str(cur)))
            if m and str(int(m.group(1))) == n:
                ctl = self.ctl

                def _apply():
                    if not ctl.set_looping(on):
                        self.q.put("循环热应用未生效：%s" % ctl.last_error)
                threading.Thread(target=_apply, daemon=True).start()
        return None

    def _settings_apply_web(self, p):
        """设置页保存：
        热应用链（端口/工程库/OBS/遥控/持久化），取值来自 payload。
        在场检测：payload 缺席的键既不动内存也不落盘——VJ 页只发 obs
        节、设置页发全量，缺席按 False/空处理曾把四端口/行为开关/遥控
        服务跨页静默清空（互 wipe）。"""
        def hint(k):               # 端口名：None=不在场，""=停用（「无」）
            if k not in p:
                return None
            v = str(p.get(k) or "")
            return "" if v == "无" else v

        vj, kb, ck = hint("vjHint"), hint("kbHint"), hint("clockHint")
        obs_in = p.get("obs")
        obs_in = obs_in if isinstance(obs_in, dict) else {}
        mon = (None if "projectorMonitor" not in obs_in else
               "" if str(obs_in.get("projectorMonitor") or "") in ("无",)
               else str(obs_in.get("projectorMonitor") or ""))
        mute = bool(obs_in["vjMute"]) if "vjMute" in obs_in else None
        vid = (str(obs_in.get("videoRoot") or "")
               if "videoRoot" in obs_in else None)
        media = (str(obs_in.get("mediaInput") or "")
                 if "mediaInput" in obs_in else None)

        def pint(src, k, d):
            try:
                v = int(str(src.get(k, d)).strip())
                assert v > 0
                return v
            except (ValueError, AssertionError):
                return d

        web_in = p.get("web")
        web_in = web_in if isinstance(web_in, dict) else {}
        web_fields = {}
        if "enabled" in web_in:
            web_fields["enabled"] = bool(web_in["enabled"])
        if "serverPort" in web_in:
            web_fields["serverPort"] = pint(web_in, "serverPort", 8765)
        if "appPort" in web_in:
            web_fields["appPort"] = pint(web_in, "appPort", 8767)
        if "taskerPort" in web_in:
            web_fields["taskerPort"] = pint(web_in, "taskerPort", 8766)
        if "midiIn" in web_in:
            web_fields["midiIn"] = str(web_in.get("midiIn") or "")
        if "mode" in web_in:
            web_fields["mode"] = web_in.get("mode")
        if "ip" in web_in:
            web_fields["ip"] = str(web_in.get("ip") or "")
        if "authToken" in web_in:
            web_fields["authToken"] = str(web_in.get("authToken") or "")
        if "hosts" in web_in:
            web_fields["hosts"] = [str(h) for h in web_in.get("hosts") or []]
        web_changed = any(self.web_cfg.get(k) != v
                          for k, v in web_fields.items())
        # 端口互异：Windows SO_REUSEADDR 允许对活动端口二次 bind（两服务
        # 随机接客、无报错），保存时就地拒绝
        srv_p = web_fields.get("serverPort", self.web_cfg.get("serverPort"))
        app_p = web_fields.get("appPort", self.web_cfg.get("appPort"))
        if srv_p and app_p and srv_p == app_p:
            return "网页端口与 APP 端口不能相同"
        self.web_cfg.update(web_fields)
        if "autoAdvance" in p or "autoPlay" in p:
            cont = (bool(p["autoPlay"]) if "autoPlay" in p
                    else self.cont_play)
            auto = (bool(p["autoAdvance"]) if "autoAdvance" in p
                    else self.auto_advance) or cont
            self.cont_play = cont
            self.auto_advance = auto
            self._toggle_auto()
        if "topMost" in p:      # 只落盘：置顶由演出模式 js_api 承担
            self.top_most = bool(p["topMost"])
        if "switchConfirm" in p:
            self.switch_confirm = bool(p["switchConfirm"])
        if "exitCloseApps" in p:
            self.exit_close_apps = bool(p["exitCloseApps"])
        if "launchAppsOnStart" in p:
            self.launch_apps = bool(p["launchAppsOnStart"])
            if self.ctl is not None:   # 总闸变化：OBS 拉起生效值即时重算
                self.ctl.cfg["autoStart"] = self._obs_autostart_effective()

        if obs_in and self.ctl is not None:
            threading.Thread(target=self._apply_obs_web,
                             args=(vid, media, mon, mute),
                             daemon=True).start()
        # 路径归属底座=payload 所选底座（合法时），否则当前生效底座；
        # 归属≠生效底座（切换保存）时只落盘不动运行态——ccfg/重扫属生效底座
        daw_new = str(p.get("daw") or "") if "daw" in p else ""
        if daw_new not in daw_ctrl.FACTS:
            daw_new = ""
        target = daw_new or self.daw
        proj = str(p.get("projectsRoot") or "") if "projectsRoot" in p else ""
        if proj:
            proj = os.path.normpath(proj)   # 手输「/」落库前归一（同 pickdir）
        rescan = (bool(proj) and target == self.daw
                  and proj != self.ccfg.get("projectsRoot"))
        if proj and target == self.daw:
            self.ccfg["projectsRoot"] = proj
        if rescan:
            self.q.put("工程库已变更，后台重扫…")
            threading.Thread(target=self._load_songs, daemon=True).start()
        ports_changed = any(v is not None and v != cur for v, cur in
                            ((vj, self.vj_hint), (kb, self.kb_hint),
                             (ck, self.clock_hint)))
        self._clock_changed = ck is not None and ck != self.clock_hint
        if vj is not None:
            self.vj_hint = vj
        if kb is not None:
            self.kb_hint = kb
        if ck is not None:
            self.clock_hint = ck
        try:
            cfg = _load_config()
            flags = {}
            if "autoAdvance" in p or "autoPlay" in p:
                flags.update({"autoAdvance": self.auto_advance,
                              "autoPlay": self.cont_play})
            if "topMost" in p:
                flags["topMost"] = self.top_most
            if "switchConfirm" in p:
                flags["switchConfirm"] = self.switch_confirm
            if "exitCloseApps" in p:
                flags["exitCloseApps"] = self.exit_close_apps
            if "launchAppsOnStart" in p:
                flags["launchAppsOnStart"] = self.launch_apps
            if vj is not None:
                flags["vjPortHint"] = vj
            if kb is not None:
                flags["kbPortHint"] = kb
            if ck is not None:
                flags["clockPortHint"] = ck
            cfg.update(flags)
            if obs_in:
                obs = cfg.get("obs") or {}
                if vid is not None:      # 空串=显式清空（与 mediaInput 同口径）
                    obs["videoRoot"] = vid
                if mon is not None:
                    obs["projectorMonitor"] = mon
                if mute is not None:
                    obs["vjMute"] = mute
                if media is not None:
                    obs["mediaInput"] = media
                # 密码落盘前升级为 DPAPI 密文（旧明文/未加密自动迁移）
                if obs.get("password") and \
                        not str(obs["password"]).startswith(_DPAPI_PREFIX):
                    obs["password"] = _dpapi_protect(str(obs["password"]))
                cfg["obs"] = obs
            # dawSettings 按底座子段落盘：root/exe 落入 target（表单所显
            # 底座）；旧平铺键收编进活跃底座子段后清除，段重构为纯子段
            old_ds = cfg.get("dawSettings") or {}
            new_ds = {}
            for d in daw_ctrl.FACTS:
                sub = old_ds.get(d)
                if not isinstance(sub, dict):
                    # 平铺键只迁给服役底座（cfg.daw 落盘值）与运行底座——
                    # 待重启态（两者不同）部分载荷保存不丢平铺段
                    sub = (daw_settings(cfg, d)
                           if d in (self.daw, str(cfg.get("daw") or self.daw))
                           else None)
                if d == target:
                    sub = dict(sub or {})
                    if proj:
                        sub["projectsRoot"] = proj
                    if "dawExe" in p and p.get("dawExe"):
                        sub["dawExe"] = os.path.normpath(str(p["dawExe"]))
                if sub:
                    new_ds[d] = sub
            cfg["dawSettings"] = new_ds
            # 底座切换：只落盘（重启生效），内存 facts/ctrl/MMC 口/素材库
            # 一律不动——热切换要重挂整条链，不值
            if daw_new:
                cfg["daw"] = daw_new
            if web_fields:
                cfg["webRemote"] = self._web_config_payload(**web_fields)
            _save_config(cfg)
        except (ValueError, OSError) as e:
            self.q.put("配置保存失败：%s" % e)
            return "配置保存失败：%s" % e
        if ports_changed:
            self._apply_ports()
        if web_changed:
            self._apply_web()
        self.q.put("设置已保存")
        return None

    def _pedal_get_web(self):
        p = self.pedal
        return {"deviceHint": self.pedal_device_hint,
                "hint": self.pedal_hint,
                "binds": self.pedal_binds, "hid": self.pedal_hid,
                "gestures": self.pedal_gestures,
                "intercept": self.pedal_intercept,
                "doubleWindow": self.pedal_double,
                "longPress": self.pedal_long,
                "remoteEnabled": self.pedal_remote_enabled,
                "connected": bool(getattr(p, "connected", False)),
                "hidActive": bool(getattr(p, "hid_active", False)),
                "deviceOnline": (p.device_online() if p is not None
                                 else False),
                "muted": bool(getattr(p, "muted", False))}

    def _pedal_devices_web(self):
        """在线键盘类设备 [(身份子串, 展示名)]（Raw Input 枚举）。已选设备
        不在场时补幽灵项（友好名查注册表，回来自动接上）。"""
        devs = pedal.pedal_list_devices_safe()
        cur = self.pedal_device_hint
        if cur and cur not in {h for h, _n in devs}:
            devs = list(devs) + [(cur, pedal.device_display(cur))]
        return {"devices": devs}

    def _pedal_learn_start_web(self, action):
        """web 学习启动：Learner 构造（逐口 midiInOpen，被占用的 BLE 口
        实测卡 3 秒）放后台线程，主线程只做瞬时状态翻转——武装时刻回主
        线程挂载（deadline 从武装起算，输入可读与「剩 X 秒」显示同步）。
        页面静音须已开（进页置位，静音已让出 MIDI 口给学习器）。"""
        if action not in dict(pedal.ACTIONS):
            return "未知动作"
        self._pedal_learn_cancel_web()
        self._pedal_learn_gen += 1
        gen = self._pedal_learn_gen
        self._pedal_learn_starting = True
        self._pedal_learn_start_err = None
        self._pedal_learn_start_t = time.time()
        threading.Thread(target=self._pedal_learn_build_web,
                         args=(gen, action), daemon=True,
                         name="pedal-learn-start").start()
        return None

    def _pedal_learn_build_web(self, gen, action):
        """后台构造学习器（winmm 开口阻塞数秒不碰 Tk），结果回主线程
        挂载或按世代作废就地关闭。"""
        try:
            learner = pedal.Learner(
                self.pedal_hint, self.pedal_device_hint, self.pedal.bridge,
                double_window=self.pedal_double,
                long_press=self.pedal_long,
                kb_ports=((self.jcfg["inHint"], kbd_auto.in_dev(self.jcfg)),
                          (self.axcfg["inHint"],
                           kbd_auto.in_dev(self.axcfg))))
        except BaseException:
            learner = None
        self.calls.put(
            lambda: self._pedal_learn_arm_web(gen, action, learner))

    def _pedal_learn_arm_web(self, gen, action, learner):
        if gen != self._pedal_learn_gen:
            if learner is not None:
                learner.close()     # 启动途中被取消：构造结果就地关闭
            return
        self._pedal_learn_starting = False
        if learner is None:
            self._pedal_learn_start_err = "学习启动失败（口被占用？）"
            return
        self._pedal_learner = (action, learner,
                               time.time() + pedal.LEARN_TIMEOUT)

    def _pedal_learn_cancel_web(self):
        """取消学习：关学习器 + 桥配置回灌（保持页面静音，离页才恢复）。
        在途的后台构造按世代一并作废（完成回调就地关闭）。"""
        self._pedal_learn_gen += 1
        self._pedal_learn_starting = False
        self._pedal_learn_start_err = None
        tup = getattr(self, "_pedal_learner", None)
        if tup is None:
            return
        tup[1].close()
        self._pedal_learner = None
        if self.pedal is not None:
            self.pedal.apply(self.pedal_hint, self.pedal_binds,
                             self.pedal_hid, self.pedal_device_hint,
                             self.pedal_intercept, self.pedal_gestures,
                             self.pedal_double, self.pedal_long)

    def _pedal_learn_poll_web(self):
        """web 学习轮询（镜像 _tick_once 学习分支）：分类/改绑迁移/应用/落盘
        在 Tk 线程一次完成，回前端展示包。"""
        tup = getattr(self, "_pedal_learner", None)
        if tup is None:
            if getattr(self, "_pedal_learn_starting", False):
                if time.time() - self._pedal_learn_start_t > 15.0:
                    # winmm 开口卡死防护（实测被占 BLE 口 3 秒超时，15 秒
                    # 必死）：作废在途构造，按启动失败收口
                    self._pedal_learn_cancel_web()
                    return {"active": False, "done": True,
                            "error": "学习启动超时（输入口无响应）"}
                return {"active": True, "starting": True}
            err = getattr(self, "_pedal_learn_start_err", None)
            if err:
                self._pedal_learn_start_err = None
                return {"active": False, "done": True, "error": err}
            return {"active": False}
        action, learner, deadline = tup
        res = learner.result()
        if res is not None:
            kind, dev, code, gesture = res
            learner.close()
            self._pedal_learner = None
            # 改绑身份=(码, 手势)：同键同手势只能属一个动作（自旧动作摘除）
            moved = []
            if kind == "hid":
                moved = [a for a, v in self.pedal_hid.items()
                         if v == code and a != action
                         and self.pedal_gestures.get(a, "single") == gesture]
                for a in moved:
                    del self.pedal_hid[a]
                    if self.pedal_binds.get(a) is None:
                        self.pedal_gestures.pop(a, None)
                self.pedal_hid[action] = code
                label = "按键「%s」%s" % (dev, pedal._GNAME.get(gesture, ""))
            else:
                self.pedal_hint = dev
                moved = [a for a, v in self.pedal_binds.items()
                         if v == code and a != action
                         and self.pedal_gestures.get(a, "single") == gesture]
                for a in moved:
                    del self.pedal_binds[a]
                    if self.pedal_hid.get(a) is None:
                        self.pedal_gestures.pop(a, None)
                self.pedal_binds[action] = code
                label = "%s 的 CC %d%s" % (dev, code,
                                           pedal._GNAME.get(gesture, ""))
            self.pedal_gestures[action] = gesture
            if moved:
                label += "（自「%s」改绑）" % "、".join(
                    dict(pedal.ACTIONS)[a] for a in moved)
            self._pedal_apply_web({})       # 应用当前态并落盘
            return {"active": False, "done": True,
                    "result": {"action": action, "gesture": gesture,
                               "label": label}}
        if time.time() > deadline:
            self._pedal_learn_cancel_web()
            return {"active": False, "done": True, "error": (
                "学习超时：所选设备没发按键（踏板连接/模式请检查）"
                if self.pedal_device_hint
                else "学习超时：未选输入设备，没等到 MIDI CC")}
        return {"active": True, "remain": max(0.0, deadline - time.time())}

    def _pedal_mute_web(self, on):
        """页面存活期静音（进页 on / 离页 off）。
        off 先收学习会话再恢复——学习者占着候选 MIDI 口，不关则踩钉哑到
        下次进页。"""
        p = self.pedal
        if p is None:
            return None
        if on:
            self._pedal_learn_cancel_web()
            p.mute()
        else:
            self._pedal_learn_cancel_web()
            p.unmute()
            p.try_open()
        return None

    def _pedal_apply_web(self, p):
        gest = p.get("gestures")
        if gest:
            if not isinstance(gest, dict) or not all(
                    k in dict(pedal.ACTIONS) and v in pedal.GESTURES
                    for k, v in gest.items()):
                return "手势载荷非法（动作或手势名不认识）"
        for field, lo, hi in (("binds", 0, 127), ("hid", 1, 0xFF)):
            vals = p.get(field)
            if vals is None:
                continue
            if not isinstance(vals, dict) or not all(
                    k in dict(pedal.ACTIONS) and isinstance(v, int)
                    and not isinstance(v, bool) and lo <= v <= hi
                    for k, v in vals.items()):
                return "%s 载荷非法" % ("绑定（CC 0-127）" if field == "binds"
                                       else "按键绑定（VK 码）")
        self.pedal_hint = str(p.get("hint") or self.pedal_hint)
        self.pedal_device_hint = str(p.get("deviceHint")
                                     if p.get("deviceHint") is not None
                                     else self.pedal_device_hint)
        # 在场检测：清空最后一个绑定要能落下去（`or` 回退旧值会让末位
        # 清除静默失败、gestures 残条目永存）；null 与缺席同义（显式
        # JSON null 不得把整表写成 None）
        if p.get("binds") is not None:
            self.pedal_binds = p["binds"]
        if p.get("hid") is not None:
            self.pedal_hid = p["hid"]
        if p.get("gestures") is not None:
            self.pedal_gestures = p["gestures"] or {}
        self.pedal_intercept = bool(p.get("intercept", self.pedal_intercept))
        try:
            # 运行期夹取（与 load_gestures 同区间）：API 直写超界值不再
            # 带着哑窗口撑到重启
            self.pedal_double = min(1.0, max(
                0.12, float(p.get("doubleWindow", self.pedal_double))))
            self.pedal_long = min(2.0, max(
                0.15, float(p.get("longPress", self.pedal_long))))
        except (TypeError, ValueError):
            pass
        self.pedal_remote_enabled = bool(p.get("remoteEnabled",
                                               self.pedal_remote_enabled))
        self.pedal.apply(self.pedal_hint, self.pedal_binds, self.pedal_hid,
                         self.pedal_device_hint, self.pedal_intercept,
                         self.pedal_gestures, self.pedal_double,
                         self.pedal_long)
        self.pedal.try_open()
        # 键名对齐启动加载器（load_binding/load_hid/load_device_cfg）与
        # deviceHint=MIDI 口名、hidDeviceHint=设备身份
        self._persist_config(
            pedal={"deviceHint": self.pedal_hint,
                   "bindings": self.pedal_binds,
                   "hidBindings": self.pedal_hid,
                   "hidDeviceHint": self.pedal_device_hint,
                   "intercept": self.pedal_intercept,
                   "gestures": self.pedal_gestures,
                   "doubleWindow": self.pedal_double,
                   "longPress": self.pedal_long,
                   "remoteEnabled": self.pedal_remote_enabled})
        return None

    def _kbd_song_key(self):
        """当前装载映射的条目 key（cur_song_path 反查 + 装载粒度：定位点
        模式下返回 pt key；未装载/游离=None）。"""
        if not self.cur_song_path:
            return None
        for k, s in self.by_key.items():
            if s["path"] == self.cur_song_path:
                return points.pt_key(k, self.cur_point_id) \
                    if self.cur_point_id is not None else k
        return None

    def _kbd_edit_target(self, song_key):
        """槽位编辑目标解析（库 key 或 pt key）→ (path, 定位点id|None,
        显示名)；条目不存活返回 None。"""
        if points.parse_pt_key(song_key):
            e = self._resolve_entry(song_key)
            if e is None:
                return None
            return e["path"], e["point"]["id"], e["display"]
        song = self.by_key.get(song_key)
        if song is None:
            return None
        return song["path"], None, song["name"]

    def _kbd_get_web(self, song_key=None):
        """/kbd 数据面：live 态 + 曲目清单（任意曲预配映射）+ 端口候选与
        命中态 + 槽位人话描述 + 最近一次切换结果。song_key 给定时返回
        该曲的映射（后台装载，首答 loading）。"""
        loaded = self._kbd_song_key()
        out = {"junoIn": self.jcfg.get("inHint", ""),
               "junoOut": self.jcfg.get("outHint", ""),
               "axIn": self.axcfg.get("inHint", ""),
               "axOut": self.axcfg.get("outHint", ""),
               "junoInDev": kbd_auto.in_dev(self.jcfg),
               "junoOutDev": self.jcfg.get("dev", 0),
               "axInDev": kbd_auto.in_dev(self.axcfg),
               "axOutDev": self.axcfg.get("dev", 0),
               "junoShift": self.juno_shift, "axShift": self.ax_shift,
               "songKey": loaded,
               # 曲目选择器同编排页口径：启用定位点的工程展开为点条目
               #（点的音色映射独立编辑，选裸条目无入口）
               "songs": [{"key": s["key"], "team": s["team"],
                          "name": s["name"]} for s in self._lib_view()],
               "lastResult": self._kb_last,
               "counts": {"juno": len(self.slots),
                          "ax": len(self.ax_slots)}}
        # 端口候选（含同名序号）与四口命中态（✔/❌ 与「第N个」）。
        # 3 秒快照缓存：键盘页/详情面板 1.5s 轮询不再每拍都在 Tk 线程
        # 枚举 winmm/BLE 端口（枚举本身快，但别给演出中的主线程加压）
        now = time.monotonic()
        snap = getattr(self, "_kbd_ports_snap", None)
        if snap and now - snap[0] < 3.0:
            out["ports"], out["portStatus"] = snap[1], snap[2]
        else:
            ins = kbd_auto.all_in_devices()
            outs = kbd_auto.all_out_devices()

            def entries(devs):
                return [{"label": l, "name": n, "dev": k} for l, n, k
                        in kbd_auto.device_entries([n for _, n in devs])]

            def side(devs, hint, dev):
                hit = kbd_auto.pick_hit(devs, hint, dev)
                if hit is None:
                    return {"hit": False}
                pos, total = kbd_auto.name_pos(devs, hit[1], hit[0])
                return {"hit": True, "name": hit[1], "pos": pos, "total": total}

            out["ports"] = {"in": entries(ins), "out": entries(outs)}
            out["portStatus"] = {
                "junoIn": side(ins, self.jcfg.get("inHint", ""),
                               kbd_auto.in_dev(self.jcfg)),
                "junoOut": side(outs, self.jcfg.get("outHint", ""),
                                self.jcfg.get("dev", 0)),
                "axIn": side(ins, self.axcfg.get("inHint", ""),
                             kbd_auto.in_dev(self.axcfg)),
                "axOut": side(outs, self.axcfg.get("outHint", ""),
                              self.axcfg.get("dev", 0))}
            self._kbd_ports_snap = (now, out["ports"], out["portStatus"])

        def with_desc(slots, fn):
            return {str(n): dict(v, desc=fn(v))
                    for n, v in (slots or {}).items()}

        def edit_song(key, name, loading, slots, ax):
            return {"key": key, "name": name, "loading": loading,
                    "slots": with_desc(slots, kbd_auto.describe_slot),
                    "axSlots": with_desc(ax, kbd_auto.describe_ax)}

        if song_key and song_key != loaded:
            tgt = self._kbd_edit_target(song_key)
            if tgt is None:
                out["editSong"] = {"key": None, "loading": False,
                                   "slots": {}, "axSlots": {}}
                return out
            path, pid, disp = tgt
            if not hasattr(self, "_kbd_song_cache"):
                self._kbd_song_cache = {}
            if song_key in self._kbd_song_cache:
                slots, ax = self._kbd_song_cache[song_key]
                out["editSong"] = edit_song(song_key, disp,
                                            False, slots, ax)
            else:               # 后台装载（网络盘 IO 不进 Tk 线程）；代际
                out["editSong"] = {"key": song_key, "name": disp,
                                   "loading": True, "slots": {}, "axSlots": {}}
                if not hasattr(self, "_kbd_cache_gen"):
                    self._kbd_cache_gen = {}
                inflight = getattr(self, "_kbd_inflight", None)
                if inflight is None:
                    inflight = self._kbd_inflight = set()
                if song_key in inflight:
                    return out  # 装载在途：不废旧代际（前端 700ms 自拉若
                                # 每次顶掉 gen，慢盘装载永不收敛）
                inflight.add(song_key)
                self._kbd_cache_gen[song_key] = \
                    self._kbd_cache_gen.get(song_key, 0) + 1
                gen = self._kbd_cache_gen[song_key]

                def run():
                    slots = kbd_auto.load_slots(path, point_id=pid)
                    ax = kbd_auto.load_slots(path, "ax", point_id=pid)
                    inflight.discard(song_key)
                    if self._kbd_cache_gen.get(song_key) != gen:
                        return    # IO 期间已保存失效：不回写旧数据
                    self._kbd_song_cache[song_key] = (slots, ax)
                threading.Thread(target=run, daemon=True).start()
            return out
        tgt = self._kbd_edit_target(loaded) if loaded else None
        out["editSong"] = edit_song(loaded, tgt[2] if tgt else None,
                                    False, self.slots, self.ax_slots)
        return out

    def _kbd_record_web(self, inst, stop=False):
        """录制（琴上捕获）：
        输入口持续跟踪最近一次 BS/PC；stop 关口保留最后捕获值。"""
        cap_old = getattr(self, "_kbd_cap", None)
        if cap_old is not None:
            try:
                cap_old["port"].close()
            except OSError:
                pass
        if stop:
            if cap_old is not None:
                cap_old["closed"] = True
            else:
                self._kbd_cap = None
            return None
        cfg = self.axcfg if inst == "ax" else self.jcfg
        cap = kbd_auto.SlotCapture()
        try:
            port = kbd_auto.RawMidiIn(cfg["inHint"], cap.feed,
                                      dev=kbd_auto.in_dev(cfg))
        except kbd_auto.PortNotFound as e:
            self._kbd_cap = None
            return str(e)
        self._kbd_cap = {"inst": inst, "cap": cap, "port": port,
                         "closed": False}
        return None

    def _kbd_record_poll_web(self):
        cap = getattr(self, "_kbd_cap", None)
        if cap is None:
            return {"active": False}
        slot = cap["cap"].slot
        if slot is not None:      # 附人话描述：录制弹层直读琴显编号
            slot = dict(slot, desc=(kbd_auto.describe_ax(slot)
                                    if cap["inst"] == "ax"
                                    else kbd_auto.describe_slot(slot)))
        return {"active": not cap["closed"], "inst": cap["inst"],
                "slot": slot}

    def _kbd_apply_web(self, p):
        if p.get("junoIn") is not None:
            self.jcfg["inHint"] = str(p["junoIn"])
        if p.get("junoOut") is not None:
            self.jcfg["outHint"] = str(p["junoOut"])
        if p.get("axIn") is not None:
            self.axcfg["inHint"] = str(p["axIn"])
        if p.get("axOut") is not None:
            self.axcfg["outHint"] = str(p["axOut"])
        # 同名端口序号（两只同型号盒消歧）：out 行落 dev、in 行落 inDev
        for cfg, ipfx, opfx in ((self.jcfg, "junoInDev", "junoOutDev"),
                                (self.axcfg, "axInDev", "axOutDev")):
            if p.get(ipfx) is not None:
                cfg["inDev"] = kbd_auto.norm_dev(p[ipfx])
            if p.get(opfx) is not None:
                cfg["dev"] = kbd_auto.norm_dev(p[opfx])
        self._persist_config(juno=self.jcfg, ax=self.axcfg)
        kb_changed = True
        self._apply_ports_kbd(kb_changed)
        return None

    def _apply_ports_kbd(self, kb_changed):
        """键盘自动化口/收发器热重建（_apply_ports 的键盘段抽离复用）。"""
        if kb_changed and self.kb_port is not None:
            try:
                self.kb_port.close()
            except Exception:
                pass
        if kb_changed:
            self.kb_port = None
            try:
                self.switcher = kbd_auto.ToneSwitcher(self.jcfg, self.q.put,
                                                      on_result=self.
                                                      _on_kb_result)
                self.ax_switcher = kbd_auto.ToneSwitcher(
                    self.axcfg, self.q.put, on_result=self._on_kb_result)
            except Exception as e:
                self.q.put("音色收发器重建失败：%s" % _err(e))
        if not self.kb_hint:
            if kb_changed:
                self.q.put("键盘自动化监听已停用")
            return
        if kb_changed or self.kb_port is None:
            try:
                self.kb_port = kbd_auto.RawMidiIn(self.kb_hint,
                                                  self._on_kb_msg)
                self.q.put("键盘自动化监听已切换：%s" % self.kb_port.name)
            except kbd_auto.PortNotFound as e:
                self.kb_err = str(e)
                self.q.put("键盘自动化停用：%s" % e)

    def _kbd_test_web(self, inst, note, song_key=None):
        """试听：向琴发送一个槽位音色。song_key 给定且非当前装载曲时，
        从该曲旁挂文件现读映射（详情面板可试任意预配曲），其余走 live。"""
        note = int(note)
        if song_key and song_key != self._kbd_song_key():
            tgt = self._kbd_edit_target(song_key)
            if tgt is None:
                return "曲目不在素材库"
            path, pid, _ = tgt
            slots = kbd_auto.load_slots(
                path, "ax" if inst == "ax" else "slots", point_id=pid)
            slot = slots.get(note)
        else:
            slots = self.ax_slots if inst == "ax" else self.slots
            slot = slots.get(note)
        if slot is None:
            return "音符 %d 未配置音色映射" % note
        sw = self.ax_switcher if inst == "ax" else self.switcher
        if sw is None:
            return "收发器未就绪"
        # 端口同步预检：输出口不在场时提交也是石沉大海，直接给明确报错
        cfg = self.axcfg if inst == "ax" else self.jcfg
        devs = kbd_auto.all_out_devices()
        hit = kbd_auto.pick_hit(devs, cfg.get("outHint", ""),
                                 cfg.get("dev", 0))
        if hit is None:
            return "未找到含「%s」的 MIDI 输出端口；现有：%s" % (
                cfg.get("outHint", ""),
                "、".join(n for _, n in devs) or "无")
        sw.submit(slot, why="web 测试")
        return None

    def _slots_save_web(self, inst, slots, song_key=None):
        """槽位保存（镜像 kbd 窗 _persist_async 语义）：song_key 给定时写
        那首歌的旁挂文件（任意曲预配），缺省写当前装载曲；仅当目标=当前
        装载曲时热更新 live 映射。"""
        if song_key:
            tgt = self._kbd_edit_target(song_key)
            if tgt is None:
                return "曲目不在素材库"
            path, pid = tgt[0], tgt[1]
        else:
            if not self.cur_song_path:
                return "当前没有打开的工程"
            path, pid = self.cur_song_path, self.cur_point_id
        key = "ax" if inst == "ax" else "slots"
        clean = {}
        for k, v in (slots or {}).items():
            if not (str(k).isdigit() and isinstance(v, dict)):
                continue
            pc = v.get("pc")
            if not isinstance(pc, int) or isinstance(pc, bool) \
                    or not 0 <= pc <= 127:
                return "PC 须为 0-127"
            slot = {"pc": pc}
            for f in ("msb", "lsb"):
                x = v.get(f)
                if x is None:
                    continue
                if not isinstance(x, int) or isinstance(x, bool) \
                        or not 0 <= x <= 127:
                    return "%s 须为 0-127" % f.upper()
                slot[f] = x
            clean[int(k)] = slot
        kbd_auto.save_slots(path, clean, key=key, point_id=pid)
        if hasattr(self, "_kbd_song_cache"):
            self._kbd_cache_gen = getattr(self, "_kbd_cache_gen", {})
            # 代际失效键与 pop 键统一：song_key=None（存当前装载曲）时按
            # 解析后的装载键作废，否则在途后台装载不被作废（陈旧映射回写）
            gen_key = song_key or self._kbd_song_key()
            self._kbd_cache_gen[gen_key] = \
                self._kbd_cache_gen.get(gen_key, 0) + 1
            self._kbd_song_cache.pop(gen_key, None)      # 脏缓存重装载
        if (path, pid) == (self.cur_song_path, self.cur_point_id):
            with self._load_lock:
                if (path, pid) == (self.cur_song_path,
                                   self.cur_point_id):  # 锁内复核：IO 期间切歌不串写
                    if inst == "ax":
                        self.ax_slots = clean
                    else:
                        self.slots = clean
        return None

    def _pl_cmd_web(self, op, key, payload=None):
        """/plcmd：编排动作（op: add/remove/up/down/clear/move/setdur/
        redict）。返回 None=成功。"""
        payload = payload or {}
        if op == "clear":
            self.pl_keys = []
            self.cur = None
            self._persist()
            return None
        if op == "setdur":        # 手动写入时长（秒数或 分:秒）
            if points.parse_pt_key(key):
                return "定位点条目时长由起止点决定，不手填"
            if key not in self.by_key:
                return "素材库里没有该工程"
            sec = cpr_meta.parse_mmss(str(payload.get("dur") or ""))
            if not sec:
                return "时长格式：秒数或 分:秒（如 213 或 3:33）"
            self.durations[key] = sec
            self.dur_src[key] = "manual"
            self._persist()
            loaded = (self.pl_keys[self.cur] if self.cur is not None
                      and self.cur < len(self.pl_keys) else None)
            if key == loaded and self.watch is not None:
                self.watch.set_duration(sec)   # watch 缺席降级态同 _switch
            self.q.put("《%s》手动时长已写为 %s"
                       % (key.split("/")[-1], cpr_meta.fmt_mmss(sec)))
            return None
        if op == "redict":        # 清手填值，重读工程内原时长（后台读文件）
            if points.parse_pt_key(key):
                return "定位点条目时长由起止点决定，无需识别"
            song = self.by_key.get(key)
            if song is None:
                return "素材库里没有该工程"
            loaded = (self.pl_keys[self.cur] if self.cur is not None
                      and self.cur < len(self.pl_keys) else None)
            path, name = song["path"], song["name"]

            def run():
                d, left = _read_span(path)
                if not d:
                    self.q.put("《%s》工程内未设循环定位条，无法识别，保留现有值"
                               % name)
                    return
                self.durations[key] = d
                self.dur_src.pop(key, None)     # 回归自动来源
                self._persist()
                if key == loaded and self.watch is not None:
                    self.watch.set_duration(d, left)
                self.q.put("已识别《%s》工程内原时长 %s"
                           % (name, cpr_meta.fmt_mmss(d)))
            threading.Thread(target=run, daemon=True).start()
            return None
        if op == "move":          # 原子重排（拖拽一次一发；from/to 0 基）
            frm, to = payload.get("from"), payload.get("to")
            if not all(isinstance(x, int) and not isinstance(x, bool)
                       for x in (frm, to)):
                return "move 参数非法"
            if not 0 <= frm < len(self.pl_keys) \
                    or not 0 <= to < len(self.pl_keys):
                return "列表刚变化，未能移动"   # 静默 200 会让前端谎报「已移动」
            # 身份校验（switch key 防线同款）：快照到执行的窗口内它端增删
            # 会使下标漂移——错移别的歌不可接受，拒绝让前端重解析重试。
            # 不带 key 视为存量调用方，维持旧行为（switch index 路径同例）
            mv_key = str(payload.get("key") or "")
            if mv_key and mv_key != self.pl_keys[frm]:
                return "列表刚变化，未能移动"
            item = self.pl_keys.pop(frm)
            self.pl_keys.insert(to, item)
            if self.cur is not None:
                if self.cur == frm:
                    self.cur = to
                elif frm < self.cur <= to:
                    self.cur -= 1
                elif to <= self.cur < frm:
                    self.cur += 1
            self._persist()
            return None
        if op == "add":
            pt = points.parse_pt_key(key)
            if pt is None:
                if key not in self.by_key:
                    return "素材库里没有该工程"
            else:       # 定位点条目（素材库已展开视图的加入路径）
                song = self.by_key.get(pt[0])
                data = self.points.get(pt[0])
                if song is None:
                    return "素材库里没有该工程"
                if not (data and data["enabled"]
                        and points.get_point(data, pt[1])):
                    return "该定位点不存在或未启用"
            if key in self.pl_keys:
                return "已在播放列表里"
            self.pl_keys.append(key)
        else:
            try:
                i = self.pl_keys.index(key)
            except ValueError:
                return "条目不在播放列表里"
            if op == "remove":
                self.pl_keys.pop(i)
                if self.cur is not None:
                    if i < self.cur:
                        self.cur -= 1
                    elif i == self.cur:
                        self.cur = (min(self.cur, len(self.pl_keys) - 1)
                                    if self.pl_keys else None)
            else:
                return "未知操作"
        self._persist()
        return None

    def _pl_export_web(self):
        """/plexport：播放列表导出为可携带 JSON（Tk 主线程弹保存对话框）。
        只搬 key+durations——工程路径/定位点/VJ 槽位都在各工程目录的
        sidecar 里随工程走，不进歌单文件。"""
        try:
            path = filedialog.asksaveasfilename(
                parent=None, title="导出歌单",
                defaultextension=".cubesetlist.json",
                initialfile=time.strftime("歌单-%Y%m%d") + ".cubesetlist.json",
                filetypes=[("Cube 歌单", "*.cubesetlist.json"),
                           ("JSON", "*.json")])
        except Exception as e:
            return {"error": _err(e)}
        if not path:
            return {"canceled": True}
        data = json.dumps({"version": 1, "playlist": self.pl_keys,
                           "durations": self.durations,
                           "durSrc": self.dur_src},
                          ensure_ascii=False, indent=2)
        try:
            # 不走 _atomic_write：它限定程序目录内，导出路径由用户任选
            pathlib.Path(path).write_text(data, encoding="utf-8")
        except (ValueError, OSError) as e:
            return {"error": "导出失败：%s" % _err(e)}
        return {"ok": True, "count": len(self.pl_keys)}

    def _pl_import_web(self):
        """/plimport：读歌单文件整体覆盖当前播放列表（Tk 主线程弹选择框）。
        先全量校验再覆盖：坏文件不动现列表；失效条目（工程不在素材库/
        定位点缺失或未启用）跳过并逐条报给前端，不静默丢歌。"""
        try:
            path = filedialog.askopenfilename(
                parent=None, title="导入歌单",
                filetypes=[("Cube 歌单", "*.cubesetlist.json"),
                           ("JSON", "*.json")])
        except Exception as e:
            return {"error": _err(e)}
        if not path:
            return {"canceled": True}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError, UnicodeDecodeError) as e:
            return {"error": "歌单文件无效：%s" % _err(e)}
        # 全量校验先行（durations/durSrc 类型也在内）：任何不合格都在
        # 动 pl_keys 之前返回——兑现「坏文件不动现列表」
        if not isinstance(data, dict) \
                or data.get("version") != 1 \
                or not isinstance(data.get("playlist"), list) \
                or not all(isinstance(k, str) and k
                           for k in data["playlist"]) \
                or not isinstance(data.get("durations", {}), dict) \
                or not isinstance(data.get("durSrc", {}), dict):
            return {"error": "歌单文件无效：不是本软件导出的歌单"}
        keys = data["playlist"]
        live, skipped = [], []
        for k in keys:
            if self._resolve_entry(k) is None:
                skipped.append("%s（%s）" % (
                    k, "定位点不存在或未启用" if points.parse_pt_key(k)
                    else "工程不在素材库"))
            else:
                live.append(k)
        if not live and keys:      # 空歌单=合法清空（与导出端对称）
            return {"error": "歌单里 %d 个条目全部失效，未导入"
                    % len(skipped)}
        self.pl_keys = live
        self.cur = None
        # 时长合并：导入键先保留本机已有值（自动识别值更新鲜），缺的从
        # 文件补；手动来源照搬（manual 语义跟着文件走）
        dur = {k: self.durations[k] for k in live if k in self.durations}
        src = {k: self.dur_src[k] for k in live if k in self.dur_src}
        for k, v in data["durations"].items():
            d = cpr_meta.safe_dur(v)
            if d > 0 and k in self.pl_keys and k not in dur:
                dur[k] = d
                if data["durSrc"].get(k) == "manual":
                    src[k] = "manual"
        self.durations, self.dur_src = dur, src
        self._persist()
        self.q.put("已导入歌单：%d 首%s" % (
            len(live), "，跳过失效条目 %d 个" % len(skipped)
            if skipped else ""))
        return {"ok": True, "loaded": len(live), "skipped": skipped}

    def _auto_play(self):
        """切换完成后自动播放：窗口标题出现≠加载完成——过早发播放键会被
        未就绪的 Cubase 吞掉（实测需再点一次）。等「正在加载」浮层消失
        （至多等 45 秒）再发播放键。"""
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.cur is None:
                return                      # 用户已手动切走
            loading = daw_ctrl.loading_windows()
            if not loading:
                break
            time.sleep(0.5)
        # 回主线程执行：本方法跑在后台线程，_transport 会摸 Tk 状态
        self.calls.put(lambda: self._transport("play"))

    def _persist(self):
        # 串行化：并发写会踩同一个临时文件（Windows 上报错或写出交错内容）
        with self._persist_lock:
            try:
                save_playlist(self.pl_keys, self.durations, self.dur_src)
            except (ValueError, OSError, RuntimeError) as e:
                self.q.put("播放列表保存失败：%s" % e)

    def _next(self):
        if self.cur is None:
            # 指针置空（位置在全部点之前）时回「当前打开工程」的首个条目
            # ——节目单头可能是另一工程（位置对位④置空语义承诺的对象）
            ws = daw_ctrl.current_project()
            nm = daw_ctrl.project_name_from_title(ws[1]) if ws else None
            if nm:
                for i, k in enumerate(self.pl_keys):
                    e = self._resolve_entry(k)
                    if e is not None and e["song_name"] == nm:
                        self._switch(i, "手动")
                        return
        self._switch(0 if self.cur is None else self.cur + 1, "手动")

    def _prev(self):
        if self.cur is None:
            self._switch(0, "手动")
        elif self.cur > 0:
            self._switch(self.cur - 1, "手动")
        else:
            self.q.put("已在播放列表开头")

    def _advance(self):
        if self.ctrl is not None and self.ctrl.busy:
            self.q.put("自动切换：正在切换中，跳过")
            return
        self._switch(self.cur + 1 if self.cur is not None else 0, "自动",
                     play_after=self.cont_play)

    def _transport(self, action):
        if self.ctrl is None:
            self.q.put("服务未就绪：启动未完成")
            return
        ts = self._transport_state()
        if transport_blocked(action, ts):
            self.q.put("%s：「%s」忽略，状态互锁" % (FOLLOW[ts], TX_NAMES.get(
                action, action)))
            return
        if action == "play" and not daw_ctrl.current_project():
            # 没有打开的工程：自动加载播放列表当前项（无指针则第一首）再播放
            if not self.pl_keys:
                self.q.put("播放列表为空，先从素材库加入歌曲再播放")
                return
            i = min(self.cur if self.cur is not None else 0,
                    len(self.pl_keys) - 1)
            if self._switch(i, "自动加载", play_after=True):
                return
        threading.Thread(target=self._transport_thread, args=(action,),
                         daemon=True).start()

    def _pedal_action(self, action):
        """踩钉触发（MIDI CC 上升沿或 HID 按键）→ 功能分发（经 calls 队列
        在主线程执行）。"""
        if action == "pause":
            # 暂停/继续一键切换（按走带态选发）：播放中→暂停、已暂停→继续；
            # 已停止发「暂停」走互锁拦截——踩钉不意外起播（起播有「开始」）
            self._transport("pause" if self._transport_state() != "paused"
                            else "resume")
        elif action in ("play", "rewind"):
            self._transport(action)
        elif action == "prev":
            self._prev()
        elif action == "next":
            self._next()
        elif action == "panic":
            self._panic()

    def pedal_remote_inject(self, payload):
        """平板转发的踩钉事件包 → 桥注入（HTTP 线程直调：桥/引擎自带锁，
        不碰 Tk）。结构校验已在端点做；这里做键码映射与时间重定基准：
        vk=scanCode（Linux 键码，主查）、kc=Android keycode（兜底）、
        dt=距包内最后事件的毫秒偏移（保包内边沿间距）、et=本包最后事件的
        APP 端单调时戳（跨包锚定——窗内间距不被到达抖动拉伸，事件序/
        弹跳/去抖按真实间距判定；不采信平板绝对时钟，PC 侧只取相对差值
        外推。超窗迟到包任何接收端无法补救，与蓝牙直连同构）。"""
        p = getattr(self, "pedal", None)
        if p is None:
            return {"ok": False, "error": "踩钉服务未启动"}
        if payload.get("hb"):
            p.bridge.note_remote_hb()
            return {"ok": True}
        events = payload.get("events") or []
        now = time.monotonic()
        t_last = p.bridge.remote_anchor(payload.get("et"), now)
        for e in events:
            vk = pedal.remote_key_to_vk(e.get("vk"), e.get("kc"))
            if vk is None:
                p.bridge.note_remote_unknown(e.get("vk"), e.get("kc"))
                continue
            dt = e.get("dt") or 0
            p.bridge.inject(vk, bool(e.get("down")), t_last - dt / 1000.0)
        return {"ok": True, "n": len(events)}

    def _thread_exc(self, a):
        """threading.excepthook：一次性后台线程的未捕获异常统一进日志
        （此前探针/装载/热应用线程死亡静默，演出中无从察觉）。"""
        try:
            self.q.put("后台线程 %s 异常：%s"
                       % (getattr(a.thread, "name", "?"),
                          _err(a.exc_value)))
        except Exception:
            pass

    def _panic(self):
        if self.ctrl is None:
            self.q.put("服务未就绪：启动未完成")
            return
        skip_keys = self._transport_state() != "playing"
        ctl = self.ctl

        def run():
            if not skip_keys:
                self.ctrl.panic()
                time.sleep(2.0)         # 全停核验：停止落地+时码尾流排空
                # （保存框/后台忙时 Cubase 停得慢，1 秒窗会误报未生效）
                if self.watch is not None \
                        and self.watch.is_transport_live():
                    self.q.put("全停核验：走带仍在播放，停止键未生效，"
                               "请手动停止 DAW")
            # 对两琴发静音序列（All Notes Off/踏板抬/全发声关）：DAW 自动化
            # 轨的 CC64 延音或挂留音不随走带停止消失（QLab Panic 同款安全网）
            for sw, cfg in ((self.switcher, self.jcfg),
                            (self.ax_switcher, self.axcfg)):
                if sw is not None:
                    sw.submit_msgs(panic_quiet_msgs(cfg), "全停静音")
            if ctl is not None:
                # 顺带熄掉 OBS 视频。绝不上主线程：stop_media=2 个 WebSocket
                # 请求（各 5s 超时）且全程持 ctl._lock（与看门狗/音符线程
                # 竞争），主线程直跑=全停按钮最坏卡 10s+（黑面+指令积压）
                ctl.stop_media()
            # 焦点收口在 daw_ctrl.panic 内（还原操作前前台）；skip_keys 路
            # 径没碰 DAW 焦点，不事后抢——本就未在播放时按全停不该把用户
            # 从当前窗口拽走

        threading.Thread(target=run, daemon=True).start()
        if self.watch is not None:  # watch 可能没起来（VJ 链构造失败而 ctrl
            self.watch.suppress_advance()   #   独立成功）——裸调用会炸队列
        if skip_keys:
            known = bool(self.clock_hint) and self.clock_port is not None
            self.q.put("全停：%s，走带键跳过，S1 空格会反向起播；"
                       "自动推进已抑制、视频已熄灭，如 DAW 仍在播放请手动"
                       "停止"
                       % ("本就未在播放" if known
                          else "时码未监听，走带状态未知"))
        else:
            self.q.put("全停：走带已停，自动推进已抑制，视频已熄灭")

    def _apply_ports(self):
        """设置页改了端口名称后热切换监听（主线程调用；找不到新端口只记
        红字并保留状态，改回或重启可恢复）。启动未完成时禁热切换——启动
        线程与主线程无锁交叉会泄漏句柄。"""
        if not self._startup_done:
            # 幂等票据：置位后由启动线程 finally（补投队列）或本方法的
            # 后续重入执行——谁消费谁清零，双消费路径天然互斥不双跑
            self._pending_ports = True
            self.q.put("端口名称已保存，启动完成后自动热切换")
            return
        if self.ctl is not None and self.sync is not None:
            try:
                if self.port is not None:
                    self.port.close()
            except Exception:
                pass
            self.port = None
            if not self.vj_hint:
                self.q.put("VJ 监听已停用")
            else:
                try:
                    self.port = mb.MidiIn(
                        self.vj_hint,
                        mb.note_handler(self.ctl, self.sync,
                                        report=self.q.put))
                    self.q.put("VJ 监听已切换：%s" % self.port.name)
                except SystemExit as e:
                    self.q.put("VJ 监听未启动：%s" % e)
        else:
            self.q.put("VJ 服务未就绪：端口名称已保存，重启程序后生效")
        # 时码监听热切换（停用/换口=走带基线复位：三态回「未播放」，防旧口
        # 断流卡「已暂停」误导互锁与全停；视频跟随同步复位不误报暂停）。
        # 仅时码口实际变化才动基线——改 VJ/键盘口不动时码，进度不清零
        clock_changed = getattr(self, "_clock_changed", True)
        if clock_changed and self.clock_port is not None:
            try:
                self.clock_port.close()
            except Exception:
                pass
        if clock_changed:
            self.clock_port = None
            if self.sync is not None:
                self.sync.reset()
            if self.watch is not None:
                self.watch.reset()
            if self.mtc is not None:
                self.mtc.reset()
        if self.ctl is not None and self.sync is not None \
                and self.watch is not None:
            if not self.clock_hint:
                if clock_changed:
                    if self.mtc is not None:
                        self.mtc = None
                    self.q.put("时码监听已停用：未设时码端口，走带三态/已播/"
                               "自动推进与停止键不可用")
            elif clock_changed:
                self.mtc = self.mtc or mtc.Decoder(
                    on_qf=self.sync.on_clock,
                    on_position=self.watch.on_position,
                    on_full=self.watch.on_full_position)
                try:
                    self.clock_port = mb.MidiIn(
                        self.clock_hint, lambda n, v: None,
                        on_mtc=self.mtc.feed, on_f8=self.mtc.note_f8,
                        on_sysex=self.mtc.full_frame)
                    self.q.put("时码监听已切换：%s" % self.clock_port.name)
                except SystemExit as e:
                    self.mtc = None
                    self.q.put("时码监听未启动：%s" % e)
        else:
            self.q.put("时码服务未就绪：端口名称已保存，重启程序后生效")
        # MMC 输出口跟随时码口热切换（同一虚拟口喂 GOTO；停用/换口即重开）
        if clock_changed:
            if self.mmc_out is not None:
                mb.close_out(self.mmc_out)
                self.mmc_out = None
            if self.clock_hint:
                self._open_mmc_out()
        if self.kb_port is not None:
            try:
                self.kb_port.close()
            except Exception:
                pass
        self.kb_port = None
        if not self.kb_hint:
            self.q.put("键盘自动化监听已停用")
        else:
            try:
                self.kb_port = kbd_auto.RawMidiIn(self.kb_hint, self._on_kb_msg)
                self.q.put("键盘自动化监听已切换：%s" % self.kb_port.name)
            except kbd_auto.PortNotFound as e:
                self.kb_err = str(e)
                self.q.put("键盘自动化停用：%s" % e)

    def _heal_link_ports(self):
        """联动端口自愈（_tick 主线程执行，与 _apply_ports 同线程天然串行）：
        只补开「已配置但当前未打开」的监听口（VJ 触发/时码/MMC 输出/键盘/
        翻谱），已在场的口绝不动（不做扰动性关开）。失败静默等下一轮
        （10s，monotonic 计时防墙钟回退暂停）。热拔掉线（MIM_CLOSE→dead
        旗标）一并重开——设备拔出/loopMIDI 中途被杀重启（虚拟口删除=
        设备移除，同一通知路径），「已掉线」并入「未打开」重开，时码
        断流不再需要重启本软件。
        失败静默等下一轮，成功才记日志；单轮 2s 预算：winmm 专线被毒化时
        枚举/开口各吃 1-3s 上限，超预算弃轮防主线程停摆（对抗复审 P2）。"""
        if not self._startup_done:
            return
        _t0 = time.monotonic()
        try:
            # VJ 触发口（热拔掉线先收尾并入「未打开」重开）
            if self.port is not None and self.port.dead:
                self.q.put("VJ 触发口已掉线，自动重开")
                try:
                    self.port.close()
                except Exception:
                    pass
                self.port = None
            if (self.ctl is not None and self.sync is not None
                    and self.vj_hint and self.port is None):
                try:
                    self.port = mb.MidiIn(
                        self.vj_hint,
                        mb.note_handler(self.ctl, self.sync,
                                        report=self.q.put))
                    self.q.put("VJ 监听已自动接上：%s" % self.port.name)
                except SystemExit:
                    pass
            if time.monotonic() - _t0 > 2.0:
                return
            # 时码口（热拔掉线先收尾并入「未打开」重开；启动失败路径
            #   会清掉 mtc 解码器：先重建再开口）
            if self.clock_port is not None and self.clock_port.dead:
                self.q.put("时码口已掉线，自动重开（走带基线复位）")
                try:
                    self.clock_port.close()
                except Exception:
                    pass
                self.clock_port = None
                self.sync.reset()
                self.watch.reset()
                if self.mtc is not None:    # 不变量：clock_port 非 None⇒三件套在，
                    self.mtc.reset()        #   守护是防未来新路径破坏（红队 P2-1）
            if (self.ctl is not None and self.sync is not None
                    and self.watch is not None and self.clock_hint
                    and self.clock_port is None):
                try:
                    if self.mtc is None:
                        self.mtc = mtc.Decoder(
                            on_qf=self.sync.on_clock,
                            on_position=self.watch.on_position,
                            on_full=self.watch.on_full_position)
                    self.clock_port = mb.MidiIn(
                        self.clock_hint, lambda n, v: None,
                        on_mtc=self.mtc.feed, on_f8=self.mtc.note_f8,
                        on_sysex=self.mtc.full_frame)
                    self.q.put("时码监听已自动接上：%s"
                               % self.clock_port.name)
                except SystemExit:
                    self.clock_port = None
                    self.mtc = None
            if time.monotonic() - _t0 > 2.0:
                return
            # MMC 输出口（成功时 _open_mmc_out 自带一条日志）
            if (self.facts.get("name") == "cubase" and self.clock_hint
                    and self.mmc_out is None):
                self._open_mmc_out(quiet=True)
            # 键盘自动化口（switcher 门：收发器没起来时键盘口开了也没人
            # 消费——理论边界，ToneSwitcher 构造几乎不失败）
            if self.kb_port is not None and self.kb_port.dead:
                self.q.put("键盘自动化口已掉线，自动重开")
                try:
                    self.kb_port.close()
                except Exception:
                    pass
                self.kb_port = None
                self.kb_err = ""
            if (self.kb_hint and self.kb_port is None
                    and self.switcher is not None):
                try:
                    self.kb_port = kbd_auto.RawMidiIn(self.kb_hint,
                                                      self._on_kb_msg)
                    self.kb_err = ""
                    self.q.put("键盘自动化监听已自动接上：%s"
                               % self.kb_port.name)
                except kbd_auto.PortNotFound:
                    pass
            # 翻谱 MIDI 口（WebRemote 内部口，同族受害场景）
            if self.web is not None:
                self.web.try_reopen_midi()
        except Exception as e:
            self.q.put("联动端口自愈异常：%s" % _err(e))

    def _regain_focus(self):
        """把键盘焦点收回本程序（工作线程调用）。置顶只保视觉 Z 序，
        不保焦点——外部事件（单实例转交等）把 Cubase 顶到前台后，若不
        收回，用户接着打字会打进 Cubase（空格=停走带）。走带/全停/切歌
        已由 daw_ctrl 按「还原操作前前台」收口，本方法只管这类外部激活。
        Tk 根全程隐藏、直接聚焦必失败——枚举本进程可见顶层窗（=pywebview
        主窗）代之。"""
        try:
            hwnd = self._visible_own_window()
            if hwnd:
                daw_ctrl.focus(hwnd, tries=2)
        except Exception:
            pass

    def _visible_own_window(self):
        """本进程当前可见顶层窗中面积最大者（WebUI 模式=pywebview 主窗；
        无则 0）。取最大而非 z 序首个：/pickdir 原生对话框等同进程可见窗
        且更顶，hits[0] 会把「收回主窗」变成「聚焦对话框」（对抗复审 P2）。
        纯 Win32 枚举无 Tk 调用，工作线程安全；隐藏的 Tk 根被
        IsWindowVisible 自然滤掉。"""
        import ctypes.wintypes as wt

        class _rect(ctypes.Structure):
            _fields_ = [("l", wt.LONG), ("t", wt.LONG),
                        ("r", wt.LONG), ("b", wt.LONG)]

        u32 = ctypes.windll.user32
        best, best_area = 0, 0

        @daw_ctrl._ctypes_cb
        def _cb(h, _l):
            nonlocal best, best_area
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value != os.getpid() or not u32.IsWindowVisible(h):
                return True
            rc = _rect()
            if u32.GetWindowRect(h, ctypes.byref(rc)):
                area = max(0, rc.r - rc.l) * max(0, rc.b - rc.t)
                if area > best_area:
                    best, best_area = h, area
            return True

        u32.EnumWindows(_cb, 0)
        return best

    def _transport_thread(self, action):
        # 焦点收口在 daw_ctrl.transport 内（还原操作前前台，不再事后
        # 收回本软件——踩钉/手机触发时用户原窗口可能不是本软件）
        if action == "play" and self._point_play():
            return
        if action == "rewind" and self._point_rewind():
            return
        if self.ctrl.transport(action, live=self._transport_state() == "playing") \
                and action == "rewind" \
                and self.watch is not None:
            self.watch.reset()      # 回零=停在零点：纪元复位让显示立即归零，
                                    # 不等下一条时码（播放/暂停无需复位——位置自证）

    def _cur_point_entry(self):
        """指针条目解析：属定位点模式且正是已装载工程时返回 entry，否则
        None（整曲歌/工程未对位/无指针/库缺项）——点感知走带的共用闸。"""
        i = self.cur
        if i is None or not (0 <= i < len(self.pl_keys)):
            return None
        entry = self._resolve_entry(self.pl_keys[i])
        if entry is None or entry["point"] is None \
                or self.cur_song_path != entry["path"]:
            return None
        return entry

    def _point_play(self):
        """定位点歌曲的「开始」：停→GOTO 段起点→原地起播。通用 play 的
        键序是停→回零→播（mr_ctrl.SEQ），回零把光标打回工程头——整曲模式
        即「从头播」，定位点模式必须改走段起点。按钮/_auto_play/_goto_worker
        的 play_after 三路都汇到 _transport_thread，在此收口一次全适配。
        非定位点情形回 False 走通用路径；mmc 未就绪时 GOTO 告警降级为
        原地起播（不回通用路径——回零到工程头更错）。"""
        entry = self._cur_point_entry()
        if entry is None:
            return False
        if self._transport_state() != "stopped" \
                and self.ctrl.transport("stop") is False:
            return True     # 停止未送达：不起播（GOTO 在播放中被 DAW 忽略）；
                            # 已停态不发停止——S1 停止键是开关，停了再停反起播
        pre = self.mtc.position() if self.mtc is not None else None
        if pre is None or abs(pre - entry["point"]["start"]) > 0.1:
            # 已停/定位会补发全帧时码：pre 即停泊位——已在段起点（切换/
            # 自动播放链路刚 GOTO 过）就不再定位，免同位 GOTO 的「未响应」
            # 误报（位置不变≠链路断）
            self._goto_send(entry["point"]["start"], entry["display"])
            time.sleep(0.15)   # GOTO 落地缓冲：START 抢在 DAW 处理 MMC 前
                               # 会从旧光标位起播，定位白做（宁稳勿快）
        self.ctrl.transport("resume")
        return True

    def _point_rewind(self):
        """定位点歌曲的「回零」：回段起点（GOTO）而非工程头。通用回零的
        RTZ 落工程头后，位置对位会把位置判成段前空档→指针置空（NOW 退化
        裸工程名、进度冻结、后续「继续」从段前素材起播）。按钮走 _transport，
        踩钉/网页/APP 的回零同径在此收口。"""
        entry = self._cur_point_entry()
        if entry is None:
            return False
        if self._transport_state() != "stopped" \
                and self.ctrl.transport("stop") is False:
            return True     # 停止未送达：不定位（播放中 GOTO 被 DAW 忽略）
        pre = self.mtc.position() if self.mtc is not None else None
        if pre is None or abs(pre - entry["point"]["start"]) > 0.1:
            self._goto_send(entry["point"]["start"], entry["display"])
        if self.watch is not None:
            self.watch.reset()  # 显示立即归段零（同通用回零的纪元复位；
                                # 段坐标 duration/left 原样保留）
        return True

    def _toggle_auto(self):
        # 生效 self.auto_advance（调用方先写属性再调用）：watch 建立时按它
        # 恢复；踩钉可在 watch 存在前翻转
        if self.watch is not None:
            self.watch.set_armed(self.auto_advance)
            self.q.put("自动切换工程%s" % ("已开启，播完自动切下一首"
                                        if self.auto_advance else "已关闭"))
        self._persist_config(autoAdvance=bool(self.auto_advance))

    def _persist_config(self, **kv):
        try:
            cfg = _load_config()
            cfg.update(kv)
            _save_config(cfg)
        except (ValueError, OSError, RuntimeError) as e:
            self.q.put("配置保存失败：%s" % e)

    # ---- 移动端遥控 ----

    def _web_config_payload(self, **fields):
        """webRemote 持久化载荷：设备表永远取注册表现值（网页认领随时在改，
        不能被设置页的旧快照覆盖），其余字段取调用方给的最新值。"""
        cfg = dict(self.web_cfg)
        cfg.update(fields)
        if self.web is not None:
            cfg["devices"] = self.web.registry.snapshot()
        return cfg

    def _persist_web_remote(self):
        # 注册表 on_change 经 calls 队列到主线程；设置页保存也走这里合流
        self._persist_config(webRemote=self._web_config_payload())

    def _apply_web(self):
        """设置页改了遥控配置后热应用（热点 PS 调用要数秒，后台线程跑）。"""
        if self.web is None:
            self.q.put("移动端遥控模块未就绪：重启程序后生效")
            return
        wc = self.web_cfg

        def run():
            try:
                self.web.apply(enabled=bool(wc.get("enabled")),
                               serverPort=wc.get("serverPort"),
                               appPort=wc.get("appPort"),
                               taskerPort=wc.get("taskerPort"),
                               midiIn=str(wc.get("midiIn") or ""),
                               mode=wc.get("mode"),
                               ip=str(wc.get("ip") or ""),
                               authToken=str(wc.get("authToken") or ""),
                               hosts=tuple(wc.get("hosts") or ()))
            except Exception as e:
                self.q.put("移动端遥控应用失败：%s" % _err(e))

        threading.Thread(target=run, daemon=True).start()

    def _web_shutdown(self):
        try:
            if self.web is not None:
                self.web.shutdown()
        except Exception:
            pass

    # ---- 主线程轮询：日志 + 跨线程调用 ----

    def _on_ui_error(self, exc, val, _tb):
        """Tk 回调异常统一进日志框：noconsole 下无 stderr，不接就"点了没反应"。"""
        self.q.put("界面异常：%s：%s" % (exc.__name__, val))

    def _log_direct(self, msg):
        """异常兜底直投：不经 self.q（排水链自身故障时再投 q 会自馈），
        内存环形与文件日志双写。"""
        try:
            self.log_ring.append((time.time(), msg, _log_lv(msg)))
            if _FILE_LOG is not None:
                _FILE_LOG.put(msg, _log_lv(msg))
        except Exception:
            pass

    def _run_call(self, fn):
        """执行单个跨线程回调：tag 供看门狗取证；按 BaseException 捕——
        MidiIn 端口降级抛 SystemExit，不在 Exception 之列，py3.14 实测漏
        过去会冲出 mainloop 终止进程（report_callback_exception 都不经过
        =闪退），而不是旧注释误记的「仅排水链死亡」。"""
        self._hb_tag = getattr(fn, "__name__", "<lambda>")
        try:
            fn()
        except BaseException as e:
            self._log_direct("动作执行异常（已恢复）：%s: %s"
                             % (type(e).__name__, _err(e)))
        self._hb_tag = ""

    def _drain_calls(self):
        """跨线程 GUI 调用队列的快速排空（50ms 独立循环）。踩钉动作从设备
        桥线程入队到执行最坏只等一个排空周期；原来搭在 400ms 状态轮询车上，
        演出中踩一脚最坏等 400ms 才起效。紧急队列（全停）优先——停摆积压
        恢复后全停必须先于陈旧普通指令；普通批次先合流（幂等回调恢复后
        成批重复，只执行最后一次）。"""
        self._hb = time.monotonic()     # 喂看门狗心跳
        self._stack_sent.feed()         # 续 C 级栈哨兵计时窗
        while self.calls_urgent:
            self._run_call(self.calls_urgent.popleft())
        batch = []
        while True:
            try:
                batch.append(self.calls.get_nowait())
            except queue.Empty:
                break
        for fn in stallguard.coalesce(batch, {"_persist_web_remote"}):
            self._run_call(fn)
        self.root.after(50, self._drain_calls)

    def urgent(self, fn):
        """紧急 GUI 调用（全停安全阀）：插队到普通指令前执行。"""
        self.calls_urgent.append(fn)

    def _tick(self):
        self._hb = time.monotonic()     # 喂看门狗心跳
        self._stack_sent.feed()         # 续 C 级栈哨兵计时窗
        self._hb_tag = "_tick_body"
        try:
            self._tick_body()
        except BaseException as e:
            # 轮询循环绝不允许死（死一次=状态/队列全部冻结）；按
            # BaseException 捕：SystemExit 在 py3.14 实测会冲出 mainloop
            # 终止进程（report_callback_exception 都不经过），必须就地吞掉
            self._log_direct("状态刷新异常（已自动恢复）：%s" % _err(e))
        self._hb_tag = ""
        self.root.after(400, self._tick)

    def _tick_body(self):
        if (self._pending_switch is not None and self.ctrl is not None
                and not self.ctrl.busy):
            k = self._pending_switch
            self._pending_switch = None
            if k in self.pl_keys:      # 排队期间被它端删掉则弃
                self._switch(self.pl_keys.index(k), "排队")
                                            # busy 期间用户点的歌，完成后接续执行
        self._check_goto_ack()
        ped = self.pedal
        if (ped is not None and not ped.muted
                and ((ped.hint and not ped.connected)
                     or (ped.hid_binds and ped.device_hint
                         and not ped.bridge.running))   # HID 泵死了也重拉，
                and time.monotonic() > self._pedal_retry):  # 此前只盯 MIDI 口
            self._pedal_retry = time.monotonic() + 10
            if ped.try_open():
                self.q.put("CC 踩钉已连接：%s" % ped.name)
        # 联动端口自愈（同款 10s 节奏，monotonic 计时）：启动早于 loopMIDI/
        # 端口迟到的场景，配置过的口（VJ/时码/MMC/键盘/翻谱）后台补开，
        # 不再要求重启本软件
        if time.monotonic() > self._link_heal_at:
            self._link_heal_at = time.monotonic() + 10
            self._heal_link_ports()
        self._tick_banner()
        # 日志排水（纯数据）：内存环形喂新前端日志页，异步线程落文件日志；
        # 级别由产生端定级（_log_lv），消费端不重判
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            self.log_ring.append((time.time(), msg, _log_lv(msg)))
            if _FILE_LOG is not None:
                _FILE_LOG.put(msg, _log_lv(msg))   # 文件日志（异步线程）

    def _transport_state(self):
        """走带三态：playing=时码 QF 活跃；paused=收过时码但已断流；
        stopped=从未收到（未发时码工程无从判断，按未播放）。"""
        w = self.watch
        if w is None or self.ctrl is None:
            return "stopped"
        if w.is_transport_live():
            return "playing"
        return "paused" if w.ever_live() else "stopped"

    def _adopt(self, name):
        """手动跟随：Cubase 里的工程被人工变更（横幅发现标题对不上指针）
        → 按标题对位播放列表。命中=采纳：指针立即就位，时长/音色映射 IO
        后台装载（网络盘工程库时主线程 400ms 轮询链上做整文件读=无界卡顿，
        审计 D 项）；未命中=游离：清指针回第一首兜底、清映射、计时器清零
        （时长 0=未知=永不推进，防旧时长误切）。只改状态，不碰窗口不抢
        焦点；播放列表未载入不动。"""
        if not self.pl_keys:
            return
        cur_e = (self._resolve_entry(self.pl_keys[self.cur])
                 if self.cur is not None and self.cur < len(self.pl_keys)
                 else None)
        if cur_e is not None and cur_e["song_name"] == name:
            return          # 当前条目（含定位点）即该工程：指针已对，不重采
        for i, k in enumerate(self.pl_keys):
            e = self._resolve_entry(k)
            if e is not None and e["song_name"] == name:
                self.cur = i
                with self._load_lock:   # 清空+代际递增原子化（同 _switch）
                    self.slots = {}
                    self.ax_slots = {}
                    self.cur_point_id = None
                    self._load_gen = getattr(self, "_load_gen", 0) + 1
                    gen = self._load_gen
                if e["point"] is not None and self.watch is not None \
                        and self.cur_song_path != e["path"]:
                    self.watch.reset()               # 跨工程采纳：旧工程绝对
                    self.watch.set_duration(0, 0.0)  # 秒立即作废——装载 IO 窗
                    #   口（网络盘秒级）内不得拿旧位置参与新工程对位/显示
                self._adopted_load(k, gen)
                self.q.put("已跟随手动打开：《%s》" % name)
                return
        self.cur = None
        self.cur_song_path = None
        self.cur_point_id = None
        self.slots = {}
        self.ax_slots = {}
        if self.watch is not None:
            self.watch.reset()
            self.watch.set_duration(0, 0.0)   # 游离歌坐标未知：左偏移一并清
        self.q.put("《%s》不在播放列表：不自动推进，NEXT 置空" % name)

    def _adopted_load(self, key, gen):
        """_adopt 的装载半程（后台线程）：读时长+音色映射（整文件 IO）。
        gen 由调用方在 _load_lock 内递增后传入；写回时锁内复核——「校验后
        写入」的 TOCTOU 窗（装载完成与切歌清空的交错=跨歌错装）靠同锁
        原子化关闭。写状态均为无锁属性/GIL 原子（_switch_done 同款）。"""
        e = self._resolve_entry(key)
        if e is None:
            return
        path = e["path"]
        pt = e["point"]

        def run():
            if pt is not None:          # 定位点条目：段区间，不动 durations
                d, left = pt["end"] - pt["start"], pt["start"]
            else:
                d_file, left = _read_span(path)
                d = self.durations.get(key) or d_file or 0.0
            pid = pt["id"] if pt is not None else None
            slots = kbd_auto.load_slots(path, point_id=pid)
            ax = kbd_auto.load_slots(path, "ax", point_id=pid)
            with self._load_lock:
                if gen != self._load_gen:
                    return              # 已被更新的一次装载/切歌取代
                if pt is None:
                    self.durations[key] = d
                if self.watch is not None:
                    self.watch.reset()  # 纪元闭合同 _switch_done：装载半程的
                    #   IO 窗口里旧工程可能还在播（尾流 QF 重灌位置），提交
                    #   时刻复位兜住，旧位置不得穿越新歌坐标系
                    if pt is None:
                        self.watch.set_duration(d, left)   # 不复位：位置自证
                    else:
                        # 定位点段坐标不在此装载：手动打开的工程停在任意
                        # 光标处，首个点条目未必含真实位置——主线程
                        # _repick_by_position 已按位置选段，此处再写会与它
                        # 竞态互相覆写（时长 0=推进暂闭，对位 tick 随后填）
                        self.watch.set_duration(0, 0.0)
                self.slots = slots
                self.ax_slots = ax
                self.cur_song_path = path
                self.cur_point_id = pid
        threading.Thread(target=run, daemon=True).start()

    def _repoint(self, j, e, why):
        """指针改指同工程点条目 j（时长缓存复用；音色映射按点粒度重装载
        ——sidecar 是小文件，主线程同步读；set_duration 不复位——时码绝对
        值自证）。日志只在走带停止时记（DAW 循环播放跨界会按循环周期刷屏）。"""
        self.cur = j
        pid = e["point"]["id"]
        self.watch.set_duration(e["point"]["end"] - e["point"]["start"],
                                e["point"]["start"])
        with self._load_lock:       # 代际递增作废在途装载，防旧结果覆写点段
            self._load_gen = getattr(self, "_load_gen", 0) + 1
            gen = self._load_gen
        slots = kbd_auto.load_slots(e["path"], point_id=pid)
        ax = kbd_auto.load_slots(e["path"], "ax", point_id=pid)
        with self._load_lock:
            if gen != self._load_gen:
                return          # 指针/段坐标已生效，映射由后来者装载
            self.slots = slots
            self.ax_slots = ax
            self.cur_song_path = e["path"]
            self.cur_point_id = pid
        if not self.watch.is_transport_live():
            self.q.put("%s：《%s》" % (why, e["display"]))

    def _repick_by_position(self, name, pos):
        """定位点指针随 DAW 位置对位（播放与手动拖光标同一路径，400ms
        tick；GOTO 在途（_goto_ack 1.2s 窗）不动——位置即将跳变，程序
        跳转是权威）：
        ①当前点条目区间含位置→不动（重复区间设计的稳定锚，不振荡）；
        ②否则指到同工程「含位置的播放列表序首个」点条目；
        ③位置在段间空档→指到同工程点条目中「起点≤位置」的播放列表序
          最后一个（=节目序最靠后的已进入段，NEXT=首个未进入的条目）；
          空档内段坐标清零——空档起播不属于任何段，推进暂闭（防「位置
          已过段尾」被误判唱完而杀掉手动播放），回段内由①/②恢复；
        ④位置在本工程全部点起点之前→指针置空（NOW 裸工程名、NEXT 空，
          「下一首」回当前打开工程的首个条目），位置进入任一段后自动恢复。
        指针在他曲条目上不动（归 _adopt 按 DAW 标题对位）。返回是否
        移动了指针。"""
        if pos is None or self.watch is None or not self.pl_keys \
                or self._goto_ack is not None:
            return False
        w = self.watch
        if w.armed and not w.fired() and w.duration > 0 \
                and w.played() >= w.duration \
                and (w.is_transport_live() or w.since_live() <= 0.5):
            return False    # 播过段尾且 QF 刚断（≤0.5s 缓冲）：停止→确认→
            #   推进协议在途（含 3 次停止重试窗），段界归属自动推进——此
            #   刻换段坐标会把「停止→已播 0 秒不切换」打成 tick/poll 相位
            #   赌局的随机死锁。缓冲盖住 live 翻假到 _goto_ack 置位的交接
            #   缝（poll 100ms + calls 50ms）；停止拖拽 since_live 大，不拦
        cur_e = (self._resolve_entry(self.pl_keys[self.cur])
                 if self.cur is not None and 0 <= self.cur < len(self.pl_keys)
                 else None)
        if cur_e is not None and cur_e["song_name"] != name:
            return False        # 指针在他曲：标题对位（_adopt）的职责
        pt = cur_e["point"] if cur_e is not None else None
        if pt is not None and points.contains(pt, pos):
            if w.duration <= 0:     # 空档清零后回到段内：静默恢复段坐标
                w.set_duration(pt["end"] - pt["start"], pt["start"])
            return False                        # ①当前段含位置：指针已对
        same = []                               # 同工程点条目（播放列表序）
        for j, k in enumerate(self.pl_keys):
            e = self._resolve_entry(k)
            if e is not None and e["song_name"] == name \
                    and e["point"] is not None:
                same.append((j, e))
        if not same:
            return False    # 本工程无点条目（未启用/裸条目模式）：不重选
        for j, e in same:                       # ②含位置的首个
            if points.contains(e["point"], pos):
                if j != self.cur:
                    self._repoint(j, e, "按位置对位")
                    return True
                return False
        best = None                             # ③空档：起点≤位置的最后一个
        for j, e in same:
            if e["point"]["start"] - points.FRAME_TOL <= pos:
                best = (j, e)
        if best is not None:
            j, e = best
            if j != self.cur:
                self.cur = j
                w.set_duration(0, 0.0)          # 空档：推进暂闭（注③）
                if not w.is_transport_live():
                    self.q.put("空档对位：《%s》" % e["display"])
                return True
            if w.duration > 0:                  # 已在最后进入段但位置在空档
                w.set_duration(0, 0.0)          #   ——同样暂闭推进
            return False
        if self.cur is not None:                # ④位置在全部起点之前
            self.cur = None
            w.set_duration(0, 0.0)
            if not w.is_transport_live():
                self.q.put("位置在《%s》全部定位点之前：指针置空" % name)
            return True
        return False

    @staticmethod
    def _now_entry_display(cur_e, name, pos):
        """NOW 显示名（与 NEXT/播放列表同一套名字）：指针条目属当前工程
        且（定位点）段区间含位置 → 条目显示名「工程 · 点名」；裸条目直接
        认；空档（位置不在任何段内=没有定位点在播）、无时码（指针只是按
        标题猜的，不当事实显）与指针不属本工程 → None（调用方回落裸工程
        名，不说谎）。"""
        if cur_e and cur_e["song_name"] == name:
            p = cur_e["point"]
            if p is None or (pos is not None and points.contains(p, pos)):
                return cur_e["display"]
        return None

    def _tick_banner(self):
        """对位引擎与 /state 快照源（400ms 随 tick）：NOW=实际打开的工程
        （外部手动改工程自动跟随对位播放列表，见 _adopt）；产出快照的
        hasProj/projName 与 nextIdx，进度/剩余等由 build_snapshot 从业务态
        独立计算。"""
        has_proj = False
        proj_name = None
        if self.ctrl is not None:
            if self.ctrl.busy:
                has_proj = True      # 切歌中=过渡态（busy 标志本身上快照）
            else:
                ws = daw_ctrl.current_project()
                has_proj = bool(ws)
                if ws:
                    name = daw_ctrl.project_name_from_title(ws[1])

                    def _want():
                        return (self._resolve_entry(self.pl_keys[self.cur])
                                if self.cur is not None
                                and self.cur < len(self.pl_keys) else None)

                    cur_e = _want()
                    want = cur_e["song_name"] if cur_e else None
                    # 手动跟随：标题变化或与指针漂移（人工在 DAW 开了别的
                    # 歌、播放列表被编辑）→ 按标题对位播放列表（_adopt），
                    # 采纳后 want 重取；busy 走上面的过渡分支，不与切歌
                    # 流程抢跑。
                    if name != self._adopted_name or (want is not None
                                                      and want != name):
                        self._adopted_name = name
                        if want is None or want != name:
                            self._adopt(name)
                            cur_e = _want()
                            want = cur_e["song_name"] if cur_e else None
                    # 定位点随位置对位：DAW 位置落在哪个点的区间里，指针
                    # /NEXT 就按哪个点算（含位置稳定锚+空档跟随+全部起点
                    # 之前置空，见 _repick_by_position 注）
                    pos = self.watch.position() \
                        if self.watch is not None else None
                    if pos is not None and self._repick_by_position(name, pos):
                        cur_e = _want()
                        want = cur_e["song_name"] if cur_e else None
                    # 下一首目标（PC 按钮/移动/网页三面单一来源）：指针置空
                    # （位置对位④）时回当前打开工程的首个条目——与 _next 同
                    # 口径；找不到（游离）兜节目单头，与 _next 的回落一致
                    self._next_idx = None
                    if self.pl_keys:
                        if self.cur is None:
                            self._next_idx = 0
                            for i, k in enumerate(self.pl_keys):
                                e2 = self._resolve_entry(k)
                                if e2 is not None \
                                        and e2["song_name"] == name:
                                    self._next_idx = i
                                    break
                        elif self.cur + 1 < len(self.pl_keys):
                            self._next_idx = self.cur + 1
                    # NOW 与 NEXT/播放列表同一套名字：指针条目属当前工程且
                    # 段含位置（裸条目/无时码信指针）→「工程 · 点名」；空档
                    # =没有定位点在播、指针不属本工程 → 裸工程名（不说谎）
                    disp = self._now_entry_display(cur_e, name, pos)
                    proj_name = disp if disp is not None else name
        # DAW 进程在场（供 /state 的 dawAlive：崩溃/误关后前端区分
        # 「已暂停」与「DAW 失联」）。枚举有成本，3s 节流（400ms 快照拍
        # 不每次都枚举）
        now_m = time.monotonic()
        if now_m - getattr(self, "_daw_alive_t", 0.0) > 3.0:
            self._daw_alive_t = now_m
            try:
                self._daw_alive = bool(
                    find_processes_by_prefix(self.facts["proc_prefix"]))
            except Exception:
                pass
        self._web_snap = web_remote.build_snapshot(self, has_proj, proj_name)

_MUTEX = None


def _acquire_single_instance():
    """命名互斥体防双开：双开会双份发走带键/双份监听 MIDI，行为错乱。
    句柄存全局防 GC（句柄关闭=互斥体销毁）；进程退出内核自动释放。
    合一后单安装用固定名；不再带底座后缀——旧双版安装残留与新装并存
    时靠发布口径「先卸旧版」保证不同名互踩不再出现。"""
    global _MUTEX
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, "Local\\CubeSetlistManager")
    return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS


def _wait_parent(pid):
    """继任实例等前任进程退出（底座重启链）：单实例互斥体随进程退出才
    释放，抢先起会撞「已在运行」弹窗。上限 90s——exitCloseApps 开时收尾
    含 close_app 60s + web 8s，15s 会低估；超时放行撞双开框自然退出。"""
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, pid)      # SYNCHRONIZE
    if not h:
        return                                      # 前任已退，直接起
    k32.WaitForSingleObject(h, 90000)
    k32.CloseHandle(h)


def main():
    global _CRASH_LOG
    if "--wait-parent" in sys.argv:   # 底座重启继任实例：等前任退出
        try:
            i = sys.argv.index("--wait-parent")
            pid = int(sys.argv[i + 1])
            sys.argv[i:i + 2] = []   # 从 argv 摘除，webui 宽高解析不误吃
            _wait_parent(pid)
        except (ValueError, IndexError, OSError):
            pass
    dpi.enable()
    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(
            0, "Cube Setlist Manager 已在运行", "Cube Setlist Manager", 0x30)
        return
    try:                        # noconsole exe 无 stderr：崩溃栈落 crash.log
        _cl = _HERE / "crash.log"
        if _cl.exists() and _cl.stat().st_size > 512 * 1024:   # 轮转防无限增长
            _cl.replace(_cl.with_name("crash.log.1"))
        _CRASH_LOG = open(_cl, "a", encoding="utf-8")
        faulthandler.enable(_CRASH_LOG)
    except OSError:
        pass
    try:
        import webui
    except ImportError:
        ctypes.windll.user32.MessageBoxW(
            0, "需要 pywebview 运行新前端：\npy -m pip install pywebview",
            "Cube Setlist Manager", 0x30)
        return
    webui.main()


if __name__ == "__main__":
    main()
