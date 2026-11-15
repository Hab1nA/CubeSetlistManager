# -*- coding: utf-8 -*-
r"""Studio One 直控 M0 探针：校准 s1_driver bundle 与命令通道。
真机四步（M0 校准清单）：
  ① deploy 拷 bundle → 重启 S1 →「设置→外部设备」设备列表应出现
    CubeSetlistRemote（列表可见=加载成功；detectorPortName 自动挂载若生效，
    设备已自动出现且已绑口，免手工添加）
  ② 未自动挂载时：手工添加设备并选「CubeSetlist Remote」口
  ③ send 各动作人工确认走带真动（start/stop/rtz/save/close）
  ④ 有失败 → 查 S1 日志与命令名（surface.xml 命令名英文内部名待证，修正后
    同步本探针 selftest 与 tests/test_mr_ctrl 的期望表）
用法：
  py -u tools/probe_s1_ctrl.py selftest     # 离线：bundle↔mr_ctrl 交叉校验
  py -u tools/probe_s1_ctrl.py path         # 打印部署源/目标与口状态
  py -u tools/probe_s1_ctrl.py deploy       # 拷 bundle 到 S1 Devices
  py -u tools/probe_s1_ctrl.py send <动作>  # 真发 CC（生产同路径）
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import mr_ctrl
import mr_deploy

_ACTIONS = ("start", "stop", "rtz", "save", "close")
_SURFACE = (pathlib.Path(__file__).resolve().parents[1] / "s1_driver"
            / "CubeSetlistManager" / "CubeSetlistRemote"
            / "CubeSetlistRemote.surface.xml")

# 期望表：CC → (命令类目, 命令内部名)。命名依据 stock 脚本/surface XML 实证
# （Transport/Start、Transport/Stop、Transport/Return to Zero、File/Save）；
# File/Close 命令名 M0 待证（快捷键表有「关闭」命令，英文内部名未直接实证）。
_EXPECT = {
    mr_ctrl.CC_START: ("Transport", "Start"),
    mr_ctrl.CC_STOP: ("Transport", "Stop"),
    mr_ctrl.CC_RTZ: ("Transport", "Return to Zero"),
    mr_ctrl.CC_SAVE: ("File", "Save"),
    mr_ctrl.CC_CLOSE: ("File", "Close"),
}


def _selftest():
    src = _SURFACE.read_text(encoding="utf-8")
    ctl = dict(re.findall(
        r'<Control name="(\w+)"[^>]*>\s*'
        r'<MidiMessage status="#B0" address="#([0-9A-Fa-f]{2})"/>',
        src))
    cmds = re.findall(
        r'<Command control="(\w+)" command\.category="([^"]+)" '
        r'command\.name="([^"]+)"/>',
        src)
    assert len(ctl) == 5, len(ctl)
    assert len(cmds) == 5, len(cmds)
    got = {int(ctl[name], 16): (cat, cmd) for name, cat, cmd in cmds}
    assert got == _EXPECT, got
    print("SELFTEST OK（s1 surface CC→命令 与 mr_ctrl 常量一致）", flush=True)


def _path():
    src = mr_deploy.s1_driver_src()
    dst = mr_deploy.s1_dst()
    print("源（随包 bundle）：%s（在=%s）" % (src, src.is_dir()))
    print("目标（S1 用户 Devices）：%s（在=%s）" % (dst, dst.exists()))
    print("loopMIDI 口「%s」在=%s" % (mr_deploy.PORT_NAME, mr_deploy.port_ok()))


def _deploy():
    msg = mr_deploy.deploy_s1_driver()
    sub = "" if msg else ("；下一步：重启 S1 →「设置→外部设备」确认/添加设备绑口"
                          " → send 各动作人工确认")
    print("deploy：%s%s" % (msg or "成功", sub))


def _send(action):
    if action not in _ACTIONS:
        print("未知动作 %r（可选：%s）" % (action, "/".join(_ACTIONS)))
        return
    err = mr_ctrl.send(action)
    print("SENT %s %s" % (action.upper(), "OK" if err is None
                          else "失败：%s" % err))

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "selftest":
        _selftest()
    elif cmd in ("path", "deploy"):
        {"path": _path, "deploy": _deploy}[cmd]()
    elif cmd == "send" and arg:
        _send(arg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
