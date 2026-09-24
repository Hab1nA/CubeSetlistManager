# -*- coding: utf-8 -*-
"""E2E 真机测试驱动：分阶段验证 Cube Setlist Manager 的各链路（模块级直跑，
与 GUI 调的是同一份代码；GUI 本身是薄封装）。
用法：py -u e2e_test.py <phase>   phase =
  preflight  环境盘点：MIDI 端口 / OBS / Cubase 进程与窗口 / JUNO 缺席降级
  ports      双端口隔离回归：loopMIDI Port 与 Keyboard Automation 各自收对音符
  obs        OBS 联动：连接→播 1.mp4→查状态→熄屏
  switch     Cubase 切歌全流程（未运行则冷启动；在运行则先关后开）并转储窗口
  transport  走带按键 E2E：时钟监听验证 播放/停止 真生效
  advance    自动推进全周期：播完最短歌→自动切下一首（需项目时钟，约 3 分钟）
不带参数 = 顺序跑 preflight ports obs switch transport kb（advance 单独跑）。"""
import glob
import os
import sys
import time

import advance
import cpr_meta
import cubase_ctrl
import kbd_auto
import midi_bridge as mb
import obs_ctrl
from obs_ctrl import ObsController, find_processes_by_prefix

# 库根：默认本机路径，换机用环境变量 CUBE_PROJECTS_ROOT 覆盖
ROOT = os.environ.get("CUBE_PROJECTS_ROOT") or r"C:\Users\XKZ\Documents\Cubase Projects"


def _pick_songs(root):
    """扫 <root>/<队伍>/<歌>/<歌>.cpr，按时长升序取最短两首当 SONG_A/SONG_B
    （advance 阶段依赖最短歌跑全周期；时长未知的歌排最后）。"""
    songs = []
    for p in glob.glob(os.path.join(root, "*", "*", "*.cpr")):
        if os.path.splitext(os.path.basename(p))[0] != os.path.basename(os.path.dirname(p)):
            continue    # 只认 <歌>/<歌>.cpr 命名（与素材库扫描同规则）
        try:
            dur = cpr_meta.read_duration(p)
        except Exception:
            dur = None
        songs.append((dur if dur is not None else 1 << 30, p))
    songs.sort()
    if len(songs) < 2:
        sys.exit("工程库 %s 下可用歌不足两首（<队伍>/<歌>/<歌>.cpr）" % root)
    return songs[0][1], songs[1][1]


SONG_A, SONG_B = _pick_songs(ROOT)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def wait_until(fn, timeout, desc, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    log("TIMEOUT（%.0fs）：%s" % (timeout, desc))
    return None


def dump_windows(tag):
    log("窗口转储（%s）：" % tag)
    for h, t, c in cubase_ctrl._windows():
        if c.startswith("Steinberg") or "Cubase" in t or "Steinberg" in t:
            log("  class=%-28s title=%r" % (c, t))


def midi_out_by_name(hint, note):
    hits = [(i, n) for i, n in mb._out_devices() if hint in n]
    if not hits:
        return "未找到输出端口 %s" % hint
    mb.send_note(hits[0][0], note)
    return None


def cfg_cubase():
    import setlist_gui
    return setlist_gui._load_config()


def make_ctrl():
    ccfg = cfg_cubase().get("cubase", {})
    return cubase_ctrl.CubaseController(
        ccfg.get("cubaseExe", cubase_ctrl.PROC_PREFIX), log=log)


# ---- 阶段 ----

def p_preflight():
    log("MIDI 输入端口：%s" % [n for _, n in mb._in_devices()])
    log("MIDI 输出端口：%s" % [n for _, n in mb._out_devices()])
    ins = [n for _, n in mb._in_devices()]
    assert any("loopMIDI Port" in n for n in ins), "缺 loopMIDI Port"
    assert any(kbd_auto.KB_PORT_HINT in n for n in ins), "缺 Keyboard Automation"
    assert not any("JUNO" in n for n in ins), "JUNO 不该在场（题目说没连）"
    log("端口前提 ✓（JUNO 确认缺席）")
    log("OBS 进程：%s" % find_processes_by_prefix("obs64"))
    log("Cubase 进程：%s" % find_processes_by_prefix("cubase"))
    dump_windows("当前")
    log("JUNO 缺席降级：%s" % kbd_auto.send_slot(
        {"msb": 85, "lsb": 64, "pc": 3}, dict(kbd_auto.DEFAULT_JUNO)))
    log("时长解析 intro=%s TAIDADA=%s" % (
        cpr_meta.fmt_mmss(cpr_meta.read_duration(SONG_A)),
        cpr_meta.fmt_mmss(cpr_meta.read_duration(SONG_B))))


def p_ports():
    got = []
    vj = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                            got.append(("vj", d1)) if s & 0xF0 == 0x90 else None)
    kb = kbd_auto.RawMidiIn(kbd_auto.KB_PORT_HINT, lambda s, d1, d2:
                            got.append(("kb", d1)) if s & 0xF0 == 0x90 else None)
    time.sleep(0.3)
    err = midi_out_by_name("loopMIDI Port", 60)
    assert not err, err
    time.sleep(0.5)
    err = midi_out_by_name(kbd_auto.KB_PORT_HINT, 61)
    assert not err, err
    time.sleep(0.5)
    vj.close()
    kb.close()
    log("收到：%s" % got)
    assert ("vj", 60) in got, "VJ 端口没收到 note60"
    assert ("kb", 61) in got, "KB 端口没收到 note61"
    assert ("kb", 60) not in got and ("vj", 61) not in got, "端口串了！"
    log("双端口隔离 ✓")


def p_obs():
    ctl = ObsController(cfg_cubase()["obs"])
    ctl.enabled = True
    ctl.start()
    assert wait_until(ctl.is_connected, 40, "OBS 连接"), "OBS 连不上"
    ctl.set_media("1.mp4", False)
    st = wait_until(lambda: ctl.media_state() == "OBS_MEDIA_STATE_PLAYING",
                    8, "1.mp4 起播")
    assert st, "OBS 媒体没在播"
    ctl.stop_media()
    time.sleep(0.5)
    log("熄屏后状态：%s" % ctl.media_state())
    ctl.shutdown()
    log("OBS 联动 ✓")


def p_switch():
    ctrl = make_ctrl()
    log("切换前 Cubase 进程：%s" % find_processes_by_prefix("cubase"))
    t0 = time.time()
    ctrl.switch_to(SONG_A, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "switch_to 完成")
    ws = cubase_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    dump_windows("冷启动/切换后")
    assert ws and any("intro" in t for _, t in ws), "intro 没打开：%r" % (ws,)
    log("第二次切换（先关后开路径）：%s" % SONG_B)
    t0 = time.time()
    ctrl.switch_to(SONG_B, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "第二次切换")
    ws = cubase_ctrl.project_windows()
    log("耗时 %.1fs，工程窗口：%s" % (time.time() - t0, [t for _, t in ws]))
    dump_windows("第二次切换后")
    assert ws and any("TAIDADA" in t for _, t in ws), "TAIDADA 没打开：%r" % (ws,)
    log("切歌 E2E ✓")


def p_transport():
    ctrl = make_ctrl()
    target = os.path.join(ROOT, "霓虹折叠", "優しい彗星", "優しい彗星.cpr")
    if not cubase_ctrl.project_windows() or \
            "優しい彗星" not in (cubase_ctrl.project_windows()[0][1] or ""):
        log("先切到 優しい彗星（有 VJ 触发轨/时钟配置的真实演出工程）…")
        ctrl.switch_to(target, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 優しい彗星")
    pulses = [0]
    notes = [0]

    port = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                              (pulses.__setitem__(0, pulses[0] + 1),
                               notes.__setitem__(0, notes[0] + 1))
                              if s in (0xF1, 0xF8)
                              else notes.__setitem__(0, notes[0] + 1)
                              if s & 0xF0 == 0x90 else None)
    time.sleep(0.3)
    base_p, base_n = pulses[0], notes[0]
    ctrl.transport("play")
    wait_until(lambda: pulses[0] > base_p + 10 or notes[0] > base_n + 3,
               15, "播放产生时钟/音符")
    log("15s 窗口：时钟脉冲 %+d，音符 %+d" % (
        pulses[0] - base_p, notes[0] - base_n))
    if pulses[0] > base_p + 10:
        n = pulses[0]
        time.sleep(1.0)
        ctrl.transport("stop")
        time.sleep(2.0)
        log("停止后 2 秒新增脉冲=%d" % (pulses[0] - n))
        assert pulses[0] - n < 5, "停止后时钟还在走"
        log("走带 E2E ✓（时钟验证）")
    elif notes[0] > base_n + 3:
        log("走带 E2E ✓（音符触发验证：播放确已启动；此工程未发时钟）")
    else:
        log("走带键 15s 内无任何 MIDI 输出——键可能没生效或工程无 loopMIDI 输出")
    port.close()


def p_advance():
    ctrl = make_ctrl()
    if not cubase_ctrl.project_windows() or \
            "intro" not in (cubase_ctrl.project_windows()[0][1] or ""):
        log("先切到 intro…")
        ctrl.switch_to(SONG_A, on_done=lambda n: log("on_done → %s" % n))
        wait_until(lambda: not ctrl.busy, 200, "切到 intro")
    dur = cpr_meta.read_duration(SONG_A)
    log("intro 时长 %.1fs，开始自动推进全周期" % dur)
    fired = []
    watch = advance.AdvanceWatch(lambda: fired.append(1), on_event=log,
                                 clock_timeout=mb.CLOCK_TIMEOUT)
    watch.set_armed(True)
    watch.set_duration(dur)
    pulses = [0]
    port = kbd_auto.RawMidiIn("loopMIDI Port", lambda s, d1, d2:
                              (watch.on_clock(),
                               pulses.__setitem__(0, pulses[0] + 1))
                              if s in (0xF1, 0xF8) else None)
    ctrl.transport("play")
    deadline = time.time() + dur + 90
    while time.time() < deadline and not fired:
        watch.poll()
        time.sleep(mb.POLL_SEC)
    port.close()
    log("结果：fired=%s 活跃 %.1fs / 时长 %.1fs / 脉冲 %d" % (
        bool(fired), watch._active, dur, pulses[0]))
    if not fired:
        log("未触发——若脉冲仍在走，说明 Cubase 播完没有自动停止（设计假设被推翻）")
        ctrl.panic()
        return
    log("播完自动触发 ✓，等待自动切换到下一首…")
    ctrl.switch_to(SONG_B, on_done=lambda n: log("on_done → %s" % n))
    wait_until(lambda: not ctrl.busy, 200, "推进后的切换")
    ws = cubase_ctrl.project_windows()
    log("推进后工程窗口：%s" % [t for _, t in ws])
    dump_windows("自动推进后")
    assert ws and "TAIDADA" in ws[0][1]
    log("自动推进全周期 ✓")


PHASES = dict(preflight=p_preflight, ports=p_ports, obs=p_obs,
              switch=p_switch, transport=p_transport, advance=p_advance)

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        PHASES[arg]()
    else:
        for name in ("preflight", "ports", "obs", "switch", "transport"):
            log("==== 阶段：%s ====" % name)
            PHASES[name]()
    log("E2E 完成")
