# -*- coding: utf-8 -*-
"""踩钉页重构批：状态卡 kv 结构/闪点去字符/高级区拆解/标题边距修复。"""
import io


def patch(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    for old, new, tag in pairs:
        assert old in s, path + " NOT FOUND: " + tag
        assert s.count(old) == 1, path + " NOT UNIQUE: " + tag
        s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(path, "ok", len(pairs))


# ================= index.html =================
HTML = [
    # 三张状态卡：kv 行结构（label 左/值右，占满卡宽）；卡3 闪点去字符
    ('''      <div class="stat-cards">
        <div class="card stat"><div class="lbl">连接状态</div>
          <div class="stat-big" id="pedal-conn">…</div>
          <div class="dim sm-txt" id="pedal-conn-sub"></div></div>
        <div class="card stat"><div class="lbl">平板转发链路</div>
          <div class="stat-big" id="pedal-link">—</div>
          <div class="dim sm-txt" id="pedal-link-sub"></div>
          <label class="switch sm" title="踏板 USB-C 连平板经 APP 转发；
            平板端 APP 设置里也要开；即改即生效">
            <input type="checkbox" id="pd-remote"><i></i>
            <span>允许平板转发踩钉</span></label></div>
        <div class="card stat"><div class="lbl">最近踩钉事件</div>
          <div class="stat-big"><span id="pedal-flash">·</span>
            <span class="dim" id="pedal-when"></span></div></div>
      </div>''',
     '''      <div class="stat-cards">
        <div class="card stat"><div class="lbl">连接状态</div>
          <div class="stat-big" id="pedal-conn">…</div>
          <div class="kv"><span class="k">MIDI 口</span>
            <span class="v off" id="pedal-kv-midi">未连</span></div>
          <div class="kv"><span class="k">输入设备</span>
            <span class="v off" id="pedal-kv-dev">离线</span></div>
          <div class="kv"><span class="k">HID 桥</span>
            <span class="v off" id="pedal-kv-hid">停</span></div>
        </div>
        <div class="card stat"><div class="lbl">平板转发链路</div>
          <div class="stat-big" id="pedal-link">—</div>
          <div class="kv"><span class="k">心跳</span>
            <span class="v off" id="pedal-kv-hb">未启用</span></div>
          <div class="kv"><span class="k">未识别键</span>
            <span class="v off" id="pedal-kv-unknown">0 个</span></div>
          <label class="switch sm" title="踏板 USB-C 连平板经 APP 转发；
            平板端 APP 设置里也要开；即改即生效">
            <input type="checkbox" id="pd-remote"><i></i>
            <span>允许平板转发踩钉</span></label></div>
        <div class="card stat"><div class="lbl">最近踩钉事件</div>
          <div class="stat-big"><span id="pedal-flash"></span>
            <span id="pedal-when">暂无事件</span></div></div>
      </div>''',
     "stat cards kv"),
    # 输入设备卡：拦截开关从高级区移入（设备行为归设备卡）
    ('''        <div class="input-row">
          <select id="pd-dev"></select>
          <button id="pd-refresh" class="btn ghost sm">刷新</button>
        </div>
        <p class="dim sm-txt" id="pd-midiport"></p>''',
     '''        <div class="input-row">
          <select id="pd-dev"></select>
          <button id="pd-refresh" class="btn ghost sm">刷新</button>
        </div>
        <label class="switch" title="勾选后被绑定按键经系统热键截留，
          不送达其它软件；即改即生效">
          <input type="checkbox" id="pd-intercept"><i></i>
          <span>拦截硬件原键</span></label>
        <p class="dim sm-txt" id="pd-midiport"></p>''',
     "intercept move"),
    # 功能绑定卡：双踩窗移入（判定节奏归绑定卡）；高级折叠区整体删除
    ('''      <div class="card"><h3>功能绑定</h3>
        <div id="pd-binds" class="bind-list"></div>
        <p class="dim sm-txt">踩一下=单踩，快踩两下=双踩。本页打开期间
          踩钉静音，关闭本页恢复。</p>
      </div>
      <details class="card"><summary>高级</summary>
        <div class="grid2">
          <div class="field"><label for="pd-win">双踩窗（秒，0.12-1.0）</label>
            <input id="pd-win" class="num" inputmode="decimal"></div>
          <div class="field"><label>硬件原键</label>
            <label class="switch" title="勾选后被绑定按键经系统热键截留，
              不送达其它软件；即改即生效"><input type="checkbox"
              id="pd-intercept"><i></i>
              <span>拦截硬件原键</span></label></div>
        </div>
      </details>
    </section>''',
     '''      <div class="card"><h3>功能绑定</h3>
        <div id="pd-binds" class="bind-list"></div>
        <div class="field pd-win"><label for="pd-win">双踩判定窗
          （秒，0.12-1.0）</label>
          <input id="pd-win" class="num" inputmode="decimal"></div>
        <p class="dim sm-txt">踩一下=单踩，快踩两下=双踩。本页打开期间
          踩钉静音，关闭本页恢复。</p>
      </div>
    </section>''',
     "details gone"),
]
patch("webapp/index.html", HTML)

# ================= app.css =================
CSS = [
    # P1 标题边距丢失根因：后面的 .page h2 margin:0 覆盖了 .page > h2
    (".page h2 { margin: 0; font-size: 20px; }",
     ".page h2 { font-size: 20px; }",
     "h2 margin"),
    # kv 行：label 左/值右占满卡宽，值带状态色
    (".card.stat > .dim { margin-top: var(--s1); }",
     ".kv { display: flex; justify-content: space-between; align-items: center;\n"
     "  gap: 12px; padding: 5px 0; font-size: var(--f-sm); }\n"
     ".kv .k { color: var(--dim); }\n"
     ".kv .v { color: var(--fg); font-variant-numeric: tabular-nums; }\n"
     ".kv .v.on { color: var(--ok); }\n"
     ".kv .v.off { color: var(--dim); }\n"
     ".kv .v.warn { color: var(--warn); }\n"
     ".stat-big + .kv { margin-top: 8px; }\n"
     "#p-pedal .card .switch { margin-top: var(--s2); }\n"
     ".pd-win { max-width: 260px; margin-top: var(--s3); }",
     "kv styles"),
    # details 样式随折叠区删除
    ("""details.card summary { cursor: pointer; color: var(--dim);
  font-weight: 700; font-size: var(--f-md); list-style: none;
  display: flex; align-items: center; gap: 6px; }
details.card summary::before { content: "▸"; transition:
  transform var(--t-fast) var(--ease); }
details.card[open] summary::before { transform: rotate(90deg); }
details.card summary:hover { color: var(--fg); }
details.card .grid2 { margin-top: var(--s3); }""",
     "",
     "details css gone"),
]
patch("webapp/app.css", CSS)

# ================= app.js =================
JS = [
    # 连接状态卡：一行文字 → 三行 kv
    ('''    c.textContent = txt;
    c.className = "stat-big " + cls;
    $("#pedal-conn-sub").textContent =
        "MIDI " + (d.connected ? "连接" : "未连")
        + " · 设备 " + (d.deviceOnline ? "在线" : "离线")
        + " · HID 桥 " + (d.hidActive ? "运行" : "停");''',
     '''    c.textContent = txt;
    c.className = "stat-big " + cls;
    setKv("pedal-kv-midi", d.connected ? "已连" : "未连", d.connected);
    setKv("pedal-kv-dev", d.deviceOnline ? "在线" : "离线",
          d.deviceOnline);
    setKv("pedal-kv-hid", d.hidActive ? "运行" : "停", d.hidActive);''',
     "conn kv"),
    # setKv 助手
    ('''function pedalLive(st) {''',
     '''function setKv(id, text, on) {
  const el = $("#" + id);
  el.textContent = text;
  el.className = "v " + (on ? "on" : "off");
}

function pedalLive(st) {''',
     "setKv"),
    # 转发卡：sub 行 → 两行 kv
    ('''    sub.textContent = pr.unknown > 0
        ? "未识别键 " + pr.unknown + " 个（链路活但键路不通）"
        : (pr.enabled ? "心跳正常" : "未启用");
    sub.className = "dim sm-txt" + (pr.unknown > 0 ? " warn-txt" : "");
  } else {''',
     '''    const hb = $("#pedal-kv-hb"), unk = $("#pedal-kv-unknown");
    hb.textContent = pr.unknown > 0 ? "未识别 " + pr.unknown
        : (pr.enabled ? "正常" : "未启用");
    hb.className = "v " + (pr.unknown > 0 ? "warn"
        : (!pr.enabled ? "off" : "on"));
    unk.textContent = pr.unknown + " 个";
    unk.className = "v " + (pr.unknown > 0 ? "warn" : "off");
  } else {''',
     "link kv"),
    # else 分支的 sub 引用清理
    ('''  } else {
    link.textContent = "—";
    link.className = "stat-big";
    sub.textContent = "";
  }''',
     '''  } else {
    link.textContent = "—";
    link.className = "stat-big";
    setKv("pedal-kv-hb", "—", false);
    setKv("pedal-kv-unknown", "—", false);
  }''',
     "link else"),
    # 事件时间：主内容亮色/空闲灰
    ('''    pedalPrevAge = age;
    $("#pedal-when").textContent = age < 60
        ? Math.round(age) + " 秒前" : "久前";
  } else {
    $("#pedal-when").textContent = "暂无事件";
  }''',
     '''    pedalPrevAge = age;
    const w = $("#pedal-when");
    w.textContent = age < 60 ? Math.round(age) + " 秒前" : "久前";
    w.className = "";
  } else {
    const w = $("#pedal-when");
    w.textContent = "暂无事件";
    w.className = "dim";
  }''',
     "when class"),
]
patch("webapp/app.js", JS)

# ================= 探针同步 =================
PR = [
    ('''    pd = js(send, "return {conn:document.getElementById('pedal-conn')"
           ".textContent,link:document.getElementById('pedal-link')"
           ".textContent,"
           "binds:document.querySelectorAll('.bind-row').length,"
           "devSel:document.getElementById('pd-dev').tagName,"
           "adv:!!document.querySelector('details.card')}")
    ok("p1.pedal.stat-cards", pd.get("conn") and pd.get("binds") == 6
       and pd.get("devSel") == "SELECT" and pd.get("adv"),
       json.dumps(pd, ensure_ascii=False))''',
     '''    pd = js(send, "return {conn:document.getElementById('pedal-conn')"
           ".textContent,link:document.getElementById('pedal-link')"
           ".textContent,"
           "binds:document.querySelectorAll('.bind-row').length,"
           "devSel:document.getElementById('pd-dev').tagName,"
           "win:!!document.getElementById('pd-win'),"
           "noDetails:!document.querySelector('#p-pedal details'),"
           "kv:document.querySelectorAll('#p-pedal .kv').length}")
    ok("p1.pedal.stat-cards", pd.get("conn") and pd.get("binds") == 6
       and pd.get("devSel") == "SELECT" and pd.get("win")
       and pd.get("noDetails") and pd.get("kv") == 5,
       json.dumps(pd, ensure_ascii=False))''',
     "p1 pedal"),
    ('''            "sub:document.getElementById('pedal-conn-sub').textContent,"
            "devOpts:[...document.getElementById('pd-dev').options]"
            ".map(o=>o.textContent)}")
    ok("bc.c2.bind-table", c4.get("rows") == 6
       and c4.get("bind0") == "CC 74·双踩"
       and c4.get("bindNext") == "按键 下一曲"
       and c4.get("unset") == "未设置",
       json.dumps(c4, ensure_ascii=False))
    ok("bc.c4.conn-sub",
       "MIDI" in (c4.get("sub") or "") and "设备" in (c4.get("sub") or "")
       and "HID" in (c4.get("sub") or ""),
       json.dumps(c4.get("sub"), ensure_ascii=False))''',
     '''            "kvMidi:document.getElementById('pedal-kv-midi').textContent,"
            "kvHid:document.getElementById('pedal-kv-hid').textContent,"
            "devOpts:[...document.getElementById('pd-dev').options]"
            ".map(o=>o.textContent)}")
    ok("bc.c2.bind-table", c4.get("rows") == 6
       and c4.get("bind0") == "CC 74·双踩"
       and c4.get("bindNext") == "按键 下一曲"
       and c4.get("unset") == "未设置",
       json.dumps(c4, ensure_ascii=False))
    ok("bc.c4.conn-kv",
       "未连" in (c4.get("kvMidi") or "") and "停" in (c4.get("kvHid") or ""),
       json.dumps([c4.get("kvMidi"), c4.get("kvHid")],
                  ensure_ascii=False))''',
     "bc c2c4"),
]
patch("out/probe_ui.py", PR)
print("ALL OK")
