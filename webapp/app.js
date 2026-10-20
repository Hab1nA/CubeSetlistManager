/* 新前端：导航 + /state 轮询渲染 + 走带命令。设计系统与交互态见 app.css。 */
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

// JS 错误取证：截图脚本从 window.__errs 读走
window.__errs = [];
addEventListener("error", e => window.__errs.push(
    e.message + "@" + (e.filename || "").split("/").pop() + ":" + e.lineno));
addEventListener("unhandledrejection",
                e => window.__errs.push("rejection: " + e.reason));

// ---- toast（替代全部 alert）----
function toast(msg, kind) {
  const box = $("#toasts");
  while (box.children.length >= 4) box.firstChild.remove();
  const t = document.createElement("div");
  t.className = "toast" + (kind === "err" ? " err" : "");
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => t.remove(), 200);
  }, 2600);
}

// ---- 两段式确认（替代 confirm）：首点变红字「确认…？」3s 回弹，再点执行 ----
function armConfirm(btn, fn, confirmText) {
  if (btn.dataset.armed) {
    clearTimeout(+btn.dataset.t);
    delete btn.dataset.armed;
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.orig;
    fn();
    return;
  }
  btn.dataset.orig = btn.textContent;
  btn.textContent = confirmText || "确认移除？";
  btn.classList.add("armed");
  btn.dataset.armed = "1";
  btn.dataset.t = setTimeout(() => {
    delete btn.dataset.armed;
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.orig;
  }, 3000);
}

// ---- popover（替代 prompt 链）：锚定行内表单，外点/ESC 关闭 ----
let popEl = null;
function _popOut(e) { if (popEl && !popEl.contains(e.target)) closePop(); }
function _popEsc(e) { if (e.key === "Escape") closePop(); }
function closePop() {
  if (!popEl) return;
  popEl.remove();
  popEl = null;
  document.removeEventListener("pointerdown", _popOut, true);
  document.removeEventListener("keydown", _popEsc, true);
}
function popover(anchor, build) {
  closePop();
  popEl = document.createElement("div");
  popEl.className = "pop";
  build(popEl, closePop);
  document.body.appendChild(popEl);
  const r = anchor.getBoundingClientRect();
  let x = Math.max(10, Math.min(r.left, innerWidth - popEl.offsetWidth - 10));
  let y = r.bottom + 8;
  if (y + popEl.offsetHeight > innerHeight - 10)
    y = Math.max(10, r.top - popEl.offsetHeight - 8);
  popEl.style.left = x + "px";
  popEl.style.top = y + "px";
  document.addEventListener("pointerdown", _popOut, true);
  document.addEventListener("keydown", _popEsc, true);
}

// ---- 导航 ----
function nav(page) {
  $$(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "p-" + page));
}
$$(".nav").forEach(b => b.addEventListener("click", () => nav(b.dataset.page)));
nav("console");

// ---- 演出模式：进出动效 + ESC/F11 + 屏角热区退出 ----
const reduceMotion = () =>
    matchMedia("(prefers-reduced-motion: reduce)").matches;
function setShow(on) {
  const body = document.body;
  if (on === body.classList.contains("show")) return;
  const btn = $("#btn-show");
  if (on) {
    nav("console");
    body.classList.add("show");
    btn.textContent = "⤢ 退出演出";
  } else if (reduceMotion()) {
    body.classList.remove("show");
    btn.textContent = "⛶ 演出";
  } else {
    $("#main").classList.add("leaving");
    setTimeout(() => {
      $("#main").classList.remove("leaving");
      body.classList.remove("show");
      btn.textContent = "⛶ 演出";
    }, 200);
  }
}
$("#btn-show").addEventListener("click",
    () => setShow(!document.body.classList.contains("show")));
$("#show-exit").addEventListener("click", () => setShow(false));
document.addEventListener("keydown", e => {
  if ((e.key === "Escape" || e.key === "F11")
      && document.body.classList.contains("show")) {
    e.preventDefault();
    setShow(false);
  }
});

// ---- 走带命令（失败可见，不再静默）----
async function cmd(action, index) {
  const body = index === undefined ? {action} : {action, index};
  try {
    const r = await fetch("/cmd", {method: "POST",
                                   headers: {"Content-Type": "application/json"},
                                   body: JSON.stringify(body)});
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      toast(j.error || "命令发送失败", "err");
      return false;
    }
    return true;
  } catch {
    toast("命令发送失败（网络）", "err");
    return false;
  }
}
$$("#transport button").forEach(b =>
    b.addEventListener("click", () => cmd(b.dataset.cmd)));
$("#console-list").addEventListener("dblclick", ev => {
  const row = ev.target.closest(".row");
  if (row) cmd("switch", Number(row.dataset.i));
});

// ---- /state 轮询渲染 ----
const fmt = s => {
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

function render(st) {
  if (!$("#p-console")) return;
  $("#now").textContent = st.open ? st.projName : "（无打开的工程）";
  const rows = st.songs || [];
  const cur = typeof st.cur === "number" ? st.cur : -1;
  const nx = rows[cur + 1];
  $("#next").textContent = nx ? "NEXT " + nx.name : "NEXT —";
  $("#clock").textContent =
      (st.tstate === "playing" || st.tstate === "paused")
          ? fmt(st.pos) + " | " + fmt(Math.max(0, st.dur - st.pos))
          : "—:— | —:—";
  $("#barfill").style.width =
      st.dur > 0 ? Math.min(100, st.pos / st.dur * 100) + "%" : "0";
  const list = $("#console-list");
  if (list.dataset.n !== String(rows.length) + ":" + cur) {
    list.dataset.n = String(rows.length) + ":" + cur;
    list.innerHTML = "";
    rows.forEach((s, i) => {
      const div = document.createElement("div");
      div.className = "row" + (i === cur ? " cur" : "");
      div.dataset.i = i;
      div.innerHTML = "<span>" + (i + 1) + ". " + s.name +
                      "</span><span class=\"dur\">" + s.dur + "</span>";
      list.appendChild(div);
    });
  }
  const busy = st.busy;
  const lamp = (id, cls) => {
    const el = $("#" + id);
    el.classList.remove("ok", "warn", "err");
    el.classList.add(cls);
  };
  lamp("l-daw", st.open ? "ok" : "err");
  lamp("l-remote", st.ready ? "ok" : "err");
  const pr = st.pedalRemote;
  lamp("l-pedal", pr && pr.link === "ok" ? "ok" : "warn");
  lamp("l-obs", "warn");
  lamp("l-loop", "warn");
  lamp("l-hot", "warn");
  $("#tstate").textContent =
      busy ? "切换中…" : {playing: "播放中", paused: "已暂停",
                          stopped: "已停"}[st.tstate] || "";
  $$("#transport button").forEach(b => b.disabled = !!busy);
}

async function poll() {
  try {
    const r = await fetch("/state");
    if (r.ok) {
      stState = await r.json();
      render(stState);
      stCur = stState.cur;
    }
  } catch {
    /* 热点瞬断：下轮再取 */
  }
}
setInterval(poll, 500);
poll();

// ================= 各数据页 =================
let stCur = null, plCache = null, stState = null;

// ---- 编排页 ----
let plSel = null;
function plRow(i, it) {
  const div = document.createElement("div");
  div.className = "row" + (i === plSel ? " sel" : "") +
                  (i === (typeof stCur === "number" ? stCur : -1) ? " cur" : "");
  div.dataset.k = it.key;
  const ops = document.createElement("span");
  ops.className = "ops";
  for (const [t, op] of [["↑", "up"], ["↓", "down"], ["✕", "remove"]]) {
    const b = document.createElement("button");
    b.textContent = t;
    if (op === "remove") b.className = "danger";
    b.addEventListener("click", ev => {
      ev.stopPropagation();
      if (op === "remove")
        armConfirm(b, () => plcmd("remove", it.key), "确认移除？");
      else plcmd(op, it.key);
    });
    ops.appendChild(b);
  }
  div.innerHTML = "<span>" + (i + 1) + ". " + it.name +
                  "</span><span class=\"dur\">" + it.dur + "</span>";
  div.appendChild(ops);
  div.addEventListener("click", () => { plSel = it.key; drawPl(); });
  div.addEventListener("dblclick", () => cmd("switch", i));
  return div;
}
function drawPl() {
  const el = $("#pl");
  if (plCache === null) return;
  el.innerHTML = "";
  plCache.pl.forEach((it, i) => el.appendChild(plRow(i, it)));
}
async function refreshPlaylist() {
  try {
    const r = await fetch("/playlist");
    if (!r.ok) return;
    const d = await r.json();
    plCache = d;
    stCur = d.cur;
    $("#pl-n").textContent = "（" + d.pl.length + "）";
    $("#lib-n").textContent = "（" + d.lib.length + "）";
    const lib = $("#lib");
    lib.innerHTML = "";
    d.lib.forEach(s => {
      const div = document.createElement("div");
      div.className = "row";
      div.innerHTML = "<span>[" + s.team + "] " + s.name +
                      "</span><span class=\"dur\">＋</span>";
      div.addEventListener("click", () => plcmd("add", s.key));
      lib.appendChild(div);
    });
    drawPl();
  } catch { /* 瞬断 */ }
}
async function plcmd(op, key) {
  try {
    const r = await fetch("/plcmd", {method: "POST",
                           headers: {"Content-Type": "application/json"},
                           body: JSON.stringify({op, key})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) toast(j.error || "操作失败", "err");
  } catch {
    toast("操作失败（网络）", "err");
  }
  refreshPlaylist();
}
$("#pl-clear").addEventListener("click", ev =>
    armConfirm(ev.currentTarget, () => plcmd("clear", ""), "确认清空？"));

// ---- 定位点页 ----
let ptProj = null, ptData = null, ptSel = null;
const parseTC = t => {
  const raw = String(t || "").trim();
  if (!raw || !/^\d+(?::\d{0,2}){0,2}$/.test(raw)) return null;
  const p = raw.split(":").map(Number);
  if (p.length > 1 && p[1] > 59) return null;
  if (p.length > 2 && p[2] > 59) return null;
  while (p.length < 3) p.unshift(0);
  return p[0] * 3600 + p[1] * 60 + p[2];
};
const fmtTC = s => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60),
        x = s % 60;
  return h ? h + ":" + String(m).padStart(2, "0") + ":" +
             String(x).padStart(2, "0")
           : m + ":" + String(x).padStart(2, "0");
};

// 时码即时校验：红框+原因文案，保存按钮随校验禁用（根治静默失败）
function validatePt() {
  const s = parseTC($("#pt-s").value), e = parseTC($("#pt-e").value);
  let msg = "";
  if (s === null) msg = "开始时码格式应为 分:秒（如 3:33）或秒数";
  else if (e === null) msg = "结束时码格式应为 分:秒（如 3:33）或秒数";
  else if (e <= s) msg = "结束必须晚于开始";
  else if (!ptSel) msg = "先在上方列表选中一个定位点";
  $("#pt-s").classList.toggle("invalid", s === null);
  $("#pt-e").classList.toggle("invalid", e === null || (s !== null && e <= s));
  const save = $("#pt-save");
  save.disabled = !!msg;
  save.title = msg || "";
  $("#pt-hint").textContent = msg;
  return !msg;
}
["pt-s", "pt-e"].forEach(id =>
    $("#" + id).addEventListener("input", validatePt));

async function refreshPoints() {
  try {
    const r = await fetch("/points");
    if (!r.ok) return;
    const d = await r.json();
    ptData = d.projects;
    if (!ptProj || !ptData[ptProj]) {
      ptProj = Object.keys(ptData).find(k => ptData[k].enabled) ||
               Object.keys(ptData)[0];
    }
    const dd = ptData[ptProj];
    if (!dd) return;
    const sel = $("#pt-proj");
    const sig = Object.keys(ptData).map(
        k => k + (ptData[k].enabled ? "1" : "0")).join("|");
    if (sel.dataset.sig !== sig) {           // 无变化不重建：避免打断展开
      sel.dataset.sig = sig;
      sel.innerHTML = "";
      Object.keys(ptData).forEach(k => {
        const o = document.createElement("option");
        o.value = k;
        o.textContent = ptData[k].name + (ptData[k].enabled ? " ✓" : "");
        if (k === ptProj) o.selected = true;
        sel.appendChild(o);
      });
    }
    $("#pt-on").checked = dd.enabled;
    const el = $("#pt-list");
    el.innerHTML = "";
    dd.points.forEach(p => {
      const div = document.createElement("div");
      div.className = "row" + (ptSel === p.id ? " cur" : "");
      div.innerHTML = "<span>" + p.name + "</span><span class=\"dur\">" +
                      fmtTC(p.start) + " → " + fmtTC(p.end) + "</span>";
      div.addEventListener("click", () => {
        ptSel = p.id;
        $("#pt-name").value = p.name;
        $("#pt-s").value = fmtTC(p.start);
        $("#pt-e").value = fmtTC(p.end);
        refreshPoints();
      });
      el.appendChild(div);
    });
    validatePt();
  } catch { /* 瞬断 */ }
}
async function ptcmd(op, payload) {
  try {
    const r = await fetch("/ptcmd", {method: "POST",
                           headers: {"Content-Type": "application/json"},
                           body: JSON.stringify({op, key: ptProj,
                                                 payload: payload || {}})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) toast(j.error || "操作失败", "err");
  } catch {
    toast("操作失败（网络）", "err");
  }
  refreshPoints();
  refreshPlaylist();
}
$("#pt-on").addEventListener("change", e =>
    ptcmd("toggle", {on: e.target.checked}));
$("#pt-proj").addEventListener("change", e => {
  ptProj = e.target.value;
  ptSel = null;
  refreshPoints();
});
$("#pt-add").addEventListener("click", () => ptcmd("add", {}));
$("#pt-del").addEventListener("click", ev => {
  if (ptSel) armConfirm(ev.currentTarget, () => ptcmd("del", {id: ptSel}),
                        "确认删除？");
});
$("#pt-save").addEventListener("click", () => {
  if (!validatePt()) return;
  ptcmd("edit", {id: ptSel, name: $("#pt-name").value,
                 start: parseTC($("#pt-s").value),
                 end: parseTC($("#pt-e").value)});
});
$("#pt-up").addEventListener("click", () => ptSel && ptcmd("move",
    {id: ptSel, step: -1}));
$("#pt-down").addEventListener("click", () => ptSel && ptcmd("move",
    {id: ptSel, step: 1}));

// ---- 日志页 ----
async function refreshLog() {
  if (!$("#p-log").classList.contains("on")) return;
  try {
    const r = await fetch("/log");
    if (!r.ok) return;
    const d = await r.json();
    const el = $("#log");
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.innerHTML = "";
    d.lines.forEach(l => {
      const div = document.createElement("div");
      if (l.msg.indexOf("界面异常") === 0) div.className = "err";
      div.textContent = new Date(l.t * 1000).toTimeString().slice(0, 8)
                        + " " + l.msg;
      el.appendChild(div);
    });
    if (stick) el.scrollTop = el.scrollHeight;
  } catch { /* 瞬断 */ }
}

// ================= 四页全功能（踩钉/键盘/VJ/设置）=================
const $v = id => $(id).value;
async function put(path, body) {
  try {
    const r = await fetch(path, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) toast(j.error || "保存失败", "err");
    return j;
  } catch {
    toast("保存失败（网络）", "err");
    return {ok: false};
  }
}

// ---- 踩钉 ----
async function refreshPedal() {
  try {
    const d = await (await fetch("/pedal")).json();
    $("#pedal-conn").textContent = d.connected ? "— 已连接" : "— 未连接";
    $("#pd-hint").value = d.hint || "";
    $("#pd-dev").value = d.deviceHint || "";
    $("#pd-g-play").value = d.gestures.play || "play";
    $("#pd-g-next").value = d.gestures.next || "next";
    $("#pd-win").value = d.doubleWindow || 0.35;
    $("#pd-intercept").checked = !!d.intercept;
    $("#pd-remote").checked = !!d.remoteEnabled;
  } catch { /* 瞬断 */ }
}
$("#pd-save").addEventListener("click", async () => {
  const r = await put("/pedalset", {
    hint: $v("#pd-hint"), deviceHint: $v("#pd-dev"),
    gestures: {play: $v("#pd-g-play"), next: $v("#pd-g-next")},
    doubleWindow: parseFloat($v("#pd-win")) || 0.35,
    intercept: $("#pd-intercept").checked,
    remoteEnabled: $("#pd-remote").checked});
  if (r.ok) toast("踩钉配置已应用");
  refreshPedal();
});

// ---- 键盘自动化 ----
let kbData = null;
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A",
                    "A#", "B"];
const noteName = n => NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1);

// 音色槽编辑：popover 行内表单（PC 必填 0-127，MSB/LSB 可空）替代 prompt 链
function slotEditor(anchor, inst, note, slot) {
  popover(anchor, (box, close) => {
    box.innerHTML =
        "<h4>音 " + noteName(note) + "（MIDI " + note + "）音色</h4>" +
        "<label>PC（0-127）<input type=\"number\" min=\"0\" max=\"127\" " +
        "id=\"sp-pc\"></label>" +
        "<label>MSB（可空）<input type=\"number\" min=\"0\" max=\"127\" " +
        "id=\"sp-msb\"></label>" +
        "<label>LSB（可空）<input type=\"number\" min=\"0\" max=\"127\" " +
        "id=\"sp-lsb\"></label>" +
        "<div class=\"field-err\" id=\"sp-err\"></div>" +
        "<div class=\"btns\">" +
        (slot ? "<button class=\"danger\" id=\"sp-clear\">清除</button>" : "") +
        "<button id=\"sp-cancel\">取消</button>" +
        "<button class=\"primary\" id=\"sp-ok\">保存</button></div>";
    const pc = $("#sp-pc", box), msb = $("#sp-msb", box), lsb = $("#sp-lsb", box);
    pc.value = slot ? slot.pc : 0;
    if (slot && slot.msb !== undefined) msb.value = slot.msb;
    if (slot && slot.lsb !== undefined) lsb.value = slot.lsb;
    const check = () => {
      const bad = pc.value === "" || Number(pc.value) < 0
                  || Number(pc.value) > 127;
      $("#sp-err", box).textContent =
          bad ? "PC 须为 0-127 的数字" : "";
      $("#sp-ok", box).disabled = bad;
    };
    pc.addEventListener("input", check);
    check();
    pc.focus();
    pc.select();
    $("#sp-cancel", box).addEventListener("click", close);
    $("#sp-ok", box).addEventListener("click", () => {
      const s = {pc: Number(pc.value)};
      if (msb.value !== "") s.msb = Number(msb.value);
      if (lsb.value !== "") s.lsb = Number(lsb.value);
      const cur = (inst === "ax" ? kbData.axSlots : kbData.slots) || {};
      cur[note] = s;
      put("/slotset", {inst, slots: cur}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已保存");
        refreshKbd();
      });
      close();
    });
    const clearBtn = $("#sp-clear", box);
    if (clearBtn) clearBtn.addEventListener("click", () => {
      const cur = (inst === "ax" ? kbData.axSlots : kbData.slots) || {};
      delete cur[note];
      put("/slotset", {inst, slots: cur}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已清除");
        refreshKbd();
      });
      close();
    });
  });
}
function slotRow(note, slot, inst) {
  const div = document.createElement("div");
  div.className = "row";
  const txt = slot ? ("PC " + slot.pc + (slot.msb !== undefined
      ? " | MSB " + slot.msb + " | LSB " + slot.lsb : "")) : "（未配置）";
  div.innerHTML = "<span>音 " + noteName(note) + "</span><span class=\"dur\">" +
                  txt + "</span>";
  div.title = "点击编辑音色";
  div.addEventListener("click", () => slotEditor(div, inst, note, slot));
  return div;
}
async function refreshKbd() {
  try {
    kbData = await (await fetch("/kbd")).json();
    $("#kbd-song").textContent = kbData.song ? "— " + kbData.song : "";
    $("#kb-jin").value = kbData.junoIn;
    $("#kb-jout").value = kbData.junoOut;
    $("#kb-ain").value = kbData.axIn;
    $("#kb-aout").value = kbData.axOut;
    $("#kb-jshift").textContent = kbData.junoShift;
    $("#kb-ashift").textContent = kbData.axShift;
    const js = $("#kb-slots");
    js.innerHTML = "";
    for (let n = 60; n <= 69; n++)
      js.appendChild(slotRow(n, kbData.slots[n], "juno"));
    const as = $("#kb-axslots");
    as.innerHTML = "";
    for (let n = 72; n <= 77; n++)
      as.appendChild(slotRow(n, kbData.axSlots[n], "ax"));
  } catch { /* 瞬断 */ }
}
$("#kb-save").addEventListener("click", async () => {
  const r = await put("/kbdset", {junoIn: $v("#kb-jin"),
    junoOut: $v("#kb-jout"), axIn: $v("#kb-ain"), axOut: $v("#kb-aout")});
  if (r.ok) toast("键盘端口已应用");
  refreshKbd();
});
$("#kb-jup").addEventListener("click", () => put("/kbdshift",
    {inst: "juno", total: (kbData.junoShift || 0) + 1}).then(refreshKbd));
$("#kb-jdown").addEventListener("click", () => put("/kbdshift",
    {inst: "juno", total: (kbData.junoShift || 0) - 1}).then(refreshKbd));
$("#kb-aup").addEventListener("click", () => put("/kbdshift",
    {inst: "ax", total: (kbData.axShift || 0) + 1}).then(refreshKbd));
$("#kb-adown").addEventListener("click", () => put("/kbdshift",
    {inst: "ax", total: (kbData.axShift || 0) - 1}).then(refreshKbd));

// ---- VJ 联动 ----
async function refreshVj() {
  try {
    const d = await (await fetch("/settings")).json();
    const sel = $("#vj-mon");
    sel.innerHTML = "";
    (d.screens || []).forEach(n => {
      const o = document.createElement("option");
      o.value = n;
      o.textContent = n;
      if (n === d.obs.projectorMonitor) o.selected = true;
      sel.appendChild(o);
    });
    $("#vj-media").value = d.obs.mediaInput || "";
    $("#vj-root").value = d.obs.videoRoot || "";
    $("#vj-mute").checked = !!d.obs.vjMute;
  } catch { /* 瞬断 */ }
}
$("#vj-save").addEventListener("click", async () => {
  const r = await put("/settings", {obs: {
    projectorMonitor: $v("#vj-mon"), mediaInput: $v("#vj-media"),
    videoRoot: $v("#vj-root"), vjMute: $("#vj-mute").checked}});
  if (r.ok) toast("VJ 配置已应用");
});

// ---- 设置页 ----
async function refreshSettings() {
  try {
    const d = await (await fetch("/settings")).json();
    $("#st-clock").value = d.clockHint;
    $("#st-vj").value = d.vjHint;
    $("#st-kb").value = d.kbHint;
    $("#st-auto").checked = d.autoAdvance;
    $("#st-cont").checked = d.autoPlay;
    $("#st-top").checked = d.topMost;
    $("#st-confirm").checked = d.switchConfirm;
    $("#st-exit").checked = d.exitCloseApps;
    $("#st-root").value = d.projectsRoot;
    $("#st-exe").value = d.dawExe;
    $("#st-autosave").checked = d.autoSave;
    $("#st-web").checked = d.web.enabled;
    $("#st-mode").value = d.web.mode;
    $("#st-srv").value = d.web.serverPort;
    $("#st-app").value = d.web.appPort;
    $("#st-tsk").value = d.web.taskerPort;
    $("#st-pg").value = d.web.midiIn;
    const ip = $("#st-ip");
    ip.innerHTML = "";
    d.adapters.forEach(n => {
      const o = document.createElement("option");
      o.value = n;
      o.textContent = n;
      if (n === d.web.ip || (n === "自动" && !d.web.ip)) o.selected = true;
      ip.appendChild(o);
    });
  } catch { /* 瞬断 */ }
}
$("#st-save").addEventListener("click", async () => {
  const r = await put("/settings", {
    clockHint: $v("#st-clock"), vjHint: $v("#st-vj"), kbHint: $v("#st-kb"),
    autoAdvance: $("#st-auto").checked, autoPlay: $("#st-cont").checked,
    topMost: $("#st-top").checked, switchConfirm: $("#st-confirm").checked,
    exitCloseApps: $("#st-exit").checked,
    projectsRoot: $v("#st-root"), dawExe: $v("#st-exe"),
    autoSave: $("#st-autosave").checked,
    web: {enabled: $("#st-web").checked, mode: $v("#st-mode"),
          serverPort: Number($v("#st-srv")), appPort: Number($v("#st-app")),
          taskerPort: Number($v("#st-tsk")), midiIn: $v("#st-pg"),
          ip: $v("#st-ip") === "自动" ? "" : $v("#st-ip")}});
  if (r.ok) toast("设置已保存并应用");
});
refreshPedal();
refreshKbd();
refreshVj();
refreshSettings();
setInterval(refreshPlaylist, 1500);
setInterval(refreshPoints, 1500);
setInterval(refreshLog, 1500);
refreshPlaylist();
refreshPoints();
refreshLog();
