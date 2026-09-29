# -*- coding: utf-8 -*-
"""Cubase 保存弹窗取证探针（M0 清单「弹窗文案校准」补课）。
背景：切歌关脏工程时 CSM 对保存确认框零反应，疑似弹窗标题与 daw_ctrl
弹窗词表失配，或被 dialog_ignores/无标题过滤/类名过滤静默漏掉。
只读探针：只枚举/打印窗口，绝不按键、绝不发消息。
子命令：
  win       一次性列出全部可见顶层窗口（含无标题窗与标准 #32770 框）
  watch     实时监测：新窗口出现即按 daw_ctrl 真实判定树标注「drain 会怎么
            处理」。取证主路径：跑起来→Cubase 手动改工程→CSM 触发切歌→
            弹窗留着别点，把新出现的行全部复制回来
  selftest  判定树自检（不碰窗口）
  attrs <hwnd>  取证单个窗口：owner/样式/矩形/使能/前台（区分保存框与
            主框架用；弹窗开着时抓现场）
  kids <hwnd>   列子窗口（按钮文本+样式，找 BS_DEFPUSHBUTTON 默认按钮）
用法：py -u tools/probe_dialog.py [--s1] <win|watch|selftest|attrs|kids>
  --s1  指到 STUDIOONE 事实表（S1 保存框取证；默认 CUBASE）"""
import ctypes
import pathlib
import sys
import time
from ctypes import wintypes

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import daw_ctrl

USE_S1 = "--s1" in sys.argv
F = daw_ctrl.STUDIOONE if USE_S1 else daw_ctrl.CUBASE
daw_ctrl.set_active(F)
_u32 = daw_ctrl._user32


def _all_windows():
    """全部可见顶层窗口 [(hwnd, 标题(可为空), 类名)]。
    不用 daw_ctrl._windows()：它滤掉无标题窗，而无标题弹窗正是疑似漏检
    机制之一，取证必须能看见它。"""
    out = []

    @daw_ctrl._ctypes_cb
    def _cb(h, _l):
        if _u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(64)
            _u32.GetClassNameW(h, cls, 64)
            n = _u32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            _u32.GetWindowTextW(h, buf, n + 1)
            out.append((h, buf.value, cls.value))
        return True

    _u32.EnumWindows(_cb, 0)
    return out


def _classify(c, t):
    """按 daw_ctrl._dialogs/_drain 的真实判定树标注处置（随 --s1 换表）。"""
    if not c.startswith(F["win_class_prefix"]):
        return ("drain 看不见（类名 %s 不在 %s* 内）"
                % (c, F["win_class_prefix"]))
    if F["title_mark"] in t:
        return "工程窗（正常识别）"
    if t in F["dialog_ignores"]:
        if F.get("confirm_by_style") and t == F["frame_title"]:
            return ("同名候选→样式判别：popup=保存框【会回车】；"
                    "overlapped=主窗静默跳过（attrs 看样式）")
        return ("【漏检①】被 dialog_ignores=%r 精确排除，drain 永远看不见"
                % (F["dialog_ignores"],))
    if not t:
        return "【漏检②】无标题，_dialogs 直接跳过"
    hit = [m for m in F["dialog_enter_marks"] if m in t]
    if hit:
        return "【会回车】命中词表 %r（若 CSM 仍未处理则是聚焦/按键问题）" % hit
    if any(m in t for m in F["dialog_log_marks"]):
        return "【仅记录】命中 log 词表，不按键（设计如此）"
    return "【漏检③】未知弹窗：词表未命中，drain 只记日志不按键"


def cmd_win():
    ws = _all_windows()
    print("可见顶层窗口 %d 个（hwnd / 类 / 标题；含无标题与 #32770）：" % len(ws))
    for h, t, c in ws:
        print("  %-9d %-32s %s" % (h, c, t))


def cmd_watch():
    print("实时监测中（Ctrl+C 停，只读绝不按键）。步骤：")
    print("  1. Cubase 打开任一工程，手动改一点内容（工程不脏弹窗不会出）")
    print("  2. 在 Cube Setlist Manager 触发切歌")
    print("  3. 弹窗留着别点，等 CSM 报「切换失败」，把这里新出现的行全部"
          "复制回来（弹窗若被自动按掉一闪而过，也把已有的行复制回来）")
    watch = (F["win_class_prefix"], "#32770")
    seen = set()
    try:
        while True:
            for h, t, c in _all_windows():
                if not any(c.startswith(p) for p in watch):
                    continue
                if (h, t, c) in seen:
                    continue
                seen.add((h, t, c))
                print(time.strftime("[%H:%M:%S] ")
                      + "hwnd=%d 类=%s 标题=「%s」→ %s" % (h, c, t, _classify(c, t)))
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n结束。")


def cmd_selftest():
    W, P = F["win_class_prefix"], F["frame_title"]
    mark = F["dialog_enter_marks"][0]
    same = ("同名候选→样式判别" if F.get("confirm_by_style") else "【漏检①】")
    cases = [
        (W + "xyz", F["title_mark"] + "テスト", "工程窗（正常识别）"),
        (W + "xyz", P, same),               # 保存框与主窗同名=最危险漏法
        (W + "xyz", "", "【漏检②】"),
        (W + "xyz", mark, "【会回车】"),
        (W + "xyz", "工程已被修改", "【漏检③】"),
        ("#32770", "保存", "drain 看不见"),
    ]
    for c, t, want in cases:
        got = _classify(c, t)
        assert got.startswith(want), (c, t, got)
    print("判定树自检通过（%d 例，%s 表）" % (len(cases), F["name"]))


def cmd_attrs(hstr):
    """取证单个窗口的判据属性（只读）。"""
    h = int(hstr)
    if not _u32.IsWindow(h):
        print("hwnd=%d 已不存在" % h)
        return
    owner = _u32.GetWindow(h, 4)                # GW_OWNER
    style = _u32.GetWindowLongW(h, -16) & 0xFFFFFFFF
    ex = _u32.GetWindowLongW(h, -20) & 0xFFFFFFFF
    rect = wintypes.RECT()
    _u32.GetWindowRect(h, ctypes.byref(rect))
    titles = {h2: t2 for h2, t2, _c in _all_windows()}
    cls = ctypes.create_unicode_buffer(64)
    _u32.GetClassNameW(h, cls, 64)
    n = _u32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(h, buf, n + 1)
    print("hwnd=%d 类=%s 标题=「%s」" % (h, cls.value, buf.value))
    print("  owner=%d %s" % (owner or 0,
          ("「%s」" % titles[owner]) if owner in titles else "（无 owner）"))
    print("  style=0x%08X exstyle=0x%08X" % (style, ex))
    print("  rect=(%d,%d)-(%d,%d) %dx%d"
          % (rect.left, rect.top, rect.right, rect.bottom,
             rect.right - rect.left, rect.bottom - rect.top))
    print("  enabled=%d iconic=%d zoomed=%d foreground=%s"
          % (_u32.IsWindowEnabled(h), _u32.IsIconic(h), _u32.IsZoomed(h),
             "是" if _u32.GetForegroundWindow() == h else "否"))


def cmd_kids(hstr):
    """列子窗口（类/样式/文本）——找默认按钮（Button 类 + BS_DEFPUSHBUTTON
    样式位 0x0001）。Steinberg 若自绘按钮则没有 Button 类，回退人眼确认。"""
    h = int(hstr)
    if not _u32.IsWindow(h):
        print("hwnd=%d 已不存在" % h)
        return
    out = []

    @daw_ctrl._ctypes_cb
    def _cb(ch, _l):
        cls = ctypes.create_unicode_buffer(64)
        _u32.GetClassNameW(ch, cls, 64)
        n = _u32.GetWindowTextLengthW(ch)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(ch, buf, n + 1)
        out.append((ch, cls.value,
                    _u32.GetWindowLongW(ch, -16) & 0xFFFFFFFF, buf.value))
        return True

    _u32.EnumChildWindows(h, _cb, 0)
    print("hwnd=%d 子窗口 %d 个：" % (h, len(out)))
    for ch, c, st, t in out:
        dflt = " ←默认按钮" if "Button" in c and st & 0x0001 else ""
        print("  %-9d %-18s style=0x%08X 「%s」%s" % (ch, c, st, t, dflt))


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--s1"]
    cmd = argv[0] if argv else ""
    arg = argv[1] if len(argv) > 1 else None
    if cmd in ("attrs", "kids") and arg:
        (cmd_attrs if cmd == "attrs" else cmd_kids)(arg)
    else:
        {"win": cmd_win, "watch": cmd_watch,
         "selftest": cmd_selftest}.get(cmd, lambda: print(__doc__))()
