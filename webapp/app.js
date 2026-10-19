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
    if (r.ok) render(await r.json());
  } catch {
    /* 热点瞬断：下轮再取 */
  }
}
setInterval(poll, 500);
poll();
