# -*- coding: utf-8 -*-
"""MIDI Remote 直控探针（真机取证）：向「CubeSetlist Remote」口发动作 CC
序列（生产同路径 mr_ctrl.send：press127+release0），同时监听时码口 MTC——
play/resume 后位置推进、stop/pause 后冻结、rewind 后归零、close 后 QF 停。
真机前提（docs/MIDIRemote直控部署.md 三步全做）：loopMIDI 建口「CubeSetlist
Remote」；driver 已在 Cubase MIDI Remote Manager 里映射到该口；工程勾 MTC
时码输出（MTC 监听=时钟口，同 App 接线）。
用法：
  py -u tools/probe_mr_ctrl.py [动作] [秒数] [MR口子串]  # 默认 play 12 CubeSetlist Remote
  py -u tools/probe_mr_ctrl.py --selftest                # JS↔Python CC 表离线自检
取证清单：
  ① play/resume：SENT 后 POS 持续推进（QF 流活跃）
  ② stop/pause：POS 冻结不再变（停止态补发全帧后静止）
  ③ rewind：POS 跳 0；close：QF 流停止（工程关=MTC 断流）
  ④ 全程无 POS=时码前提没满足；有 POS 但动作无效=driver 未映射/未受理"""
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import midi_bridge as mb
import mr_ctrl
import mtc

ACTIONS = ("play", "stop", "pause", "resume", "rewind", "close")
_JS = (pathlib.Path(__file__).resolve().parents[1] / "mr_driver" / "Local"
       / "CubeSetlistManager" / "CubeSetlistRemote"
       / "CubeSetlistManager_CubeSetlistRemote.js")


def _selftest():
    src = _JS.read_text(encoding="utf-8")
    table = dict(kv.split("=") for kv in
                 re.search(r"CC-TABLE:\s*(.+)", src).group(1).split())
    cc = {"start": mr_ctrl.CC_START, "stop": mr_ctrl.CC_STOP,
          "rtz": mr_ctrl.CC_RTZ, "save": mr_ctrl.CC_SAVE,
          "close": mr_ctrl.CC_CLOSE}
    assert {k: int(v) for k, v in table.items()} == cc
    assert re.findall(
        r"makeCommandBinding\(\w+\.mSurfaceValue,\s*'([^']+)',\s*'([^']+)'\)",
        src) == [("Transport", "Start"), ("Transport", "Stop"),
                 ("Transport", "Return to Zero"), ("File", "Save"),
                 ("File", "Close")]
    print("SELFTEST ✓（driver JS CC-TABLE/命令绑定 ↔ mr_ctrl 常量一致）",
          flush=True)


def _fmt(sec):
    return "--:--" if sec is None else "%02d:%05.2f" % (int(sec // 60),
                                                        sec % 60)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
        return
    action = (sys.argv[1] if len(sys.argv) > 1 else "play").lower()
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    hint = sys.argv[3] if len(sys.argv) > 3 else mr_ctrl.PORT_HINT_DEFAULT
    if action not in ACTIONS:
        print("未知动作 %r（可选：%s）" % (action, "/".join(ACTIONS)), flush=True)
        return
    mr_ctrl.set_port_hint(hint)

    pos = []
    qf = [0]
    dec = mtc.Decoder(on_qf=lambda: qf.__setitem__(0, qf[0] + 1),
                      on_position=pos.append)
    try:
        port = mb.MidiIn(mb.PORT_HINT, lambda n, v: None,
                         on_mtc=dec.feed, on_f8=dec.note_f8,
                         on_sysex=dec.full_frame)
    except SystemExit as e:
        print("MTC 监听未启动：%s（探针需时码口在线）" % e, flush=True)
        return
    print("=== MR 直控 %s（%d 秒观察窗，CC 序列 %s）==="
          % (action, seconds, mr_ctrl.SEQ[action]), flush=True)
    time.sleep(1.0)     # 等 IN 口 SysEx 缓冲池挂稳
    n_before = len(pos)
    err = mr_ctrl.send(action)
    print("SENT %s%s" % (action.upper(), "" if err is None
                         else "（失败：%s）" % err), flush=True)
    if err:
        port.close()
        return
    deadline = time.time() + seconds
    seen = n_before
    try:
        while time.time() < deadline:
            if len(pos) > seen:
                seen = len(pos)
                print("POS %s" % _fmt(pos[-1]), flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    after = pos[n_before:]
    base = pos[n_before - 1] if n_before else None
    last = after[-1] if after else base
    print("VERDICT: QF=%d POS_BASE=%s POS_LAST=%s POS_SAMPLES=%d"
          % (qf[0], _fmt(base), _fmt(last), len(after)), flush=True)
    if not pos:
        print("结论：无 MTC 回流——工程未开/时码输出未勾/时钟口不对"
              "（先跑通 probe_mmc_goto 再来）", flush=True)
    elif action in ("play", "resume"):
        if after and last > (after[0] if after[0] is not None else 0):
            print("结论：直控生效——位置推进中", flush=True)
        else:
            print("结论：位置未推进——检查动作前走带态（已停应先 play）/driver"
                  " 未映射", flush=True)
    elif action in ("stop", "pause"):
        uniq = len({round(p, 2) for p in after})
        print("结论：%s——停止后只补全帧（样本少且终值冻结）"
              % ("疑似生效" if uniq <= 2 else "位置仍在变（可能没停住）"),
              flush=True)
    elif action == "rewind":
        print("结论：%s" % ("生效——已归零" if last is not None and last < 1.0
                            else "未归零（终值 %s）" % _fmt(last)), flush=True)
    else:   # close：工程关=MTC 断流，QF 不再涨即证据
        print("结论：%s" % ("疑似生效——观察窗内 QF 已停" if qf[0] == 0
                            else "QF 仍在涨——工程可能没关（看 Cubase 窗口）"),
              flush=True)


if __name__ == "__main__":
    main()
