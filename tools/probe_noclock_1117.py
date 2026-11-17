# -*- coding: utf-8 -*-
"""挂账②「无时钟工程」app 侧真机验证（2026-11-17 自动化轮）。
前提：沙箱 app 已启动（prep 已跑、py setlist_gui.py 在跑）。
验证项（清单原文：未开发送 MIDI 时码的工程——视频触发正常、走带跟随显示
「未启用，未收到时码」、暂停/继续按钮保持可用）：
  a. /state：clockPortHint 在监听（vj.portOn）但从未收到时码 → follow=off
     （VJ 页「未启用/未收到时码」标签的数据源，webapp 同源映射）；
  b. VJ 触发链路通：向 VJ Automation 口发 note 72 → /state vj.video 变
     1.mp4（OBS 媒体状态细验由 ③ 探针做；OBS 未连接时本项记 SKIP）；
  c. 暂停/继续按钮保持可用：POST /cmd play → 200（互锁按停止态放行）+
     /log 出现「走带控制：<err>」——无 DAW 时预期「DAW 不在线」，
     证明互锁放行+发送如实报错（按钮没有被禁用）。
真工程保真版（Cubase 开着不发时码的工程）本轮未排期（REPORT 备注项）。独立工具
不进测试套件；纯标准库。"""
import http.client
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import midi_bridge as mb          # noqa: E402

PORT = 8767


def _app_host():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    return (cfg.get("webRemote") or {}).get("ip") or "127.0.0.1"


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
    if r.status != 200:
        print("POST %s -> %s %s" % (path, r.status,
                                    b[:120].decode("utf-8", "replace")))
    return r.status, (json.loads(b) if b else {})


def _send_note(hint, note):
    """e2e 同款：按名挑输出口发音符；返回 None=成功，str=错误。"""
    hits = [(i, n) for i, n in mb.out_devices() if hint in n]
    if not hits:
        return "未找到输出端口 %s（现有：%s）" % (hint, [n for _, n in mb.out_devices()])
    mb.send_note(hits[0][0], note)
    return None


def main():
    ok = True
    print("== 挂账② 无时钟工程（app 侧）==")
    st, s = get("/state")
    assert st == 200, "app 未运行（先启动沙箱 app）"
    print("state: busy=%s cur=%s tstate=%r live=%s" % (
        s.get("busy"), s.get("cur"), s.get("tstate"), s.get("live")))
    vj = s.get("vj") or {}
    follow, port_on = vj.get("follow"), vj.get("portOn")
    print("follow=%r portOn=%s obs=%s" % (follow, port_on, s.get("obs")))

    # a. 时码口监听中但从未收到时码 → follow=off
    assert port_on, "时钟口未监听（检查 clockPortHint=VJ Automation）"
    if follow != "off":
        print("FAIL a: follow=%r 期望 off（口在听但无时码）" % follow)
        ok = False
    else:
        print("PASS a: follow=off（未收到时码）")

    # b. VJ 触发链路通（OBS 未连则记 SKIP，由 ③ 探针复验）
    if s.get("obs"):
        err = _send_note("VJ Automation", 72)
        if err:
            print("SKIP b: %s" % err)
        else:
            time.sleep(1.5)
            st, s2 = get("/state")
            vid = ((s2.get("vj") or {}).get("video")) or ""
            print("触发后 vj.video=%r" % vid)
            if "1.mp4" not in str(vid):
                print("FAIL b: 触发后 video=%r，期望含 1.mp4" % vid)
                ok = False
            else:
                print("PASS b: note 72 -> 1.mp4 在播（触发链路通）")
    else:
        if follow == "off" and port_on:
            print("SKIP b: OBS 未连接（触发链路由 ③ 探针验）")
        else:
            print("SKIP b: OBS 未连接")

    # c. play 不被互锁拦（无 DAW → mr 发送报错但 HTTP 200）
    st, d = post("/cmd", {"action": "play"})
    if st != 200:
        print("FAIL c1: /cmd play -> %s（互锁拦了播放）%s" % (st, d))
        ok = False
    else:
        print("PASS c1: /cmd play -> 200（互锁按停止态放行）")
        time.sleep(2.5)
        lines = (get("/log")[1] or {}).get("lines") or []
        hit = [it.get("msg") for it in lines if "走带控制" in (it.get("msg") or "")]
        if not hit:
            print("FAIL c2: /log 无「走带控制」行（play 未到达发送层？）")
            ok = False
        else:
            print("log: %s" % hit[-1])
            if "DAW 不在线" in hit[-1]:
                print("PASS c2: 发送层如实报错（无 DAW）——按钮可用性成立")
            else:
                print("WARN c2: 走带控制行不含 DAW 不在线：%s" % hit[-1])
    print("== 挂账② 判定：%s ==" % ("PASS" if ok else "FAIL（见上）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
