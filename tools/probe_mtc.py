# -*- coding: utf-8 -*-
"""MTC 时码探针（真机取证）：被动监听指定 MIDI 输入口，实时打印四分之一帧
（类型/半字节）、解码位置、全帧十六进制与 F8 计数——验证 DAW 的 MTC 输出
接线与全帧行为（probe_ax09 范式；longdata 缓冲池由 MidiIn 按 on_sysex 自动
挂载，本探针即其真机验证）。
用法：
  py -u tools/probe_mtc.py [端口名子串] [秒数]   # 默认 loopMIDI Port 60 秒
  py -u tools/probe_mtc.py --selftest            # 合成序列离线自检
取证清单（真机跑时逐项核对输出）：
  ① 播放中 QF 行是否流动、类型序 0→7 循环、位置是否跟播放头（拖头/定位应立即跟）
  ② 停止/定位时 SYSEX 行是否出现、投递是否完整（一条含 F0…F7）
  ③ 暂停/停止时 QF 是否断流（断流=三态判「已暂停」的依据）
  ④ 只见 F8 增长=工程只开了 MIDI 时钟没开 MTC 时码（去同步设置勾时码输出）"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import midi_bridge as mb
import mtc

_TYPE_NAMES = ("帧低", "帧高", "秒低", "秒高", "分低", "分高", "时低", "时率")


def _selftest():
    """合成序列先行：QF 往返 + 全帧解析 + 乱序弃组（零设备依赖）。"""
    pos = []
    d = mtc.Decoder(on_qf=lambda: None, on_position=pos.append)
    for b in mtc.qf_bytes(0, 1, 2, 15, rate=3):
        d.feed(b)
    assert abs(pos[0] - (62 + 0.5)) < 1e-9, pos          # 1:02 + 15/30
    assert mtc.parse_full_frame(
        b"\xf0\x7f\x7f\x01\x01\x61\x00\x00\x10\xf7") == 3600 + 16 / 30
    for b in mtc.qf_bytes(0, 0, 9, 0)[3:]:
        d.feed(b)                                        # 孤条弃组
    d.feed(0x70)                                         # 垃圾字节不炸
    print("SELFTEST ✓（QF 往返 / 全帧解析 / 乱序弃组）", flush=True)


def _fmt(sec):
    return "%d:%06.3f" % (int(sec) // 60, sec % 60)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
        return
    hint = sys.argv[1] if len(sys.argv) > 1 else mb.PORT_HINT
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    stats = {"qf": 0, "sysex": 0, "last_pos": None}
    types = [0] * 8                 # 各类型 QF 计数（乱序/缺型一眼可见）

    def on_pos(sec):
        stats["last_pos"] = sec
        print("POS %s" % _fmt(sec), flush=True)

    dec = mtc.Decoder(on_qf=lambda: None, on_position=on_pos)

    def on_mtc_byte(data1):
        # 原始字节在这层统计/抽样（解码器的 on_qf 无参，只做活跃证明——
        # 签名不匹配会被 _emit 吞掉，正是「回调签名错位零痕迹」教训的现场）
        stats["qf"] += 1
        types[(data1 >> 4) & 7] += 1
        if (data1 >> 4) & 7 == 0:   # 每轮装配起点打一行，避免 120 行/秒刷屏
            print("QF  cycle#%d" % stats["qf"], flush=True)

    def on_sysex(data):
        stats["sysex"] += 1
        print("SYSEX %s%s" % (data[:16].hex(" "),
                              "…" if len(data) > 16 else ""), flush=True)
        dec.full_frame(data)            # 打印之外照常喂解码器

    try:
        port = mb.MidiIn(hint, lambda n, v: print("NOTE %d vel %d" % (n, v),
                                                  flush=True),
                         on_mtc=on_mtc_byte, on_f8=dec.note_f8,
                         on_sysex=on_sysex)
    except SystemExit as e:
        print(e, flush=True)
        return
    print("=== 监听 %s（%d 秒）：在 DAW 里播放/暂停/定位/停止 ==="
          % (port.name, seconds), flush=True)
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    print("=== 汇总 ===", flush=True)
    print("VERDICT: QF=%d SYSEX=%d F8=%d LAST_POS=%s TYPES=%s"
          % (stats["qf"], stats["sysex"], dec.f8_seen,
             _fmt(stats["last_pos"]) if stats["last_pos"] is not None
             else "无", types), flush=True)
    if stats["qf"]:
        print("结论：时码在流——QF 断流判暂停、位置即进度", flush=True)
    elif dec.f8_seen:
        print("结论：只有 MIDI 时钟（F8）没有时码——去工程同步设置勾选 "
              "MTC 时码输出到该端口", flush=True)
    else:
        print("结论：端口上没有任何走带消息——检查工程输出接线/播放状态",
              flush=True)


if __name__ == "__main__":
    main()
