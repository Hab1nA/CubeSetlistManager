# -*- coding: utf-8 -*-
"""挂账③「VJ 槽位循环开关热应用」真机验证（2026-11-17 自动化轮）。
清单原文：在播视频格切「播一遍停/循环」→ OBS 中途合并 looping 是否即时
生效（不 RESTART 重开视频）。0.16.2 起挂账。
流程：拉起 OBS→熄屏清残留→note 72 起播→在播中 /vjloop on=true 热合并
→ 盯 GetMediaInputStatus 过 12s 视频末尾（ENDED=未拾取/PLAYING 持续=
即时生效）→ 复验下次触发生效 → 清理（标记复位+熄屏）。
独立工具不进测试套件；纯标准库+仓库模块。"""
import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import midi_bridge as mb          # noqa: E402
import obs_ctrl                   # noqa: E402

PORT = 8767
OBS_EXE = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"
VID_DUR = 12.0

APP_HOST = None


def _app_host():
    global APP_HOST
    if APP_HOST is None:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        APP_HOST = (cfg.get("webRemote") or {}).get("ip") or "127.0.0.1"
    return APP_HOST


def get(path):
    c = http.client.HTTPConnection(_app_host(), PORT, timeout=4)
    c.request("GET", path)
    r = c.getresponse()
    b = r.read()
    c.close()
    return r.status, (json.loads(b) if b else {})


def post(path, body):
    c = http.client.HTTPConnection(_app_host(), PORT, timeout=4)
    c.request("POST", path, json.dumps(body),
              {"Content-Type": "application/json"})
    r = c.getresponse()
    b = r.read()
    c.close()
    return r.status, (json.loads(b) if b else {})


def _send_note(hint, note):
    hits = [(i, v) for i, v in mb.out_devices() if hint in v]
    if not hits:
        return "未找到输出端口 %s" % hint
    mb.send_note(hits[0][0], note)
    return None


def _ensure_obs():
    """OBS 不在场则拉起，等 obs-websocket 4455 端口就绪。"""
    procs = obs_ctrl.find_processes_by_prefix("obs64")
    launched = False
    if not procs:
        print("OBS 不在场，拉起 obs64.exe…")
        subprocess.Popen([OBS_EXE], cwd=os.path.dirname(OBS_EXE))
        launched = True
        deadline = time.time() + 30
        while time.time() < deadline:
            s = socket.socket()
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", 4455))
                s.close()
                break
            except OSError:
                time.sleep(1.0)
        else:
            sys.exit("OBS websocket 4455 端口 30s 未就绪")
    return launched


def _wait_playing(ctl, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = ctl.media_state()
        if st == "OBS_MEDIA_STATE_PLAYING":
            return True
        time.sleep(0.5)
    return False


def main():
    print("== 挂账③ VJ 循环热应用 ==")
    st, s = get("/state")
    assert st == 200, "app 未运行（先启动沙箱 app）"
    launched = _ensure_obs()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    ctl = obs_ctrl.ObsController(cfg.get("obs") or {})
    ctl.enabled = True
    ctl.start()
    t0 = time.time()
    while time.time() - t0 < 40 and not ctl.is_connected():
        time.sleep(0.5)
    assert ctl.is_connected(), "探针 ObsController 连不上 OBS（40s）"
    print("OBS 已连接（探针独立连接，app 侧连接另在跑）")
    ctl.stop_media()                 # 清残留：用户 OBS 可能存着上次文件
    time.sleep(1.0)

    # 1. 触发起播（无循环基线）
    err = _send_note("VJ Automation", 72)
    assert not err, err
    assert _wait_playing(ctl), "note 72 后 10s 未起播"
    print("基线起播 ✓（looping=False）")

    # 2. 在播中热合并 looping=True
    st, d = post("/vjloop", {"note": 72, "mode": "loop"})
    assert st == 200, "POST /vjloop -> %s %s" % (st, d)
    merge_t = time.time()
    print("已在播中切「循环」（/vjloop mode=loop）")

    # 3. 盯过视频末尾（12s 视频已在播数秒；盯到 merge 后 30s）
    merged_live = False
    ended = False
    while time.time() - merge_t < 30:
        st_ = ctl.media_state()
        if st_ == "OBS_MEDIA_STATE_ENDED":
            ended = True
            break
        if st_ == "OBS_MEDIA_STATE_PLAYING":
            merged_live = True
        time.sleep(1.0)
    print("盯守 30s：seen PLAYING=%s ENDED=%s" % (merged_live, ended))

    if ended:
        verdict = ("NOT_MERGED：OBS 未拾取在播合并（ENDED 定格不复活，"
                   "槽位标记下次触发生效——与 set_looping 文档预期一致）")
        passed = True
    else:
        st_ = ctl.media_state()
        merged_live = merged_live and st_ == "OBS_MEDIA_STATE_PLAYING"
        if merged_live:
            verdict = "MERGED：OBS 在播中拾取 looping 合并（过末尾续播）"
            passed = True
        else:
            verdict = "UNKNOWN：30s 内既无 ENDED 也非 PLAYING（state=%r）" % st_
            passed = False
    print("判定①（在播中合并）：%s" % verdict)

    # 4. 复验「下次触发生效」：RESTART 带 looping=True，过末尾应续播
    re_pass = None
    err = _send_note("VJ Automation", 72)
    if err:
        print("复验跳过：%s" % err)
        re_pass = None
    else:
        assert _wait_playing(ctl), "复验 note 72 后 10s 未起播"
        t1 = time.time()
        ok_loop = False
        ended2 = False
        while time.time() - t1 < 30:
            st_ = ctl.media_state()
            if st_ == "OBS_MEDIA_STATE_ENDED":
                ended2 = True
                break
            if st_ == "OBS_MEDIA_STATE_PLAYING":
                ok_loop = True
            time.sleep(1.0)
        # 12s 视频 + 起播延迟；t1+22s 仍在播=循环生效
        if time.time() - t1 >= 20 and ok_loop and not ended2:
            re_pass = True
            print("复验 ✓：RESTART 带 looping=True 过末尾续播（下次触发生效闭环）")
        elif ended2:
            re_pass = False
            print("复验 ✗：RESTART 后仍 ENDED（槽位标记未生效，意外）")
        else:
            re_pass = False
            print("复验 ✗：状态=%r" % ctl.media_state())
    # final: re_pass False → passed=False（复验失败=闭环破）
    if re_pass is False:
        passed = False

    # 5. 清理：标记复位 + 熄屏
    post("/vjloop", {"note": 72, "mode": "off"})
    ctl.stop_media()
    print("清理完成：槽位标记 off、熄屏（停媒体+忘文件）")
    if launched:
        subprocess.run(["taskkill", "/IM", "obs64.exe"],
                       capture_output=True)
        print("已关闭本探针拉起的 OBS")

    print("== 挂账③ 判定：%s ==" % ("PASS" if passed else "FAIL（见上）"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
