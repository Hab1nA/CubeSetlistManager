# -*- coding: utf-8 -*-
"""弹窗判定回归测试：Cubase 保存确认框与空主框架同名（标题=光杆
「Cubase Pro」，词表不可达）导致脏工程切歌零反应——按窗口样式判别的
防线。样式值=真机探针实测（tools/probe_dialog.py attrs）：
弹窗 popup=0x96C80000（禁用态 0x9EC80000），工程窗/主框架 overlapped=
0x1FCF0000；GetWindowLongW 语义=有符号，回归时防丢掩码。"""
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
    """替身环境：_windows/_user32/focus/human_enter 全部可脚本化。"""

    def __init__(self, wins, enabled=None, styles=None, focus_ok=True):
        self.wins = wins                # [(hwnd, title, class)]
        self.enabled = enabled or {}
        self.styles = styles or {}
        self.focus_ok = focus_ok
        self.presses, self.logs = [], []
        self._old = (daw_ctrl._windows, daw_ctrl._user32,
                     daw_ctrl.focus, daw_ctrl.human_enter)
        daw_ctrl._windows = lambda: self.wins
        daw_ctrl._user32 = self
        daw_ctrl.focus = self._focus
        daw_ctrl.human_enter = lambda: self.presses.append(1)

    def _focus(self, h, tries=1):
        return self.focus_ok

    def IsWindowEnabled(self, h):
        return self.enabled.get(h, 1)

    def GetWindowLongW(self, h, idx):
        return ctypes.c_int32(self.styles.get(h, 0)).value  # 真实语义=有符号

    def close(self):
        (daw_ctrl._windows, daw_ctrl._user32,
         daw_ctrl.focus, daw_ctrl.human_enter) = self._old


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
    """_dialogs：Cubase 放行 frame_title 同名候选；S1 照旧排除（行为不变）。"""
    cub_env = _Env([(8, PROJ, "SteinbergWindowClass1"),
                    (9, "Cubase Pro", "SteinbergWindowClass1"),
                    (10, "Cubase Pro Hub", "SteinbergWindowClass1")])
    try:
        assert dict(daw_ctrl._dialogs(CUB))[9] == "Cubase Pro"
        assert 8 not in dict(daw_ctrl._dialogs(CUB))       # 工程窗排除
        assert 10 not in dict(daw_ctrl._dialogs(CUB))      # Hub 照旧排除
    finally:
        cub_env.close()
    s1_env = _Env([(11, "Studio One", "CCLWindowClass")])
    try:
        assert daw_ctrl._dialogs(S1) == []                 # S1 行为零变化
    finally:
        s1_env.close()


def test_signed_style_regression():
    """GetWindowLongW 有符号返回（0x96C80000→负数）掩码不丢。"""
    env = _Env([(12, "Cubase Pro", "SteinbergWindowClasss")],
               styles={12: DLG})
    try:
        assert env.GetWindowLongW(12, -16) < 0             # 真实 WinAPI 语义
        assert _run(env)
    finally:
        env.close()
