// attack2_movepend.js —— 攻击面三：movePend 在途门（修复后回归版）
// 从 webapp/app.js 原文抽取 movePl/movePlKey/refreshPlaylist（brace-match），
// drop 处理器按原文照抄、服务器 move 按后端语义建模。修复后断言：
// A seq 丢弃→自动重拉，门保持到回显落地；B 网络瞬断→重拉一次落地；
// C drop 在门内被吞（不产生第二条陈旧 move）。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");

function extractFn(name) {
  let i = SRC.indexOf("function " + name + "(");
  if (i < 0) throw new Error(name + " not found");
  const pre = i >= 6 && SRC.slice(i - 6, i) === "async " ? "async " : "";
  const j = SRC.indexOf("{", i);
  let d = 0;
  for (let k = j; k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}") { if (--d === 0) return pre + SRC.slice(i, k + 1); }
  }
  throw new Error("unbalanced");
}

// ---- 服务器模型：_pl_cmd_web move 语义 ----
let SERVER = [];
function srvMove(from, to) {
  if (!(0 <= from < SERVER.length) || !(0 <= to < SERVER.length)) return;
  SERVER.splice(to, 0, SERVER.splice(from, 1)[0]);
}

// ---- 可控 deferred 网络层 ----
let defers = [];
global.fetch = () => { const d = {kind: "fetch"}; d.p = new Promise((res, rej) => {
  d.res = v => res({ok: true, json: async () => v}); d.rej = rej; });
  defers.push(d); return d.p; };
global.post = (path, body) => { const d = {kind: "post:" + body.op};
  d.p = new Promise(res => { d.res = () => {
    if (path === "/plcmd" && body.op === "move")
      srvMove(body.payload.from, body.payload.to);
    res({ok: true}); }; });
  defers.push(d); return d.p; };
global.toast = () => {};
global.drawLib = () => {}; global.drawPl = () => {};
global.closePop = () => {};
global.plCache = null; global.stCur = 0; global.dragKey = null;
global.popEl = null; global.popAnchor = null;

const glue = `
let movePend = false;
${extractFn("movePl")}
${extractFn("movePlKey")}
${extractFn("refreshPlaylist")}
global.__api = {get movePend() { return movePend; },
  movePl, movePlKey, refreshPlaylist};
`;
(0, eval)(glue);
const {movePlKey} = global.__api;
const setPl = arr => { global.plCache =
  {pl: arr.map(k => ({key: k, name: k})), cur: 0}; };
const tick = () => new Promise(r => setImmediate(r));
const take = kind => {
  const i = defers.findIndex(d => d.kind === kind);
  if (i < 0) throw new Error("缺 " + kind + "；队列=" +
      defers.map(d => d.kind).join(","));
  return defers.splice(i, 1)[0];
};
const plView = () => JSON.stringify(global.plCache.pl.map(x => x.key));
const srvView = () => JSON.stringify(SERVER);
// 把队列里全部挂起 fetch 按序喂真值（并发丢弃/重拉的个数不定，逐个 take 会抓错）
const drainFetch = () => {
  const n = defers.filter(d => d.kind === "fetch").length;
  for (let k = 0; k < n; k++)
    take("fetch").res({pl: SERVER.map(x => ({key: x})), cur: 0});
  return n;
};

// drop 处理器原文语义：解析键→movePl（门在 movePl 内部）
function drop(dragK, targetK, before) {
  const pl = global.plCache.pl;
  const tIdx = pl.findIndex(x => x.key === targetK) + (before ? 0 : 1);
  const from = pl.findIndex(x => x.key === dragK);
  global.dragKey = null;
  global.__api.movePl(from, from < tIdx ? tIdx - 1 : tIdx);
}
async function reset(server) {
  defers.forEach(d => d.kind === "fetch" && d.res(null));
  defers = [];
  await tick(); await tick(); await tick();
  defers = [];
  SERVER = server.slice();
  setPl(server.slice());
}

(async () => {
  const verdicts = [];

  // ===== A：refresh 被 seq 丢弃 → movePl 自动重拉，门保持到落地 =====
  console.log("场景A：拖 B 到顶在途；自身 refresh 被并发更高 seq 丢弃");
  await reset(["A", "B", "C", "D"]);
  drop("B", "A", true);                       // movePend=true，POST 在途
  await tick();
  take("post:move").res();                    // SERVER=[B,A,C,D]
  await tick();                               // movePl → refresh F1(seq=n)
  global.__api.refreshPlaylist();             // 并发 refresh（seq=n+1）
  await tick();
  take("fetch").res({pl: SERVER.map(k => ({key: k})), cur: 0});  // F1 被丢弃
  await tick();                               // movePl 检测未落地 → 重拉 F3
  const pendA = global.__api.movePend;        // F2/F3 在途：门必须仍关
  const staleA = plView();
  drainFetch();                               // 并发 refresh 与重拉全落地
  await tick(); await tick();
  const freshA = plView(), gateA = global.__api.movePend;
  movePlKey("C", -1);                         // 此时按新表解析：C idx2→1
  await tick();
  take("post:move").res();
  await tick();
  drainFetch();
  await tick(); await tick();
  console.log("  F3 在途时门=" + pendA + "（true=保持），plCache=" + staleA
      + "；落地后=" + freshA + "，门=" + gateA);
  console.log("  C↑ 按新表解析 → 服务端=" + srvView()
      + "（期望 [B,C,A,D]=C 上移一位，无多余换位）");
  verdicts.push(["A seq 丢弃→重拉保门→新表解析",
      pendA === true && staleA === '["A","B","C","D"]'
      && freshA === '["B","A","C","D"]'
      && srvView() === '["B","C","A","D"]']);

  // ===== B：refresh 瞬断 → 重拉一次落地；B 已在顶时 ↑ 为无操作 =====
  console.log("\n场景B：拖 B 到顶落地，refresh 瞬断 → 重拉落地；再点 B↑");
  await reset(["A", "B", "C"]);
  drop("B", "A", true);
  await tick();
  take("post:move").res();                    // SERVER=[B,A,C]
  await tick();
  take("fetch").rej(new Error("net down"));   // F1 瞬断
  await tick();
  const pendB = global.__api.movePend;        // 重拉在途：门必须仍关
  take("fetch").res({pl: SERVER.map(k => ({key: k})), cur: 0});
  await tick(); await tick();
  movePlKey("B", -1);                         // B 已 idx0 → 越界 no-op
  await tick(); await tick();
  const postsB = defers.filter(d => d.kind === "post:move").length;
  console.log("  瞬断期间门=" + pendB + "；落地后 B↑ 为 no-op，"
      + "追加 move 包=" + postsB + "，服务端=" + srvView());
  verdicts.push(["B 瞬断→重拉保门→无反向压回",
      pendB === true && postsB === 0 && srvView() === '["B","A","C"]']);

  // ===== C：POST 在途时二次拖拽 → drop 被 movePl 入口门吞掉 =====
  console.log("\n场景C：点 ↑（POST1 在途、门关），随即二次拖拽 drop");
  await reset(["A", "B", "C"]);
  movePlKey("B", -1);
  await tick();
  const pendC = global.__api.movePend;
  drop("C", "A", false);                      // 门内 → 应被吞
  await tick(); await tick();
  take("post:move").res();                    // POST1: 1→0 → [B,A,C]
  await tick();
  drainFetch();                               // POST1 的回显链全落地
  await tick(); await tick();
  const extraC = defers.filter(d => d.kind === "post:move").length;
  console.log("  drop 时门=" + pendC + "，被吞（链尾多余 move 包="
      + extraC + "）；服务端=" + srvView());
  verdicts.push(["C drop 被门吞", pendC === true && extraC === 0
      && srvView() === '["B","A","C"]']);

  console.log("\n==== 结论 ====");
  verdicts.forEach(([name, hit]) =>
      console.log("  " + (hit ? "[PASS] " : "[FAIL] ") + name));
  process.exit(verdicts.every(v => v[1]) ? 0 : 1);
})();
