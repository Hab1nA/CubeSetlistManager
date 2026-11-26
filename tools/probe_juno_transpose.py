# -*- coding: utf-8 -*-
"""JUNO-DS 面板移调（带小灯）MIDI 可驱动性真机探针——移调路由闸门。
背景：软件现行移调走 GM Master Coarse Tuning 写 SYSTEM:Master Key Shift，面板
不显示、无法在琴上手动复位。官方 MIDI Imple p21 Setup 块另有面板同款两项：
  01 00 00 12  Transpose Value  59-70 即 -5..+6 半音 （[TRANSPOSE] 键小灯）
  01 00 00 13  Octave Shift     61-67 即 -3..+3 八度 （OCTAVE 键小灯）
面板移调作用于手弹音，电脑发的音验证不了——每步都要观察者看小灯、在琴上
按键听音高后作答。一步一发：回车才发，答完再走下一步。
  ① 基线：现行 Master Key Shift +2（预期小灯不亮、电脑发的音也升高）
  ② Transpose Value +2      ③ Octave Shift +1
  ④ 单条两字节写回 (64,64)  ⑤ Transpose +2 后切音色，移调还在不在
  ⑥ Master Key Shift 断电后记不记得   ⑦ Transpose 断电后记不记得
收尾一律三项复位。琴保持 PATCH 模式；跑之前先关本软件主程序（它会往同一个
输出口发音色/移调，与探针互相覆盖）。
用法：py -u tools/probe_juno_transpose.py [输出端口名子串，默认 JUNO]
监听模式（Cubase 补发取证，不向琴发任何东西）：
  py -u tools/probe_juno_transpose.py --monitor [秒数，默认 120]
  监听「Keyboard Automation」输入口，打印到达的移调控制器与音符及其时刻。"""
import pathlib
import queue
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import kbd_auto
import midi_bridge as mb

DEV = 0x10                     # JUNO 设备 ID（与软件默认 deviceId 一致）
CH = 0                         # patchCh 1 → 0 基
ADDR_TRANSPOSE = (0x01, 0x00, 0x00, 0x12)
ADDR_OCTAVE = (0x01, 0x00, 0x00, 0x13)
NEUTRAL = 64                   # 两项的原调值


def dt1(addr, data, dev=DEV):
    """Roland DT1 写一个地址的 1..N 字节数据（校验和与 mode_sysex 同式）。"""
    body = bytes(addr) + bytes(data)
    ck = (0x80 - (sum(body) & 0x7F)) & 0x7F
    return bytes([0xF0, 0x41, dev, 0x00, 0x00, 0x3A, 0x12]) + body \
        + bytes([ck, 0xF7])


def _selftest():
    # 与软件里久经验证的模式切换消息逐字节一致：证明地址/校验和格式没写错
    for mode in range(5):
        assert dt1((0x01, 0x00, 0x00, 0x00), [mode]) \
            == kbd_auto.mode_sysex(mode, DEV), mode
    both = dt1(ADDR_TRANSPOSE, [NEUTRAL + 2, NEUTRAL + 1])
    assert both[:12] == bytes([0xF0, 0x41, DEV, 0, 0, 0x3A, 0x12,
                               0x01, 0x00, 0x00, 0x12, 0x42])
    assert both[-2] == (0x80 - ((1 + 0x12 + 0x42 + 0x41) & 0x7F)) & 0x7F
    print("selftest OK")


def _word(t):
    return t[0] | t[1] << 8 | t[2] << 16


def play(h, note=60, sec=1.2):
    """电脑发一个 C4：面板移调不作用于它，仅 ①⑥ 两步用来听整体音高。"""
    mb._winmm.midiOutShortMsg(h, _word((0x90 | CH, note, 100)))
    time.sleep(sec)
    mb._winmm.midiOutShortMsg(h, _word((0x80 | CH, note, 0)))


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def step(n, title, expect, fn):
    print("\n=== %s %s" % (n, title), flush=True)
    print("    预期：%s" % expect, flush=True)
    ask("    回车发送…")
    err = fn()
    print("    已发送%s" % ("，失败：%s" % err if err else ""), flush=True)
    r = ask("    结果（小灯亮不亮 / 琴上按键音高，可留空）：")
    return r


def reset_all(h):
    """三项回原调：Transpose/Octave 单条两字节 + Master Key Shift 0x40。"""
    e1 = mb.send_sysex_h(h, dt1(ADDR_TRANSPOSE, [NEUTRAL, NEUTRAL]))
    e2 = mb.send_sysex_h(h, kbd_auto.shift_msgs(0)[0][1])
    return e1 or e2


def probe(hint):
    hits = [(i, n) for i, n in mb._out_devices() if hint in n]
    assert hits, "未找到含「%s」的 MIDI 输出端口" % hint
    print("输出端口：%s" % hits[0][1], flush=True)
    h, err = mb.open_out(hits[0][0])
    assert h is not None, "midiOutOpen 失败：%s" % err
    notes = []
    try:
        sx = lambda data: (lambda e: e)(mb.send_sysex_h(h, data))
        short = lambda msgs: [mb._winmm.midiOutShortMsg(h, _word(m))
                              for m in msgs]
        pc = lambda msb, lsb, pc_: short(
            [(0xB0 | CH, 0, msb), (0xB0 | CH, 32, lsb), (0xC0 | CH, pc_, 0)])

        notes.append(step("①", "基线：现行路由 Master Key Shift +2",
                          "两个小灯都不亮；电脑发的 C4 升高 2 个半音",
                          lambda: (sx(kbd_auto.shift_msgs(2)[0][1]), play(h))))
        notes.append(step("②", "面板移调 Transpose Value +2（Setup 01 00 00 12）",
                          "[TRANSPOSE] 小灯亮、屏幕显示 +2；琴上按键升 2 个半音",
                          lambda: (sx(dt1(ADDR_TRANSPOSE, [NEUTRAL + 2])),
                                   play(h))))
        notes.append(step("③", "八度 Octave Shift +1（Setup 01 00 00 13）",
                          "OCTAVE [UP] 小灯亮；琴上按键再升 1 个八度",
                          lambda: sx(dt1(ADDR_OCTAVE, [NEUTRAL + 1]))))
        notes.append(step("④", "单条两字节写回原调 (64, 64)",
                          "两个小灯都灭；琴上按键回原调",
                          lambda: sx(dt1(ADDR_TRANSPOSE, [NEUTRAL, NEUTRAL]))))
        notes.append(step("⑤", "Transpose +2 后切一次音色（PRST:0001）",
                          "切音色后小灯仍亮、移调仍在",
                          lambda: (sx(dt1(ADDR_TRANSPOSE, [NEUTRAL + 2])),
                                   time.sleep(0.5), pc(87, 64, 0))))
        notes.append(step("⑥", "Master Key Shift +2：断电重启后记不记得",
                          "下面先发 +2，再给琴断电重启；重启后回车发一个 C4",
                          lambda: sx(kbd_auto.shift_msgs(2)[0][1])))
        ask("    现在给 JUNO 断电、开机，等它完全启动后回车（发一个 C4）…")
        play(h)
        notes.append(ask("    结果：C4 音高正常=不记忆；升高 2 个半音=写进了"
                         "琴的存储："))
        notes.append(step("⑦", "Transpose +2：断电重启后记不记得",
                          "下面先发 +2，再给琴断电重启",
                          lambda: sx(dt1(ADDR_TRANSPOSE, [NEUTRAL + 2]))))
        ask("    现在给 JUNO 断电、开机，等它完全启动后回车…")
        notes.append(ask("    结果：[TRANSPOSE] 小灯灭=不记忆；仍亮=写进了"
                         "存储："))
    finally:
        err = reset_all(h)
        print("\n收尾：三项已复位%s" % ("，失败：%s" % err if err else ""),
              flush=True)
        mb.close_out(h)
    print("\n==== 记录（按 ①–⑦）====")
    for i, r in enumerate(notes):
        print("  %s %s" % ("①②③④⑤⑥⑦"[i], r or "（未填）"))


def monitor(sec):
    """只听不发：打印 Cubase 经触发轨送来的移调控制器/音符与到达时刻。"""
    q = queue.Queue()
    t0 = time.time()

    def on_msg(status, d1, d2):
        q.put((time.time() - t0, status & 0xF0, d1, d2))

    port = kbd_auto.RawMidiIn(kbd_auto.KB_PORT_HINT, on_msg)
    print("监听「%s」→ %s，%d 秒。现在到 Cubase 里依次做：" % (
        kbd_auto.KB_PORT_HINT, port.name, sec), flush=True)
    print("  1 停止态把光标拖进移调段      应出现 CC16=<目标值>")
    print("  2 停止态拖到移调段之外        应出现 CC16=64")
    print("  3 光标停在移调段内，按播放     看是否再出现一次 CC16")
    print("  4 播放中停止，原地再按播放     同上")
    print("  5 全停（本软件 Shift+空格）后原地按播放   同上（决定全停归零"
          "能否自动恢复）")
    n = 0
    try:
        end = time.time() + sec
        while time.time() < end:
            try:
                t, st, d1, d2 = q.get(timeout=0.5)
            except queue.Empty:
                continue
            n += 1
            if st == 0xB0 and d1 in (kbd_auto.JUNO_SHIFT_CC,
                                     kbd_auto.AX_SHIFT_CC):
                tag = "JUNO " if d1 == kbd_auto.JUNO_SHIFT_CC else "AX-09"
                print("+%6.2fs  CC%-2d=%-3d  %s %+d 半音" % (
                    t, d1, d2, tag, d2 - kbd_auto.SHIFT_CC_ZERO),
                    flush=True)
            elif st in (0x90, 0x80) and (d1 in kbd_auto.SHIFT_NOTES
                                         or d1 in kbd_auto.AX_SHIFT_NOTES):
                print("+%6.2fs  %s %s" % (
                    t, kbd_auto.note_name(d1),
                    "按" if st == 0x90 and d2 else "抬"), flush=True)
            elif st in (0x90, 0x80) and st == 0x90 and d2:
                print("+%6.2fs  音符 %s 按下" % (t, kbd_auto.note_name(d1)),
                      flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    print("结束：共 %d 条消息" % n, flush=True)


def main():
    _selftest()
    args = [a for a in sys.argv[1:]]
    if args and args[0] == "--monitor":
        monitor(int(args[1]) if len(args) > 1 else 120)
        return
    probe(args[0] if args else "JUNO")


if __name__ == "__main__":
    main()
