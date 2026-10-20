/* 新前端：导航 + /state 轮询渲染 + 各页功能。组件类名见 app.css。 */
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

// JS 错误取证：截图脚本从 window.__errs 读走
window.__errs = [];
addEventListener("error", e => window.__errs.push(
    e.message + "@" + (e.filename || "").split("/").pop() + ":" + e.lineno));
addEventListener("unhandledrejection",
                e => window.__errs.push("rejection: "
                    + String(e.reason && e.reason.stack || e.reason)
                      .split("\n").slice(0, 2).join(" ⏎ ")));

let stState = null, plCache = null, stCur = null;

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

// ---- 两段式确认（替代 confirm）：首点变「确认…？」3s 回弹，再点执行 ----
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

// ---- popover（替代 prompt 链 / ⋯ 菜单）：锚定面板，外点/ESC 关闭 ----
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
function menuItem(box, text, cls, fn) {
  const b = document.createElement("button");
  b.className = "menu-item" + (cls ? " " + cls : "");
  b.textContent = text;
  b.addEventListener("click", () => fn(b));
  box.appendChild(b);
  return b;
}

// ---- 导航 ----
let curPage = "console", booted = false;
function nav(page) {
  curPage = page;
  $$(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "p-" + page));
  // 切页即时拉数据（不等轮询节拍；boot 前各页状态量尚未初始化）
  if (booted) {
    if (page === "console" || page === "setlist") refreshPlaylist();
    else if (page === "points") refreshPoints();
    else if (page === "log") refreshLog();
  }
}
$$(".nav").forEach(b => b.addEventListener("click", () => nav(b.dataset.page)));
document.addEventListener("click", ev => {
  const b = ev.target.closest("[data-nav]");
  if (b) nav(b.dataset.nav);
});
nav("console");
const pageOn = (...ids) => ids.some(id => curPage === id);

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
  } else if (reduceMotion()) {
    body.classList.remove("show");
  } else {
    $("#main").classList.add("leaving");
    setTimeout(() => {
      $("#main").classList.remove("leaving");
      body.classList.remove("show");
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

// ---- HTTP（失败可见，不静默）----
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
async function post(path, body) {
  try {
    const r = await fetch(path, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) toast(j.error || "操作失败", "err");
    return j;
  } catch {
    toast("操作失败（网络）", "err");
    return {ok: false};
  }
}

// ---- 走带 ----
$$("#transport [data-cmd]").forEach(b =>
    b.addEventListener("click", () => cmd(b.dataset.cmd)));
const SMART = {stopped: ["▶ 开始", "play"], playing: ["⏸ 暂停", "pause"],
               paused: ["▷ 继续", "resume"]};
$("#smart").addEventListener("click", () => {
  const a = $("#smart").dataset.action;
  if (a) cmd(a);
});
$("#next-go").addEventListener("click", () => {
  if (typeof stCur === "number") cmd("switch", stCur + 1);
});

const fmt = s => {
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

// ---- 主控台渲染 ----
let pedalPrevAge = null;
function render(st) {
  if (!$("#p-console")) return;
  $("#now").textContent = st.open ? st.projName : "（无打开的工程）";
  // 状态 chip + 时钟
  const chip = $("#tchip");
  const ts = st.busy ? "busy" : (st.tstate || "stopped");
  chip.className = "chip " + (st.tstate || "stopped");
  chip.textContent = st.busy ? "切换中…" :
      {playing: "播放中", paused: "已暂停", stopped: "未在播放"}[st.tstate]
      || "未在播放";
  const live = st.tstate === "playing" || st.tstate === "paused";
  const pos = live ? st.pos : 0;
  $("#clock").textContent = "已播 " + fmt(pos) + " · 剩余 " +
      fmt(Math.max(0, (st.dur || 0) - pos));
  $("#clock").classList.toggle("dim", !live);
  $("#clock").classList.toggle("playing", live);
  $("#barfill").style.width =
      st.dur > 0 ? Math.min(100, st.pos / st.dur * 100) + "%" : "0";
  // 智能三态播放钮
  const m = st.busy ? ["切换中…", ""] : (SMART[st.tstate] || SMART.stopped);
  const smart = $("#smart");
  smart.textContent = m[0];
  smart.dataset.action = m[1];
  smart.disabled = !!st.busy;
  $$("#transport [data-cmd]").forEach(b => b.disabled = !!st.busy);
  // NEXT 卡
  const rows = st.songs || [];
  const cur = typeof st.cur === "number" ? st.cur : -1;
  const nx = rows[cur + 1];
  $("#next").textContent = nx ? nx.name : (rows.length ? "（末尾）" : "—");
  $("#next").classList.toggle("has", !!nx);
  $("#next-go").disabled = !nx || !!st.busy;
  // 曲目列表（三态；签名不变不动 DOM，悬停态不被轮询冲掉）
  const list = $("#console-list");
  const sig = rows.length + ":" + cur + ":" +
      rows.map(r => r.name + r.dur).join("|");
  if (list.dataset.sig !== sig) {
    list.dataset.sig = sig;
    list.innerHTML = "";
    rows.forEach((s, i) => list.appendChild(consoleRow(s, i, cur)));
  }
  $("#console-empty").hidden = rows.length > 0;
  $("#tstate").textContent = chip.textContent;
  lamps(st);
  drawChips(st, cur);
  pedalLive(st);
  renderVjState();
  renderAppAddr();
}
function consoleRow(s, i, cur) {
  const div = document.createElement("div");
  div.className = "row" + (i === cur ? " cur" : "")
      + (i < cur ? " played" : "");
  div.dataset.i = i;
  const no = document.createElement("span");
  no.className = "no";
  no.textContent = (i + 1) + ".";
  const eq = document.createElement("span");
  eq.className = "eq";
  eq.innerHTML = "<i></i><i></i><i></i>";
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = s.name;
  const go = document.createElement("button");
  go.className = "btn ghost xs go-switch";
  go.textContent = "切歌";
  go.addEventListener("click", ev => {
    ev.stopPropagation();
    cmd("switch", i);
  });
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = s.dur;
  div.append(no, eq, nm, go, dur);
  div.addEventListener("dblclick", () => cmd("switch", i));
  return div;
}

// ---- 状态灯（真实状态 + tooltip + 点击跳页）----
function lamps(st) {
  const set = (id, cls, tip) => {
    const el = $("#" + id);
    el.classList.remove("ok", "warn", "err");
    if (cls) el.classList.add(cls);
    el.dataset.tip = tip || "";
  };
  set("l-daw", st.open ? "ok" : "err",
      st.open ? "工程已打开：" + (st.projName || "") : "未打开工程");
  set("l-obs", st.obs ? "ok" : "warn",
      st.obs ? "obs-websocket 已连接" : "OBS 未连接");
  set("l-loop", st.loop ? "ok" : "warn",
      st.loop ? "loopMIDI 运行中" : "loopMIDI 未运行");
  const pr = st.pedalRemote;
  set("l-pedal", !pr ? "" : pr.link === "ok" ? "ok" : "warn",
      !pr ? "踩钉桥未就绪" : (pr.link === "ok"
      ? "转发链路健康（15s 内有信号）" : pr.link === "stale"
      ? "转发链路超时（>15s 无信号）" : "未收到转发信号"));
  set("l-hot", st.webIp ? "ok" : "",
      st.webIp ? "服务地址 " + st.webIp : "移动端遥控服务未启动");
  set("l-remote", st.ready ? "ok" : "err",
      st.ready ? "遥控服务就绪" : "遥控服务未就绪");
}
$$(".lamp").forEach(el => el.addEventListener("click",
    () => nav(el.dataset.pageNav)));

// ---- 定位点快跳 chips（当前曲目的定位点 → /cmd switch 即 GOTO）----
function drawChips(st, cur) {
  const box = $("#chips");
  if (!plCache || cur < 0 || !plCache.pl[cur]) {
    box.hidden = true;
    return;
  }
  const baseKey = plCache.pl[cur].key.startsWith("pt:")
      ? plCache.pl[cur].key.slice(3).split("#")[0] : plCache.pl[cur].key;
  const items = [];
  plCache.pl.forEach((it, i) => {
    if (it.pt && it.key.startsWith("pt:")
        && it.key.slice(3).split("#")[0] === baseKey)
      items.push({i, name: it.name.split(" · ").pop(), cur: i === cur});
  });
  if (!items.length) {
    box.hidden = true;
    return;
  }
  const sig = baseKey + ":" + items.map(x => x.name + x.cur).join("|");
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.hidden = false;
  box.innerHTML = "";
  const lbl = document.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = "定位点快跳";
  box.appendChild(lbl);
  items.forEach(x => {
    const b = document.createElement("button");
    b.className = "chip-btn" + (x.cur ? " cur" : "");
    b.textContent = x.name;
    b.title = "点击切到该定位点（同工程免重开）";
    b.addEventListener("click", () => cmd("switch", x.i));
    box.appendChild(b);
  });
}

// ---- 踩钉事件闪灯（lastEventAge 回落=新事件）----
function pedalLive(st) {
  const pr = st.pedalRemote;
  const link = $("#pedal-link");
  if (pr) {
    link.textContent = pr.link === "ok" ? "健康" :
        pr.link === "stale" ? "超时" : "无信号";
    link.className = "stat-big " +
        (pr.link === "ok" ? "ok" : pr.link === "stale" ? "warn" : "err");
  } else {
    link.textContent = "—";
    link.className = "stat-big";
  }
  const age = pr && pr.lastEventAge;
  const flash = $("#pedal-flash");
  if (typeof age === "number") {
    if (pedalPrevAge !== null && age < pedalPrevAge) {
      flash.classList.add("on");
      setTimeout(() => flash.classList.remove("on"), 600);
    }
    pedalPrevAge = age;
    $("#pedal-when").textContent = age < 60
        ? Math.round(age) + " 秒前" : "久前";
  } else {
    $("#pedal-when").textContent = "";
  }
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

// ================= 编排页 =================
let plSel = null, slQ = "", dragKey = null;

function libRow(s, inPl) {
  const div = document.createElement("div");
  div.className = "row";
  div.dataset.k = s.key;
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = s.team;
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = s.name;
  const add = document.createElement("button");
  add.className = "lib-add";
  add.textContent = inPl ? "已加入" : "＋";
  if (inPl) add.classList.add("in-pl");
  else add.addEventListener("click", ev => {
    ev.stopPropagation();
    plcmd("add", s.key);
  });
  div.append(tag, nm, add);
  div.addEventListener("click", () => { if (!inPl) plcmd("add", s.key); });
  return div;
}
function drawLib() {
  if (!plCache) return;
  const lib = $("#lib");
  const inPl = new Set(plCache.pl.map(it => it.key.startsWith("pt:")
      ? it.key.slice(3).split("#")[0] : it.key));
  const sig = plCache.lib.map(s => s.key + (inPl.has(s.key) ? 1 : 0)).join("|")
      + "#" + slQ;
  if (lib.dataset.sig === sig) return;
  lib.dataset.sig = sig;
  lib.innerHTML = "";
  $("#lib-n").textContent = plCache.lib.length + " 首";
  plCache.lib.forEach(s => {
    const hit = !slQ || (s.name + " " + s.team).toLowerCase().includes(slQ);
    if (!hit) return;
    const r = libRow(s, inPl.has(s.key));
    lib.appendChild(r);
  });
}
function plRow(i, it) {
  const div = document.createElement("div");
  div.className = "row";
  div.dataset.k = it.key;
  const handle = document.createElement("span");
  handle.className = "drag";
  handle.title = "拖拽排序";
  handle.textContent = "⠿";
  const no = document.createElement("span");
  no.className = "no";
  const nm = document.createElement("span");
  nm.className = "nm";
  const dur = document.createElement("span");
  dur.className = "dur";
  const ops = document.createElement("span");
  ops.className = "ops";
  const del = document.createElement("button");
  del.className = "danger";
  del.textContent = "✕";
  del.title = "移出播放列表";
  del.addEventListener("click", ev => {
    ev.stopPropagation();
    armConfirm(del, () => plcmd("remove", it.key), "确认移除？");
  });
  ops.appendChild(del);
  div.append(handle, no, nm, dur, ops);
  div.addEventListener("click", () => { plSel = it.key; markPlSel(); });
  // 拖拽排序：手柄按下才允许拖（不挡文本选择/点击）
  handle.addEventListener("pointerdown", () => div.draggable = true);
  div.addEventListener("dragstart", ev => {
    dragKey = it.key;
    div.classList.add("dragging");
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", it.key);
  });
  div.addEventListener("dragend", () => {
    div.draggable = false;
    div.classList.remove("dragging");
    $$("#pl .row").forEach(r =>
        r.classList.remove("drop-above", "drop-below"));
    dragKey = null;
  });
  div.addEventListener("dragover", ev => {
    if (!dragKey || dragKey === it.key) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
    const r = div.getBoundingClientRect();
    const before = ev.clientY < r.top + r.height / 2;
    div.classList.toggle("drop-above", before);
    div.classList.toggle("drop-below", !before);
  });
  div.addEventListener("dragleave", () =>
      div.classList.remove("drop-above", "drop-below"));
  div.addEventListener("drop", ev => {
    if (!dragKey || dragKey === it.key) return;
    ev.preventDefault();
    const r = div.getBoundingClientRect();
    const before = ev.clientY < r.top + r.height / 2;
    const tIdx = plCache.pl.findIndex(x => x.key === it.key)
        + (before ? 0 : 1);
    const from = plCache.pl.findIndex(x => x.key === dragKey);
    div.classList.remove("drop-above", "drop-below");
    movePl(from, from < tIdx ? tIdx - 1 : tIdx);
  });
  no.textContent = (i + 1) + ".";
  nm.textContent = it.name;
  dur.textContent = it.dur;
  return div;
}
async function movePl(from, to) {
  if (from === to || from < 0 || to < 0 || !plCache) return;
  const item = plCache.pl[from];
  const times = Math.abs(to - from);
  const op = to > from ? "down" : "up";
  dragKey = null;                      // 拖拽收尾：恢复轮询重绘
  for (let n = 0; n < times; n++)
    await post("/plcmd", {op, key: item.key});
  toast("已移动《" + item.name + "》");
  refreshPlaylist();
}
function drawPl() {
  if (!plCache || dragKey) return;     // 拖拽中不重绘（防行从指针下消失）
  const el = $("#pl");
  const want = plCache.pl.map(it => it.key).join("|");
  const curIdx = typeof stCur === "number" ? stCur : -1;
  const sel = plSel;
  const sig = want + "#" + curIdx + "#" + sel + "#" + slQ
      + "#" + plCache.pl.map(x => x.name + x.dur).join("~");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = "";
  plCache.pl.forEach((it, i) => {
    if (!slQ || it.name.toLowerCase().includes(slQ)) {
      const r = plRow(i, it);
      if (i === curIdx) r.classList.add("cur");
      if (it.key === sel) r.classList.add("sel");
      el.appendChild(r);
    }
  });
  $("#pl-n").textContent = plCache.pl.length + " 首";
  $("#pl-empty").hidden = plCache.pl.length > 0;
}
function markPlSel() {
  $$("#pl .row").forEach(r => r.classList.toggle("sel", r.dataset.k === plSel));
}
$("#sl-search").addEventListener("input", e => {
  slQ = e.target.value.trim().toLowerCase();
  drawLib();
  drawPl();
});
$("#pl-more").addEventListener("click", ev => {
  popover(ev.currentTarget, (box, close) => {
    menuItem(box, "清空播放列表", "danger", b =>
        armConfirm(b, () => { plcmd("clear", ""); close(); }, "确认清空？"));
  });
});
async function refreshPlaylist() {
  try {
    const r = await fetch("/playlist");
    if (!r.ok) return;
    const d = await r.json();
    plCache = d;
    stCur = d.cur;
    drawLib();
    drawPl();
  } catch { /* 瞬断 */ }
}
async function plcmd(op, key) {
  const j = await post("/plcmd", {op, key});
  if (j.ok && op === "add") toast("已加入播放列表");
  refreshPlaylist();
}

// ================= 定位点页 =================
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
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? h + ":" + String(m).padStart(2, "0") + ":" +
             String(x).padStart(2, "0")
           : m + ":" + String(x).padStart(2, "0");
};
// 时码即时校验：红框+原因文案，保存随校验禁用（根治静默失败）
function validatePt() {
  if (ptSel === null) return false;
  const s = parseTC($("#pt-s").value), e = parseTC($("#pt-e").value);
  let msg = "";
  if (s === null) msg = "开始时码格式应为 分:秒（如 3:33）或秒数";
  else if (e === null) msg = "结束时码格式应为 分:秒（如 3:33）或秒数";
  else if (e <= s) msg = "结束必须晚于开始";
  $("#pt-s").classList.toggle("invalid", s === null);
  $("#pt-e").classList.toggle("invalid", e === null || (s !== null && e <= s));
  $("#pt-save").disabled = !!msg;
  $("#pt-save").title = msg;
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
    if (sel.dataset.sig !== sig) {       // 无变化不重建：不打断展开选择
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
    // 主列表（差量：签名不变不动 DOM）
    const el = $("#pt-list");
    const lsig = ptProj + ":" + dd.points.map(
        p => p.id + p.name + p.start + p.end).join("|") + ":" + ptSel;
    if (el.dataset.sig !== lsig) {
      el.dataset.sig = lsig;
      el.innerHTML = "";
      dd.points.forEach(p => {
        const div = document.createElement("div");
        div.className = "row" + (ptSel === p.id ? " cur" : "");
        div.dataset.id = p.id;
        const nm = document.createElement("span");
        nm.className = "nm";
        nm.textContent = p.name;
        const dur = document.createElement("span");
        dur.className = "dur";
        dur.textContent = fmtTC(p.start) + " → " + fmtTC(p.end)
            + "（" + fmtTC(p.end - p.start) + "）";
        div.append(nm, dur);
        div.addEventListener("click", () => selectPt(p.id));
        el.appendChild(div);
      });
      if (!dd.points.length)
        el.innerHTML = "<div class='empty-state sm'>还没有定位点，"
            + "点上方「添加」以当前时码为起点新建</div>";
    }
    // 编辑卡
    const has = ptSel !== null && dd.points.some(p => p.id === ptSel);
    $("#pt-none").hidden = has;
    $("#pt-form").hidden = !has;
    if (has) {
      const gi = ptGotoIndex();
      const g = $("#pt-goto");
      g.disabled = gi === null;
      g.title = gi === null
          ? "该定位点未启用或不在播放列表（启用后自动加入）" : "";
      drawTimeline(dd.points);
      validatePt();
    }
  } catch { /* 瞬断 */ }
}
function selectPt(id) {
  ptSel = id;
  const dd = ptData[ptProj];
  const p = dd && dd.points.find(x => x.id === id);
  if (p) {
    $("#pt-name").value = p.name;
    $("#pt-s").value = fmtTC(p.start);
    $("#pt-e").value = fmtTC(p.end);
  }
  refreshPoints();
}
function ptGotoIndex() {
  if (!plCache || ptSel === null || !ptProj) return null;
  const key = "pt:" + ptProj + "#" + ptSel;
  const i = plCache.pl.findIndex(x => x.key === key);
  return i >= 0 ? i : null;
}
function drawTimeline(points) {
  const tl = $("#pt-tl");
  if (!points.length) {
    tl.hidden = true;
    $("#pt-tl-max").textContent = "—";
    return;
  }
  const max = Math.max(1, ...points.map(p => p.end));
  const sig = points.map(p => p.id + ":" + p.start + ":" + p.end).join("|");
  if (tl.dataset.sig === sig) return;
  tl.dataset.sig = sig;
  tl.hidden = false;
  tl.innerHTML = "";
  points.forEach(p => {
    const s = document.createElement("i");
    s.className = "seg";
    s.style.left = (p.start / max * 100) + "%";
    s.style.width = (Math.max(1, (p.end - p.start) / max * 100)) + "%";
    s.title = p.name + " " + fmtTC(p.start) + "-" + fmtTC(p.end);
    tl.appendChild(s);
  });
  $("#pt-tl-max").textContent = fmtTC(max);
}
async function ptcmd(op, payload) {
  const j = await post("/ptcmd", {op, key: ptProj, payload: payload || {}});
  if (j.ok && op !== "move") toast("定位点已更新");
  refreshPoints();
  refreshPlaylist();
}
$("#pt-on").addEventListener("change", e =>
    ptcmd("toggle", {on: e.target.checked}));
$("#pt-proj").addEventListener("change", e => {
  ptProj = e.target.value;
  ptSel = null;
  $("#pt-form").hidden = true;
  $("#pt-none").hidden = false;
  refreshPoints();
});
$("#pt-add").addEventListener("click", () => ptcmd("add", {}));
$("#pt-del").addEventListener("click", ev => {
  if (ptSel !== null)
    armConfirm(ev.currentTarget, () => ptcmd("del", {id: ptSel}),
               "确认删除？");
});
$("#pt-save").addEventListener("click", () => {
  if (!validatePt()) return;
  ptcmd("edit", {id: ptSel, name: $("#pt-name").value,
                 start: parseTC($("#pt-s").value),
                 end: parseTC($("#pt-e").value)});
});
$("#pt-goto").addEventListener("click", () => {
  const i = ptGotoIndex();
  if (i !== null) {
    cmd("switch", i);
    toast("定位到《" + $("#pt-name").value + "》");
  }
});
$("#pt-up").addEventListener("click", () =>
    ptSel !== null && ptcmd("move", {id: ptSel, step: -1}));
$("#pt-down").addEventListener("click", () =>
    ptSel !== null && ptcmd("move", {id: ptSel, step: 1}));

// ================= 日志页（增量 append + 工具条）=================
let logLast = null;
function logLevel(msg) {
  if (/界面异常|失败|异常|错误/.test(msg)) return "err";
  if (/未生效|超时|降级|未就绪|忽略/.test(msg)) return "warn";
  return "";
}
function logLine(l) {
  const div = document.createElement("div");
  const lv = logLevel(l.msg);
  div.className = "ln" + (lv ? " " + lv : "");
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = new Date(l.t * 1000).toTimeString().slice(0, 8);
  const m = document.createElement("span");
  m.className = "m";
  m.textContent = l.msg;
  div.append(t, m);
  return div;
}
async function refreshLog() {
  if (!pageOn("log")) return;
  try {
    const r = await fetch("/log");
    if (!r.ok) return;
    const d = await r.json();
    const lines = d.lines || [];
    const el = $("#log");
    const auto = $("#lg-auto").checked;
    const stick = auto && (logLast === null
        || el.scrollTop + el.clientHeight >= el.scrollHeight - 40);
    const first = lines.length ? lines[0].t + "|" + lines[0].msg : "";
    if (logLast && first === logLast.first
        && lines.length >= logLast.count) {
      for (let i = logLast.count; i < lines.length; i++)
        el.appendChild(logLine(lines[i]));
    } else {
      el.innerHTML = "";                 // 首刷 / 环形缓冲回绕：全量重建
      lines.forEach(l => el.appendChild(logLine(l)));
    }
    logLast = {count: lines.length, first};
    if (stick) el.scrollTop = el.scrollHeight;
  } catch { /* 瞬断 */ }
}
$("#lg-filter").addEventListener("change", e =>
    $("#log").classList.toggle("hide-ok", e.target.value === "err"));
$("#lg-clear").addEventListener("click", ev => {
  if (logLast) logLast = {count: 0, first: ""};
  armConfirm(ev.currentTarget, () => {
    $("#log").innerHTML = "";
    toast("日志已清空（仅本页视图，环形缓冲不受影响）");
  }, "确认清空？");
});
$("#lg-copy").addEventListener("click", () => {
  const txt = $$("#log .ln").map(l => l.textContent).join("\n");
  copyText(txt, "日志已复制");
});
function copyText(txt, msg) {
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(txt)
        .then(() => toast(msg), () => fallbackCopy(txt, msg));
  else fallbackCopy(txt, msg);
}
function fallbackCopy(txt, msg) {
  const ta = document.createElement("textarea");
  ta.value = txt;
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    toast(msg);
  } catch {
    toast("复制失败", "err");
  }
  ta.remove();
}

// ================= 四页表单（踩钉/键盘/VJ/设置）=================
const $v = id => $(id).value;

// ---- 踩钉 ----
async function refreshPedal() {
  try {
    const d = await (await fetch("/pedal")).json();
    const c = $("#pedal-conn");
    c.textContent = d.connected ? "已连接" : "未连接";
    c.className = "stat-big " + (d.connected ? "ok" : "err");
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
  const r = await post("/pedalset", {
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
    const h = document.createElement("h4");
    h.textContent = "音 " + noteName(note) + "（MIDI " + note + "）音色";
    box.appendChild(h);
    const mk = (label, id, val) => {
      const l = document.createElement("label");
      l.textContent = label;
      const i = document.createElement("input");
      i.type = "number";
      i.min = "0";
      i.max = "127";
      i.id = id;
      if (val !== undefined && val !== null) i.value = val;
      l.appendChild(i);
      box.appendChild(l);
      return i;
    };
    const pc = mk("PC（0-127）", "sp-pc", slot ? slot.pc : 0);
    const msb = mk("MSB（可空）", "sp-msb",
                   slot && slot.msb !== undefined ? slot.msb : null);
    const lsb = mk("LSB（可空）", "sp-lsb",
                   slot && slot.lsb !== undefined ? slot.lsb : null);
    const err = document.createElement("div");
    err.className = "field-err";
    err.id = "sp-err";
    box.appendChild(err);
    const btns = document.createElement("div");
    btns.className = "btns";
    box.appendChild(btns);
    const check = () => {
      const bad = pc.value === "" || Number(pc.value) < 0
                  || Number(pc.value) > 127;
      err.textContent = bad ? "PC 须为 0-127 的数字" : "";
      okBtn.disabled = bad;
    };
    if (slot) {
      const clr = document.createElement("button");
      clr.className = "danger";
      clr.textContent = "清除";
      clr.addEventListener("click", () => {
        const cur = (inst === "ax" ? kbData.axSlots : kbData.slots) || {};
        delete cur[note];
        post("/slotset", {inst, slots: cur}).then(j => {
          if (j.ok) toast("音 " + noteName(note) + " 已清除");
          refreshKbd();
        });
        close();
      });
      btns.appendChild(clr);
    }
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    cancel.addEventListener("click", close);
    const okBtn = document.createElement("button");
    okBtn.className = "primary";
    okBtn.textContent = "保存";
    okBtn.addEventListener("click", () => {
      const s = {pc: Number(pc.value)};
      if (msb.value !== "") s.msb = Number(msb.value);
      if (lsb.value !== "") s.lsb = Number(lsb.value);
      const cur = (inst === "ax" ? kbData.axSlots : kbData.slots) || {};
      cur[note] = s;
      post("/slotset", {inst, slots: cur}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已保存");
        refreshKbd();
      });
      close();
    });
    btns.append(cancel, okBtn);
    pc.addEventListener("input", check);
    check();
    setTimeout(() => { pc.focus(); pc.select(); });
  });
}
function slotRow(note, slot, inst) {
  const div = document.createElement("div");
  div.className = "row";
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = "音 " + noteName(note);
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = slot ? ("PC " + slot.pc + (slot.msb !== undefined
      ? " · MSB " + slot.msb + " · LSB " + slot.lsb : "")) : "（未配置）";
  div.append(nm, dur);
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
    const build = (el, from, to, slots, inst) => {
      const sig = Object.keys(slots || {}).map(k =>
          k + ":" + JSON.stringify(slots[k])).join("|") + "#" + from;
      if (el.dataset.sig === sig) return;
      el.dataset.sig = sig;
      el.innerHTML = "";
      for (let n = from; n <= to; n++)
        el.appendChild(slotRow(n, (slots || {})[n], inst));
    };
    build($("#kb-slots"), 60, 69, kbData.slots, "juno");
    build($("#kb-axslots"), 72, 77, kbData.axSlots, "ax");
  } catch { /* 瞬断 */ }
}
$("#kb-save").addEventListener("click", async () => {
  const r = await post("/kbdset", {junoIn: $v("#kb-jin"),
    junoOut: $v("#kb-jout"), axIn: $v("#kb-ain"), axOut: $v("#kb-aout")});
  if (r.ok) toast("键盘端口已应用");
  refreshKbd();
});
const shift = (inst, total) => post("/kbdshift", {inst, total})
    .then(refreshKbd);
$("#kb-jup").addEventListener("click", () =>
    shift("juno", (kbData.junoShift || 0) + 1));
$("#kb-jdown").addEventListener("click", () =>
    shift("juno", (kbData.junoShift || 0) - 1));
$("#kb-jreset").addEventListener("click", () => shift("juno", 0));
$("#kb-aup").addEventListener("click", () =>
    shift("ax", (kbData.axShift || 0) + 1));
$("#kb-adown").addEventListener("click", () =>
    shift("ax", (kbData.axShift || 0) - 1));
$("#kb-areset").addEventListener("click", () => shift("ax", 0));

// ---- VJ 联动 ----
async function refreshVj() {
  try {
    const d = await (await fetch("/settings")).json();
    const sel = $("#vj-mon");
    const sig = (d.screens || []).join("|") + "#" + d.obs.projectorMonitor;
    if (sel.dataset.sig !== sig) {
      sel.dataset.sig = sig;
      sel.innerHTML = "";
      (d.screens || []).forEach(n => {
        const o = document.createElement("option");
        o.value = n;
        o.textContent = n;
        if (n === d.obs.projectorMonitor) o.selected = true;
        sel.appendChild(o);
      });
    }
    $("#vj-media").value = d.obs.mediaInput || "";
    $("#vj-root").value = d.obs.videoRoot || "";
    $("#vj-mute").checked = !!d.obs.vjMute;
    const ms = $("#vj-media-st");
    ms.textContent = d.obs.mediaInput || "未配置";
    ms.className = "stat-big " + (d.obs.mediaInput ? "ok" : "warn");
  } catch { /* 瞬断 */ }
}
$("#vj-save").addEventListener("click", async () => {
  const r = await post("/settings", {obs: {
    projectorMonitor: $v("#vj-mon"), mediaInput: $v("#vj-media"),
    videoRoot: $v("#vj-root"), vjMute: $("#vj-mute").checked}});
  if (r.ok) toast("VJ 配置已应用");
  refreshVj();
});
function renderVjState() {
  const el = $("#vj-obs");
  if (!stState) return;
  el.textContent = stState.obs ? "已连接" : "未连接";
  el.className = "stat-big " + (stState.obs ? "ok" : "warn");
}

// ---- 设置页（分组卡 + 脏标记）----
const ST_FIELDS = ["st-clock", "st-vj", "st-kb", "st-auto", "st-cont",
                   "st-top", "st-confirm", "st-exit", "st-root", "st-exe",
                   "st-autosave", "st-web", "st-mode", "st-srv", "st-app",
                   "st-tsk", "st-pg", "st-ip"];
let stBase = null;
const stSnap = () => ST_FIELDS.map(id => {
  const el = $("#" + id);
  return el.type === "checkbox" ? el.checked : el.value;
}).join("¦");
function stDirty() {
  const dirty = stBase !== null && stSnap() !== stBase;
  $("#st-dirty").hidden = !dirty;
  $("#st-save").disabled = dirty ? false : true;
}
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
    const isig = d.adapters.join("|") + "#" + (d.web.ip || "");
    if (ip.dataset.sig !== isig) {
      ip.dataset.sig = isig;
      ip.innerHTML = "";
      d.adapters.forEach(n => {
        const o = document.createElement("option");
        o.value = n;
        o.textContent = n;
        if (n === d.web.ip || (n === "自动" && !d.web.ip)) o.selected = true;
        ip.appendChild(o);
      });
    }
    stBase = stSnap();
    stDirty();
  } catch { /* 瞬断 */ }
}
$("#p-settings").addEventListener("input", stDirty);
$("#p-settings").addEventListener("change", stDirty);
$("#st-save").addEventListener("click", async () => {
  const r = await post("/settings", {
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
  if (r.ok) {
    toast("设置已保存并应用");
    refreshSettings();
  }
});
function renderAppAddr() {
  const wrap = $("#app-addr-wrap");
  const a = stState && stState.webIp && stState.appPort
      ? "http://" + stState.webIp + ":" + stState.appPort + "/" : "";
  wrap.hidden = !a;
  if (a) $("#app-addr").textContent = a;
}
$("#app-addr").addEventListener("click", () =>
    copyText($("#app-addr").textContent, "已复制：" + $("#app-addr").textContent));

// ---- 启动 ----
setInterval(poll, 500);
poll();
setInterval(() => {
  if (pageOn("console", "setlist")) refreshPlaylist();
}, 1500);
setInterval(() => { if (pageOn("points")) refreshPoints(); }, 1500);
setInterval(refreshLog, 1500);
refreshPlaylist();
refreshPoints();
refreshLog();
refreshPedal();
refreshKbd();
refreshVj();
refreshSettings();
booted = true;
