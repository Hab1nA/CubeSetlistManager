// attack3_movepend.js —— 三轮攻击面二：movePl 重拉失败二次放门的正确性
// 二轮回修：movePend 统一在途门 + `if (!await refreshPlaylist())
// await refreshPlaylist()` 两连拉，finally 放门。攻击点：
//   A 两拉都失败（POST 成功但 /playlist 断）→ 放门后陈旧解析（移错行/
//     反向压回/静默谎报——后端 move 越界 return None 仍 200 ok）
//   B 1.5s 轮询拍抢 seq：movePl 内拉被「旧响应」判定丢弃→再拉，最坏几次、
//     是否活锁（结构上限 2 次，非循环——实证）
//   C 连点三次 ↑（每次等门放）第三拍是否新鲜（健康路径对照）
//   D 重拉期间 dragKey 恢复→drawPl 拖拽守卫是否被重拉放大（拖拽中不重绘、
//     dragend 后一拍内旧行残留）；门内第二击被静默吞
// 全部函数从 app.js 原文抽取逐字执行。
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

// ---- 原文抽取：movePend 声明 + movePl + movePlKey + refreshPlaylist + drawPl ----
const CODE = [
  "let movePend = false, dragKey = null;",
  "let selRowKey = null, slQ = \"\";",          // drawPl 引用的同域状态
  "const ptBaseOf = k => k.slice(3, k.lastIndexOf(\"#\"));",
  cut("const durCls =", "\nfunction libRow("),   // plRow 需要的时长着色
  cut("function plRow(", "\nlet movePend"),   // drawPl 重建行时需要
  cut("async function movePl(", "\nfunction movePlKey("),
  cut("function movePlKey(", "\nfunction drawPl("),
  cut("async function refreshPlaylist(", "\nasync function plcmd2("),
  cut("function drawPl(", "\nfunction markPlSel("),
  "global.__mp = () => movePend; global.__drag = () => dragKey;",
  "global.__setDrag = v => { dragKey = v; };",
  "global.__movePl = movePl; global.__movePlKey = movePlKey;",
  "global.__refresh = refreshPlaylist; global.__drawPl = drawPl;"
].join("\n");

// ---- 最小 DOM 桩（drawPl/plRow 需要的形状）----
function fakeEl() {
  return {dataset: {}, style: {}, classList: {add() {}, remove() {},
      toggle() {}}, children: [], hidden: false, draggable: false,
      title: "", value: "",
    addEventListener() {}, appendChild(c) { this.children.push(c); },
    append(...cs) { this.children.push(...cs); },
    remove() {}, querySelector: () => null, focus() {}};
}
const els = {};
global.$ = s => els[s] || (els[s] = fakeEl());
global.document = {createElement: () => fakeEl()};
global.$$ = () => [];
let plResets = 0;                    // drawPl 实际重建 DOM 的次数
els["#pl"] = fakeEl();
Object.defineProperty(els["#pl"], "innerHTML", {
  get() { return this._h || ""; },
  set(v) { this._h = v; if (v === "") plResets++; }
});
els["#pl-n"] = fakeEl(); els["#pl-empty"] = fakeEl(); els["#lib"] = fakeEl();
els["#lib-empty"] = fakeEl(); els["#lib-n"] = fakeEl();
Object.defineProperty(els["#lib"], "innerHTML", {
  get() { return this._h || ""; }, set(v) { this._h = v; }});

global.popEl = null; global.popAnchor = null; global.closePop = () => {};
global.drawLib = () => {};
const toasts = [];
global.toast = (m) => toasts.push(m);

// ---- 后端真值模型（setlist_gui._pl_cmd_web move 段逐字同构）----
const backend = {pl_keys: [], cur: 0};
function beMove(frm, to) {
  if (!Number.isInteger(frm) || !Number.isInteger(to)) return "move 参数非法";
  if (!(frm >= 0 && frm < backend.pl_keys.length)
      || !(to >= 0 && to < backend.pl_keys.length))
    return null;                      // 越界静默 None → HTTP 200 ok
  const item = backend.pl_keys.splice(frm, 1)[0];
  backend.pl_keys.splice(to, 0, item);
  if (backend.cur != null) {
    if (backend.cur === frm) backend.cur = to;
    else if (frm < backend.cur && backend.cur <= to) backend.cur -= 1;
    else if (to <= backend.cur && backend.cur < frm) backend.cur += 1;
  }
  return null;
}

// ---- 网络桩：POST /plcmd 立即达；GET /playlist 可控（失败/时序）----
let plFail = false;                   // true=GET 全部 reject
let plGets = 0;
let plTag = "interval";               // fetch 发起时刻的调用方标记
let plOrder = false;                  // true=入队受控时序
let plQueue = [];
const snap = () => ({ok: true, json: async () => ({lib: [],
    pl: backend.pl_keys.map(k => ({key: k, name: k})), cur: backend.cur})});
global.fetch = async path => {
  if (String(path).indexOf("/playlist") < 0) throw new Error("unexpected " + path);
  plGets++;
  const tag = plTag;
  if (plOrder)
    return new Promise((res, rej) => plQueue.push({tag, res, rej}));
  if (plFail) throw new Error("network down");
  return snap();
};
global.post = async (_p, body) => {   // POST 恒达
  if (body.op === "move") beMove(body.payload.from, body.payload.to);
  return {ok: true};
};

(0, eval)(CODE);
const movePl = global.__movePl, movePlKey = global.__movePlKey;
const refresh = global.__refresh, drawPl = global.__drawPl;

function setPlCache(keys) {
  global.plCache = {lib: [], pl: keys.map(k => ({key: k, name: k})),
                    cur: backend.cur};
}
const tick = () => new Promise(r => setImmediate(r));
async function waitGate() {           // movePlKey 是发后不管：等门落地
  let n = 0;
  while (global.__mp() && n++ < 500) await tick();
  await tick();
}
const fails = [];
const ok = (name, cond, detail) => {
  console.log((cond ? "PASS " : "FAIL ") + name
      + (detail ? " | " + detail : ""));
  if (!cond) fails.push(name);
};
const pc = () => JSON.stringify(global.plCache.pl.map(x => x.key));
const bk = () => JSON.stringify(backend.pl_keys);

(async () => {
  // ========= 场景 A：两拉全失败 → 放门后陈旧解析（三变体） =========
  // A-基础：POST 达、/playlist 全断（不对称断网）→ 门放+缓存陈旧，
  //         再点 ↑ 同曲 → 陈旧 from 把第一击打回（反向压回复活）
  backend.pl_keys = ["A", "B", "C"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]);
  plFail = true; toasts.length = 0;
  await movePlKey("C", -1); await waitGate();
  ok("A1 第一击 POST 成功（后端 [A,C,B]）+ 两拉全败后门已放",
     bk() === '["A","C","B"]' && global.__mp() === false,
     "backend=" + bk() + " movePend=" + global.__mp());
  ok("A2 plCache 陈旧 [A,B,C]（放门≠缓存新鲜）", pc() === '["A","B","C"]',
     "plCache=" + pc());
  await movePlKey("C", -1); await waitGate();   // 用户等门放后再点 ↑C
  ok("A3 第二击按陈旧表 from=2,to=1 → 反向压回 [A,B,C]",
     bk() === '["A","B","C"]', "backend=" + bk());

  // A-移错行：断网窗口内他端重排，陈旧 from 移到别的行
  backend.pl_keys = ["A", "C", "B"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]);        // 陈旧（两拉失败所留）
  backend.pl_keys = ["C", "A", "B"];  // 他端把 C 挪到队首
  plFail = false;
  await movePlKey("C", -1); await waitGate();
  ok("A4 陈旧解析移错行（动了 B 而非 C → [C,B,A]）",
     bk() === '["C","B","A"]', "backend=" + bk());

  // A-静默谎报：断网窗口内他端删曲，陈旧 from 越界 → 后端静默 None+200ok
  backend.pl_keys = ["A", "C", "B"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]);
  backend.pl_keys = ["C", "B"];       // 他端移除 A
  toasts.length = 0;
  await movePlKey("C", -1); await waitGate();
  ok("A5 越界 move：后端不动表却回 ok，前端 toast 谎报「已移动」",
     bk() === '["C","B"]' && toasts.includes("已移动《C》"),
     "backend=" + bk() + " toasts=" + JSON.stringify(toasts));

  // ========= 场景 B：1.5s 轮询拍抢 seq → 最多几拉？活锁？ =========
  backend.pl_keys = ["A", "B", "C"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]); plFail = false;
  plOrder = true; plQueue = [];
  const gets0 = plGets;
  plTag = "move";
  const p = movePl(2, 1);             // POST 达 → 后端 [A,C,B]
  await tick();                       // move#1 fetch 入队
  plTag = "interval"; refresh();      // 轮询拍插入（seq 更高）
  await tick();
  ok("B1 时序受控（move#1 与 interval 两拉在队）", plQueue.length === 2
     && plQueue[0].tag === "move" && plQueue[1].tag === "interval",
     "queue=" + JSON.stringify(plQueue.map(x => x.tag)));
  plQueue[1].res(snap());             // interval 先落地（plCache 更新，true）
  await tick();
  plTag = "move";                     // movePl 第二连拉的 fetch 即将发起
  plQueue[0].res(snap());             // move#1 后到：seq 旧 → 判 false
  await tick();                       // movePl 触发第二连拉
  ok("B2 move#1 被抢 seq 判 false → 自动第二连拉",
     plQueue.length === 3 && plQueue[2].tag === "move",
     "queue=" + JSON.stringify(plQueue.map(x => x.tag)));
  plTag = "interval"; refresh();      // 最坏：第二连拉又被轮询拍抢 seq
  await tick();
  plQueue[3].res(snap());             // interval#2 落地
  await tick();
  plQueue[2].res(snap());             // move#2 后到 → 又 false
  await p;                            // 门放（结构上限 2 次内拉，无第三次）
  ok("B3 最坏双抢 seq：内拉恰 2 次后放门（无活锁），plCache 已新鲜",
     global.__mp() === false && pc() === '["A","C","B"]'
     && plGets - gets0 === 4,         // move#1+interval#1+move#2+interval#2
     "gets=" + (plGets - gets0) + " plCache=" + pc());
  plOrder = false;

  // ========= 场景 C：连点三次 ↑（每次等门放）健康路径对照 =========
  backend.pl_keys = ["A", "B", "C"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]); plFail = false; plOrder = false;
  await movePlKey("C", -1); await waitGate();   // ① [A,C,B]
  await movePlKey("C", -1); await waitGate();   // ② 新鲜 from=1 → [C,A,B]
  await movePlKey("C", -1); await waitGate();   // ③ from=0,to=-1 入口早退
  ok("C1 三连 ↑ 净效果 [C,A,B]（第三次早退），缓存每击后新鲜",
     bk() === '["C","A","B"]' && pc() === '["C","A","B"]',
     "backend=" + bk() + " plCache=" + pc());

  // ========= 场景 D：重拉期间拖拽交错 + 门内第二击 =========
  backend.pl_keys = ["A", "B", "C"]; backend.cur = 0;
  setPlCache(["A", "B", "C"]); plFail = false; plOrder = true; plQueue = [];
  plResets = 0;
  global.__setDrag("C");              // 用户开始拖 C
  plTag = "move";
  const pd = movePl(2, 1);            // drop：入口 dragKey=null 恢复轮询重绘
  await tick();
  ok("D1 movePl 入口即恢复 dragKey=null", global.__drag() === null);
  global.__setDrag("B");              // 重拉在途：用户又开始拖 B（旧行上）
  plOrder = false;
  plQueue[0].res(snap());             // 新拍落地 → refresh 内调 drawPl
  await pd;
  ok("D2 新拍落地但 dragKey 在拖 → drawPl 守卫生效不重建 DOM",
     plResets === 0, "drawPl DOM rebuilds=" + plResets);
  global.__setDrag(null);             // dragend：dragKey 清但无人调 drawPl
  ok("D3 dragend 后旧行残留（无即时重绘，等下一 1.5s 拍）",
     plResets === 0);
  drawPl();                           // 下一轮询拍
  ok("D4 下一拍 drawPl 以新 plCache 重建（守卫只推迟不丢失）",
     plResets === 1, "rebuilds=" + plResets);

  backend.pl_keys = ["A", "B", "C"]; setPlCache(["A", "B", "C"]);
  plOrder = true; plQueue = []; plTag = "move";
  const p1 = movePl(2, 1);
  await tick();                       // p1 的 move#1 已入队、门未放
  await movePl(0, 1);                 // 门内第二击：应直接 return
  ok("D5 门内第二击被吞（无第二次 POST、无提示）",
     global.__mp() === true && bk() === '["A","C","B"]',
     "backend=" + bk() + " movePend=" + global.__mp());
  plOrder = false;
  plQueue[0].res(snap());
  await p1;

  console.log(fails.length ? "存在反例: " + fails.join(" ; ")
                           : "全部场景按推演落地");
  process.exit(fails.length ? 1 : 0);
})();
