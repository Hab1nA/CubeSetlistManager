# -*- coding: utf-8 -*-
"""三项挂账验证的环境准备（2026-11-17 自动化真机验证轮）：
①建立沙箱（备份仓库根 playlist.json → 写沙箱 config.json/playlist.json →
  ffmpeg 生成 12s 测试视频）；②盘点前置条件（loopMIDI 端口/OBS/Cubase/工程）。
只写文件不启动任何程序；还原走 probe_race1117_restore.py。
独立工具不进测试套件；纯标准库。"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import midi_bridge as mb          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SB = ROOT / "out" / "race1117"
CFG = ROOT / "config.json"
PL = ROOT / "playlist.json"

# 竞争测试三首（真机库最短三首，见 PLAN.md；A=intro 须发 MTC 时码）
A, B, C = "霓虹折叠/intro", "霓虹折叠/ROLLING GIRL", "霓虹折叠/TEST"
A_MANUAL_DUR = 20.0              # A 手动短时长：播 ~20s 即自动停止走带


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"   # 无外网时绑回环（lan 模式 bind 照常工作）


def main():
    SB.mkdir(parents=True, exist_ok=True)
    (SB / "backup").mkdir(exist_ok=True)
    (SB / "vj").mkdir(exist_ok=True)
    (SB / "logs").mkdir(exist_ok=True)

    # ---- 备份真实歌单（字节级）----
    if PL.exists():
        shutil.copy2(PL, SB / "backup" / "playlist.json.orig")
        print("已备份 playlist.json → out/race1117/backup/playlist.json.orig")

    # ---- 沙箱配置（端口名与用户实况一致；OBS 指沙箱视频目录）----
    obs = {
        "videoRoot": str(SB / "vj"),
        "mediaInput": "舞台视频",
        "vjMute": True,
        "autoStart": False,        # OBS 由探针拉起，避免 app 的三重门口径耦合
    }
    cfg = {
        "daw": "cubase",
        "dawSettings": {"cubase": {
            "dawExe": r"C:\Program Files\Steinberg\Cubase 15\Cubase15.exe",
            "projectsRoot": r"C:\Users\XKZ\Documents\Cubase Projects"}},
        "vjPortHint": "VJ Automation",
        "kbPortHint": "Keyboard Automation",
        "clockPortHint": "VJ Automation",   # 与用户实况同口：时码+VJ 音符同口
        "mrPortHint": "CubeSetlist Remote",
        "launchAppsOnStart": False,
        "exitCloseApps": False,
        "obs": obs,
        "webRemote": {"enabled": True, "mode": "lan", "ip": _lan_ip(),
                      "serverPort": 8765, "appPort": 8767, "taskerPort": 8766,
                      "midiIn": "", "devices": [], "hosts": []},
    }
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print("已写沙箱 config.json（webRemote 绑 %s:8767）" % cfg["webRemote"]["ip"])

    # ---- 沙箱歌单：A/B/C 三条目；A 手动 20s + endBeh=next ----
    pl = {"version": 1, "daw": "cubase",
          "playlist": [A, B, C],   # 键=素材库全键「队伍/歌」，裸歌名会被加载器判失效
          "durations": {A: A_MANUAL_DUR, B: 188.3, C: 118.8},
          "durSrc": {A: "manual", B: "auto", C: "auto"},
          "endBeh": ["next", "", ""]}   # A 播完→自动切下一首（竞争测试的前提）
    PL.write_text(json.dumps(pl, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    print("已写沙箱 playlist.json：%s（A 手动 %.0fs + endBeh=next）" % (pl["playlist"], A_MANUAL_DUR))

    # ---- ffmpeg 生成 12s 测试视频（不碰用户 StageVideos）----
    vid = SB / "vj" / "1.mp4"
    if not vid.exists():
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                            "-f", "lavfi", "-i",
                            "testsrc2=duration=12:size=640x360:rate=25",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("ffmpeg 生成测试视频失败：%s" % r.stderr[-400:])
    print("测试视频 %s（12s，%.0f KB）" % (vid, vid.stat().st_size / 1024))

    # ---- 前置盘点（只读）----
    print("---- 前置盘点 ----")
    ins = [n for _, n in mb.in_devices()]
    outs = [n for _, n in mb.out_devices()]
    print("loopMIDI 输入口：%s" % ins)
    print("loopMIDI 输出口：%s" % outs)
    for need in ("VJ Automation", "CubeSetlist Remote"):
        print("  口 %s：%s" % (need, "✓" if any(need in n for n in ins) else "✗ 缺"))
    for exe, name in ((r"C:\Program Files\obs-studio\bin\64bit\obs64.exe", "OBS"),
                      (r"C:\Program Files\Steinberg\Cubase 15\Cubase15.exe", "Cubase")):
        print("  %s：%s" % (name, "✓" if os.path.exists(exe) else "✗ 缺"))
    for p in (SB / "vj" / "1.mp4",):
        print("  %s：%s" % (p.name, "✓" if p.exists() else "✗"))
    print("prep 完成")


if __name__ == "__main__":
    main()
