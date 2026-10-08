# -*- coding: utf-8 -*-
"""BLE MIDI（WinRT）链路探针：枚举/打开/收发逐级验证，真机 BLE 验收载体。

用法：
  py -u tools/probe_winrt_ble.py                # 只枚举（只读，无打开）
  py -u tools/probe_winrt_ble.py --open         # 枚举 + 打开全部端点再关闭
  py -u tools/probe_winrt_ble.py --send 60      # 打开 + 向第一个 BLE 输出口
                                                #   发 Note On（真机听琴验证）

前提：py -m pip install winrt-runtime winrt-Windows.Foundation
      winrt-Windows.Foundation.Collections winrt-Windows.Devices.Enumeration
      winrt-Windows.Devices.Midi winrt-Windows.Storage.Streams

验证口径（2026-10-09 PoC 定案）：
- 本机无活体 BLE MIDI 琴时，枚举应列出已配对的 BLE 端点（如 TurnerPro 对）
  ——枚举层回归基准；TurnerPro GATT 不应答，--open 对它超时属预期。
- 真机收发验收（WIDI U-Host/Bud Pro 到货后）：配对 → --open 应秒开 →
  琴上按键 --send 前先挂输入监听观察回包。
只读安全：不打开任何 winmm 口、不碰 loopMIDI；--send 只打 BLE 口。
"""
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import midi_ble

argv = sys.argv[1:]
do_open = "--open" in argv or "--send" in argv
send_note = None
if "--send" in argv:
    i = argv.index("--send")
    send_note = int(argv[i + 1]) if i + 1 < len(argv) else 60

print("winrt 可用：%s" % ("是" if midi_ble._HAS_WINRT else
                          "否（pip install winrt-runtime …，见文件头）"))
if not midi_ble._HAS_WINRT:
    sys.exit(2)

ok = True
for side, label in (("in", "输入"), ("out", "输出")):
    try:
        devs = midi_ble.scan(side)
    except Exception as e:
        print("枚举%s失败：%s: %s" % (label, type(e).__name__, e))
        ok = False
        continue
    print("BLE %s端点 %d 个：" % (label, len(devs)))
    for dev_id, name in devs:
        print("  %s" % name)
        if not name.endswith(midi_ble.BLE_MARK):
            print("    FAIL：合成名缺后缀 %s" % midi_ble.BLE_MARK)
            ok = False

if not do_open:
    print("结论：%s（只枚举；--open 开口验证，--send 真机收发）"
          % ("✓" if ok else "✗"))
    sys.exit(0 if ok else 1)

# ---- 打开验证（限时=生产同款 IO_TIMEOUT）----
for side in ("in", "out"):
    for dev_id, name in midi_ble.scan(side):
        t0 = time.time()
        if side == "in":
            got = []
            port, err = midi_ble.open_in(dev_id, lambda *a: got.append(a))
        else:
            port, err = midi_ble.open_out(dev_id)
        dt = time.time() - t0
        if port is None:
            print("  打开 %s：FAIL（%.1fs）%s" % (name, dt, err))
            ok = False
            continue
        print("  打开 %s：✓（%.1fs）" % (name, dt))
        if side == "out" and send_note is not None:
            e = port.send_short(0x90 | (send_note << 8) | 100 << 16)
            time.sleep(0.1)
            e2 = port.send_short(0x80 | (send_note << 8))
            print("    发 Note On/Off %d：%s" % (
                send_note, "✓" if e is None and e2 is None else "FAIL %s/%s"
                % (e, e2)))
            ok = ok and e is None and e2 is None
        port.close()
print("结论：%s" % ("✓ 全链路通过" if ok else "✗ 存在问题"))
sys.exit(0 if ok else 1)
