// attack3_nextgo.js —— 三轮攻击面一：next-go 回修本身的正确性
// 二轮回修把 key 改成 stState.songs[i]，注释声称「key 必须与 index 同拍
// 取自 /state」。但 i = stCur+1，而 stCur 有两个更新点：poll() 的
// stState.cur（/state 500ms 拍）与 refreshPlaylist() 的 d.cur（/playlist
// 1.5s 拍）。/playlist 拍覆盖 stCur 后，i 与 stState.songs 不再同拍，
// #next 卡显示文本（render 用 stState.cur）与 i（用 stCur）也不同拍。
// 本脚本从 app.js 原文抽取处理器逐字执行，验证三个反例。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");

function extractFn(marker) {          // 抽整个函数体（含声明）
  const i = SRC.indexOf(marker);
  if (i < 0) throw new Error("marker not found: " + marker);
  const b = SRC.indexOf("{", i);
  let d = 0;
  for (let k = b; k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}") { if (--d === 0) return SRC.slice(i, k + 1); }
  }
  throw new Error("unbalanced");
}
function extractHandler(marker) {     // 抽事件处理器箭头函数体
  const i = SRC.indexOf(marker);
  if (i < 0) throw new Error("marker not found: " + marker);
  const j = SRC.indexOf("=>", i);
  const b = SRC.indexOf("{", j);
  let d = 0;
  for (let k = b; k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}") { if (--d === 0) return SRC.slice(b + 1, k); }
  }
  throw new Error("unbalanced");
}

let wire = null;                      // /cmd 报文
let backendErr = null;
global.fetch = async (_p, opt) => {
  wire = JSON.parse(opt.body);
  return {ok: true, json: async () => ({ok: true})};
};
global.toast = () => {};
global.$ = () => ({addEventListener() {}});   // 处理器尾行传按钮用

// 后端 _h_cmd+_switch 同构：key 优先按执行时刻 pl_keys 反查；否则验 index
function backendSwitch(body, backend) {
  if (typeof body.key === "string" && body.key) {
    if (!backend.pl_keys.includes(body.key))
      return {err: "该曲目已不在播放列表"};
    backend.cur = backend.pl_keys.indexOf(body.key);
    return {switched: backend.pl_keys[backend.cur]};
  }
  if (!(Number.isInteger(body.index)
        && body.index >= 0 && body.index < backend.pl_keys.length))
    return {err: "index 越界"};
  backend.cur = body.index;
  return {switched: backend.pl_keys[body.index]};
}

// 前端 switchTo 原文（confirm 关闭时直发）+ cmd 原文
(0, eval)(extractFn("async function cmd("));
global.cmd = cmd;
(0, eval)(extractFn("function switchTo("));
global.swLastFire = 0;
global.armConfirm = () => {};

// #next 卡显示逻辑（render() 原文行）：文本与 disabled 用 stState 拍
function nextCardText() {
  const rows = stState.songs || [];
  const cur = typeof stState.cur === "number" ? stState.cur : -1;
  const nx = rows[cur + 1];
  return {text: nx ? nx.name : (rows.length ? "末尾" : "—"),
          enabled: !!nx};
}

const song = k => ({key: k, name: k, dur: "1:00"});
let backend, fails = [];

function runClick(label, expect) {
  wire = null; backendErr = null;
  global.swLastFire = 0;              // 跨场景重置 switchTo 单发去重窗
  (0, eval)("(async()=>{" + extractHandler('$("#next-go")') + "})()");
  const card = nextCardText();
  const r = wire ? backendSwitch(wire, backend) : {err: "未发包"};
  const out = {card: card.text, enabled: card.enabled,
               wire: wire ? {index: wire.index, key: wire.key} : null,
               result: r};
  console.log("[" + label + "] " + JSON.stringify(out));
  expect(out);
}

// ---- 场景 A（核心反例）：他端/自动推进把 cur 2→3，/playlist 拍先落地 ----
// stState（/state 旧拍）cur=2；stCur/plCache（/playlist 新拍）cur=3。
// 卡面显示 D，点击实际切 E：显示与目标错一首。
backend = {pl_keys: ["A", "B", "C", "D", "E", "F"], cur: 3};
global.stState = {cur: 2,
                  songs: ["A", "B", "C", "D", "E", "F"].map(song)};
global.stCur = 3;                     // ← refreshPlaylist 的 d.cur 覆盖
global.plCache = {pl: ["A", "B", "C", "D", "E", "F"].map(song), cur: 3};
runClick("A 混拍 off-by-one", o => {
  fails.push(o.card === "D" && o.result.switched === "E"
      && o.wire.index === 4 && o.wire.key === "E"
      ? null : "A: 未复现（卡面=" + o.card + " 实切=" + o.result.switched + "）");
});

// ---- 场景 B：末尾 i 越界（stCur 新拍=末曲，stState 旧拍=次末）----
// 卡面显示末曲 F 且按钮可用；i=len → key=undefined → 后端 400。
backend = {pl_keys: ["A", "B", "C", "D", "E", "F"], cur: 5};
global.stState = {cur: 4,
                  songs: ["A", "B", "C", "D", "E", "F"].map(song)};
global.stCur = 5;
global.plCache = {pl: ["A", "B", "C", "D", "E", "F"].map(song), cur: 5};
runClick("B 末尾越界", o => {
  fails.push(o.card === "F" && o.enabled
      && o.wire && o.wire.key === undefined && o.wire.index === 6
      && o.result.err === "index 越界"
      ? null : "B: 未按预期（" + JSON.stringify(o) + "）");
});

// ---- 场景 C：回退分支（/state 无 key 的跨版本）借 plCache 旧拍键 ----
// 真值已被他端重排 [D,A,B,C]（cur=2），plCache 停在旧序 [A,B,C,D]。
// 期望下一首=truth[3]=C；回退借到旧拍 pl[3]=D → 切错曲。
// 对照砍掉回退（key=undefined 走 index 路径）→ 后端按现表切 C=正确。
backend = {pl_keys: ["D", "A", "B", "C"], cur: 2};
global.stState = {cur: 2,
                  songs: ["D", "A", "B", "C"].map(k => ({name: k}))}; // 无 key
global.stCur = 2;                     // poll 拍（与 stState 同拍）
global.plCache = {pl: ["A", "B", "C", "D"].map(song), cur: 2};  // 旧序
runClick("C 回退借旧拍键（跨版本）", o => {
  fails.push(o.wire && o.wire.key === "D" && o.result.switched === "D"
      ? null : "C: 未复现借键错切（" + JSON.stringify(o) + "）");
});
// 同场景砍回退对照：index=3 落在真值表上 → C
{
  const body = {action: "switch", index: 3};
  const r = backendSwitch(body, backend);
  console.log("[C-对照 砍回退走 index] " + JSON.stringify(r));
  fails.push(r.switched === "C" ? null : "C-对照: index 路径应切 C");
}

// ---- 场景 D（对照，回修有效的同拍情形）：stCur 未被 /playlist 覆盖 ----
backend = {pl_keys: ["A", "B", "C", "D"], cur: 2};
global.stState = {cur: 2, songs: ["A", "B", "C", "D"].map(song)};
global.stCur = 2;
global.plCache = {pl: ["D", "A", "B", "C"].map(song), cur: 2};  // 旧序
runClick("D 同拍对照（修复有效）", o => {
  fails.push(o.card === "D" && o.result.switched === "D"
      ? null : "D: 同拍对照失败（" + JSON.stringify(o) + "）");
});

fails = fails.filter(Boolean);
console.log(fails.length
    ? "结论：存在反例 " + JSON.stringify(fails)
    : "结论：全部场景按推演落地");
process.exit(fails.length ? 1 : 0);
