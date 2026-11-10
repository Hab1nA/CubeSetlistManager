# -*- coding: utf-8 -*-
"""MIM_CLOSE 真链路集成探针：用本机真实 loopMIDI 端口验证 MidiIn 的
MIM_CLOSE→dead 旗标链路——物理拔线走的就是这条系统通知路径。
做法：建 MidiIn 后绕过 MidiIn.close()（那是主动关，会先置 _closing、走
专线收尾），直接底层 winmm 三连 Stop→Reset→Close 关掉句柄——此刻
_closing 未置位，MidiIn 收到的 MIM_CLOSE 通知来自真关闭事件，会把 dead
置 True。3 秒内 dead 翻真=链路通；超时=链路断（见失败提示）。
注意：运行前须关闭主程序——主程序开着会占住同名端口，midiInOpen 会失败。
结尾重建同名新口恢复现场。全程无网络无文件写入，纯本地 MIDI 设施。
独立工具不进测试套件。
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("tools", 1)[0])
import midi_bridge as mb          # noqa: E402

PRIORITY = ("VJ Automation", "Keyboard Automation", "Score Automation",
            "CubeSetlist Remote")    # 依次找本机已存在的 loopMIDI 口
WAIT_SEC = 3.0                     # 等 MIM_CLOSE 送达的窗口
STEP = 0.05                        # 轮询步进


def main():
    ins = mb._in_devices()
    hint = next((h for h in PRIORITY if any(h in n for _, n in ins)), None)
    if hint is None:
        sys.exit("本机没有可用的 loopMIDI 端口（找过：%s；现有输入口：%s）。"
                 "请先在 loopMIDI 建一个虚拟端口"
                 % ("、".join(PRIORITY),
                    "、".join(n for _, n in ins) or "无"))
    print("运行前须关闭主程序（它开着会占住 %r，midiInOpen 会失败）" % hint,
          flush=True)
    port = mb.MidiIn(hint, lambda n, v: None)
    assert port.dead is False, "MidiIn 初始 dead 应为 False"
    print("已开 %r，绕过 close() 直接底层关句柄…" % hint, flush=True)
    h = port._h
    mb._winmm.midiInStop(h)
    mb._winmm.midiInReset(h)
    mb._winmm.midiInClose(h)     # 非主动关：_closing 未置位，MIM_CLOSE 必达
    deadline = time.time() + WAIT_SEC
    while time.time() < deadline and not port.dead:
        time.sleep(STEP)
    if not port.dead:
        print("%.0f 秒内未收到 MIM_CLOSE（可能：_dispatch 没挂 MIM_CLOSE 分支/"
              "消息号口径不符/dead 旗标没接进通知路径）" % WAIT_SEC, flush=True)
        sys.exit(1)
    print("MIM_CLOSE 已送达，dead 旗标置位（物理拔线同路径）", flush=True)
    reborn = mb.MidiIn(hint, lambda n, v: None)  # 重建同名新口恢复现场
    print("现场已恢复：重建新口 %r（name=%s）" % (hint, reborn.name), flush=True)


if __name__ == "__main__":
    main()
