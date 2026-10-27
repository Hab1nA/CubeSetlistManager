// attack2_nextgo.js —— 攻击面一（前端）：NEXT 按钮 key 与 index 必须同拍
// 二轮攻击实证旧版从 plCache（1.5s 拍）借键、index 用 /state（500ms 拍），
// 混排窗口切错歌；修复=同拍取 stState.songs[i].key。本脚本从 app.js 原文
// 抽取现行 next-go 处理器执行，断言修复行为（兼作回归）。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");

function extractHandler(marker) {
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

let wire = null;
global.fetch = async (_path, opt) => {
  wire = JSON.parse(opt.body);
  return {ok: true, json: async () => ({ok: true})};
};
global.toast = () => {};
global.$ = () => ({addEventListener() {}});

// 原文 cmd + switchTo 尾行同构
let i0 = SRC.indexOf("async function cmd(");
let b0 = SRC.indexOf("{", i0), d0 = 0;
for (let k = b0; k < SRC.length; k++) {
  if (SRC[k] === "{") d0++;
  else if (SRC[k] === "}") { if (--d0 === 0) { (0, eval)(SRC.slice(i0, k + 1)); break; } }
}
const cmd = global.cmd;
global.switchTo = (i, _b, _dbl, key) => cmd("switch", i, key);

// ---- 场景：他端在 plCache 落后窗口内把 D 挪到队首 ----
// 服务端真值（已重排）：[D,A,B,C]，cur=2（B 在放）→ NEXT 期望 C。
// /state 新拍：songs 带 key；/playlist 旧拍：仍 [A,B,C,D]。
const TRUTH = ["D", "A", "B", "C"];
global.stState = {cur: 2,
                  songs: TRUTH.map(k => ({key: k, name: k, dur: "1:00"}))};
global.stCur = 2;
global.plCache = {pl: ["A", "B", "C", "D"].map(k => ({key: k, name: k}))};

(0, eval)("(async()=>{" + extractHandler('$("#next-go")') + "})()");
setImmediate(() => {
  const want = TRUTH[3];
  const pass = wire && wire.key === want && wire.index === 3;
  console.log("期望报文 key=" + want + "；实际 " + JSON.stringify(wire));
  console.log(pass ? "PASS 同拍取键，不再借旧拍 plCache" : "FAIL 混拍借键复现");

  // ---- 对照：/state 无 key（跨版本旧后端）→ 退 plCache 借键（可接受
  //      残余：真后端恒带 key）----
  wire = null;
  global.stState = {cur: 2, songs: TRUTH.map(k => ({name: k}))};
  (0, eval)("(async()=>{" + extractHandler('$("#next-go")') + "})()");
  setImmediate(() => {
    console.log("对照（/state 无 key）: " + JSON.stringify(wire)
        + " → 退 plCache 借键（跨版本残余，真后端恒带 key）");
    process.exit(pass ? 0 : 1);
  });
});
