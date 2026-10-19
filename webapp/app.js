/* 新前端骨架：导航 + /state 轮询渲染 + 走带命令。P2 逐页填肉。 */
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

// ---- 导航 ----
function nav(page) {
  $$(".nav").forEach(b => b.classList.toggle("on", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("on", p.id === "p-" + page));
}
$$(".nav").forEach(b => b.addEventListener("click", () => nav(b.dataset.page)));
nav("console");
$("#btn-show").addEventListener("click", () => {
  document.body.classList.toggle("show");
  $("#btn-show").textContent =
      document.body.classList.contains("show") ? "⤢ 退出演出" : "⛶ 演出";
});

// ---- 走带命令 ----
async function cmd(action, index) {
  const body = index === undefined ? {action} : {action, index};
  try {
    const r = await fetch("/cmd", {method: "POST",
                                   headers: {"Content-Type": "application/json"},
                                   body: JSON.stringify(body)});
    return r.ok;
  } catch {
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
const LAMPS = ["l-daw", "l-obs", "l-loop", "l-pedal", "l-hot", "l-remote"];
const fmt = s => {
  s = Math.max(0, Math.round(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
};

function render(st) {
  const page = $("#p-console");
  if (!page) return;
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
  // 状态灯：/state 现有字段尽量映射；缺的先落 err 灯（P2 补端点）
  const busy = st.busy;
  const lamp = (id, cls) => {
    const el = $("#" + id);
    el.classList.remove("ok", "warn", "err");
    el.classList.add(cls);
  };
  lamp("l-daw", st.open ? "ok" : "err");
  lamp("l-remote", st.ready ? "ok" : "err");
  lamp("l-pedal", st.pedalRemote && st.pedalRemote.ok ? "ok" : "warn");
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

// ================= P2 页面 =================
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
      plcmd(op, it.key);
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
  if (op === "remove") {
    const it = (plCache.pl || []).find(x => x.key === key);
    if (it && !confirm("把《" + it.name + "》移出播放列表？")) return;
  }
  await fetch("/plcmd", {method: "POST",
                         headers: {"Content-Type": "application/json"},
                         body: JSON.stringify({op, key})});
  refreshPlaylist();
}
$("#pl-clear").addEventListener("click", async () => {
  if (confirm("确定清空播放列表？（素材库不受影响）")) plcmd("clear", "");
});

// ---- 定位点页 ----
let ptProj = null, ptData = null, ptSel = null;
const parseTC = t => {
  const p = String(t || "").split(":").map(Number);
  if (p.some(isNaN)) return null;
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
    sel.innerHTML = "";
    Object.keys(ptData).forEach(k => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = ptData[k].name + (ptData[k].enabled ? " ✓" : "");
      if (k === ptProj) o.selected = true;
      sel.appendChild(o);
    });
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
  } catch { /* 瞬断 */ }
}
async function ptcmd(op, payload) {
  await fetch("/ptcmd", {method: "POST",
                         headers: {"Content-Type": "application/json"},
                         body: JSON.stringify({op, key: ptProj,
                                               payload: payload || {}})});
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
$("#pt-del").addEventListener("click", () => {
  if (ptSel) ptcmd("del", {id: ptSel});
});
$("#pt-save").addEventListener("click", () => {
  const s = parseTC($("#pt-s").value), e = parseTC($("#pt-e").value);
  if (ptSel && s !== null && e !== null)
    ptcmd("edit", {id: ptSel, name: $("#pt-name").value, start: s, end: e});
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

// ---- 摘要页（踩钉/键盘/VJ/设置）----
function renderSummary() {
  const st = stState;
  if (!st) return;
  const item = (k, v) => "<div class=\"item\"><b>" + k + "</b><span>" + v +
                         "</span></div>";
  $("#pedal-sum").innerHTML =
      item("有线踩钉", st.pedalRemote && st.pedalRemote.ok ? "已连接"
              : "未连接") +
      item("远程转发", st.pedalRemote ? "支持" : "不支持");
  $("#kbd-sum").innerHTML = item("状态", "见旧版键盘自动化窗（后续迁入）");
  $("#vj-sum").innerHTML = item("视频联动", st.open ? "跟随当前工程"
                                                   : "无打开的工程");
  $("#set-sum").innerHTML =
      item("DAW", "Cubase") +
      item("遥控服务", st.ready ? "运行中" : "未启动") +
      item("工程库", "见旧版设置窗");
}
setInterval(refreshPlaylist, 1500);
setInterval(refreshPoints, 1500);
setInterval(refreshLog, 1500);
refreshPlaylist();
refreshPoints();
refreshLog();

