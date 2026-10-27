# -*- coding: utf-8 -*-
"""attack3_portsel.py —— 三轮攻击面三（后端半）：dataset.dev 与后端
label/pick()/消歧的全程同轴性，用 kbd_auto 纯函数端到端推演。

链路：/kbd ports.in/out[] ← _kbd_get_web entries()（_device_entries 的
  dev=同名 0 基序）→ 前端 option.dataset.dev=String(e.dev) → 保存 pick()
  {name,dev} → /kbdset _kbd_apply_web cfg["inDev"/"dev"]=_norm_dev →
  重启后 _in_dev/_pick_hit/_name_pos 消歧 → portSelVal 回显。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kbd_auto  # noqa: E402

FAILS = []


def ok(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (" | " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def entries_of(devs):
    """_kbd_get_web 的 entries() 同构（setlist_gui.py:2329-2331）"""
    return [{"label": l, "name": n, "dev": k}
            for l, n, k in kbd_auto._device_entries([n for _, n in devs])]


def side_of(devs, hint, dev):
    """_kbd_get_web 的 side() 同构（setlist_gui.py:2333-2338）"""
    hit = kbd_auto._pick_hit(devs, hint, dev)
    if hit is None:
        return {"hit": False}
    pos, total = kbd_auto._name_pos(devs, hit[1], hit[0])
    return {"hit": True, "name": hit[1], "pos": pos, "total": total}


def frontend_pick(sel_value, entries, hint, saved_dev):
    """app.js pick()（1974-1983 行）同构"""
    if sel_value in ("-1", ""):
        return {"name": hint, "dev": saved_dev or 0}   # 幽灵/空：保名保序
    try:
        i = int(sel_value)
    except ValueError:
        return {"name": hint, "dev": saved_dev or 0}
    e = entries[i] if 0 <= i < len(entries) else None
    return ({"name": e["name"], "dev": e["dev"]} if e
            else {"name": hint, "dev": saved_dev or 0})


def port_sel_val(entries, hint, pos):
    """app.js portSelVal()（1621-1631 行）同构：返回选中的 entries 下标"""
    if hint:
        i = -1
        if pos is not None and pos > 1:
            i = next((k for k, e in enumerate(entries)
                      if e["name"] == hint and e["dev"] == pos - 1), -1)
        if i < 0:
            i = next((k for k, e in enumerate(entries)
                      if e["name"] == hint), -1)
        if i >= 0:
            return i
    return -1  # 幽灵


def apply_web(cfg, name, dev):
    """_kbd_apply_web 的 inDev 落键段同构（setlist_gui.py:2447-2448）"""
    cfg["inHint"] = name
    cfg["inDev"] = kbd_auto._norm_dev(dev)


# ---- ① 同名双口手选第 2 个：落盘/重启命中/回显三处同轴 ----
DEVS = [(0, "USB-Midi"), (1, "USB-Midi"), (2, "JUNO-DS")]
ents = entries_of(DEVS)
ok("①a entries.dev=同名 0 基序（非 winmm 枚举号）",
   [(e["name"], e["dev"], e["label"]) for e in ents] == [
       ("USB-Midi", 0, "USB-Midi · 第1个"),
       ("USB-Midi", 1, "USB-Midi · 第2个"),
       ("JUNO-DS", 0, "JUNO-DS")],
   str([(e["name"], e["dev"]) for e in ents]))

cfg = {}
saved = frontend_pick("1", ents, "", 0)          # 用户停在「USB-Midi · 第2个」
apply_web(cfg, saved["name"], saved["dev"])
ok("①b 落盘 inDev=1", cfg.get("inDev") == 1, str(cfg))

eff = kbd_auto._in_dev(cfg)                       # 重启后生效序
hit = kbd_auto._pick_hit(DEVS, cfg["inHint"], eff)
ok("①c 重启 _pick_hit 命中第 2 只 USB-Midi（winmm 1 号口）",
   hit == (1, "USB-Midi"), str(hit))

st = side_of(DEVS, cfg["inHint"], eff)
sel = port_sel_val(ents, cfg["inHint"], st.get("pos"))
ok("①d portStatus pos=2 → portSelVal 选中「USB-Midi · 第2个」",
   st == {"hit": True, "name": "USB-Midi", "pos": 2, "total": 2}
   and sel == 1 and ents[sel]["dev"] == 1,
   "side=%s sel=%s" % (st, sel))

# ---- ② 幽灵项保存：dev 是否被静默归 0 ----
cfg2 = {"inHint": "USB-Midi", "inDev": 1}         # 已存配置=第 2 口，设备离线
kb_juno_in_dev = kbd_auto._in_dev(cfg2)            # /kbd 返回的 junoInDev
g = frontend_pick("-1", ents_if_offline := [], "USB-Midi", kb_juno_in_dev)
ok("② 停在幽灵项保存：dev 保序不归 0（dev 参数来自 junoInDev）",
   g == {"name": "USB-Midi", "dev": 1}, str(g))
cfg3 = {"inHint": "JUNO-DS", "outHint": "USB-Midi"}  # 对照：无 inDev 的旧配置
g2 = frontend_pick("-1", [], "JUNO-DS", kbd_auto._in_dev(cfg3))
ok("②b 旧配置（无 inDev 键）幽灵保存 dev=0=原语义", g2["dev"] == 0, str(g2))

# ---- ③ 枚举序漂移：dev 身份是否随枚举序漂移 ----
DEVS_SWAP = [(0, "USB-Midi"), (1, "USB-Midi"), (2, "JUNO-DS")]
# 物理口换序（重启/重插后 winmm 先报原第 2 只）：dev=1 仍指「枚举第 2 个」
hit_swap = kbd_auto._pick_hit(DEVS_SWAP, "USB-Midi", 1)
ok("③ dev=枚举序身份：序号不锚物理口（产品既有语义边界，非回修引入）",
   hit_swap == (1, "USB-Midi"),
   "换序后 dev=1 命中枚举位 1（若物理换位=指到另一只）")

# ---- ④ _pick_hit 子串回退与 dataset.dev 的语义分歧面 ----
DEVS_PREFIX = [(0, "USB-Midi"), (1, "USB-Midi 2")]
hit_prefix = kbd_auto._pick_hit(DEVS_PREFIX, "USB-Midi", 1)
ents_prefix = entries_of(DEVS_PREFIX)
ok("④a 精确名命中时 dev 组=同名组：USB-Midi 唯一精确命中，dev 无歧义",
   hit_prefix == (0, "USB-Midi")
   and [(e["name"], e["dev"]) for e in ents_prefix]
       == [("USB-Midi", 0), ("USB-Midi 2", 0)],
   "hit=%s entries=%s" % (hit_prefix, [(e["name"], e["dev"]) for e in ents_prefix]))
DEVS_SUB = [(0, "USB-A"), (1, "USB-B"), (2, "JUNO-DS")]
hit_sub = kbd_auto._pick_hit(DEVS_SUB, "USB", 1)
ents_sub = entries_of(DEVS_SUB)
ok("④b 手编宽 hint（非精确名）走子串回退：dev 跨名组编序，与 entries 的"
   "同名组语义分歧——但 UI pick() 恒存精确名，手编配置才可达",
   hit_sub == (1, "USB-B")
   and all(e["dev"] == 0 for e in ents_sub),
   "hit=%s（dev=1 在子串组=USB-B；entries 里 dev 恒按同名组=0）" % (hit_sub,))

# ---- ⑤ dev 越界收敛：拔掉一只后剩余选择仍落真实口 ----
DEVS_ONE = [(0, "USB-Midi"), (2, "JUNO-DS")]      # 拔掉一只 USB-Midi
hit_one = kbd_auto._pick_hit(DEVS_ONE, "USB-Midi", 1)
st_one = side_of(DEVS_ONE, "USB-Midi", 1)
ents_one = entries_of(DEVS_ONE)
sel_one = port_sel_val(ents_one, "USB-Midi", st_one.get("pos"))
ok("⑤ dev=1 越界收敛末位：命中剩余口且回显同一项",
   hit_one == (0, "USB-Midi") and st_one["pos"] == 1 and sel_one == 0,
   "hit=%s side=%s sel=%s" % (hit_one, st_one, sel_one))

# ---- ⑥ portStatus 3s 快照陈旧 × fillPortSel 回落消歧：错档窗口 ----
DEVS3 = [(0, "P"), (1, "P"), (2, "P")]            # 三只同名，已存 dev=2（第3只）
cfg3p = {"inHint": "P", "inDev": 2}
st_fresh_snap = side_of(DEVS3, "P", 2)            # 拔线前快照：pos=3
DEVS_AFTER = [(1, "P"), (2, "P")]                 # 拔掉第 1 只（枚举重排）
st_fresh = side_of(DEVS_AFTER, "P", 2)            # 新快照：clamp → 第2只 pos=2
ents_after = entries_of(DEVS_AFTER)
sel_fresh = port_sel_val(ents_after, "P", st_fresh["pos"])
sel_stale = port_sel_val(ents_after, "P", st_fresh_snap["pos"])  # 旧 pos=3
ok("⑥ 新快照 pos=2 → 回显第 2 只（clamp 正确）", sel_fresh == 1,
   "st_fresh=%s sel=%s" % (st_fresh, sel_fresh))
ok("⑦ 陈旧 pos=3 → dev===2 找不到 → 回落同名第 1 只=错档（≤3s 快照+1.5s"
   " 轮询窗内保存即错绑）", sel_stale == 0 and ents_after[0]["dev"] == 0,
   "sel_stale=%s（错绑到枚举位 0；修复前该回落恒取第 1 只=永错，"
   "现仅拓扑变化后 ~4.5s 窗内错）" % sel_stale)

print()
print("FAILS: " + ("; ".join(FAILS) if FAILS else "无"))
sys.exit(1 if FAILS else 0)
