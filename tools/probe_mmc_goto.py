# -*- coding: utf-8 -*-
"""MMC GOTO 定位探针（真机取证）：向指定输出口发 MMC GOTO SysEx，同时监听
同口回流的 MTC——位置应跳到目标时码（停止/定位态 DAW 会补发全帧）。
真机前提：DAW 端 Transport→项目同步设置→机器控制勾 MMC Slave Active，
MMC 输入端口=本探针输出口（Device ID 127=响应所有 ID）。
用法：
  py -u tools/probe_mmc_goto.py [输出口子串] [分:秒] [秒数]  # 默认 loopMIDI Port 1:00 15秒
  py -u tools/probe_mmc_goto.py --selftest                  # GOTO 字节离线自检
取证清单：
  ① 发送后 SYSEX 行出现全帧（F0 7F 7F 01 01…）且 POS 跳到目标值
  ② 位置不跳=MMC Slave 未勾/口不对/工程没开（结合 DAW 同步指示器查）
  ③ 播放中发（真机可选）：观察是否立即跳（v1 程序统一先停后定位，此项仅取证）"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import midi_bridge as mb
import points


def _selftest():
    assert points.mmc_goto_bytes(0.0) == (
        b"\xf0\x7f\x7f\x06\x44\x06\x01\x60\x00\x00\x00\x00\xf7")
    assert points.mmc_goto_bytes(3661.5)[7] == 0x61      # 1h + 30fps 码
    assert points.parse_pt_key("pt:A/B#2") == ("A/B", 2)
    print("SELFTEST ✓（GOTO 字节 / key 往返）", flush=True)


def _parse_target(text):
    """分:秒（或 时:分:秒）→ 秒。"""
    parts = [int(x) for x in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
        return
    hint = sys.argv[1] if len(sys.argv) > 1 else mb.PORT_HINT
    target = _parse_target(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    seconds = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    hits = [(i, n) for i, n in mb._out_devices() if hint in n]
    if not hits:
        print("找不到含「%s」的输出端口" % hint, flush=True)
        return
    idx, name = hits[0]
    frame = points.mmc_goto_bytes(target)
    last_pos = []
    dec = mtc_dec = __import__("mtc").Decoder(
        on_qf=lambda: None, on_position=lambda sec: last_pos.append(sec))
    try:
        port = mb.MidiIn(hint, lambda n, v: None,
                         on_mtc=mtc_dec.feed, on_f8=mtc_dec.note_f8,
                         on_sysex=mtc_dec.full_frame)
    except SystemExit as e:
        print(e, flush=True)
        return
    print("=== 输出 %s → GOTO %s（%d 秒观察窗）==="
          % (name, points.fmt_tc(target), seconds), flush=True)
    err = mb.send_sysex(idx, frame)
    print("SENT %s%s" % (frame.hex(" "), "" if err is None
                         else "（失败：%s）" % err), flush=True)
    deadline = time.time() + seconds
    seen = -1
    try:
        while time.time() < deadline:
            if len(last_pos) > seen:
                seen = len(last_pos)
                print("POS %s" % points.fmt_tc(last_pos[-1]), flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    got = last_pos[-1] if last_pos else None
    print("VERDICT: SYSEX_SENT=%s FULLFRAMES=%d LAST_POS=%s TARGET=%s"
          % (err is None, len(last_pos),
             points.fmt_tc(got) if got is not None else "无",
             points.fmt_tc(target)), flush=True)
    if got is not None and abs(got - target) < 1.0:
        print("结论：GOTO 生效——光标已定位到目标时码", flush=True)
    elif got is not None:
        print("结论：有全帧但位置未到目标——检查帧率/时间换算或工程偏移",
              flush=True)
    else:
        print("结论：无全帧回流——MMC Slave 未勾/输入口不对/工程未开/"
              "时码输出未勾（全帧只在停止与定位时补发）", flush=True)


if __name__ == "__main__":
    main()
