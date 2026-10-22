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
  stopRecord();       // 录制随层关闭统一收口（外点/ESC/清除都经此）
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
  if (booted && page !== curPage) {
    // 踩钉页进出 = 页面级静音开关（调绑/学习不误触发演出动作）
    if (curPage === "pedal") pedalMute(false);
    if (page === "pedal") {
      pedalMute(true);
      refreshPedal();
      refreshPedalDevices();
    }
  }
  curPage = page;
  $$(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "p-" + page));
  // 切页即时拉数据（不等轮询节拍；boot 前各页状态量尚未初始化）
  if (booted) {
    if (page === "console" || page === "setlist") refreshPlaylist();
    else if (page === "points") refreshPoints();
    else if (page === "log") refreshLog();
    else if (page === "kbd") refreshKbd();
  }
}
// 刷新/关闭兜底：踩钉页打开时页面卸载，静音必须撤（否则踩钉永久哑火）；
// 录制会话同理停口（否则 winmm 输入口被无限期占用）
addEventListener("pagehide", () => {
  const post = (body) => fetch("/pedal/mute", {method: "POST",
      keepalive: true, headers: {"Content-Type": "application/json"},
      body}).catch(() => {});
  if (curPage === "pedal")
    post(JSON.stringify({on: false}));
  if (recInst)
    fetch("/kbdrecord", {method: "POST", keepalive: true,
                         headers: {"Content-Type": "application/json"},
                         body: JSON.stringify({inst: recInst,
                                               stop: true})}).catch(() => {});
});
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
// 切歌统一入口（全部 switch 命令经此）：设置开了「切歌确认」且当前有
// 打开工程时走两段式（首点武装按钮，3 秒内再操作执行），否则直发。
// dbl=双击行场景：按钮是 hover 才现的行内「切歌」，武装时补 toast 提示；
// 双击的第二击已执行发包，随后的 dblclick 事件用 swLastFire 挡掉不重复武装。
let swLastFire = 0;
function switchTo(i, btn, dbl) {
  const st = stState;
  const fire = () => { swLastFire = Date.now(); cmd("switch", i); };
  if (dbl && Date.now() - swLastFire < 400) return;
  if (st && st.confirm && st.open && typeof st.cur === "number"
      && i !== st.cur) {
    if (dbl && btn && !btn.dataset.armed)
      toast("再操作一次确认切换");
    armConfirm(btn, fire, "确认切换？");
    return;
  }
  fire();
}
$("#next-go").addEventListener("click", () => {
  // 无当前曲（未恢复/游离）时 NEXT 卡显示的是第一首：切换目标随之落 0
  switchTo(typeof stCur === "number" ? stCur + 1 : 0, $("#next-go"));
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
  // 全停是切换卡死时的安全阀（后端 busy 放行），其余走带键 busy 期禁用
  $$("#transport [data-cmd]").forEach(b =>
      b.disabled = !!st.busy && b.dataset.cmd !== "panic");
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
  // 切歌后键盘页跟随刷新（曲目下拉/映射换到新歌）
  if (pageOn("kbd") && st.cur !== render.kbCur) {
    render.kbCur = st.cur;
    if (!kbEditKey) refreshKbd();   // 显式选中曲时不动用户的选择
  }
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
    switchTo(i, go);
  });
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = s.dur;
  div.append(no, eq, nm, go, dur);
  div.addEventListener("dblclick", () => switchTo(i, go, true));
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
  const off = () => {          // 隐藏时清残留（含签名：同数据回来要重建）
    box.hidden = true;
    box.innerHTML = "";
    delete box.dataset.sig;
  };
  if (!plCache || !plCache.pl || cur < 0 || !plCache.pl[cur]) {
    off();
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
    off();
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
    b.addEventListener("click", () => switchTo(x.i, b));
    box.appendChild(b);
  });
}

// ---- 踩钉事件闪灯（lastEventAge 回落=新事件）----
function pedalLive(st) {
  const pr = st.pedalRemote;
  const link = $("#pedal-link");
  const sub = $("#pedal-link-sub");
  if (pr) {
    link.textContent = pr.link === "ok" ? "健康" :
        pr.link === "stale" ? "超时" : "无信号";
    link.className = "stat-big " +
        (pr.link === "ok" ? "ok" : pr.link === "stale" ? "warn" : "err");
    sub.textContent = pr.unknown > 0
        ? "未识别键 " + pr.unknown + " 个（链路活但键路不通）"
        : (pr.enabled ? "心跳正常" : "未启用");
    sub.className = "dim sm-txt" + (pr.unknown > 0 ? " warn-txt" : "");
  } else {
    link.textContent = "—";
    link.className = "stat-big";
    sub.textContent = "";
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

// ---- 编排时长三态（自动绿/手动黄/未知红「未知」；pt=段长按绿显示）----
const durCls = src => src === "manual" ? "dur-manual"
    : src === "unknown" ? "dur-unknown" : "dur-auto";
const durText = it => it.durSrc === "unknown" ? "未知" : (it.dur || "未知");

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
  const dur = document.createElement("span");
  dur.className = "dur " + durCls(s.durSrc);
  dur.textContent = durText(s);
  const add = document.createElement("button");
  add.className = "lib-add";
  add.textContent = inPl ? "已加入" : "＋";
  if (inPl) add.classList.add("in-pl");
  else add.addEventListener("click", ev => {
    ev.stopPropagation();
    plcmd2("add", s.key);
  });
  div.append(tag, nm, dur, add);
  div.addEventListener("click", () => { if (!inPl) plcmd2("add", s.key); });
  return div;
}
function drawLib() {
  if (!plCache || !plCache.lib || !plCache.pl) return;
  const lib = $("#lib");
  // 精确键匹配：素材库视图（含展开的 pt 条目）与播放列表同一键空间，
  // 部分移除的点条目要能单独加回（宿主归一会把它们全标「已加入」）
  const inPl = new Set(plCache.pl.map(it => it.key));
  const sig = plCache.lib.map(s => s.key + (inPl.has(s.key) ? 1 : 0)
      + (s.durSrc || "") + (s.dur || "")).join("|") + "#" + slQ;
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
// 播放列表行 ⋯ 菜单：写入时长（手动，B5）/重新识别（回工程内原值）
function plMoreMenu(anchor, it) {
  popover(anchor, (box, close) => {
    menuItem(box, "写入时长…", "", () => {
      close();
      durPopover(anchor, it);
    });
    menuItem(box, "重新识别时长", "", () => {
      plcmd2("redict", it.key);
      close();
    });
  });
}
// 时长输入严格版（与后端 parse_mmss 对齐：拒 0/空段/科学计数/inf/nan/超界）
const parseDur = t => {
  const m = String(t || "").trim().match(/^(\d+)(?::(\d{1,2}))?$/);
  if (!m) return null;
  const v = m[2] !== undefined ? Number(m[1]) * 60 + Number(m[2])
      : Number(m[1]);
  return v > 0 && v < 1e7 ? v : null;
};
function durPopover(anchor, it) {
  popover(anchor, (box, close) => {
    const h = document.createElement("h4");
    h.textContent = "写入时长";
    box.appendChild(h);
    const sub = document.createElement("div");
    sub.className = "dim sm-txt";
    sub.textContent = it.name;
    box.appendChild(sub);
    const l = document.createElement("label");
    l.textContent = "秒数或 分:秒";
    const inp = document.createElement("input");
    inp.placeholder = "如 213 或 3:33";
    inp.inputMode = "numeric";
    if (it.durSrc !== "unknown" && it.dur) inp.value = it.dur;
    l.appendChild(inp);
    box.appendChild(l);
    const err = document.createElement("div");
    err.className = "field-err";
    box.appendChild(err);
    const btns = document.createElement("div");
    btns.className = "btns";
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    cancel.addEventListener("click", close);
    const okBtn = document.createElement("button");
    okBtn.className = "primary";
    okBtn.textContent = "写入";
    okBtn.addEventListener("click", () => {
      if (parseDur(inp.value) === null) {
        err.textContent = "格式应为 秒数 或 分:秒（如 3:33）";
        return;
      }
      plcmd2("setdur", it.key, {dur: inp.value.trim()});
      close();
    });
    btns.append(cancel, okBtn);
    box.appendChild(btns);
    setTimeout(() => { inp.focus(); inp.select(); });
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
  dur.className = "dur " + durCls(it.durSrc);
  dur.textContent = durText(it);
  if (it.durSrc === "manual")
    dur.title = "手动时长（⋯ 菜单可重新识别）";
  const ops = document.createElement("span");
  ops.className = "ops";
  const mk = (txt, title, cls) => {
    const b = document.createElement("button");
    b.textContent = txt;
    b.title = title;
    if (cls) b.className = cls;
    ops.appendChild(b);
    return b;
  };
  const up = mk("↑", "上移");
  up.addEventListener("click", ev => {
    ev.stopPropagation();
    movePl(i, i - 1);
  });
  const down = mk("↓", "下移");
  down.addEventListener("click", ev => {
    ev.stopPropagation();
    movePl(i, i + 1);
  });
  const more = mk("⋯", "写入时长 / 重新识别");
  more.addEventListener("click", ev => {
    ev.stopPropagation();
    plMoreMenu(more, it);
  });
  const del = mk("✕", "移出播放列表", "danger");
  del.addEventListener("click", ev => {
    ev.stopPropagation();
    armConfirm(del, () => plcmd2("remove", it.key), "确认移除？");
  });
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
  return div;
}
async function movePl(from, to) {
  if (from === to || from < 0 || to < 0 || !plCache
      || to >= plCache.pl.length) return;
  const item = plCache.pl[from];
  dragKey = null;                      // 拖拽收尾：恢复轮询重绘
  const j = await post("/plcmd", {op: "move", payload: {from, to}});
  if (j.ok) toast("已移动《" + item.name + "》");
  refreshPlaylist();
}
function drawPl() {
  if (!plCache || !plCache.pl || dragKey) return;  // 拖拽中不重绘（防行从指针下消失）
  const el = $("#pl");
  const want = plCache.pl.map(it => it.key).join("|");
  const curIdx = typeof stCur === "number" ? stCur : -1;
  const sel = plSel;
  const sig = want + "#" + curIdx + "#" + sel + "#" + slQ
      + "#" + plCache.pl.map(x => x.name + x.dur + (x.durSrc || ""))
          .join("~");
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
        armConfirm(b, () => { plcmd2("clear", ""); close(); }, "确认清空？"));
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
async function plcmd2(op, key, payload) {
  const j = await post("/plcmd", {op, key, payload: payload || {}});
  if (j.ok && op === "add") toast("已加入播放列表");
  if (j.ok && op === "setdur") toast("时长已写入");
  if (j.ok && op === "redict") toast("重新识别中…");
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
    switchTo(i, $("#pt-goto"));
    if (!$("#pt-goto").dataset.armed)
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

// ---- 踩钉（六项绑定表 + 学习/清除 + 设备枚举 + 页面静音）----
const PD_ACTIONS = [["pause", "暂停/继续"], ["play", "开始"],
                    ["rewind", "回零"], ["panic", "全停"],
                    ["prev", "上一首"], ["next", "下一首"]];
const HID_NAMES = {0x08: "退格", 0x0D: "回车", 0x20: "空格",
                   0x21: "上翻页", 0x22: "下翻页", 0x23: "End", 0x24: "Home",
                   0x25: "左", 0x26: "上", 0x27: "右", 0x28: "下",
                   0x2E: "Delete", 0xAD: "静音", 0xAE: "音量−",
                   0xAF: "音量＋", 0xB0: "下一曲", 0xB1: "上一曲",
                   0xB2: "停止", 0xB3: "播放/暂停"};
const hidName = vk => vk >= 0x70 && vk <= 0x87 ? "F" + (vk - 0x6F)
    : (vk >= 0x30 && vk <= 0x39) || (vk >= 0x41 && vk <= 0x5A)
      ? String.fromCharCode(vk)
    : HID_NAMES[vk] || "VK 0x" + vk.toString(16).toUpperCase()
        .padStart(2, "0");
const pdActionName = a => (PD_ACTIONS.find(x => x[0] === a) || [ "", a ])[1];
let pdData = null, pdFormInit = false, learnAction = null, learnTimer = null;

function pedalMute(on) {
  fetch("/pedal/mute", {method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({on})}).catch(() => {});
}

async function refreshPedalDevices() {
  try {
    const d = await (await fetch("/pedal/devices")).json();
    const sel = $("#pd-dev");
    const cur = pdData ? pdData.deviceHint : "";
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "（不区分来源，仅 MIDI 踩钉）";
    sel.appendChild(none);
    let hit = !cur;
    (d.devices || []).forEach(dev => {
      const o = document.createElement("option");
      o.value = dev[0];
      o.textContent = dev[1];
      if (dev[0] === cur) { o.selected = true; hit = true; }
      sel.appendChild(o);
    });
    if (cur && !hit) {            // 幽灵项：已选设备当前不在场
      const o = document.createElement("option");
      o.value = cur;
      o.textContent = cur + "（离线）";
      o.selected = true;
      sel.appendChild(o);
    }
  } catch { /* 瞬断 */ }
}

function bindText(d, action) {
  const g = {single: "", double: "·双踩"}[d.gestures[action] || "single"];
  const parts = [];
  if (d.binds[action] !== undefined)
    parts.push("CC " + d.binds[action] + g);
  if (d.hid[action] !== undefined)
    parts.push("按键 " + hidName(d.hid[action]) + g);
  return parts.join("＋") || "未设置";
}

function drawBinds(d) {
  const el = $("#pd-binds");
  const sig = JSON.stringify([d.binds, d.hid, d.gestures, learnAction]);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = "";
  PD_ACTIONS.forEach(([a, name]) => {
    const row = document.createElement("div");
    row.className = "bind-row";
    row.dataset.action = a;
    const nm = document.createElement("span");
    nm.className = "b-name";
    nm.textContent = name;
    const bd = document.createElement("span");
    bd.className = "b-bind" + (bindText(d, a) !== "未设置" ? " has" : "");
    bd.textContent = bindText(d, a);
    const learn = document.createElement("button");
    learn.className = "btn ghost sm b-learn";
    learn.textContent = a === learnAction ? "学习中…" : "学习";
    learn.addEventListener("click", () => startLearn(a));
    const clr = document.createElement("button");
    clr.className = "btn danger sm";
    clr.textContent = "清除";
    clr.addEventListener("click", () => clearBind(a));
    row.append(nm, bd, learn, clr);
    el.appendChild(row);
  });
}

async function refreshPedal(force) {
  try {
    const d = await (await fetch("/pedal")).json();
    pdData = d;
    const c = $("#pedal-conn");
    // 三态：无绑定提示 / 通道异常警示 / 静音中（本页打开期间，如实标注）
    let txt = "已静音", cls = "";
    if (!Object.keys(d.binds || {}).length
        && !Object.keys(d.hid || {}).length) {
      txt = "无绑定"; cls = "";
    } else if (d.hid && Object.keys(d.hid).length && !d.deviceOnline
               && !d.deviceHint) {
      txt = "未选设备"; cls = "warn";
    } else if (d.hint && !d.connected && !d.hidActive) {
      txt = "口未连"; cls = "warn";
    } else if (d.muted) {
      txt = "已静音（关本页恢复）";
    } else {
      txt = "已连接"; cls = "ok";
    }
    c.textContent = txt;
    c.className = "stat-big " + cls;
    $("#pedal-conn-sub").textContent =
        "MIDI " + (d.connected ? "连接" : "未连")
        + " · 设备 " + (d.deviceOnline ? "在线" : "离线")
        + " · HID 桥 " + (d.hidActive ? "运行" : "停");
    // 表单字段只在首拉/保存后回填：1.5s 轮询无条件回写会打穿正在编辑的
    // 提示名/双踩窗/勾选（改一半被洗掉、开关视觉回弹）
    if (force || !pdFormInit) {
      $("#pd-hint").value = d.hint || "";
      $("#pd-win").value = d.doubleWindow || 0.35;
      $("#pd-intercept").checked = !!d.intercept;
      $("#pd-remote").checked = !!d.remoteEnabled;
      pdFormInit = true;
    }
    drawBinds(d);
    if (!$("#pd-dev").options.length) refreshPedalDevices();
  } catch { /* 瞬断 */ }
}

async function pedalPayload() {
  const win = parseFloat($v("#pd-win"));
  return {hint: $v("#pd-hint"), deviceHint: $v("#pd-dev"),
          binds: pdData ? pdData.binds : {}, hid: pdData ? pdData.hid : {},
          gestures: pdData ? pdData.gestures : {},
          doubleWindow: isNaN(win) ? 0.35 : win,
          intercept: $("#pd-intercept").checked,
          remoteEnabled: $("#pd-remote").checked};
}

async function startLearn(action) {
  const j = await post("/pedal/learn", {action});
  if (!j.ok) return;
  // 未选设备时键盘踩钉抓不到（raw 归属门）——学习前提醒，别让人白等 8 秒
  if (pdData && !pdData.deviceHint
      && Object.keys(pdData.hid || {}).length)
    toast("未选输入设备：踏板按键不会被学习抓取（只等 MIDI CC）", "err");
  learnAction = action;
  drawBinds(pdData || {binds: {}, hid: {}, gestures: {}});
  pollLearn();
}
function pollLearn() {
  clearTimeout(learnTimer);
  learnTimer = setTimeout(async () => {
    if (learnAction === null) return;
    try {
      const d = await (await fetch("/pedal/learn")).json();
      const btn = $('.bind-row[data-action="%s"] .b-learn'
                    .replace("%s", learnAction));
      if (d.active) {
        if (btn) btn.textContent = "学习中 剩" + Math.ceil(d.remain) + "秒";
        pollLearn();
      } else if (d.done && d.result) {
        const act = learnAction;
        learnAction = null;
        toast("「" + pdActionName(act) + "」已绑定 " + d.result.label);
        refreshPedal();       // 服务端已迁移+落盘，拉新态重绘
      } else if (d.done && d.error) {
        learnAction = null;
        if (btn) btn.textContent = "学习";
        toast(d.error, "err");
      } else {
        learnAction = null;
        refreshPedal();
      }
    } catch {
      if (learnAction !== null) pollLearn();
    }
  }, 300);
}
async function clearBind(action) {
  if (!pdData) return;
  const binds = {...pdData.binds}, hid = {...pdData.hid},
        gestures = {...pdData.gestures};
  if (binds[action] === undefined && hid[action] === undefined) return;
  delete binds[action];
  delete hid[action];
  delete gestures[action];
  // 只发三个绑定表：隐式提交设备/提示名等未保存编辑会把表单之外的
  // 状态一并写死（旧窗 _clear 只动该动作）
  const r = await post("/pedalset", {binds, hid, gestures});
  if (r.ok) toast("已清除「" + pdActionName(action) + "」");
  refreshPedal();
}
$("#pd-refresh").addEventListener("click", refreshPedalDevices);
$("#pd-save").addEventListener("click", async () => {
  const r = await post("/pedalset", await pedalPayload());
  if (r.ok) {
    toast("踩钉配置已应用");
    refreshPedal(true);
  }
});

// ---- 键盘自动化（曲目选择 / 端口下拉 / 录制 / 人话描述 / 试听预检）----
let kbData = null, kbPortsInit = false, kbSongInit = false;
let kbEditKey = null;        // null=跟随已装载曲；否则显式选中的曲 key
let recTimer = null;
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A",
                    "A#", "B"];
const noteName = n => NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1);

// 端口下拉：value=候选序号（保存时映射回 name+dev）；仅在首拉/刷新/保存
// 后重建（轮询不回写未保存的选择），状态行每轮更新
function fillPortSel(id, list, hintKey) {
  const sel = $("#" + id);
  const entries = (kbData.ports || {})[list] || [];
  const names = entries.map(e => e.name);
  const sig = id + ":" + entries.map(e => e.name + "#" + e.dev).join("|")
      + "#" + (hintKey && !names.includes(hintKey) ? hintKey : "");
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  sel.dataset.list = list;
  sel.innerHTML = "";
  entries.forEach((e, i) => {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = e.label;
    o.title = e.name;
    sel.appendChild(o);
  });
  if (hintKey && !names.includes(hintKey)) {
    const o = document.createElement("option");   // 幽灵项保名（未上电）
    o.value = "-1";
    o.textContent = hintKey + "（未接入）";
    sel.appendChild(o);
  }
}
function portSelVal(id, hint, pos) {   // 选中当前配置对应的候选
  const sel = $("#" + id);
  const entries = (kbData.ports || {})[sel.dataset.list] || [];
  if (hint) {
    let i = pos > 1 ? entries.findIndex(e => e.name === hint
                                            && e.dev === pos - 1) : -1;
    if (i < 0) i = entries.findIndex(e => e.name === hint);
    if (i >= 0) { sel.value = String(i); return; }
  }
  sel.value = "-1";
}
function renderKbdPorts(force) {
  const ps = kbData.portStatus || {};
  const txt = s => !s || !s.hit ? "❌"
      : "✔" + (s.total > 1 ? "第" + s.pos + "个" : "");
  $("#kb-jstat").textContent =
      "输入 " + txt(ps.junoIn) + " ｜ 输出 " + txt(ps.junoOut);
  $("#kb-astat").textContent =
      "输入 " + txt(ps.axIn) + " ｜ 输出 " + txt(ps.axOut);
  if (!force && kbPortsInit) return;
  fillPortSel("kb-jin", "in", kbData.junoIn);
  fillPortSel("kb-jout", "out", kbData.junoOut);
  fillPortSel("kb-ain", "in", kbData.axIn);
  fillPortSel("kb-aout", "out", kbData.axOut);
  portSelVal("kb-jin", kbData.junoIn, (ps.junoIn || {}).pos);
  portSelVal("kb-jout", kbData.junoOut, (ps.junoOut || {}).pos);
  portSelVal("kb-ain", kbData.axIn, (ps.axIn || {}).pos);
  portSelVal("kb-aout", kbData.axOut, (ps.axOut || {}).pos);
  kbPortsInit = true;
}
function renderKbdSong(force) {
  const sel = $("#kb-song");
  const songs = kbData.songs || [];
  const sig = songs.map(x => x.key).join("|") + "#" + kbData.songKey;
  if (force || !kbSongInit || sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = "";
    const o = document.createElement("option");
    o.value = "";
    o.textContent = kbData.songKey ? "跟随已装载曲" : "（未装载曲目）";
    sel.appendChild(o);
    songs.forEach(x => {
      const op = document.createElement("option");
      op.value = x.key;
      op.textContent = x.name;
      op.title = x.key;
      sel.appendChild(op);
    });
    kbSongInit = true;
  }
  sel.value = kbEditKey || "";       // null → 跟随项
  if (sel.selectedIndex < 0) sel.value = "";
  $("#kb-status").textContent =
      (kbData.editSong && kbData.editSong.loading)
      ? "曲目映射装载中…"
      : (kbData.lastResult ? "最近切换：" + kbData.lastResult : "");
}

// 录制（琴上捕获）：popover 内启动，捕获值实时回填表单；closePop 统一停录
let recInst = null;
function stopRecord() {
  if (recInst) {
    post("/kbdrecord", {inst: recInst, stop: true});
    recInst = null;
    clearTimeout(recTimer);
  }
}
function pollRecord(inst, recBtn, pc, msb, lsb, err) {
  clearTimeout(recTimer);
  recTimer = setTimeout(async () => {
    try {
      const d = await (await fetch("/kbd/record")).json();
      if (d.active && d.inst === inst && recInst === inst) {
        if (d.slot) {
          pc.value = d.slot.pc;
          msb.value = d.slot.msb !== undefined ? d.slot.msb : "";
          lsb.value = d.slot.lsb !== undefined ? d.slot.lsb : "";
          err.textContent = "已捕获（可继续在琴上选）";
        } else {
          err.textContent = "录制中：请在琴上选音色…";
        }
        pollRecord(inst, recBtn, pc, msb, lsb, err);
      } else {
        recBtn.textContent = "录制";
      }
    } catch {
      recBtn.textContent = "录制";
    }
  }, 300);
}

// 音色槽编辑：popover 行内表单（PC 必填 0-127，MSB/LSB 可空）+ 录制捕获
function slotEditor(anchor, inst, note, slot) {
  popover(anchor, (box, close) => {
    // 编辑目标快照（key + 槽位字典都定格在打开时刻）：切歌后旧表单的
    // 保存必须仍写它打开时的那首歌与那份映射，不得混入新装载曲内容
    const editKey = kbEditKey || (kbData ? kbData.songKey : null);
    const src = kbData && kbData.editSong
        ? (inst === "ax" ? kbData.editSong.axSlots : kbData.editSong.slots)
        : {};
    const editSlots = {};
    Object.keys(src || {}).forEach(k => editSlots[Number(k)] = {...src[k]});
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
    // 无装载曲且未显式选曲：保存/清除目标不定（会整段替换保存时刻意外
    // 装载的曲）——禁止落盘，引导先选曲
    if (editKey === null) err.textContent = "未装载曲目：先在上方选择曲目";
    box.appendChild(err);
    const btns = document.createElement("div");
    btns.className = "btns";
    box.appendChild(btns);
    const check = () => {
      const bad = editKey === null || pc.value === ""
                  || Number(pc.value) < 0 || Number(pc.value) > 127
                  || (msb.value !== "" && (Number(msb.value) < 0
                      || Number(msb.value) > 127))
                  || (lsb.value !== "" && (Number(lsb.value) < 0
                      || Number(lsb.value) > 127));
      err.textContent = bad
          ? (editKey === null ? "未装载曲目：先在上方选择曲目"
             : "PC/MSB/LSB 须为 0-127 的数字") : "";
      okBtn.disabled = bad;
    };
    const curSlots = () => editSlots;
    if (slot && editKey !== null) {
      const clr = document.createElement("button");
      clr.className = "danger";
      clr.textContent = "清除";
      clr.disabled = editKey === null;
      clr.addEventListener("click", () => {
        const cur = curSlots();
        delete cur[note];
        post("/slotset", {inst, slots: cur, songKey: editKey}).then(j => {
          if (j.ok) toast("音 " + noteName(note) + " 已清除");
          refreshKbd();
        });
        close();
      });
      btns.appendChild(clr);
    }
    const rec = document.createElement("button");
    rec.textContent = "录制";
    rec.title = "到琴上选音色，捕获自动回填";
    rec.addEventListener("click", async () => {
      const j = await post("/kbdrecord", {inst});
      if (!j.ok) return;
      recInst = inst;
      rec.textContent = "录制中…";
      err.textContent = "录制中：请在琴上选音色…";
      pollRecord(inst, rec, pc, msb, lsb, err);
    });
    btns.appendChild(rec);
    const cancel = document.createElement("button");
    cancel.textContent = "取消";
    cancel.addEventListener("click", close);   // closePop 会统一停录
    const okBtn = document.createElement("button");
    okBtn.className = "primary";
    okBtn.textContent = "保存";
    okBtn.addEventListener("click", () => {
      const s = {pc: Number(pc.value)};
      if (msb.value !== "") s.msb = Number(msb.value);
      if (lsb.value !== "") s.lsb = Number(lsb.value);
      const cur = curSlots();
      cur[note] = s;
      post("/slotset", {inst, slots: cur, songKey: editKey}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已保存");
        refreshKbd();
      });
      close();
    });
    btns.append(cancel, okBtn);
    pc.addEventListener("input", check);
    msb.addEventListener("input", check);
    lsb.addEventListener("input", check);
    check();
    setTimeout(() => { pc.focus(); pc.select(); });
  });
}
function slotRow(note, slot, inst) {
  const div = document.createElement("div");
  div.className = "slot" + (slot ? "" : " unconfigured");
  const n = document.createElement("span");
  n.className = "s-note";
  n.textContent = "音 " + noteName(note);
  const pc = document.createElement("span");
  pc.className = "s-pc";
  pc.textContent = slot ? "PC " + slot.pc : "（未配置）";
  const sub = document.createElement("span");
  sub.className = "s-sub";
  sub.textContent = slot ? (slot.desc || "") : "";
  const test = document.createElement("button");
  test.className = "btn ghost xs s-test";
  test.textContent = "试";
  test.title = "向琴发送该音色（连通测试）";
  test.addEventListener("click", ev => {
    ev.stopPropagation();
    if (!slot) {
      toast("该音符未配置音色映射", "err");
      return;
    }
    post("/kbdtest", {inst, note}).then(j => {
      if (j.ok) toast("已发送测试：音 " + noteName(note));
    });
  });
  div.append(n, pc, sub, test);
  div.title = slot && slot.desc ? slot.desc : "点击编辑音色";
  div.addEventListener("click", () => slotEditor(div, inst, note, slot));
  return div;
}
async function refreshKbd(force) {
  try {
    const seq = refreshKbd.seq = (refreshKbd.seq || 0) + 1;
    const q = kbEditKey && kbData && kbEditKey !== kbData.songKey
        ? "?song=" + encodeURIComponent(kbEditKey) : "";
    const d = await (await fetch("/kbd" + q)).json();
    if (seq !== refreshKbd.seq) return;   // 旧响应不覆盖新请求
    kbData = d;
    $("#kb-jshift").textContent = kbData.junoShift;
    $("#kb-ashift").textContent = kbData.axShift;
    renderKbdPorts(force);
    renderKbdSong(force);
    const es = kbData.editSong || {slots: {}, axSlots: {}};
    const build = (el, from, to, slots, inst) => {
      const sig = Object.keys(slots || {}).map(k =>
          k + ":" + JSON.stringify(slots[k])).join("|");
      if (el.dataset.sig === sig) return;
      el.dataset.sig = sig;
      el.innerHTML = "";
      for (let n = from; n <= to; n++)
        el.appendChild(slotRow(n, (slots || {})[String(n)], inst));
    };
    build($("#kb-slots"), 60, 69, es.slots, "juno");
    build($("#kb-axslots"), 72, 77, es.axSlots, "ax");
    // 任意曲首答 loading（后台装载中）：短暂自拉直到数据就位
    if (es.loading && pageOn("kbd"))
      setTimeout(() => { if (pageOn("kbd")) refreshKbd(); }, 700);
  } catch { /* 瞬断 */ }
}
$("#kb-song").addEventListener("change", e => {
  kbEditKey = e.target.value || null;
  refreshKbd(true);
});
$("#kb-refresh").addEventListener("click", () => refreshKbd(true));
$("#kb-save").addEventListener("click", async () => {
  const pick = (id, hint, dev) => {
    const sel = $("#" + id);
    const i = Number(sel.value);
    if (sel.value === "-1" || sel.value === "" || isNaN(i))
      // 幽灵项/空值：保名保序号（hint 未配置时为空=停用，不误绑第一口）
      return {name: hint, dev: dev || 0};
    const entries = ((kbData.ports || {})[sel.dataset.list] || []);
    const e = entries[i];
    return e ? {name: e.name, dev: e.dev} : {name: hint, dev: dev || 0};
  };
  const jin = pick("kb-jin", kbData.junoIn, kbData.junoInDev),
        jout = pick("kb-jout", kbData.junoOut, kbData.junoOutDev),
        ain = pick("kb-ain", kbData.axIn, kbData.axInDev),
        aout = pick("kb-aout", kbData.axOut, kbData.axOutDev);
  const r = await post("/kbdset", {
    junoIn: jin.name, junoInDev: jin.dev,
    junoOut: jout.name, junoOutDev: jout.dev,
    axIn: ain.name, axInDev: ain.dev,
    axOut: aout.name, axOutDev: aout.dev});
  if (r.ok) {
    toast("键盘端口已应用");
    refreshKbd(true);
  }
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
    fillSel("vj-mon", d.obs.projectorMonitor, d.screens || [], "（不在场）");
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
// ---- 端口/显示器下拉共用填充：首项「无」（值空串=停用）+ 在线候选 +
// 幽灵项（已存名不在清单时显示「原名（未接入）」，value=原名：保存即保名，
// 设备回来自动接上）。cur 为已存提示名，前缀命中在线口时选中完整名。
function fillSel(id, cur, list, absentTxt) {
  const el = $("#" + id);
  const hit = cur && list.find(n => n.includes(cur));
  const sig = id + ":" + list.join("|") + "#" + (hit || cur || "");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "无";
  el.appendChild(none);
  list.forEach(n => {
    const o = document.createElement("option");
    o.value = n;
    o.textContent = n;
    el.appendChild(o);
  });
  if (hit) {
    el.value = hit;
  } else if (cur) {
    const o = document.createElement("option");
    o.value = cur;
    o.textContent = cur + absentTxt;
    el.appendChild(o);
    el.value = cur;
  } else {
    none.selected = true;   // 空值不高亮任何候选（保存「无」=停用/关闭）
  }
}

// ---- 遥控网络状态行（B4）：热点/局域网五态 + 双端口监听指示 +
//      浏览器遥控完整地址（webStatus 来自后端缓存线程，/state 的 webIp）----
const NET_TXT = {
  "hot-on": ["热点已开", "ok"], "hot-off": ["热点未开", ""],
  "hot-bad": ["热点不可用", "err"], "lan-ready": ["局域网就绪", "ok"],
  "lan-none": ["无可用局域网地址", "err"], "lan-multi": ["多网卡待指定",
                                                            "warn"]
};
function renderNetStat(d) {
  const el = $("#st-netstat");
  const ws = d.webStatus || {};
  const ls = ws.listen || {};
  el.innerHTML = "";
  const mk = (txt, cls) => {
    const s = document.createElement("span");
    s.className = "pill" + (cls ? " " + cls : "");
    s.textContent = txt;
    el.appendChild(s);
  };
  const net = NET_TXT[ws.net] || ["状态未知", ""];
  mk(net[0], net[1]);
  mk("网页端口 " + (ls.serverPort ? "监听中" : "未监听"),
     ls.serverPort ? "ok" : "err");
  mk("APP 端口 " + (ls.appPort ? "监听中" : "未监听"),
     ls.appPort ? "ok" : "err");
  if (stState && stState.webIp) {
    const a = document.createElement("span");
    a.className = "dim sm-txt";
    a.textContent = "浏览器遥控 http://" + stState.webIp + ":"
        + $v("#st-srv") + "/";
    el.appendChild(a);
  }
}

async function refreshSettings(force) {
  try {
    const d = await (await fetch("/settings")).json();
    renderNetStat(d);   // 状态行不受脏保护（非表单字段，随时可刷）
    // 用户已弄脏时不覆盖：慢响应迟到会静默吃掉正在编辑的值
    // （保存成功后的回读走 force=true 重抓基线）
    if (!force && stBase !== null && stSnap() !== stBase) return;
    fillSel("st-clock", d.clockHint, d.ports || [], "（未接入）");
    fillSel("st-vj", d.vjHint, d.ports || [], "（未接入）");
    fillSel("st-kb", d.kbHint, d.ports || [], "（未接入）");
    fillSel("st-pg", d.web.midiIn, d.ports || [], "（未接入）");
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
    const ip = $("#st-ip");
    // 候选 {name, ip}：显示网卡名、value=IP（后端 lan_ip 存 IP，提交网卡
    // 名永远配不上）；「自动」（值空串）= 不指定
    const isig = d.adapters.map(a => a.name + "=" + a.ip).join("|")
        + "#" + (d.web.ip || "");
    if (ip.dataset.sig !== isig) {
      ip.dataset.sig = isig;
      ip.innerHTML = "";
      const auto = document.createElement("option");
      auto.value = "";
      auto.textContent = "自动";
      auto.selected = !d.web.ip;
      ip.appendChild(auto);
      (d.adapters || []).forEach(a => {
        const o = document.createElement("option");
        o.value = a.ip;
        o.textContent = a.name;
        o.title = a.ip;
        if (a.ip && a.ip === d.web.ip) o.selected = true;
        ip.appendChild(o);
      });
      // 已存 IP 不在当前候选（网卡换了/未上电）：幽灵项保住显示与提交值，
      // 不保会被浏览器回落「自动」→ 保存即静默清空绑定
      if (d.web.ip && !(d.adapters || []).some(a => a.ip === d.web.ip)) {
        const o = document.createElement("option");
        o.value = d.web.ip;
        o.textContent = d.web.ip + "（不在场）";
        o.selected = true;
        ip.appendChild(o);
      }
    }
    renderNetStat(d);
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
          ip: $v("#st-ip")}});
  if (r.ok) {
    toast("设置已保存并应用");
    refreshSettings(true);
  }
});
// ---- 目录选择（/pickdir：原生对话框，选择结果回填并触发脏标记）----
async function pickDir(inputId) {
  toast("请在弹出的窗口中选择目录…");
  try {
    const r = await fetch("/pickdir", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: "{}"});
    const j = await r.json();
    if (!r.ok) {
      toast(j.error || "目录选择失败", "err");
      return;
    }
    if (j.path) {
      $("#" + inputId).value = j.path;
      $("#" + inputId).dispatchEvent(new Event("input", {bubbles: true}));
      toast("已选择：" + j.path);
    }
  } catch {
    toast("目录选择失败（网络）", "err");
  }
}
$$(".browse").forEach(b =>
    b.addEventListener("click", () => pickDir(b.dataset.pick)));

// ---- 编排窄窗 tab（<900px 双列折叠为单列）----
$$("#sl-tabs button").forEach(b => b.addEventListener("click", () => {
  $$("#sl-tabs button").forEach(x => x.classList.toggle("on", x === b));
  const want = "#" + b.dataset.col;
  $$(".cols .col").forEach(c =>
      c.classList.toggle("show", !!c.querySelector(want)));
}));

// ---- QR 码（无依赖最小实现：字节模式、ECC L、版本 1-5、8 掩码择优）----
// 算法移植自公有域实现（Project Nayuki 思路）：GF(256)/RS/函数图案/格式位
// 均按标准；掩码罚分仅影响择优（任何掩码都是合法可扫的 QR）。
const QR_DATA_CW = [0, 19, 34, 55, 80, 108];
const QR_EC_CW = [0, 7, 10, 15, 20, 26];
function qrMul(x, y) {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11D);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xFF;
}
function qrRs(data, ecCw) {
  const gen = new Array(ecCw).fill(0);
  gen[ecCw - 1] = 1;
  let root = 1;
  for (let i = 0; i < ecCw; i++) {
    for (let j = 0; j < ecCw; j++) {
      gen[j] = qrMul(gen[j], root);
      if (j + 1 < ecCw) gen[j] ^= gen[j + 1];
    }
    root = qrMul(root, 2);
  }
  const rem = new Array(ecCw).fill(0);
  for (const b of data) {
    const factor = b ^ rem[0];
    rem.shift();
    rem.push(0);
    for (let i = 0; i < ecCw; i++) rem[i] ^= qrMul(gen[i], factor);
  }
  return rem;
}
function qrMaskBit(mask, x, y) {
  switch (mask) {
    case 0: return (x + y) % 2 === 0;
    case 1: return y % 2 === 0;
    case 2: return x % 3 === 0;
    case 3: return (x + y) % 3 === 0;
    case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
    case 5: return x * y % 2 + x * y % 3 === 0;
    case 6: return (x * y % 2 + x * y % 3) % 2 === 0;
    default: return ((x + y) % 2 + x * y % 3) % 2 === 0;
  }
}
function qrBuild(text, forceMask) {
  const bytes = Array.from(new TextEncoder().encode(text));
  let ver = 1;
  while (ver <= 5 && bytes.length * 8 + 12 > QR_DATA_CW[ver] * 8) ver++;
  if (ver > 5) return null;
  const dataCw = QR_DATA_CW[ver], ecCw = QR_EC_CW[ver];
  const size = ver * 4 + 17;
  const M = Array.from({length: size}, () => new Array(size).fill(false));
  const F = Array.from({length: size}, () => new Array(size).fill(false));
  const set = (x, y, v) => { M[y][x] = v; F[y][x] = true; };
  // 计时图案
  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  // 三个定位图案（9x9 含分隔带，越界部分跳过——角上超出矩阵）
  const finder = (cx, cy) => {
    for (let dy = -4; dy <= 4; dy++)
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx, y = cy + dy;
        if (0 <= x && x < size && 0 <= y && y < size) {
          const d = Math.max(Math.abs(dx), Math.abs(dy));
          set(x, y, d !== 2 && d !== 4);
        }
      }
  };
  finder(3, 3);
  finder(size - 4, 3);
  finder(3, size - 4);
  // 校正图案（v≥2）：位置列表角落与定位图案重叠的三组跳过
  if (ver >= 2) {
    const n = Math.floor(ver / 7) + 2;
    const step = Math.floor((ver * 4 + n * 2 + 1) / (n * 2 - 2)) * 2;
    const pos = [6];
    for (let p = ver * 4 + 10; pos.length < n; p -= step) pos.unshift(p);
    const max = Math.max(...pos);
    for (const cy of pos)
      for (const cx of pos)
        if (!((cx === 6 && cy === 6) || (cx === 6 && cy === max)
              || (cx === max && cy === 6)))
          for (let dy = -2; dy <= 2; dy++)
            for (let dx = -2; dx <= 2; dx++)
              set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }
  // 格式位（先占位，最终值依掩码回写）
  const drawFmt = mask => {
    const data = (1 << 3) | mask;              // ECC L = 01
    let rem = data;
    for (let i = 0; i < 10; i++)
      rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = ((data << 10) | rem) ^ 0x5412;
    const bit = i => ((bits >>> i) & 1) !== 0;
    for (let i = 0; i <= 5; i++) set(8, i, bit(i));
    set(8, 7, bit(6));
    set(8, 8, bit(7));
    set(7, 8, bit(8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
    set(8, size - 8, true);
  };
  drawFmt(0);
  // 数据位流 → 码字（字节模式 + 终止符 + 填充 + RS 纠错）
  const bits = [];
  const push = (v, n) => {
    for (let i = n - 1; i >= 0; i--) bits.push((v >>> i) & 1);
  };
  push(4, 4);
  push(bytes.length, 8);
  bytes.forEach(b => push(b, 8));
  push(0, Math.min(4, dataCw * 8 - bits.length));
  while (bits.length % 8) bits.push(0);
  const cw = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = v * 2 + bits[i + j];
    cw.push(v);
  }
  for (let i = 0; cw.length < dataCw; i++) cw.push(i % 2 ? 0x11 : 0xEC);
  const all = cw.concat(qrRs(cw, ecCw));
  // 之字放置
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++)
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
        if (!F[y][x] && i < all.length * 8) {
          M[y][x] = ((all[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0;
          i++;
        }
      }
  }
  // 掩码：罚分择优（规则 1/2/3/4；仅影响择优不影响合法性）
  const R3A = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
  const R3B = R3A.slice().reverse();
  const eqPat = (get, x, pat) =>
      pat.every((v, k) => get(x + k) === !!v);
  const penalty = () => {
    let s = 0;
    for (let y = 0; y < size; y++) {           // 规则1：行/列同色长跑
      let rx = 1, cx = 1;
      for (let x = 1; x < size; x++) {
        if (M[y][x] === M[y][x - 1]) rx++;
        else { if (rx >= 5) s += rx - 2; rx = 1; }
        if (M[x][y] === M[x - 1][y]) cx++;
        else { if (cx >= 5) s += cx - 2; cx = 1; }
      }
      if (rx >= 5) s += rx - 2;
      if (cx >= 5) s += cx - 2;
    }
    for (let y = 0; y < size - 1; y++)         // 规则2：2x2 同色块
      for (let x = 0; x < size - 1; x++) {
        const v = M[y][x];
        if (v === M[y][x + 1] && v === M[y + 1][x]
            && v === M[y + 1][x + 1])
          s += 3;
      }
    for (let y = 0; y < size; y++)             // 规则3：finder 样式
      for (let x = 0; x + 11 <= size; x++)
        if (eqPat(k => M[y][x + k], 0, R3A)
            || eqPat(k => M[y][x + k], 0, R3B)
            || eqPat(k => M[x + k][y], 0, R3A)
            || eqPat(k => M[x + k][y], 0, R3B))
          s += 40;
    let dark = 0;                              // 规则4：暗模块占比
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) if (M[y][x]) dark++;
    s += Math.floor(Math.abs(dark * 20 - size * size * 10)
                    / size / size) * 10;
    return s;
  };
  let best = null, bestMask = 0, bestScore = Infinity;
  const from = forceMask === undefined ? 0 : forceMask;
  const to = forceMask === undefined ? 7 : forceMask;
  for (let m = from; m <= to; m++) {
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++)
        if (!F[y][x]) M[y][x] ^= qrMaskBit(m, x, y);
    drawFmt(m);
    const sc = penalty();
    if (sc < bestScore) { bestScore = sc; best = M.map(r => r.slice()); bestMask = m; }
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++)
        if (!F[y][x]) M[y][x] ^= qrMaskBit(m, x, y);   // 异或还原试下一掩码
  }
  return {size, matrix: best, mask: bestMask};
}
function qrSvg(text) {
  const q = qrBuild(text);
  if (!q) return "";
  const s = q.size, qz = 2;                    // 四周 2 模块静区
  let d = "";
  for (let y = 0; y < s; y++)
    for (let x = 0; x < s; x++)
      if (q.matrix[y][x]) d += "M" + (x + qz) + "," + (y + qz) + "h1v1h-1z";
  return "<svg viewBox=\"0 0 " + (s + qz * 2) + " " + (s + qz * 2)
      + "\" xmlns=\"http://www.w3.org/2000/svg\" shape-rendering=\"crispEdges\">"
      + "<rect width=\"" + (s + qz * 2) + "\" height=\"" + (s + qz * 2)
      + "\" fill=\"#fff\"/><path d=\"" + d + "\" fill=\"#101418\"/></svg>";
}
window.__qr = {build: qrBuild, svg: qrSvg};   // 测试钩子（探针/控制台校验用）

function renderAppAddr() {
  const wrap = $("#app-addr-wrap");
  const a = stState && stState.webIp && stState.appPort
      ? "http://" + stState.webIp + ":" + stState.appPort + "/" : "";
  wrap.hidden = !a;
  if (a) {
    $("#app-addr").textContent = a;
    const qr = $("#qr-box");
    if (qr.dataset.addr !== a) {
      qr.dataset.addr = a;
      qr.innerHTML = qrSvg(a);
    }
  }
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
setInterval(() => { if (pageOn("pedal")) refreshPedal(); }, 1500);
setInterval(refreshLog, 1500);
refreshPlaylist();
refreshPoints();
refreshLog();
refreshPedal();
refreshKbd();
refreshVj();
refreshSettings();
booted = true;
