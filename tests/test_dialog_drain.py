# -*- coding: utf-8 -*-
"""弹窗判定回归测试：Cubase 与 S1 的保存确认框都与常驻主窗同名（标题=
光杆「Cubase Pro」/「Studio One」，词表不可达）导致脏工程零反应——按窗口
样式判别的防线（confirm_by_style）。样式值=真机探针实测
（tools/probe_dialog.py attrs）：弹窗 popup=0x96C80000（禁用态
0x9EC80000），工程窗/主框架 overlapped=0x1FCF0000；GetWindowLongW 语义=
有符号，回归时防丢掩码。S1 的保存框样式真机取证后若异于 DLG，改这里。"""
import ctypes
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import daw_ctrl

CUB = daw_ctrl.CUBASE
S1 = daw_ctrl.STUDIOONE
DLG = 0x96C80000        # 保存框：WS_POPUP|CAPTION|SYSMENU，无 THICKFRAME/MAX
DLG_OFF = 0x9EC80000    # 同上 + WS_DISABLED（叠层模态的下层框）
FRAME = 0x1FCF0000      # 工程窗/主框架：overlapped，有 THICKFRAME/MAX
PROJ = "Cubase Pro 工程 - テスト"


class _Env:
    """替身环境：_windows/_user32/focus/human_enter/find_processes_by_prefix
    全部可脚本化。pids=窗口 hwnd→归属进程 PID（缺省全归 DAW 进程 1234）。"""

    def __init__(self, wins, enabled=None, styles=None, focus_ok=True,
                 pids=None, children=None, classnames=None):
        self.wins = wins                # [(hwnd, title, class)]
        self.enabled = enabled or {}
        self.styles = styles or {}
        self.pids = pids or {}
        self.children = children or {}  # {hwnd: [(子hwnd, 类, 样式)]}
        self.classnames = classnames or {}  # {子hwnd: 类名}
        self.focus_ok = focus_ok
        self.presses, self.logs, self.posted = [], [], []
        self._old = (daw_ctrl._windows, daw_ctrl._user32,
                     daw_ctrl.focus, daw_ctrl.human_enter,
                     daw_ctrl.find_processes_by_prefix)
        daw_ctrl._windows = lambda: self.wins
        daw_ctrl._user32 = self
        daw_ctrl.focus = self._focus
        daw_ctrl.human_enter = lambda: self.presses.append(1)
        daw_ctrl.find_processes_by_prefix = lambda prefix: [(1234, prefix)]

    def _focus(self, h, tries=1):
        return self.focus_ok

    def IsWindowEnabled(self, h):
        return self.enabled.get(h, 1)

    def GetWindowLongW(self, h, idx):
        return ctypes.c_int32(self.styles.get(h, 0)).value  # 真实语义=有符号

    def GetWindowThreadProcessId(self, h, out):
        out._obj.value = self.pids.get(h, 1234)   # byref 包装：经 _obj 写回
        return 1

    def GetClassNameW(self, h, buf, n):
        cls = self.classnames.get(h)
        if cls is None:
            cls = next((c for w, _t, c in self.wins if w == h), "")
        buf.value = cls
        return len(cls)

    def EnumChildWindows(self, h, cb, l):
        for ch, _c, _s in self.children.get(h, []):
            cb(ch, l)
        return 1

    def PostMessageW(self, h, msg, w, l):
        self.posted.append((h, msg, w, l))
        return 1

    def close(self):
        (daw_ctrl._windows, daw_ctrl._user32,
         daw_ctrl.focus, daw_ctrl.human_enter,
         daw_ctrl.find_processes_by_prefix) = self._old


def _run(env, facts=CUB, seen=None, rounds=1):
    seen = set() if seen is None else seen
    pressed = False
    for _ in range(rounds):
        pressed = daw_ctrl._drain(facts, "关闭", seen, env.logs.append) \
            or pressed
    return pressed


def test_same_name_save_dialog_pressed():
    """脏工程保存框（与主框架同名+popup 样式）→ 回车。"""
    env = _Env([(1, "Cubase Pro", "SteinbergWindowClassx")],
               styles={1: DLG})
    try:
        seen = set()
        assert _run(env, seen=seen)
        assert env.presses == [1]
        assert seen == {(1, "Cubase Pro")}
    finally:
        env.close()


def test_real_frame_not_pressed():
    """同名但 overlapped 样式=真主框架 → 静默跳过，绝不回车。"""
    env = _Env([(2, "Cubase Pro", "SteinbergWindowClassy")],
               styles={2: FRAME})
    try:
        assert not _run(env) and env.presses == [] and env.logs == []
    finally:
        env.close()


def test_disabled_twin_skipped():
    """叠层双弹窗：下层禁用框跳过（不白按不占 seen），上层按下。"""
    env = _Env([(3, "Cubase Pro", "SteinbergWindowClassz"),
                (4, "Cubase Pro", "SteinbergWindowClassz")],
               enabled={3: 0}, styles={3: DLG_OFF, 4: DLG})
    try:
        assert _run(env)
        assert env.presses == [1]       # 只按了 enabled 的那层
    finally:
        env.close()


def test_enter_mark_still_works():
    """词表路径回归：「未找到端口」照旧回车。"""
    env = _Env([(5, "未找到端口", "SteinbergWindowClassw")])
    try:
        assert _run(env) and env.presses == [1]
    finally:
        env.close()


def test_unknown_logged_once():
    """未知弹窗只记一次（seen 去重防刷屏）。"""
    env = _Env([(6, "卸载工程", "SteinbergWindowClassu")])
    try:
        seen = set()
        _run(env, seen=seen, rounds=2)
        assert len(env.logs) == 1 and "未知" in env.logs[0]
    finally:
        env.close()


def test_retry_until_pressed():
    """确认框按空（聚焦失败）不记 seen → 下轮重试；按下成功才记。"""
    env = _Env([(7, "Cubase Pro", "SteinbergWindowClassr")],
               styles={7: DLG}, focus_ok=False)
    try:
        seen = set()
        assert not _run(env, seen=seen) and seen == set()
        env.focus_ok, env.wins = True, [(7, "Cubase Pro",
                                         "SteinbergWindowClassr")]
        assert _run(env, seen=seen) and seen == {(7, "Cubase Pro")}
    finally:
        env.close()


def test_dialogs_membership():
    """_dialogs：Cubase/S1 都放行 frame_title 同名候选（样式判别在 _drain）。"""
    cub_env = _Env([(8, PROJ, "SteinbergWindowClass1"),
                    (9, "Cubase Pro", "SteinbergWindowClass1"),
                    (10, "Cubase Pro Hub", "SteinbergWindowClass1")])
    try:
        assert dict(daw_ctrl._dialogs(CUB))[9] == "Cubase Pro"
        assert 8 not in dict(daw_ctrl._dialogs(CUB))       # 工程窗排除
        assert 10 not in dict(daw_ctrl._dialogs(CUB))      # Hub 照旧排除
    finally:
        cub_env.close()
    s1_env = _Env([(11, "Studio One", "CCLWindowClass"),
                   (12, "Studio One - 曲", "CCLWindowClass")])
    try:
        d = dict(daw_ctrl._dialogs(S1))
        assert d[11] == "Studio One"                       # 同名候选放行
        assert 12 not in d                                 # 工程窗排除
    finally:
        s1_env.close()


def test_s1_same_name_save_dialog_pressed():
    """S1 退出保存框（与 Start 页主窗同名+popup 样式）→ 回车。"""
    env = _Env([(13, "Studio One", "CCLWindowClassx")], styles={13: DLG})
    try:
        seen = set()
        assert _run(env, facts=S1, seen=seen)
        assert env.presses == [1]
        assert seen == {(13, "Studio One")}
    finally:
        env.close()


def test_s1_std_dialog_class_pressed():
    """S1 保存框真机形态：类=#32770（dialog_classes 准入）+popup → 回车。"""
    env = _Env([(15, "Studio One", "#32770")], styles={15: DLG})
    try:
        assert _run(env, facts=S1) and env.presses == [1]
    finally:
        env.close()


def test_s1_std_dialog_foreign_pid_ignored():
    """#32770 全系统共用：别家进程的同名弹窗绝不误按。"""
    env = _Env([(16, "Studio One", "#32770")], styles={16: DLG},
               pids={16: 9999})
    try:
        assert not _run(env, facts=S1) and env.presses == []
    finally:
        env.close()


def test_s1_click_default_button():
    """S1 confirm_action=click_default：TaskDialog 收不到合成回车（真机
    实测），确认改为对 BS_DEFPUSHBUTTON 子钮投 BM_CLICK，不走 focus/回车。"""
    env = _Env([(17, "Studio One", "#32770")], styles={17: DLG,
               180: 0x50000001, 181: 0x50000000},
               children={17: [(180, "Button", 0), (181, "Button", 0)]},
               classnames={180: "Button", 181: "Button"})
    try:
        seen = set()
        assert _run(env, facts=S1, seen=seen)
        assert env.posted == [(180, 0x00F5, 0, 0)]     # BM_CLICK=默认钮「是」
        assert env.presses == []                        # 不发合成回车
        assert seen == {(17, "Studio One")}
    finally:
        env.close()


def test_s1_click_default_fallback_enter():
    """click_default 找不到默认钮（自绘按钮）→ 回退仿人回车，不漏确认。"""
    env = _Env([(18, "Studio One", "#32770")], styles={18: DLG},
               children={18: []})
    try:
        seen = set()
        assert _run(env, facts=S1, seen=seen)
        assert env.posted == [] and env.presses == [1]
        assert seen == {(18, "Studio One")}
    finally:
        env.close()


def test_s1_start_page_frame_not_pressed():
    """S1 同名 overlapped 样式=Start 页主窗 → 静默跳过，绝不回车。"""
    env = _Env([(14, "Studio One", "CCLWindowClassy")], styles={14: FRAME})
    try:
        assert not _run(env, facts=S1) and env.presses == []
    finally:
        env.close()


def test_signed_style_regression():
    """GetWindowLongW 有符号返回（0x96C80000→负数）掩码不丢。"""
    env = _Env([(12, "Cubase Pro", "SteinbergWindowClasss")],
               styles={12: DLG})
    try:
        assert env.GetWindowLongW(12, -16) < 0             # 真实 WinAPI 语义
        assert _run(env)
    finally:
        env.close()
