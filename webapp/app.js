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

// ---- toast（替代全部 alert；err 档 5 秒+可手动点关）----
function toast(msg, kind) {
  const box = $("#toasts");
  while (box.children.length >= 4) box.firstChild.remove();
  const t = document.createElement("div");
  t.className = "toast" + (kind === "err" ? " err" : "");
  t.textContent = msg;
  t.addEventListener("click", () => t.remove());   // 手动关（err 长驻）
  box.appendChild(t);
  setTimeout(() => {
    t.classList.add("out");
    setTimeout(() => t.remove(), 200);
  }, kind === "err" ? 5000 : 2600);
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
let popEl = null, popAnchor = null;
function _popOut(e) { if (popEl && !popEl.contains(e.target)) closePop(); }
function _popEsc(e) { if (e.key === "Escape") closePop(); }
function closePop() {
  if (!popEl) return;
  popEl.remove();
  popEl = null;
  document.removeEventListener("pointerdown", _popOut, true);
  document.removeEventListener("keydown", _popEsc, true);
  if (popAnchor && popAnchor.isConnected)   // 焦点还回锚点（键盘流不丢）
    popAnchor.focus();
  popAnchor = null;
}
function popover(anchor, build) {
  closePop();
  popAnchor = anchor;
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
  // 演出模式下点「去编排加入曲目」等跨页导航：先退出演出模式
  // （侧栏/状态栏被藏的非预期状态，时序红队 P2）
  if (page !== "console" && document.body.classList.contains("show"))
    setShow(false);
  if (booted && page !== curPage) {
    if (popEl) closePop();   // 换页收口弹层
    if (curPage === "setlist" && (recInst || recPend)) stopRecord();
    // 踩钉页进出 = 页面级静音开关（调绑/学习不误触发演出动作）
    if (curPage === "pedal") pedalMute(false);
    if (page === "pedal") {
      pedalMute(true);
      refreshPedal(true);       // 重建表单基线（别处改过配置时不再显旧态）
      refreshPedalDevices();
    }
  }
  curPage = page;
  // 回主控台强制重测跑马灯：隐藏页期间 render 的 0 宽测量会把「溢出」
  // 误判为不溢出（dataset.txt 已更新不再复测）；进/出演出模式字号切换
  // 同理（时序红队 P2）
  if (page === "console") {
    const n = $("#now");
    if (n) delete n.dataset.txt;
  }
  $$(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "p-" + page));
  // 切页即时拉数据（不等轮询节拍；boot 前各页状态量尚未初始化）。
  // settings/VJ 无周期轮询：进页必拉（启动失败自愈——否则表单空白且
  // 保存钮灰死；VJ 映射同时强制重扫视频目录，兑现「即放即扫」）
  if (booted) {
    if (page === "console" || page === "setlist") refreshPlaylist();
    if (page === "console") refreshLog();
    else if (page === "setlist") { refreshPoints(); refreshKbdDetail(); }
    else if (page === "kbd") refreshKbd();
    else if (page === "vj") refreshVj(true);
    else if (page === "settings") refreshSettings();
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
  if (recInst || recPend)
    fetch("/kbdrecord", {method: "POST", keepalive: true,
                         headers: {"Content-Type": "application/json"},
                         body: JSON.stringify({inst: recInst || recPend,
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
  const nowEl = $("#now");
  if (nowEl) delete nowEl.dataset.txt;   // 字号 42↔72 切换：强制重测滚动距
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
// 空格=智能三态钮（主控台/演出模式；输入框与弹层内不劫持）
document.addEventListener("keydown", e => {
  if (e.key !== " " || e.repeat) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT"
            || t.tagName === "TEXTAREA" || t.tagName === "BUTTON"
            || t.isContentEditable)) return;
  if (popEl) return;
  if (!pageOn("console")) return;
  e.preventDefault();
  const a = $("#smart").dataset.action;
  if (a) cmd(a);
});

// ---- HTTP（失败可见，不静默）----
// switch 带 key：后端在执行前一刻按 key 反查列表（多端重排后下标会
// 漂移到邻曲——红队 P0）；其它动作忽略 key
async function cmd(action, index, key) {
  const body = index === undefined ? {action}
      : key === undefined ? {action, index} : {action, index, key};
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
    toast("命令发送失败：网络异常", "err");
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
    toast("操作失败：网络异常", "err");
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
function switchTo(i, btn, dbl, key) {
  const st = stState;
  const fire = () => {
    // 确认窗 3s 内列表可能被重排（多端操作）：按 key 实时重解析。
    // key 已不在列表=条目被移除：中止并提示，绝不发武装时刻的旧下标
    // （红队 P0：陈旧索引切错歌）
    if (key && plCache && plCache.pl) {
      const j = plCache.pl.findIndex(x => x.key === key);
      if (j < 0) {
        toast("该曲目已不在播放列表", "err");
        refreshPlaylist();
        return;
      }
      i = j;
    }
    swLastFire = Date.now();
    cmd("switch", i, key);
  };
  if (dbl && Date.now() - swLastFire < 400) return;
  // 非双击的单发去重窗须盖过 busy 传播（执行+快照 400ms+/state 500ms）：
  // 400ms 窗内 t 与 t+0.45s 两击可双发，第二发对同曲再来一次完整关开
  // 工程（结论攻击实证）
  if (!dbl && Date.now() - swLastFire < 900) return;
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
  // 无当前曲（未恢复/游离）时 NEXT 卡显示的是第一首：切换目标随之落 0；
  // 有 plCache 时尽量带 key（后端按 key 反查防漂移）
  const i = typeof stCur === "number" ? stCur + 1 : 0;
  const it = plCache && plCache.pl && plCache.pl[i];
  switchTo(i, $("#next-go"), false, it ? it.key : undefined);
});

const fmt = s => {
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

// ---- 主控台渲染 ----
let pedalPrevAge = null;
function render(st) {
  if (!$("#p-console")) return;
  $("#now").textContent = st.open ? st.projName : "未打开工程";
  // 超长歌名跑马灯：文本变化才重测重挂（每 500ms remove/add 会把
  // 14s 动画永远打回 0）；测量时临时 nowrap 拿单行宽（平时 break-all
  // 折行恒不溢出）
  const nowEl = $("#now");
  if (nowEl.dataset.txt !== nowEl.textContent) {
    nowEl.dataset.txt = nowEl.textContent;
    nowEl.classList.remove("marq");
    const probeW = nowEl.offsetWidth;
    nowEl.style.whiteSpace = "nowrap";
    const fullW = nowEl.scrollWidth;
    nowEl.style.whiteSpace = "";
    if (fullW > probeW + 2) {
      nowEl.style.setProperty("--marq", -(fullW - probeW) + "px");
      nowEl.classList.add("marq");
    }
  }
  // 状态 chip + 时钟
  const chip = $("#tchip");
  const ts = st.busy ? "busy" : (st.tstate || "stopped");
  chip.className = "chip " + (st.tstate || "stopped");
  chip.textContent = st.busy ? "切换中…" :
      {playing: "播放中", paused: "已暂停", stopped: "未在播放"}[st.tstate]
      || "未在播放";
  const live = st.tstate === "playing" || st.tstate === "paused";
  const pos = live ? st.pos : 0;
  $("#clock").textContent = !live && pos <= 0 && !(st.dur > 0)
      ? "时长未知" : "已播 " + fmt(pos) + " · "
        + (st.dur > 0 ? "剩余 " + fmt(Math.max(0, st.dur - pos))
           : "时长未知");
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
  smart.classList.toggle("pulse", !!st.busy);   // busy 脉动（F 批）
  // 全停是切换卡死时的安全阀（后端 busy 放行），其余走带键 busy 期禁用；
  // 行内切歌/定位点快跳同禁——关闭确认时的双击=两次完整工程切换
  // （时序红队 P2）
  $$("#transport [data-cmd]").forEach(b =>
      b.disabled = !!st.busy && b.dataset.cmd !== "panic");
  $$("#console-list .go-switch, #chips .chip-btn").forEach(b =>
      b.disabled = !!st.busy);
  // NEXT 卡
  const rows = st.songs || [];
  const cur = typeof st.cur === "number" ? st.cur : -1;
  const nx = rows[cur + 1];
  $("#next").textContent = nx ? nx.name : (rows.length ? "末尾" : "—");
  $("#next").classList.toggle("has", !!nx);
  $("#next-go").disabled = !nx || !!st.busy;
  // 曲目列表（三态；签名不变不动 DOM，悬停态不被轮询冲掉）。
  // 签名必须含 plCache 键列：行上的 dataset.k（switch 防 P0 漂移的凭据）
  // 取自建行时刻的 plCache——若 /playlist 后到而签名不含键，行永不重建、
  // key 恒空，防线静默失效
  const list = $("#console-list");
  const sig = rows.length + ":" + cur + ":"
      + rows.map(r => (r.key || "") + r.name + r.dur).join("|");
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
  if (st.ver) $("#side-ver").textContent = st.ver;   // 版本号（git describe）
}
function consoleRow(s, i, cur) {
  const div = document.createElement("div");
  div.className = "row" + (i === cur ? " cur" : "")
      + (i < cur ? " played" : "");
  div.dataset.i = i;
  // key 直取 /state 行数据（同源同拍）；旧版从 plCache[i] 借键——两缓存
  // 节拍不同（500ms vs 1.5s），借键错位曾把切歌按钮指向错曲（红队 P1）
  div.dataset.k = s.key || ((plCache && plCache.pl && plCache.pl[i])
      ? plCache.pl[i].key : "");
  div.tabIndex = 0;              // 键盘可达：Enter=切歌（F3）
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
    switchTo(i, go, false, div.dataset.k);
  });
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = s.dur;
  div.append(no, eq, nm, go, dur);
  div.addEventListener("dblclick", () => switchTo(i, go, true, div.dataset.k));
  div.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchTo(i, go, false, div.dataset.k);
    }
  });
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
      st.obs ? "obs-websocket 已连接"
      : (st.vj && st.vj.obsErr ? "OBS 未连接：" + st.vj.obsErr
         : "OBS 未连接"));
  set("l-loop", st.loop ? "ok" : "warn",
      st.loop ? "loopMIDI 运行中" : "loopMIDI 未运行");
  const pr = st.pedalRemote;
  set("l-pedal", !pr ? "" : pr.link === "ok" ? "ok" : "warn",
      (!pr ? "踩钉桥未就绪" : (pr.link === "ok"
      ? "转发链路健康：15s 内有信号" : pr.link === "stale"
      ? "转发链路超时：15s 内无信号" : (pr.enabled
      ? "未收到转发信号" : "未启用转发")))
      + "；点击进入踩钉页，打开期间踩钉临时静音");
  set("l-hot", st.webIp ? "ok" : "",
      st.webIp ? "服务地址 " + st.webIp : "移动端遥控服务未启动");
  set("l-remote", st.ready ? "ok" : "err",
      st.ready ? "遥控服务就绪" : "遥控服务未就绪");
}
$$(".lamp").forEach(el => el.addEventListener("click",
  () => nav(el.dataset.pageNav)));
// 侧栏退出钮：两段式确认（误触代价=整场演出中断）
$("#btn-exit").addEventListener("click", ev =>
    armConfirm(ev.currentTarget, () => {
      post("/exit", {}).then(() => toast("正在退出…"));
    }, "确认退出？"));

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
      ? ptBaseOf(plCache.pl[cur].key) : plCache.pl[cur].key;
  const items = [];
  plCache.pl.forEach((it, i) => {
    if (it.pt && it.key.startsWith("pt:")
        && ptBaseOf(it.key) === baseKey)
      items.push({i, key: it.key,
                  name: it.name.split(" · ").pop(), cur: i === cur});
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
    b.title = "点击切到该定位点，同工程免重开";
    b.addEventListener("click", () => switchTo(x.i, b, false, x.key));
    box.appendChild(b);
  });
}

// ---- 踩钉事件闪灯（lastEventAge 回落=新事件）----
function setKv(id, text, on) {
  const el = $("#" + id);
  el.textContent = text;
  el.className = "v " + (on ? "on" : "off");
}

function pedalLive(st) {
  const pr = st.pedalRemote;
  const link = $("#pedal-link");
  if (pr) {
    const off = !pr.enabled && pr.link !== "ok" && pr.link !== "stale";
    link.textContent = pr.link === "ok" ? "健康"
        : pr.link === "stale" ? "超时" : (off ? "未启用" : "无信号");
    link.className = "stat-big " +
        (pr.link === "ok" ? "ok" : pr.link === "stale" ? "warn"
         : off ? "" : "err");
    const hb = $("#pedal-kv-hb"), unk = $("#pedal-kv-unknown");
    hb.textContent = pr.unknown > 0 ? "未识别 " + pr.unknown
        : (pr.enabled ? "正常" : "未启用");
    hb.className = "v " + (pr.unknown > 0 ? "warn"
        : (!pr.enabled ? "off" : "on"));
    unk.textContent = pr.unknown + " 个";
    unk.className = "v " + (pr.unknown > 0 ? "warn" : "off");
  } else {
    link.textContent = "—";
    link.className = "stat-big";
    setKv("pedal-kv-hb", "—", false);
    setKv("pedal-kv-unknown", "—", false);
  }
  const age = pr && pr.lastEventAge;
  const flash = $("#pedal-flash");
  if (typeof age === "number") {
    if (pedalPrevAge !== null && age < pedalPrevAge) {
      flash.classList.add("on");
      setTimeout(() => flash.classList.remove("on"), 600);
    }
    pedalPrevAge = age;
    const w = $("#pedal-when");
    w.textContent = age < 60 ? Math.round(age) + " 秒前" : "久前";
    w.className = "";
  } else {
    const w = $("#pedal-when");
    w.textContent = "暂无事件";
    w.className = "dim";
  }
}

let pollFails = 0;
async function poll() {
  try {
    // 5s 超时：进程活着但 HTTP 挂起时也走断连指示（挂起的 fetch
    // 不再永远 pending）；旧内核无 AbortSignal.timeout 则退化为原行为
    const opt = window.AbortSignal && AbortSignal.timeout
        ? {signal: AbortSignal.timeout(5000)} : {};
    const r = await fetch("/state", opt);
    if (r.ok) {
      if (pollFails >= 3) toast("连接已恢复");
      pollFails = 0;
      $("#tstate").classList.remove("err");
      stState = await r.json();
      render(stState);
      stCur = stState.cur;
    } else {
      throw new Error(r.status);
    }
  } catch {
    pollFails++;
    if (pollFails >= 3) {          // 连续失败：常驻断连标识（恢复即灭）
      const el = $("#tstate");
      el.textContent = "连接中断…重试中";
      el.classList.add("err");
    }
    /* 热点瞬断：下轮再取 */
  }
}

// ================= 编排页 =================
let plSel = null, selRowKey = null, ptSelPending = null;
let slQ = "", dragKey = null;

// ---- 编排时长三态（自动绿/手动黄/未知红「未知」；pt=段长按绿显示）----
// pt 键解析与后端 points.parse_pt_key 同口径（rpartition "#"）：
// 工程路径含 # 时按首个 # 切会把 base 与 id 全解错（红队 P2）
const ptBaseOf = k => k.slice(3, k.lastIndexOf("#"));
const ptIdOf = k => Number(k.slice(k.lastIndexOf("#") + 1));
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
  div.addEventListener("click", () => {
    // 行单击=选中该行；pt 条目选中该定位点（详情面板直接进编辑）；
    // ＋ 为唯一加入入口（防误触）
    const k = s.key;
    if (k.startsWith("pt:")) {
      const base = ptBaseOf(k);
      selectSong(k, base, ptIdOf(k));
    } else selectSong(k, k, null);
  });
  return div;
}
function drawLib() {
  if (!plCache || !plCache.lib || !plCache.pl) return;
  const lib = $("#lib");
  // 精确键匹配：素材库视图（含展开的 pt 条目）与播放列表同一键空间，
  // 部分移除的点条目要能单独加回（宿主归一会把它们全标「已加入」）
  const inPl = new Set(plCache.pl.map(it => it.key));
  const sig = plCache.lib.map(s => s.key + (inPl.has(s.key) ? 1 : 0)
      + (s.durSrc || "") + (s.dur || "")).join("|") + "#" + slQ
      + "#" + (selRowKey || "");
  if (lib.dataset.sig === sig) return;
  lib.dataset.sig = sig;
  lib.innerHTML = "";
  const shown = [];
  plCache.lib.forEach(s => {
    const hit = !slQ || (s.name + " " + s.team).toLowerCase().includes(slQ);
    if (hit) shown.push(s);
  });
  const emptyEl = $("#lib-empty");
  emptyEl.hidden = shown.length > 0;
  // 场景区分：真空库引导设根；搜索无果提示清词（文本独立节点，
  // 不覆盖容器——按钮是静态子节点）
  if (!shown.length) {
    const btn = emptyEl.querySelector("button");
    let txt = emptyEl.querySelector(".empty-txt");
    if (!txt) {
      txt = document.createElement("div");
      txt.className = "empty-txt";
      emptyEl.insertBefore(txt, btn);
    }
    const libEmpty = !plCache.lib.length;
    txt.textContent = libEmpty ? "素材库为空"
        : "没有匹配「" + (slQ || "") + "」的曲目";
    btn.style.display = libEmpty ? "" : "none";
  }
  $("#lib-n").textContent = plCache.lib.length + " 首";
  $("#lib").hidden = shown.length === 0;   // 空态充满列表区（U2）
  shown.forEach(s => {
    const row = libRow(s, inPl.has(s.key));
    if (s.key === selRowKey) row.classList.add("sel");
    lib.appendChild(row);
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
        err.textContent = "格式应为 秒数 或 分:秒，如 3:33";
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
    dur.title = "手动时长，⋯ 菜单可重新识别";
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
    movePlKey(it.key, -1);
  });
  const down = mk("↓", "下移");
  down.addEventListener("click", ev => {
    ev.stopPropagation();
    movePlKey(it.key, 1);
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
  div.addEventListener("click", () => {
    const k = it.key;
    if (k.startsWith("pt:")) {
      const base = ptBaseOf(k);
      selectSong(k, base, ptIdOf(k));
    } else selectSong(k, k, null);
  });
  // 拖拽排序：手柄按下才允许拖（不挡文本选择/点击）；pointerup 未进
  // dragstart 的复位（F 批：拖拽细节）
  handle.addEventListener("pointerdown", () => div.draggable = true);
  handle.addEventListener("pointerup", () => div.draggable = false);
  handle.addEventListener("pointercancel", () => div.draggable = false);
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
let movePend = false;    // move 在途：连点按新位置算（回显未回不来时
                         // 旧快照连发两次=净效果抵消，结论攻击实证）
async function movePl(from, to) {
  if (from === to || from < 0 || to < 0 || !plCache
      || to >= plCache.pl.length) return;
  const item = plCache.pl[from];
  dragKey = null;                      // 拖拽收尾：恢复轮询重绘
  movePend = true;
  try {
    const j = await post("/plcmd", {op: "move", payload: {from, to}});
    if (j.ok) toast("已移动《" + item.name + "》");
    await refreshPlaylist();           // 回显落地后才放行下一次移动
  } finally {
    movePend = false;
  }
}
// 点击时刻按键重解析再发 move：连点 ↑/拖拽落在在途刷新上时，建行下标
// 已过期（时序红队 P1：移错行）
function movePlKey(key, delta) {
  if (movePend || !plCache || !plCache.pl) return;
  const from = plCache.pl.findIndex(x => x.key === key);
  if (from < 0) return;
  movePl(from, from + delta);
}
function drawPl() {
  if (!plCache || !plCache.pl || dragKey) return;  // 拖拽中不重绘（防行从指针下消失）
  const el = $("#pl");
  const want = plCache.pl.map(it => it.key).join("|");
  const curIdx = typeof stCur === "number" ? stCur : -1;
  const sel = selRowKey;
  const sig = want + "#" + curIdx + "#" + sel + "#" + slQ
      + "#" + plCache.pl.map(x => x.name + x.dur + (x.durSrc || ""))
          .join("~");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = "";
  // 团队匹配与素材库同口径（pt 条目经宿主工程查团队）
  const teamOf = it => {
    const base = it.key.startsWith("pt:")
        ? ptBaseOf(it.key) : it.key;
    const lib = plCache.lib.find(x => x.key === base);
    return lib ? (lib.team || "").toLowerCase() : "";
  };
  let rendered = 0;
  plCache.pl.forEach((it, i) => {
    if (!slQ || it.name.toLowerCase().includes(slQ)
        || teamOf(it).includes(slQ)) {
      rendered++;
      const r = plRow(i, it);
      if (i === curIdx) r.classList.add("cur");
      if (it.key === sel) r.classList.add("sel");
      el.appendChild(r);
    }
  });
  $("#pl-n").textContent = plCache.pl.length + " 首";
  // 空态充满列表区（U2）；真空表引导加曲、搜索无果提示清词
  $("#pl").hidden = rendered === 0;
  $("#pl-empty").hidden = rendered > 0;
  $("#pl-empty").textContent = plCache.pl.length
      ? "没有匹配「" + slQ + "」的曲目，试试清空搜索词"
      : "从上方素材库点 ＋ 加入曲目";
}
function markPlSel() {
  $$("#pl .row").forEach(r =>
      r.classList.toggle("sel", r.dataset.k === selRowKey));
  $$("#lib .row").forEach(r =>
      r.classList.toggle("sel", r.dataset.k === selRowKey));
}
// 选中曲（素材库/播放列表行点击，pt 条目归一到宿主工程）：
// 详情面板（定位点+音色映射）跟随；换曲清定位点选择防串编辑
function selectSong(rowKey, base, ptId) {
  selRowKey = rowKey;
  ptSelPending = ptId || null;
  if (recInst) stopRecord();     // 录制目标定格在开始时的曲，换选即弃
  if (plSel !== base) {
    plSel = base;
    ptSel = null;
    ptProj = null;              // 定位点半边同清：ptcmd 全以 ptProj 定向，
                                // 停在旧曲上=对旧曲误写（时序红队 P1）
    // 数据在途时先清面板；dataset.sig 必须一并清：否则差量守卫见
    // 「签名同」跳过重绘（探针 p1 实证）
    dtKbd = null;
    dtPtN = null;
    updateDtBadge();
    $("#dt-kbd-stat").textContent = "映射装载中…";
    $("#dt-title").textContent = "曲目详情 · 装载中…";
    for (const id of ["dt-slots", "dt-axslots"]) {
      const el = $("#" + id);
      el.innerHTML = "";
      delete el.dataset.sig;
    }
    const pl = $("#pt-list");
    pl.innerHTML = "";
    delete pl.dataset.sig;
    $("#pt-form").hidden = true;
    $("#pt-none").hidden = true;   // 数据在途：空态/表单都不显，防误交互
    $("#pt-mmc").hidden = true;
    $("#pt-on").disabled = true;   // 数据未到不可拨（防回弹诱发双击）
    markPlSel();
    refreshPoints();
    refreshKbdDetail();
  } else {
    markPlSel();
    if (ptSelPending !== null) refreshPoints();   // 同曲换点：进编辑
  }
  // 窄窗（页签可见）选中后切到详情列，给可见反馈
  if (getComputedStyle($("#sl-tabs")).display !== "none")
    $('#sl-tabs [data-col="dt"]').click();
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
    const seq = refreshPlaylist.seq = (refreshPlaylist.seq || 0) + 1;
    const r = await fetch("/playlist");
    if (!r.ok) return;
    const d = await r.json();
    if (seq !== refreshPlaylist.seq) return;   // 旧响应不覆盖新请求
    plCache = d;
    stCur = d.cur;
    drawLib();
    drawPl();
    // 弹层锚点被差量重建拿掉（他端改列表）：菜单悬空坐标即刻收口
    if (popEl && popAnchor && !popAnchor.isConnected) closePop();
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
let ptProj = null, ptData = null, ptSel = null, ptCap = null;
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
// 时码即时校验：红框+原因文案，保存随校验禁用（根治静默失败）；
// <3 秒段软提示（黄色）不禁保存（停止重试窗可能吃掉自动推进）
function validatePt() {
  if (ptSel === null) return false;
  const s = parseTC($("#pt-s").value), e = parseTC($("#pt-e").value);
  let msg = "";
  if (s === null) msg = "开始时码格式应为 分:秒 或 秒数，如 3:33";
  else if (e === 0) msg = "结束点未定，工程末尾未知：请补填后保存";
  else if (e <= s) msg = "结束必须晚于开始";
  $("#pt-s").classList.toggle("invalid", s === null);
  $("#pt-e").classList.toggle("invalid", e === null || (s !== null && e <= s));
  $("#pt-save").disabled = !!msg;
  $("#pt-save").title = msg;
  const hint = $("#pt-hint");
  if (msg) {
    hint.textContent = msg;
    hint.className = "field-err";
  } else if (s !== null && e - s < 3) {
    hint.textContent = "段长不足 3 秒：停止重试窗可能吃掉自动推进";
    hint.className = "field-err warn-txt";
  } else {
    hint.textContent = "";
  }
  return !msg;
}
["pt-s", "pt-e"].forEach(id =>
    $("#" + id).addEventListener("input", validatePt));

async function refreshPoints() {
  try {
    const seq = refreshPoints.seq = (refreshPoints.seq || 0) + 1;
    const r = await fetch("/points");
    if (!r.ok) return;
    const d = await r.json();
    if (seq !== refreshPoints.seq) return;     // 旧响应不覆盖新请求
    ptData = d.projects;
    ptCap = {cubase: d.cubase !== false, mmcReady: d.mmcReady !== false};
    // 能力位：非 Cubase 底座禁用开关；MMC 未就绪常驻提示（定位不可用）
    const on = $("#pt-on");
    on.disabled = !ptCap.cubase;
    on.closest(".switch").title = ptCap.cubase
        ? "启用后该工程条目展开为定位点播放" : "定位点仅 Cubase 支持";
    $("#pt-mmc").hidden = ptCap.mmcReady || !ptCap.cubase;
    // 详情面板跟随选中曲（未选=引导空态）；定位点数据缺席按空表渲染
    const hasSel = !!plSel;
    $("#dt-none").hidden = hasSel;
    $("#dt-body").hidden = !hasSel;
    if (!hasSel) return;
    ptProj = plSel;
    const song = plCache && (plCache.lib.find(x => x.key === plSel)
                              || (plCache.pl || []).find(x => x.key === plSel));
    $("#dt-title").textContent = "曲目详情 · "
        + ((song ? song.name.split(" · ")[0] : "")
           || (ptData[plSel] || {}).name || plSel);
    const dd = ptData[ptProj] || {enabled: false, points: []};
    if (!ptOnPending) $("#pt-on").checked = dd.enabled;
    if (ptSelPending !== null) {   // 点行点击：选中该点并填编辑表单
      const pp = dd.points.find(x => x.id === ptSelPending);
      ptSelPending = null;
      if (pp) {
        ptSel = pp.id;
        $("#pt-name").value = pp.name;
        $("#pt-s").value = fmtTC(pp.start);
        $("#pt-e").value = fmtTC(pp.end);
      }
    }
    dtPtN = dd.points.length;
    updateDtBadge();
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
            + " · 段长 " + fmtTC(p.end - p.start);
        div.append(nm, dur);
        div.addEventListener("click", () => selectPt(p.id));
        el.appendChild(div);
      });
      if (!dd.points.length)
        el.innerHTML = "<div class='empty-state sm'>尚无定位点，"
            + "点「添加」以当前时码新建</div>";
    }
    // 编辑卡
    const has = ptSel !== null && dd.points.some(p => p.id === ptSel);
    $("#pt-none").hidden = has || !dd.points.length;
    $("#pt-form").hidden = !has;
    if (has) {
      const gi = ptGotoIndex();
      const g = $("#pt-goto");
      g.disabled = gi === null;
      g.title = gi === null
          ? "该定位点未启用或不在播放列表，启用后自动加入" : "";
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
  await refreshPlaylist();   // 先列表后定位点：跳到这点按钮即时就位
  refreshPoints();
}
let ptOnPending = false;    // toggle 在途：轮询回写跳过（防视觉回弹诱发双击）
$("#pt-on").addEventListener("change", async e => {
  ptOnPending = true;
  await ptcmd("toggle", {on: e.target.checked});
  ptOnPending = false;
});
// add 后自动选中末点（新点追加在表尾）；在途禁用防 504 误重试双加点
// （超时后任务仍在主线程排队执行——连点两次=两个重复定位点，红队 P1）
$("#pt-add").addEventListener("click", async () => {
  if (!ptProj) return;                    // 未选曲：详情为空态不可加
  const btn = $("#pt-add");
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    const j = await post("/ptcmd", {op: "add", key: ptProj, payload: {}});
    if (!j.ok) return;
    await refreshPoints();
    const dd = ptData[ptProj];
    const last = dd && dd.points[dd.points.length - 1];
    if (last) {
      selectPt(last.id);
      if (!last.end) {    // 工程末尾未知：黄字提醒补填（同旧窗）
        $("#pt-hint").textContent = "结束点未定，工程末尾未知：请补填后保存";
        $("#pt-hint").className = "field-err warn-txt";
      }
    }
    toast("定位点已更新");
  } finally {
    btn.disabled = false;
  }
});
$("#pt-del").addEventListener("click", ev => {
  if (ptSel !== null) {
    // 武装时刻捕获 id：3s 窗口内改选另一个点再确认，删的必须是当初
    // 武装的那个（时序红队 P2：闭包读活动 ptSel 删错点）
    const id = ptSel;
    armConfirm(ev.currentTarget, () => ptcmd("del", {id}),
               "确认删除？");
  }
});
$("#pt-save").addEventListener("click", () => {
  if (!$("#pt-name").value.trim()) {
    $("#pt-hint").textContent = "名称不能为空";
    $("#pt-hint").className = "field-err";
    return;
  }
  if (!validatePt()) return;
  ptcmd("edit", {id: ptSel, name: $("#pt-name").value,
                 start: parseTC($("#pt-s").value),
                 end: parseTC($("#pt-e").value)});
});
$("#pt-goto").addEventListener("click", () => {
  const i = ptGotoIndex();
  if (i !== null) {
    switchTo(i, $("#pt-goto"), false,
        "pt:" + ptProj + "#" + ptSel);
    if (!$("#pt-goto").dataset.armed)
      toast("定位到《" + $("#pt-name").value + "》");
  }
});
$("#pt-up").addEventListener("click", () =>
    ptSel !== null && ptcmd("move", {id: ptSel, step: -1}));
$("#pt-down").addEventListener("click", () =>
    ptSel !== null && ptcmd("move", {id: ptSel, step: 1}));

// ============ 运行日志卡（主控台页内，增量 append + 工具条） ============
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
  if (!pageOn("console")) return;
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
  armConfirm(ev.currentTarget, async () => {
    $("#log").innerHTML = "";
    // 以服务器现值建增量水位（红队 P1-4：确认前轮询会把清空打回）：
    // 清空后只追加确认时刻之后的新行
    try {
      const r = await fetch("/log");
      if (r.ok) {
        const lines = (await r.json()).lines || [];
        logLast = {count: lines.length,
                   first: lines.length
                       ? lines[0].t + "|" + lines[0].msg : ""};
      } else logLast = {count: 0, first: ""};
    } catch { logLast = {count: 0, first: ""}; }
    toast("日志已清空：仅本页视图，环形缓冲不受影响");
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
    const r0 = await fetch("/pedal/devices");
    if (!r0.ok) return;
    const d = await r0.json();
    const sel = $("#pd-dev");
    const cur = pdData ? pdData.deviceHint : "";
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "不区分来源，仅 MIDI 踩钉";
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
      o.textContent = cur + " · 离线";
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
    const bound = d.binds[a] !== undefined || d.hid[a] !== undefined;
    // 学习中的动作禁清除：后端学习完成会迁移改绑+落盘，清除会被静默还原
    if (!bound || a === learnAction) clr.disabled = true;
    clr.addEventListener("click", () => clearBind(a));
    row.append(nm, bd, learn, clr);
    el.appendChild(row);
  });
}

async function refreshPedal(force) {
  try {
    const r0 = await fetch("/pedal");
    if (!r0.ok) return;                   // 504 错误体不当数据（P1-2）
    const d = await r0.json();
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
      txt = "已静音 · 关本页恢复";
    } else {
      txt = "已连接"; cls = "ok";
    }
    c.textContent = txt;
    c.className = "stat-big " + cls;
    setKv("pedal-kv-midi", d.connected ? "已连" : "未连", d.connected);
    setKv("pedal-kv-dev", d.deviceOnline ? "在线" : "离线",
          d.deviceOnline);
    setKv("pedal-kv-hid", d.hidActive ? "运行" : "停", d.hidActive);
    // 表单字段只在首拉/保存后回填：1.5s 轮询无条件回写会打穿正在编辑的
    // 双踩窗/开关（改一半被洗掉、开关视觉回弹）；即改即生效控件在
    // 各自 change 后置 force 刷新基线
    if (force || !pdFormInit) {
      $("#pd-win").value = d.doubleWindow || 0.35;
      $("#pd-intercept").checked = !!d.intercept;
      $("#pd-remote").checked = !!d.remoteEnabled;
      pdFormInit = true;
    }
    // MIDI 踩钉口是学习 CC 时自动记录的内部端口名：只读展示，不可手填
    $("#pd-midiport").textContent = d.hint
        ? "MIDI 踩钉口：" + d.hint + " · 学习时自动记录"
        : "MIDI 踩钉口：未记录，学习时自动写入";
    drawBinds(d);
    if (!$("#pd-dev").options.length) refreshPedalDevices();
  } catch { /* 瞬断 */ }
}

// 即改即生效：每控件单发自己的键，后端在场检测不动其余配置；
// 失败必须回读基线（表单保护会挡住轮询回写，勾选态会停在错误值）
async function pedalApply(payload, msg) {
  const r = await post("/pedalset", payload);
  if (r.ok) {
    if (msg) toast(msg);
    refreshPedal(true);
  } else {
    refreshPedal(true);
  }
}
$("#pd-dev").addEventListener("change", () =>
    pedalApply({deviceHint: $v("#pd-dev")}, "输入设备已应用"));
$("#pd-remote").addEventListener("change", e =>
    pedalApply({remoteEnabled: e.target.checked},
               e.target.checked ? "已允许平板转发踩钉"
                                : "已停用平板转发踩钉"));
$("#pd-intercept").addEventListener("change", e =>
    pedalApply({intercept: e.target.checked}, "拦截硬件原键已应用"));
$("#pd-win").addEventListener("change", () => {
  const v = parseFloat($v("#pd-win"));
  if (isNaN(v)) {          // 脏输入回滚为当前生效值（后端夹取 0.12-1.0）
    toast("双踩窗须为数字，单位秒", "err");
    refreshPedal(true);
    return;
  }
  pedalApply({doubleWindow: v}, "双踩窗已应用");
});

async function startLearn(action) {
  const j = await post("/pedal/learn", {action});
  if (!j.ok) return;
  // 未选设备时键盘踩钉抓不到（raw 归属门）——学习前提醒，别让人白等 8 秒
  if (pdData && !pdData.deviceHint
      && Object.keys(pdData.hid || {}).length)
    toast("未选输入设备：踏板按键不会被学习抓取，只等 MIDI CC", "err");
  learnAction = action;
  drawBinds(pdData || {binds: {}, hid: {}, gestures: {}});
  pollLearn();
}
function pollLearn() {
  clearTimeout(learnTimer);
  learnTimer = setTimeout(async () => {
    if (learnAction === null) return;
    try {
      const r = await fetch("/pedal/learn");
      // 504=主线程忙：不得当「学习结束」消费（后端 Learner 还在跑，
      // 结果会无人认领）。有界重试，放弃时还原按钮态（红队 P1）
      if (!r.ok) {
        pollLearn.fails = (pollLearn.fails || 0) + 1;
        if (pollLearn.fails > 5) {
          const act = learnAction;
          learnAction = null;
          const btn = act && $('.bind-row[data-action="%s"] .b-learn'
                               .replace("%s", act));
          if (btn) btn.textContent = "学习";
          toast("学习状态查询连续失败，请重试学习", "err");
        } else pollLearn();
        return;
      }
      pollLearn.fails = 0;
      const d = await r.json();
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
      if (learnAction === null) return;      // 网络异常：同样有界重试
      pollLearn.fails = (pollLearn.fails || 0) + 1;
      if (pollLearn.fails > 5) {
        const act = learnAction;
        learnAction = null;
        const btn = act && $('.bind-row[data-action="%s"] .b-learn'
                               .replace("%s", act));
        if (btn) btn.textContent = "学习";
        toast("学习状态查询连续失败，请重试学习", "err");
      } else pollLearn();
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

// ---- 键盘自动化（键盘页=全局端口/移调只读；曲目映射在编排页详情面板）----
let kbData = null, kbPortsInit = false;
let recTimer = null;
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A",
                    "A#", "B"];
const noteName = n => NOTE_NAMES[n % 12] + (Math.floor(n / 12) - 1);

// 端口下拉：value=候选序号（保存时映射回 name+dev）。选项层随签名刷新
// （设备上电/掉电即反映，幽灵项自动清除），选中值按端口名保留——用户
// 未保存的手选不被回写打穿；已存配置的回填只在首拉/刷新/保存后（force）
function fillPortSel(id, list, hintKey) {
  const sel = $("#" + id);
  const entries = (kbData.ports || {})[list] || [];
  const names = entries.map(e => e.name);
  const sig = id + ":" + entries.map(e => e.name + "#" + e.dev).join("|")
      + "#" + (hintKey && !names.includes(hintKey) ? hintKey : "");
  if (sel.dataset.sig === sig) return;
  sel.dataset.sig = sig;
  sel.dataset.list = list;
  // 重建前记住选中项的端口名（value 是序号，重建后语义失效）
  const keepName = sel.selectedIndex >= 0 && sel.options[sel.selectedIndex]
      ? (sel.options[sel.selectedIndex].title || "") : "";
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
    o.textContent = hintKey + " · 未接入";
    o.title = hintKey;
    sel.appendChild(o);
  }
  if (keepName) {
    const i = Array.from(sel.options)
        .findIndex(o => o.title === keepName);
    if (i >= 0) {
      sel.value = String(i);
    } else {
      // 手选项已掉线：回落到当前已存配置对应的选项（在线→选中；
      // 不在线→幽灵项保名），绝不静默落到第一项=保存时错绑别的口
      const hi = names.indexOf(hintKey);
      if (hi >= 0) sel.value = String(hi);
      else {
        const g = Array.from(sel.options).find(o => o.value === "-1");
        if (g) sel.value = "-1";
      }
    }
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
  // 接驳状态徽：配置的口名在候选中出现且命中第几个（同名多口消歧）
  const setSt = (id, s) => {
    const el = $("#" + id);
    if (!el) return;
    const on = !!(s && s.hit);
    el.textContent = on
        ? "已接" + (s.total > 1 ? " · 第" + s.pos + "口" : "") : "未接";
    el.className = "f-st " + (on ? "on" : "off");
  };
  setSt("kb-jin-st", ps.junoIn);
  setSt("kb-jout-st", ps.junoOut);
  setSt("kb-ain-st", ps.axIn);
  setSt("kb-aout-st", ps.axOut);
  // 选项层每轮刷新（自带签名守卫+选中名保留）；回填已存配置仅 force
  fillPortSel("kb-jin", "in", kbData.junoIn);
  fillPortSel("kb-jout", "out", kbData.junoOut);
  fillPortSel("kb-ain", "in", kbData.axIn);
  fillPortSel("kb-aout", "out", kbData.axOut);
  if (!force && kbPortsInit) return;
  portSelVal("kb-jin", kbData.junoIn, (ps.junoIn || {}).pos);
  portSelVal("kb-jout", kbData.junoOut, (ps.junoOut || {}).pos);
  portSelVal("kb-ain", kbData.axIn, (ps.axIn || {}).pos);
  portSelVal("kb-aout", kbData.axOut, (ps.axOut || {}).pos);
  kbPortsInit = true;
}
function renderKbdStatus() {
  const el = $("#kb-status");
  if (!el) return;
  el.innerHTML = "";
  const mk = (txt, cls, title) => {
    const s = document.createElement("span");
    s.className = "pill" + (cls ? " " + cls : "");
    s.textContent = txt;
    if (title) s.title = title;
    el.appendChild(s);
  };
  // 当前曲音色配置计数（有配置才亮）；无工程时如实标注
  if (kbData.counts) {
    mk("JUNO " + kbData.counts.juno + "/10", kbData.counts.juno ? "ok" : "");
    mk("AX " + kbData.counts.ax + "/6", kbData.counts.ax ? "ok" : "");
  } else {
    mk("未打开工程");
  }
  if (kbData.lastResult)
    mk("最近切换 " + kbData.lastResult, "", kbData.lastResult);
}

// 录制（琴上捕获）：点槽卡即开录，再点同卡=停止并保存（直写该曲）；
// 全局同时至多一个录制会话；换曲/换页/关窗一律收口释放输入口。
// start 在途令牌 + 点击时刻 key 快照：慢网络下 start 返回时目标曲可能
// 已被换掉——凭快照即刻撤销会话，绝不让「B 曲名下存 A 曲快照」发生
// （时序红队 P0：跨曲整表覆写）
let recInst = null, recNote = null, recCap = null, recCtx = null,
    recCard = null, recPend = null;
function stopRecord() {
  if (!recInst && !recPend) return;
  if (recInst) post("/kbdrecord", {inst: recInst, stop: true});
  else if (recPend) post("/kbdrecord", {inst: recPend, stop: true});
  recInst = recNote = recCap = null;
  recCtx = null;
  recPend = null;
  clearTimeout(recTimer);
  if (recCard) {
    if (recCard.isConnected) {
      recCard.classList.remove("recording");
      const sub = recCard.querySelector(".s-sub");
      if (sub) sub.textContent = "";
    }
    recCard = null;
  }
}
function pollRecord(inst, onCap) {
  clearTimeout(recTimer);
  recTimer = setTimeout(async () => {
    try {
      const r = await fetch("/kbd/record");
      // 504=主线程忙（如别处开着原生目录框）：错误体不是会话数据，
      // 不得当 inactive 消费（否则后端会话还在、前端已弃管——输入口
      // 泄漏）。有界重试，放弃时补发 stop 排队释放（红队 P1）
      if (!r.ok) {
        pollRecord.fails = (pollRecord.fails || 0) + 1;
        if (pollRecord.fails > 5 && recInst === inst) {
          stopRecord();
          toast("录制状态查询连续失败，已请求停止录制", "err");
        } else if (recInst === inst) pollRecord(inst, onCap);
        return;
      }
      pollRecord.fails = 0;
      const d = await r.json();
      if (d.active && d.inst === inst && recInst === inst) {
        if (d.slot) onCap(d.slot);
        pollRecord(inst, onCap);
      } else if (recInst === inst) {
        // 会话被后端收口（超时/异常）：卡片退出录制态并如实告知
        const card = recCard;
        recInst = recNote = recCap = null; recCtx = null; recCard = null;
        if (card && card.isConnected) {
          card.classList.remove("recording");
          const sub = card.querySelector(".s-sub");
          if (sub) sub.textContent = "录制会话已结束";
        }
      }
    } catch {                                 // 网络异常：同样有界重试
      if (recInst !== inst) return;
      pollRecord.fails = (pollRecord.fails || 0) + 1;
      if (pollRecord.fails > 5) {
        stopRecord();
        toast("录制状态查询连续失败，已请求停止录制", "err");
      } else pollRecord(inst, onCap);
    }
  }, 300);
}

// 音色槽卡片直录：点卡=开录（捕获实时显示在卡上），再点=停止并保存；
// 「测试」「✕清除」与录制同卡并列，无二级弹层。快照（key+槽位字典）
// 定格在开录时刻：保存目标就是开录时选中的那首歌与那份映射
async function cardRecord(div, inst, note) {
  const es = dtKbd && dtKbd.editSong;
  const ready = !!(plSel && es && es.key === plSel && !es.loading);
  if (recInst === inst && recNote === note) {   // 再点=停止并保存
    const cap = recCap, ctx = recCtx, card = recCard, had = !!recInst;
    recInst = recNote = recCap = null; recCtx = null; recCard = null;
    clearTimeout(recTimer);
    if (card) card.classList.remove("recording");
    post("/kbdrecord", {inst, stop: true});     // 关口释放输入口
    if (!had) return;
    if (!cap) {
      // 捕获可能在途（300ms 轮拍）：停录瞬间补拉一次再判空，避免把
      // 后端已捕获的音色当「没收到」丢弃（红队 P2 停录竞态）
      let lastCap = null;
      try {
        const r = await fetch("/kbd/record");
        if (r.ok) lastCap = ((await r.json()).slot) || null;
      } catch { /* 瞬断按无捕获处理 */ }
      const got = lastCap;
      if (!got) {
        const sub = div.querySelector(".s-sub");
        if (sub) sub.textContent = "没收到音色信息，请检查琴口";
        return;
      }
      const slots = {...ctx.slots, [note]: got};
      post("/slotset", {inst, slots, songKey: ctx.key}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已录制："
            + (got.desc || "PC " + got.pc));
        refreshKbdDetail();
      });
      return;
    }
    const slots = {...ctx.slots, [note]: cap};
    post("/slotset", {inst, slots, songKey: ctx.key}).then(j => {
      if (j.ok) toast("音 " + noteName(note) + " 已录制："
          + (cap.desc || "PC " + cap.pc));
      refreshKbdDetail();
    });
    return;
  }
  if (!ready) {   // 红队 P0-1：目标不定不录（防串曲整表覆写）
    toast(!es || es.loading ? "该曲映射装载中…稍候再试"
          : "映射不可用，曲目不在素材库", "err");
    return;
  }
  if (recInst) stopRecord();                    // 全局单录制会话
  if (recPend) return;                          // start 在途：不重入
  const key0 = plSel, es0c = es;
  recPend = inst;
  post("/kbdrecord", {inst}).then(j => {
    recPend = null;
    if (!j.ok) return;
    if (plSel !== key0) {                       // 在途窗口换曲：即刻撤销
      post("/kbdrecord", {inst, stop: true});
      toast("录制已取消：目标曲已变化", "err");
      return;
    }
    recInst = inst;
    recNote = note;
    recCap = null;
    recCard = div;
    const src = inst === "ax" ? es0c.axSlots : es0c.slots;
    const snap = {};
    Object.keys(src || {}).forEach(k => snap[Number(k)] = {...src[k]});
    recCtx = {key: key0, slots: snap};
    div.classList.add("recording");
    const sub = div.querySelector(".s-sub");
    if (sub) sub.textContent = "录制中：请在琴上选音色…";
    pollRecord(inst, cap => {
      recCap = cap;
      const s2 = recCard && recCard.querySelector(".s-sub");
      if (s2) s2.textContent = "已捕获：" + (cap.desc || "PC " + cap.pc)
          + " · 再点本卡保存";
    });
  });
}
function slotRow(note, slot, inst) {
  const div = document.createElement("div");
  div.className = "slot" + (slot ? "" : " unconfigured");
  div.dataset.inst = inst;
  div.dataset.note = note;
  const n = document.createElement("span");
  n.className = "s-note";
  n.textContent = "音 " + noteName(note);
  const pc = document.createElement("span");
  pc.className = "s-pc";
  pc.textContent = slot ? (slot.desc || "PC " + slot.pc) : "未配置";
  const sub = document.createElement("span");
  sub.className = "s-sub";
  const ops = document.createElement("span");
  ops.className = "s-ops";
  const test = document.createElement("button");
  test.className = "btn ghost xs s-test";
  test.textContent = "测试";
    test.title = "向琴发送该音色做连通测试";
  test.addEventListener("click", ev => {
    ev.stopPropagation();
    if (!slot) {
      toast("该音符未配置音色映射", "err");
      return;
    }
    post("/kbdtest", {inst, note, songKey: plSel}).then(j => {
      if (j.ok) toast("已发送测试：音 " + noteName(note));
    });
  });
  ops.appendChild(test);
  if (slot) {
    const clr = document.createElement("button");
    clr.className = "btn ghost xs s-clear";
    clr.textContent = "✕";
    clr.title = "清除该音色映射";
    clr.addEventListener("click", ev => {
      ev.stopPropagation();
      const es = dtKbd && dtKbd.editSong;
      if (!plSel || !es || es.key !== plSel || es.loading) {
        toast("映射未就绪", "err");
        return;
      }
      const slots = {...(inst === "ax" ? es.axSlots : es.slots)};
      delete slots[note];
      post("/slotset", {inst, slots, songKey: plSel}).then(j => {
        if (j.ok) toast("音 " + noteName(note) + " 已清除");
        refreshKbdDetail();
      });
    });
    ops.appendChild(clr);
  }
  div.append(n, pc, sub, ops);
  div.title = "点击录制；再点一次停止并保存";
  div.tabIndex = 0;
  div.addEventListener("click", () => cardRecord(div, inst, note));
  return div;
}
async function refreshKbd(force) {
  try {
    const seq = refreshKbd.seq = (refreshKbd.seq || 0) + 1;
    const r = await fetch("/kbd");
    if (!r.ok) return;                    // 504 错误体不当数据（P1-2）
    const d = await r.json();
    if (seq !== refreshKbd.seq) return;   // 旧响应不覆盖新请求
    kbData = d;
    $("#kb-jshift").textContent = fmtShift(kbData.junoShift || 0);
    $("#kb-ashift").textContent = fmtShift(kbData.axShift || 0);
    for (const id of ["kb-jshift", "kb-ashift"]) {   // 移调数值变化脉冲
      const el = $("#" + id);
      if (el.dataset.v !== undefined && el.dataset.v !== el.textContent) {
        el.classList.remove("pulse");
        void el.offsetWidth;                 // 重启动画
        el.classList.add("pulse");
      }
      el.closest(".shift-val")
          .classList.toggle("hot", el.textContent !== "0");
      el.dataset.v = el.textContent;
    }
    renderKbdPorts(force);
    renderKbdStatus();
  } catch { /* 瞬断 */ }
}

// ---- 编排页详情面板的音色映射（选中曲的 /kbd?song= 视图）----
let dtKbd = null, dtPtN = null;
function updateDtBadge() {
  const el = $("#dt-n");
  if (!el) return;          // 详情列未在 DOM（防御：响应迟到/宿主壳差异）
  if (!plSel) { el.textContent = "未选中"; return; }
  el.textContent = "定位点 " + (dtPtN === null ? "—" : dtPtN);
}
function buildSlots(el, slots, from, to, inst) {
  const sig = Object.keys(slots || {}).map(k =>
      k + ":" + JSON.stringify(slots[k])).join("|");
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = "";
  for (let n = from; n <= to; n++)
    el.appendChild(slotRow(n, (slots || {})[String(n)], inst));
  // 差量重建后录制指示重挂：他端改了同曲任一槽位会换节点，红框/副行
  // 会话还在跑却消失（时序红队 P2）
  if (recInst === inst && recNote !== null) {
    const c = Array.from(el.children)
        .find(x => Number(x.dataset.note) === recNote);
    if (c) {
      recCard = c;
      c.classList.add("recording");
      const sub = c.querySelector(".s-sub");
      if (sub && !sub.textContent) sub.textContent = "录制中：请在琴上选音色…";
    }
  }
}
async function refreshKbdDetail() {
  if (!plSel) {
    dtKbd = null;
    return;
  }
  updateDtBadge();
  try {
    const seq = refreshKbdDetail.seq = (refreshKbdDetail.seq || 0) + 1;
    const r = await fetch("/kbd?song=" + encodeURIComponent(plSel));
    if (!r.ok) return;                    // 504 错误体不当数据（P1-2）
    const d = await r.json();
    if (seq !== refreshKbdDetail.seq) return;
    // 数据须属于当前选中曲且非装载中才渲染：导航/换曲瞬间的陈旧 plSel
    // 响应不得把别曲映射画到新曲名下（显示层与编辑层同一口径）
    const es0 = d.editSong;
    if (!es0 || es0.key !== plSel || es0.loading) {
      dtKbd = null;
      $("#dt-kbd-stat").textContent = !es0 || es0.loading
          ? "映射装载中…" : "映射不可用，曲目不在素材库";
      return;
    }
    dtKbd = d;
    const es = es0;
    $("#dt-kbd-stat").textContent = es.loading ? "映射装载中…"
        : "JUNO " + Object.keys(es.slots || {}).length + "/10 · AX "
          + Object.keys(es.axSlots || {}).length + "/6";
    buildSlots($("#dt-slots"), es.slots, 60, 69, "juno");
    buildSlots($("#dt-axslots"), es.axSlots, 72, 77, "ax");
    updateDtBadge();
    // 任意曲首答 loading（后台装载中）：短暂自拉直到数据就位
    if (es.loading && pageOn("setlist"))
      setTimeout(() => { if (pageOn("setlist")) refreshKbdDetail(); }, 700);
  } catch { /* 瞬断 */ }
}
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
// 移调无手调入口：轨上键位驱动，页面只读显示（变化脉冲提示）
const fmtShift = v => (v > 0 ? "+" : "") + v;

// ---- VJ 联动 ----
let vjMapRows = null, vjMapRoot = null, vjLoaded = false;
async function refreshVjMap(root, force) {
  if (!force && root === vjMapRoot && vjMapRows) return;  // 目录没变不重扫
  try {
    const r = await fetch("/vjmap");
    if (!r.ok) return;
    const d = await r.json();
    vjMapRoot = d.videoRoot;
    vjMapRows = d.rows;
    const box = $("#vjmap");
    box.innerHTML = "";
    if (!vjMapRoot) {
      box.innerHTML = "<div class='empty-state sm vj-empty'>未设置视频目录，"
          + "先在下方「视频联动」里选择</div>";
      return;
    }
    // 全局移调同款卡片语言：键帽音名 + 目标大字 + 状态副行，网格多列
    d.rows.forEach(row => {
      const cell = document.createElement("div");
      cell.className = "slot vj-cell"
          + (row.blackout ? " blackout" : "")
          + (row.hit || row.blackout ? "" : " miss");
      cell.dataset.file = row.file || "";
      cell.title = row.blackout ? "F#6：熄灭投影画面"
          : (row.hit ? "视频库：" + row.hit
             : "视频库中未找到 " + row.file + "，可按「N 描述」命名放入视频目录");
      const n = document.createElement("span");
      n.className = "s-note";
      n.textContent = row.name;
      const f = document.createElement("span");
      f.className = "s-pc";
      f.textContent = row.blackout ? "熄屏" : row.file;
      const sub = document.createElement("span");
      sub.className = "s-sub";
      sub.textContent = row.blackout ? "熄灭投影画面"
          : (row.hit ? "已就位" : "缺视频");
      cell.append(n, f, sub);
      box.appendChild(cell);
    });
  } catch { /* 瞬断 */ }
}

async function refreshVj(force) {
  try {
    const d = await (await fetch("/settings")).json();
    vjLoaded = true;
    $("#vj-save").disabled = false;
    $("#vj-save").title = "";
    fillSel("vj-mon", d.obs.projectorMonitor, d.screens || [], " · 不在场");
    $("#vj-media").value = d.obs.mediaInput || "";
    $("#vj-root").value = d.obs.videoRoot || "";
    $("#vj-mute").checked = !!d.obs.vjMute;
    const ms = $("#vj-media-st");
    ms.textContent = d.obs.mediaInput || "未配置";
    ms.className = "stat-big" + (d.obs.mediaInput ? "" : " dim");
    refreshVjMap((d.obs.videoRoot || "").trim(), !!force);
  } catch { /* 瞬断：进页/保存会重拉 */ }
}
$("#vj-save").addEventListener("click", async () => {
  const r = await post("/settings", {obs: {
    projectorMonitor: $v("#vj-mon"), mediaInput: $v("#vj-media"),
    videoRoot: $v("#vj-root"), vjMute: $("#vj-mute").checked}});
  if (r.ok) toast("VJ 配置已应用");
  refreshVj(true);
});
function renderVjState() {
  const el = $("#vj-obs");
  if (!stState) return;
  const vj = stState.vj || {};
  // OBS 卡：连接态 + 错误详情副行（断连原因/启动失败文案，不进日志翻找）
  const err = vj.obsErr || "";
  el.textContent = stState.obs ? "已连接" : "未连接";
  el.className = "stat-big " + (stState.obs ? "ok" : "warn");
  $("#vj-obs-sub").textContent =
      err || (stState.ready ? "" : "正在启动…");
  // 走带跟随卡（与旧监控格同源文案，值域 playing/paused/stopped/off）
  const f = $("#vj-follow");
  const FOLLOW_TXT = {playing: "播放中", paused: "已暂停",
                      stopped: "未在播放", off: "未跟随"};
  f.textContent = FOLLOW_TXT[vj.follow] || "—";
  f.className = "stat-big " +
      (vj.follow === "playing" ? "ok"
       : vj.follow === "paused" ? "warn" : "");
  f.title = vj.vjEnabled === false ? "VJ 联动已停用：未配端口"
      : vj.portOn === false ? "端口未启动" : "";
  // 视频名只在播放/暂停时挂出（同旧监控格；stopped/播完不带）
  $("#vj-video").textContent =
      (vj.follow === "playing" || vj.follow === "paused") && vj.video
      ? "视频：" + vj.video : "";
  // 映射网格：在播视频对应的音符卡高亮（按 N.mp4 编号匹配）
  if (vjMapRows) {
    const num = x => (x.match(/^(\d+)/) || [])[1] || "";
    const cur = num((vj.video || "").split("/").pop());
    $$("#vjmap .vj-cell").forEach(chip => {
      chip.classList.toggle("live",
          !!cur && !chip.classList.contains("blackout")
          && num(chip.dataset.file || "") === cur);
    });
  }
}

// ---- 设置页（分组卡 + 脏标记）----
const ST_FIELDS = ["st-clock", "st-vj", "st-kb", "st-auto", "st-cont",
                   "st-confirm", "st-exit", "st-root", "st-exe",
                   "st-autosave", "st-web", "st-mode", "st-srv", "st-app",
                   "st-tsk", "st-pg", "st-ip"];
let stBase = null;
const stSnap = () => ST_FIELDS.map(id => {
  const el = $("#" + id);
  return el.type === "checkbox" ? el.checked : el.value;
}).join("¦");
const portOk = id => {
  const v = Number($("#" + id).value);
  return Number.isInteger(v) && v >= 1 && v <= 65535;
};
function stDirty() {
  const ports = ["st-srv", "st-app", "st-tsk"];
  const bad = ports.some(id => !portOk(id));
  ports.forEach(id =>
      $("#" + id).classList.toggle("invalid", !portOk(id)));
  const dirty = stBase !== null && stSnap() !== stBase;
  $("#st-dirty").hidden = !dirty;
  $("#st-save").disabled = dirty && !bad ? false : true;
  $("#st-save").title = bad ? "端口须为 1-65535 的整数" : "";
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
    fillSel("st-clock", d.clockHint, d.ports || [], " · 未接入");
    fillSel("st-vj", d.vjHint, d.ports || [], " · 未接入");
    fillSel("st-kb", d.kbHint, d.ports || [], " · 未接入");
    fillSel("st-pg", d.web.midiIn, d.ports || [], " · 未接入");
    $("#st-auto").checked = d.autoAdvance;
    $("#st-cont").checked = d.autoPlay;
    $("#st-confirm").checked = d.switchConfirm;
    $("#st-exit").checked = d.exitCloseApps;
    $("#st-root").value = d.projectsRoot;
    $("#st-exe").value = d.dawExe;
    $("#st-autosave").checked = d.autoSave;
    $("#st-web").checked = d.web.enabled;
    $("#st-mode").value = d.web.mode;
    // config 里存了非法 mode（手改文件）：回落首个候选，保存即归一
    if (!$("#st-mode").value) $("#st-mode").value = "hotspot";
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
        o.textContent = d.web.ip + " · 不在场";
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
    switchConfirm: $("#st-confirm").checked,
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
    toast("目录选择失败：网络异常", "err");
  }
}
$$(".browse").forEach(b =>
    b.addEventListener("click", () => pickDir(b.dataset.pick)));

// ---- 设置路径即时校验（工程库根=目录 / Cubase 程序=文件）----
const PATH_KIND = {"st-root": "dir", "st-exe": "file"};
async function checkPath(id) {
  const el = $("#" + id);
  const v = el.value.trim();
  if (!v || !PATH_KIND[id]) return;
  try {
    const r = await fetch("/checkpath", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: v})});
    if (!r.ok) return;
    const d = await r.json();
    const bad = !d.exists || (PATH_KIND[id] === "dir" && !d.isdir)
        || (PATH_KIND[id] === "file" && d.isdir);
    el.classList.toggle("invalid", bad);
    el.title = bad ? (!d.exists ? "路径不存在"
        : (PATH_KIND[id] === "dir" ? "该路径不是目录" : "该路径不是文件"))
        : "";
  } catch { /* 瞬断 */ }
}
["st-root", "st-exe"].forEach(id => {
  $("#" + id).addEventListener("change", () => checkPath(id));
});

// ---- 编排窄窗 tab（<900px 双列折叠为单列）----
$$("#sl-tabs button").forEach(b => b.addEventListener("click", () => {
  $$("#sl-tabs button").forEach(x => x.classList.toggle("on", x === b));
  $$(".cols .col").forEach(c =>
      c.classList.toggle("show", c.dataset.col === b.dataset.col));
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
setInterval(() => {
  if (pageOn("setlist")) { refreshPoints(); refreshKbdDetail(); }
  if (pageOn("kbd")) refreshKbd();   // 状态行/移调徽标随切歌走
}, 1500);
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
