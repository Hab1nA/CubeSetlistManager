// attack4_movestuck.js —— 四轮攻击面二：moveStuck 保门与轮询放门交错
// 攻击点（对三轮回修本身的攻击）：
//   T1 离页卡死：moveStuck 置位后离开 console/setlist 页 → 1.5s 轮询不跑，
//      门不释放；回页时 nav() 的裸 refreshPlaylist()（app.js:124）也不放门
//      （放门检查只在轮询 .then 里）→ 多等 ≤1 个轮询拍；卡死期间 ↑/drop
//      静默吞（无二次提示）。
//   T2 plcmd2 成功不放门：add/remove 内部 refreshPlaylist 落地也不放门，
//      最坏多等 1 拍（1.5s）。
//   T3 放门后新 move 是否基于新表（交错正确性）。
//   T4 放门前最后一瞬的点击被同步门检查吞掉（可接受性验证）。
//   T5 move 越界（他端删曲+陈旧 plCache）+网络通：错误 toast + 落地放门。
//   T6 断网叠加：POST 网络失败 + 两拉皆败 → 保门 + 双 toast；恢复后轮询放。
//   T7 纯并发假卡死：seq 竞抢让 movePl 的两拉都被「旧响应」丢弃 → 谎报
//      「网络不稳」并保门（并非断网），下拍轮询自愈。
// 全部函数从 webapp/app.js 原文逐字抽取执行；后端 move 段按
// setlist_gui._pl_cmd_web 现行语义（越界返回错误串→HTTP 400）同构模拟。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");

function cut(startMarker, endMarker) {
  const i = SRC.indexOf(startMarker);
  if (i < 0) throw new Error("no start: " + startMarker);
  const j = SRC.indexOf(endMarker, i);
  if (j < 0) throw new Error("no end: " + endMarker);
  return SRC.slice(i, j);
}

// ---- 原文抽取 ----
const CODE = [
  "let stState = null, plCache = null, stCur = null;",
  "let movePend = false, dragKey = null, moveStuck = false;",
  cut("async function post(", "\n// ---- 走带"),
  cut("async function movePl(", "\nfunction movePlKey("),
  cut("function movePlKey(", "\nfunction drawPl("),
  cut("async function refreshPlaylist(", "\nasync function plcmd2("),
  cut("async function plcmd2(", "\n\n// ================= 定位点页"),
  // 1.5s 轮询块内层逐字（app.js 2561-2568），外壳换函数
  "global.__tick15 = () => {\n  "
    + cut('if (pageOn("console", "setlist")) {\n    refreshPlaylist()',
          "if (landed && moveStuck) { moveStuck = false; movePend = false; }")
    + 'if (landed && moveStuck) { moveStuck = false; movePend = false; }\n'
    + "    });\n  }\n};",
  "global.__mp = () => movePend; global.__ms = () => moveStuck;"
  + " global.__pc = () => plCache; global.__refresh = refreshPlaylist;"
  + " global.__plcmd2 = plcmd2; global.__movePlKey = movePlKey;"
  + " global.__setCache = keys => { plCache = {pl: keys.map("
  + "k => ({key: k, name: k}))}; };"
].join("\n");

// ---- 桩 ----
global.$ = () => ({classList: {add() {}, remove() {}, toggle() {}}, dataset: {},
                   textContent: "", hidden: false});
global.$$ = () => [];
global.drawLib = () => {}; global.drawPl = () => {};
global.popEl = null; global.popAnchor = null; global.closePop = () => {};
const toasts = [], posts = [];
global.toast = (m) => toasts.push(m);
let pageOnResult = true;
global.pageOn = () => pageOnResult;

// ---- 后端真值模型（_pl_cmd_web move/add 现行语义同构）----
const backend = {keys: []};
function bePlcmd(op, key, payload) {
  if (op === "add") { backend.keys.push(key); return null; }
  if (op === "move") {
    const frm = payload.from, to = payload.to;
    if (!Number.isInteger(frm) || !Number.isInteger(to)) return "move 参数非法";
    if (!(frm >= 0 && frm < backend.keys.length)
        || !(to >= 0 && to < backend.keys.length))
      return "移动下标越界";
    const it = backend.keys.splice(frm, 1)[0];
    backend.keys.splice(to, 0, it);
    return null;
  }
  return null;
}

// ---- 网络桩 ----
let getFail = false, postThrow = false, plOrder = false;
const plQueue = [];
const plSnapshot = () => ({ok: true, json: async () =>
    ({lib: [], pl: backend.keys.map(k => ({key: k, name: k})), cur: 0})});
global.fetch = async (path, opts) => {
  const p = String(path);
  if (p.includes("/plcmd")) {
    if (postThrow) throw new Error("network down");
    const body = JSON.parse(opts.body);
    posts.push(body);
    const err = bePlcmd(body.op, body.key, body.payload);
    if (err) return {ok: false, json: async () => ({error: err})};
    return {ok: true, json: async () => ({ok: true})};
  }
  if (p.includes("/playlist")) {
    if (plOrder)
      return new Promise((res, rej) => plQueue.push({res, rej}));
    if (getFail) throw new Error("network down");
    return plSnapshot();
  }
  throw new Error("unexpected " + path);
};

(0, eval)(CODE);
const mpl = global.__movePlKey, tick15 = global.__tick15;
const tick = () => new Promise(r => setImmediate(r));
async function settle() { for (let i = 0; i < 10; i++) await tick(); }

const fails = [];
const ok = (name, cond, detail) => {
  console.log((cond ? "PASS " : "FAIL ") + name
      + (detail ? " | " + detail : ""));
  if (!cond) fails.push(name);
};
const pc = () => JSON.stringify((global.__pc() || {pl: []}).pl.map(x => x.key));
const bk = () => JSON.stringify(backend.keys);

(async () => {
  // ==== T1 断网保门 → 离页不放门 → 回页 nav 拉也不放门 → 轮询放门 ====
  backend.keys = ["A", "B", "C"];
  global.__setCache(["A", "B", "C"]);
  getFail = true; posts.length = 0; toasts.length = 0;
  await mpl("C", -1); await settle();
  ok("T1a POST 落地（后端 [A,C,B]）两拉皆败 → 保门 moveStuck=true",
     bk() === '["A","C","B"]' && global.__ms() === true && global.__mp() === true,
     "backend=" + bk() + " stuck=" + global.__ms() + " pend=" + global.__mp());
  ok("T1b 断网瞬间 toast（成功提示+网络不稳各一）", toasts.length === 2
     && toasts[1].includes("网络不稳"), JSON.stringify(toasts));
  pageOnResult = false;                      // 离开 console/setlist（settings 等）
  getFail = false;
  await tick15(); await settle();            // 1.5s 拍：pageOn=false 不跑
  ok("T1c 离页期间轮询不放门（stuck 仍 true）", global.__ms() === true);
  posts.length = 0;
  await mpl("B", -1); await settle();        // 卡死期间再点 ↑：静默吞
  ok("T1d 卡死期间 ↑ 被吞（无 POST、无新 toast）",
     posts.length === 0 && toasts.length === 2);
  await global.__refresh(); await settle();  // 回页 nav() 的裸拉（app.js:124）
  ok("T1e 回页 nav 的 refreshPlaylist 落地也不放门（放门只在轮询 .then）",
     pc() === '["A","C","B"]' && global.__ms() === true,
     "plCache=" + pc() + " stuck=" + global.__ms());
  pageOnResult = true;
  await tick15(); await settle();
  ok("T1f 回页后第一个 1.5s 拍放门（最坏多等 1 拍）",
     global.__ms() === false && global.__mp() === false && pc() === '["A","C","B"]');

  // ==== T2 plcmd2 成功不放门（最坏多等 1 拍）====
  backend.keys = ["A", "B", "C"];
  global.__setCache(["A", "B", "C"]);
  getFail = true; posts.length = 0; toasts.length = 0;
  await mpl("C", -1); await settle();        // 保门
  ok("T2a 再次保门成立", global.__ms() === true && bk() === '["A","C","B"]');
  getFail = false;
  await global.__plcmd2("add", "D"); await settle();
  ok("T2b plcmd2 add 成功且其 refreshPlaylist 已落地（plCache 含 D）",
     pc() === '["A","C","B","D"]' && bk() === '["A","C","B","D"]',
     "plCache=" + pc() + " backend=" + bk());
  ok("T2c 但门未放（moveStuck 仍 true，放门检查只在轮询 .then）",
     global.__ms() === true);
  posts.length = 0;
  await mpl("A", 1); await settle();
  ok("T2d 期间 move 仍被吞（无 POST）", posts.length === 0);
  await tick15(); await settle();
  ok("T2e 下一拍轮询放门", global.__ms() === false && global.__mp() === false);

  // ==== T3 放门后新 move 基于新表（交错正确性）====
  posts.length = 0;
  await mpl("A", 1); await settle();
  ok("T3 放门后 move 按新表解析：plCache [A,C,B,D] → from=0,to=1，"
     + "后端 [C,A,B,D]（无反向压回）",
     posts.length === 1 && posts[0].payload.from === 0
     && posts[0].payload.to === 1 && bk() === '["C","A","B","D"]',
     "post=" + JSON.stringify(posts.map(p => p.payload)) + " backend=" + bk());

  // ==== T4 放门前最后一瞬点击被吞（同步门检查）====
  backend.keys = ["B", "A", "C"]; global.__setCache(["B", "A", "C"]);
  getFail = true; posts.length = 0;
  await mpl("C", -1); await settle();        // 保门（后端 [B,C,A]... 实为换位）
  getFail = false; posts.length = 0;
  await mpl("A", 1);                         // 点击先于放门：同步门检查即吞
  const swallowed = posts.length === 0 && global.__mp() === true;
  await tick15(); await settle();            // 放门
  await mpl("C", -1); await settle();        // 放门后的点击
  ok("T4 放门前一瞬点击被吞；放门后点击基于放门时新表正常发",
     swallowed && posts.length === 1,
     "swallowed=" + swallowed + " posts=" + JSON.stringify(posts.map(
         p => p.payload)) + " backend=" + bk());

  // ==== T5 越界（他端删曲+陈旧 plCache）+ 网络通 ====
  backend.keys = ["A", "B"];                  // 他端已删 C/D
  global.__setCache(["A", "B", "C", "D"]);    // 本端陈旧
  posts.length = 0; toasts.length = 0;
  await mpl("D", -1); await settle();
  ok("T5a 越界 POST 被 400 拒 → toast「移动下标越界」",
     posts.length === 1 && toasts.some(t => t.includes("移动下标越界")),
     "toasts=" + JSON.stringify(toasts));
  ok("T5b 错误后仍走落地链：plCache 刷新为真值、门释放、无保门",
     pc() === '["A","B"]' && global.__mp() === false
     && global.__ms() === false && bk() === '["A","B"]',
     "plCache=" + pc() + " pend=" + global.__mp() + " stuck=" + global.__ms());

  // ==== T6 断网+越界双叠加（POST 网络失败，GET 也断）====
  backend.keys = ["A", "B"];
  global.__setCache(["A", "B", "C"]);
  getFail = true; postThrow = true; posts.length = 0; toasts.length = 0;
  await mpl("C", -1); await settle();
  ok("T6a 双 toast（网络异常+网络不稳保门），后端未动",
     toasts.length === 2 && toasts[0].includes("网络异常")
     && toasts[1].includes("网络不稳") && bk() === '["A","B"]'
     && global.__ms() === true,
     "toasts=" + JSON.stringify(toasts) + " backend=" + bk());
  postThrow = false; getFail = false;
  await tick15(); await settle();
  ok("T6b 网络恢复后轮询放门、plCache=真值", global.__ms() === false
     && pc() === '["A","B"]');

  // ==== T7 纯并发假卡死（seq 竞抢，非断网）====
  backend.keys = ["A", "B", "C"];
  global.__setCache(["A", "B", "C"]);
  posts.length = 0; toasts.length = 0; plOrder = true; plQueue.length = 0;
  const moveP = mpl("C", -1);                // POST ok → r1(seq1) 入队
  await settle();
  ok("T7a r1 在队（后端已 [A,C,B]）",
     plQueue.length === 1 && bk() === '["A","C","B"]',
     "queue=" + plQueue.length + " backend=" + bk());
  const addP = global.__plcmd2("add", "D");  // r2(seq2) 入队
  await settle();
  ok("T7b r2 在队（plcmd2 的裸拉）", plQueue.length === 2);
  plQueue[1].res(plSnapshot()); await settle();      // 先回 r2（seq 新→接受）
  plQueue[0].res(plSnapshot()); await settle();      // 后回 r1（seq 旧→丢弃）
  ok("T7c r1 被判旧丢弃后 movePl 已补发 r3", plQueue.length >= 3,
     "queue=" + plQueue.length);
  global.__refresh(); await settle();                // r4(seq4) 入队（模拟 nav/他因，发后不管）
  ok("T7d r4 在队", plQueue.length >= 4);
  plQueue[3].res(plSnapshot()); await settle();      // 先回 r4（接受）
  plQueue[2].res(plSnapshot()); await settle();      // 后回 r3（旧→丢弃）
  await moveP; await addP; await settle();
  ok("T7e 两拉皆被 seq 竞抢丢弃 → 谎报「网络不稳」并保门（网络其实全通）",
     global.__ms() === true && toasts.some(t => t.includes("网络不稳")),
     "stuck=" + global.__ms() + " toasts=" + JSON.stringify(toasts));
  ok("T7f plCache 本身是新鲜的（r4 已落地）——保门纯多余但无害",
     pc() === '["A","C","B","D"]', "plCache=" + pc());
  plOrder = false;
  await tick15(); await settle();
  ok("T7g 下一拍轮询自愈放门", global.__ms() === false);

  console.log(fails.length ? "\n== 有 FAIL ==" : "\n== 全部通过 ==");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
