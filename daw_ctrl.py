# -*- coding: utf-8 -*-
"""DAW 底座控制器（Cubase / Studio One 双后端）：切歌 = 先关闭当前工程
（自动保存）再打开下一个。不做多工程切换——Cubase 本机实测：多工程打开后
「激活」无法由软件可靠控制（聚焦≠激活，仅切窗口聚焦不激活工程），必须先关
后开；S1 v1 保守沿用同一状态机（多窗口直切的简化留待真机校准后评估）。
关闭工程 = 对工程窗口句柄 PostMessage(WM_CLOSE)（Cubase E2E 实测 0.5s 生效；
键盘 Ctrl+W 受 DAW 内部激活/焦点路由影响会静默失效，已废弃）。
有未保存修改时弹保存确认框：自动保存策略=对弹窗仿人回车（默认键=保存）。
打开工程统一走 CLI（"<exe>" "<工程路径>"）：未运行=冷启动；已运行=单实例
转交打开（Cubase E2E 实测最稳；Hub 上 Ctrl+O+键入路径受前台锁/对话框预填/
内部激活态影响，时灵时不灵，已废弃）。模态弹窗阻塞期间单实例转交会被
Cubase 丢弃，放行弹窗后要补发一次打开请求。
走带=键序表驱动（facts["transport"]），需真聚焦窗口（transport 用键盘），
切歌链路则完全不依赖焦点。全程只走正常键序/消息，绝不强杀进程（强杀会留
崩溃标记弹「检测到崩溃」）。

双底座：各 DAW 的「事实表」（进程名前缀/窗口类与标题标记/弹窗词表/键序表/
工程扩展名等）收拢在 CUBASE / STUDIOONE 两个 dict，其余全部逻辑共用。
模块级 ACTIVE 事实表由 set_active() 按config 选定（单进程单底座），
Cubase 行为与重构前逐位一致。"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

from obs_ctrl import find_processes_by_prefix, launch_detached

WM_CLOSE = 0x0010
GW_OWNER = 4
CLOSE_TIMEOUT = 30      # 关工程等待上限（秒）
OPEN_TIMEOUT = 120      # 打开/冷启动等待上限（大工程+采样库加载）
KEY_GAP = 0.03          # 走带键逐事件间隔
ENTER_HOLD = 0.12       # 仿人回车按下保持时长（零间隔连发会被弹窗无视）

# 键名 → 虚拟键码；值=tuple 时第二位为扩展键标志（NumEnter 等小键盘键
# 须带 KEYEVENTF_EXTENDEDKEY，否则宿主收到的是主键盘同码键，语义可能不同）
VK = {"ESC": 0x1B, "MENU": 0x12, "RETURN": 0x0D, "SPACE": 0x20,
      "NUM0": 0x60, "NUM1": 0x61, "NUMDOT": 0x6E,
      "NUMENTER": (0x0D, True)}
ACTION_NAMES = {"play": "播放", "pause": "暂停", "resume": "继续",
                "rewind": "回零", "stop": "停止"}   # 日志用中文动作名

# ---- Cubase 事实表（与重构前模块常量逐项对应，行为零变化）----
# 工程窗口标题「<版本名> 工程 - 项目名」：版本名随工程最后保存的 Cubase 版本变
# （实测同一台 Cubase 15 下：15 存的显示 Cubase Pro，13.0.40 存的显示
#   Cubase Version 13.0.40），所以只认中间的固定标记，不认前缀。
# 「未找到端口」是模态确认框（工程引用的 MIDI 端口不存在），必须回车放行，
# 否则工程窗口永远出不来（E2E 实测 TAIDADA 卡死）；其余只记录防误按。
CUBASE = dict(
    name="cubase",
    display_name="Cubase",
    proc_prefix="cubase",               # Cubase15.exe → 进程名小写前缀
    title_mark=" 工程 - ",
    win_class_prefix="SteinbergWindowClass",   # 自绘窗口（带随机后缀）
    dialog_enter_marks=("保存", "激活", "未找到端口"),
    dialog_log_marks=("安全模式", "丢失", "锁定", "无法"),
    dialog_ignores=("Cubase Pro Hub", "Cubase Pro"),   # 常驻窗，非弹窗
    hub_title="Cubase Pro Hub",
    frame_title="Cubase Pro",           # 关完工程只剩的空主框架
    loading_marks=("正在加载",),         # 加载浮层标题前缀
    song_ext=".cpr",
    close_before_open=True,             # 先关后开（激活模型，见文件头）
    name_after_mark=True,               # 「<版本名> 工程 - <歌名>」：歌名在后
    probe_duration=True,                # .cpr RIFF 解析时长（cpr_meta）
    app_suffix="",                      # 窗口标题后缀（Cubase 版保持原样）
    # 走带键序=本机默认：空格(仅停止态当播放)/小键盘0(停止)/小键盘1(回零)。
    # play=回零从头播/pause=原地停/resume=从当前位置继续/stop/rewind=停止+
    # 回零（播放中定位到零点会继续播，须先停——回零即停在零点）。stop 与
    # pause 同键序：stop 给踩钉/自动推进用，pause 给 UI 用，语义不同键序一致。
    transport={
        "play": ("ESC", "NUM0", "NUM1", "SPACE"),
        "stop": ("ESC", "NUM0"),
        "pause": ("ESC", "NUM0"),
        "resume": ("ESC", "SPACE"),
        "rewind": ("ESC", "NUM0", "NUM1"),
    },
)

# ---- Studio One 7 事实表（v1=按调研推断，待真机 M0 校准修订——
# docs/StudioOne迁移调研.md §四 八项清单；标注「待采样」的项校准后更新）----
STUDIOONE = dict(
    name="studioone",
    display_name="Studio One",
    proc_prefix="studio one",           # Studio One.exe → 进程名小写前缀
    title_mark="Studio One - ",         # 真机采样 2026-09-26：「Studio One -
                                        # lingo演出5.16」（Start 页=光杆"Studio
                                        # One"，不含标记不被误认工程窗）
    name_after_mark=True,               # 歌名在标记后（与 Cubase 同向）
    win_class_prefix="CCLWindowClass",  # 真机采样 2026-09-26（Start 页主窗）；
                                        # Song 文档窗同类与否待采样
    dialog_enter_marks=("保存",),        # 待采样：最小集，未知弹窗只记录不按键
    dialog_log_marks=("丢失", "无法"),
    dialog_ignores=("Studio One",),     # Start 页主窗标题（真机采样 2026-09-26）
    hub_title=None,
    # 真机 2026-09-27 实测：Start 页态 CLI 递交被静默丢弃（120s 无窗口，
    # 与 Cubase 空框架吞转交同款）→ frame_title 置 Start 页标题启用「关框架
    # 退出→带路径冷启动」特例（Start 页 WM_CLOSE=正常退出，用户点 X 同款）
    frame_title="Studio One",
    loading_marks=(),                   # 待采样：空=跳过浮层等待
    song_ext=".song",
    probe_duration=False,               # .song 私有容器无解析，时长手填
    app_suffix=" S1",
    # 真机 2026-09-27 实测：CLI 递交=同实例同窗口换歌（标题原地翻转，不弹
    # 任何确认框）；**未保存修改被静默丢弃**（.song mtime 不变实证）→ 无需
    # 先关后开，autoSave 无实际作用（文档注明：切歌前自行保存）。
    close_before_open=False,
    # 待校准中：Space=播放/停止切换（真机双向已验）、小键盘.（NUMDOT）=
    # 返回零点（用户查 S1 默认键表；真机单向待验）。S1 无独立暂停键，
    # stop/pause/resume 同键序；play=先回零再播；rewind=先停再回零。
    transport={
        "play": ("NUMDOT", "SPACE"),
        "stop": ("SPACE",),
        "pause": ("SPACE",),
        "resume": ("SPACE",),
        "rewind": ("SPACE", "NUMDOT"),
    },
)

FACTS = {"cubase": CUBASE, "studioone": STUDIOONE}
ACTIVE = CUBASE      # 当前底座事实表；GUI 启动时按 config set_active()


def set_active(facts):
    global ACTIVE
    ACTIVE = facts


_user32 = ctypes.WinDLL("user32")

# ---- SendInput（走带键用）：INPUT 的 union 必须含 MOUSEINIT 才是真实尺寸 ----
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT),
                              ctypes.c_int)
_user32.EnumWindows.argtypes = (ctypes.c_void_p, wintypes.LPARAM)
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
_user32.GetWindow.restype = wintypes.HWND


def _key(vk, up=False, ext=False):
    flags = KEYEVENTF_EXTENDEDKEY if ext else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    inp = _INPUT(type=INPUT_KEYBOARD)
    inp.ki = _KEYBDINPUT(vk, 0, flags, 0, None)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def tap(vk, gap=KEY_GAP):
    """敲一个虚拟键。vk 可为键表值（int 或 (code, ext)）。"""
    if isinstance(vk, tuple):
        vk, ext = vk
    else:
        ext = False
    _key(vk, ext=ext)
    time.sleep(gap)
    _key(vk, up=True, ext=ext)
    time.sleep(gap)


def human_enter():
    """仿人回车：按下-保持~120ms-抬起；零间隔连发被 Steinberg 弹窗无视。"""
    _key(VK["RETURN"])
    time.sleep(ENTER_HOLD)
    _key(VK["RETURN"], up=True)
    time.sleep(KEY_GAP)


# ---- 窗口枚举 / 聚焦 ----

def _windows():
    """可见顶层窗口 [(hwnd, title, class)]。"""
    out = []

    @_ctypes_cb
    def _cb(h, _l):
        if _user32.IsWindowVisible(h):
            n = _user32.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _user32.GetWindowTextW(h, buf, n + 1)
                cls = ctypes.create_unicode_buffer(64)
                _user32.GetClassNameW(h, cls, 64)
                out.append((h, buf.value, cls.value))
        return True

    _user32.EnumWindows(_cb, 0)
    return out


def _ctypes_cb(fn):
    return ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                              wintypes.LPARAM)(fn)


def project_windows():
    """当前打开的工程窗口 [(hwnd, 标题)]（按标题标记+窗口类前缀识别；
    类前缀为空=不限类，只认标题标记）。"""
    f = ACTIVE
    return [(h, t) for h, t, c in _windows()
            if f["title_mark"] in t and c.startswith(f["win_class_prefix"])]


def project_name_from_title(title):
    """工程窗口标题 → 歌名（方向由事实表 name_after_mark 定：Cubase=
    标记后段「Cubase Pro 工程 - アイドル」→'アイドル'；S1 推断格式=
    标记前段「アイドル — Studio One」）；非工程标题 → None。"""
    mark = ACTIVE["title_mark"]
    if mark not in title:
        return None
    before, _sep, after = title.partition(mark)
    name = after if ACTIVE["name_after_mark"] else before
    return name.rstrip("*") or None    # S1 脏工程标记=歌名尾加*（真机采样）


def project_title_shape(name):
    """歌名 → 期望窗口标题的「对齐形态」（_wait_open 匹配用：
    后缀全等防同名前缀误判）。"""
    f = ACTIVE
    return (f["title_mark"] + name) if f["name_after_mark"] \
        else (name + f["title_mark"])


def loading_windows():
    """加载中浮层 [(hwnd, 标题)]（类前缀+标题前缀命中；类前缀或词表为空
    =该底座无浮层判定，恒空）。"""
    f = ACTIVE
    if not f["win_class_prefix"] or not f["loading_marks"]:
        return []
    return [(h, t) for h, t, c in _windows()
            if c.startswith(f["win_class_prefix"])
            and any(t.startswith(m) for m in f["loading_marks"])]


def current_project():
    ws = project_windows()
    return ws[0] if ws else None


def ui_alive():
    """DAW 是否有可见界面（工程/Hub/主框架/弹窗任一自绘窗）。
    进程在而界面全无 = 正在退出的空壳（或冷启动窗口尚未出现）。
    类前缀为空（未知窗口类的底座）时退化为「有标题窗口即在」，近似恒真。"""
    f = ACTIVE
    return any(c.startswith(f["win_class_prefix"]) and t
               for _h, t, c in _windows())


def wait_exit(timeout=45):
    """等 DAW 进程退净（消亡尾巴）；超时返回 False。"""
    prefix = ACTIVE["proc_prefix"]
    deadline = time.time() + timeout
    while find_processes_by_prefix(prefix) and time.time() < deadline:
        time.sleep(0.5)
    return not find_processes_by_prefix(prefix)


def focus(hwnd, tries=6):
    """置前台（transport 走带键需要）。后台进程被前台锁拦：空敲 ALT +
    AttachThreadInput 附加前台线程 + 最小化先恢复，SwitchToThisWindow 兜底。"""
    kernel32 = ctypes.windll.kernel32
    for _ in range(tries):
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        _key(VK["MENU"])
        _key(VK["MENU"], up=True)
        t_this = kernel32.GetCurrentThreadId()
        fg = _user32.GetForegroundWindow()
        t_fg = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(t_fg) and t_fg != t_this
        if attached:
            _user32.AttachThreadInput(t_this, t_fg, True)
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(t_this, t_fg, False)
        if _user32.GetForegroundWindow() != hwnd:
            _user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.3)
        if _user32.GetForegroundWindow() == hwnd:
            return True
    return False


class SwitchError(Exception):
    pass


class DawController:
    """串行切歌控制器。switch_to 线程安全：正忙返回 False 并忽略请求，
    流程在独立线程跑，日志/完成经 log/on_done 回调交给 GUI。"""

    def __init__(self, facts, exe, auto_save=True, log=print):
        self.facts = facts
        set_active(facts)       # 模块级函数跟随本控制器的底座
        self.exe = exe
        self.auto_save = auto_save
        self._log = log
        self.busy = False
        self._lock = threading.Lock()
        self._seen = set()

    # ---- 对 GUI 的入口 ----

    def switch_to(self, song_path, on_done=None):
        with self._lock:
            if self.busy:
                return False
            self.busy = True
        threading.Thread(target=self._switch, args=(song_path, on_done),
                         daemon=True).start()
        return True

    def transport(self, action):
        """走带键序由事实表 facts["transport"] 驱动（键名见 VK），
        发到当前工程窗口（键盘路径，需聚焦）。"""
        ws = current_project()
        if not ws:
            self._log("走带控制：没有工程窗口")
            return False
        if not focus(ws[0]):
            self._log("走带控制：无法聚焦工程窗口")
            return False
        for k in self.facts["transport"].get(action, ()):
            tap(VK[k])
        self._log("走带 %s → 《%s》" % (ACTION_NAMES.get(action, action),
                                       project_name_from_title(ws[1])))
        return True

    def panic(self):
        """向所有工程窗口发停止键序——激活错乱也保证静音。"""
        ws = project_windows()
        n = 0
        for h, t in ws:
            if focus(h):
                for k in self.facts["transport"]["stop"]:
                    tap(VK[k])
                n += 1
        self._log("全停：已向 %d/%d 个工程窗口发送停止" % (n, len(ws)))

    # ---- 切歌流程（工作线程内执行） ----

    def _switch(self, path, on_done):
        opened = None
        self._seen = set()                  # 弹窗标题去重（每次切换重新记）
        try:
            self._log("切换开始：%s" % _disp(path))
            if not os.path.exists(path):
                raise SwitchError("工程文件不存在")
            for _ in range(5):              # 先关后开：清掉所有已开工程
                cur = current_project()
                if not cur or not self.facts.get("close_before_open", True):
                    break                   # S1：直接换歌（同窗替换，不弹框）
                self._close(cur[0])
            else:
                raise SwitchError("工程窗口关不完（异常状态）")
            opened = self._open(path)
            self._log("切换完成：《%s》已打开（按「开始」播放）"
                      % project_name_from_title(opened))
        except SwitchError as e:
            self._log("切换失败：%s" % e)
        finally:
            self.busy = False
            if on_done:
                on_done(project_name_from_title(opened) if opened else None)

    def _close(self, hwnd):
        """PostMessage WM_CLOSE 直达窗口（Cubase E2E 实测 0.5s 生效）。
        有未保存修改时弹确认框：自动保存策略=对弹窗仿人回车（默认键=保存）。"""
        self._log("关闭工程（WM_CLOSE）…")
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        deadline = time.time() + CLOSE_TIMEOUT
        kicked = False
        while time.time() < deadline:
            if not _win_alive(hwnd):
                time.sleep(0.5)
                self._log("工程已关闭")
                return
            if not kicked and time.time() < deadline - CLOSE_TIMEOUT / 2:
                kicked = True               # 过半没关：确认框可能没应到，重发
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            self._drain_dialogs("关闭")
            time.sleep(0.4)
        raise SwitchError("关闭超时（有无未处理的确认框？）")

    def _open(self, path):
        """统一走 CLI：未运行=冷启动；已运行=单实例转交打开。
        Cubase 特例（事实表驱动）：关完工程后只剩空主框架（无 Hub）时，
        转交会被丢弃（E2E 实测）→ 先 WM_CLOSE 框架退出应用再冷启动（实测
        关闭中收到的打开请求会被接管执行，总耗时约 30s）。其他底座
        frame_title=None 时跳过该特例。"""
        f = self.facts
        name = os.path.splitext(os.path.basename(path))[0]
        if self.running() and not project_windows():
            hub = frame = None
            for h, t, c in _windows():
                if c.startswith(f["win_class_prefix"]) and t:
                    if f["hub_title"] and t == f["hub_title"]:
                        hub = h
                    elif f["frame_title"] and t == f["frame_title"]:
                        frame = h
            if hub is None and frame is not None:
                self._log("%s 空闲无工程：退出后重新启动…"
                          % f["display_name"])
                _user32.PostMessageW(frame, WM_CLOSE, 0, 0)
                wait_exit(45)
        self._log("启动打开工程（单实例转交/冷启动）…")
        r = launch_detached(self.exe, '"%s"' % path)   # 参数必须带引号
        if r <= 32:
            raise SwitchError("启动失败（ShellExecute 代码 %s）" % r)
        title = self._wait_open(name, path)
        self._log("工程窗口已出现：《%s》" % name)
        return title

    def _wait_open(self, name, path):
        """等工程窗口出现，返回其标题。模态弹窗阻塞期间单实例转交会被
        Cubase 丢弃（E2E 实测）→ 放行弹窗后补发一次。
        不做周期性补发：大工程加载慢，定时重发会连环打开同一工程。"""
        deadline = time.time() + OPEN_TIMEOUT
        kick_at = None
        while time.time() < deadline:
            for h, t in project_windows():
                if t.endswith(project_title_shape(name)):
                    return t        # 形态全等，防同名前缀误判
            pressed = self._drain_dialogs("打开")
            if pressed and kick_at is None:
                kick_at = time.time() + 3   # 刚放行模态，稍等再补发
            if kick_at is not None and time.time() >= kick_at:
                self._log("弹窗已处理，重新发送打开请求")
                launch_detached(self.exe, '"%s"' % path)
                kick_at = None              # 只补一次，弹窗再次放行才再补
            time.sleep(0.5)
        raise SwitchError("等待《%s》窗口超时（%d 秒）" % (name, OPEN_TIMEOUT))

    def _drain_dialogs(self, stage):
        """处理自绘弹窗一轮（详见 _drain），返回是否放了确认框。"""
        return _drain(self.facts, stage, self._seen, self._log)

    def running(self):
        return bool(find_processes_by_prefix(self.facts["proc_prefix"]))


def _drain(f, stage, seen, log):
    """处理自绘弹窗一轮：命中确认类仿人回车（返回 True，调用方据此补发
    被模态吞掉的请求），其余只记录防误按，同一标题只记一次。
    弹窗判定=排除法（非工程窗口、非常驻窗）。"""
    pressed = False
    for h, t in _dialogs(f):
        if t in seen:
            continue
        seen.add(t)
        if any(m in t for m in f["dialog_enter_marks"]):
            log("[%s] 弹窗「%s」→ 回车" % (stage, t))
            if focus(h):
                human_enter()
                pressed = True
        elif any(m in t for m in f["dialog_log_marks"]):
            log("[%s] 弹窗「%s」（仅记录，不按键）" % (stage, t))
        else:
            log("[%s] 未知弹窗「%s」（不按键）" % (stage, t))
    return pressed


def close_app(timeout=60, log=print):
    """退出整个 DAW（只走 WM_CLOSE，绝不强杀）：先关所有工程窗口（未保存
    修改的确认框回车=保存，沿用切歌策略），工程清完后对 Hub/主框架等常驻窗
    补 WM_CLOSE 退出应用；超时只放弃不强杀。返回是否已退出。"""
    f = ACTIVE
    seen, closed = set(), set()
    for h, _t in project_windows():
        _user32.PostMessageW(h, WM_CLOSE, 0, 0)
        closed.add(h)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not find_processes_by_prefix(f["proc_prefix"]):
            return True
        _drain(f, "退出", seen, log)
        for h, t, c in _windows():
            if (c.startswith(f["win_class_prefix"]) and t in f["dialog_ignores"]
                    and h not in closed):
                _user32.PostMessageW(h, WM_CLOSE, 0, 0)
                closed.add(h)
        time.sleep(0.5)
    return not find_processes_by_prefix(f["proc_prefix"])


def _dialogs(f):
    """自绘弹窗 = 事实表窗口类前缀的可见窗口里，非工程窗口、非常驻窗
    （标题固定，排除）的其余有标题窗口。
    注：Cubase 实测「未找到端口」弹窗没有 owner，owner 判定不可用。"""
    out = []
    for h, t, c in _windows():
        if not c.startswith(f["win_class_prefix"]) or not t:
            continue
        if f["title_mark"] in t or t in f["dialog_ignores"]:
            continue
        out.append((h, t))
    return out


def _win_alive(hwnd):
    """窗口仍存活且仍是工程窗口（销毁后 IsWindow=0）。"""
    return bool(_user32.IsWindow(hwnd)) and \
        any(h == hwnd for h, _ in project_windows())


def _disp(path):
    """工程路径 → '队伍\\歌名' 展示。"""
    return os.path.join(os.path.basename(os.path.dirname(
        os.path.dirname(path))), os.path.basename(os.path.dirname(path)))
