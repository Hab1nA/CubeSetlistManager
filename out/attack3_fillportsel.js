// attack3_fillportsel.js —— 三轮攻击面三（前端半）：fillPortSel 回修本身
// 二轮回修：选中身份=端口名+dev 二元组（keepDev），回落带 pos 消歧。
// 攻击点：
//   ① 枚举序变化后身份保留是否真按 (name,dev) 对（修复有效性对照）
//   ② portStatus 3s 快照陈旧 × 手选项掉线回落：pos 过期 → 错档选中 →
//     保存错绑（错档窗口实证；pos 新鲜时正确）
//   ③ 幽灵项身份（dev="ghost"）重建后重锚
// fillPortSel 从 app.js 原文抽取逐字执行。
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

// ---- 最小 DOM ----
function mkOption() {
  return {value: "", textContent: "", title: "", dataset: {}, className: "",
          selected: false};
}
function mkSelect(id) {
  const s = {id, dataset: {}, _children: [], _val: null, _explicit: false,
             get options() { return this._children; },
             get selectedIndex() {
               if (this._val !== null)
                 return this._children.findIndex(o => o.value === this._val);
               return this._children.length ? 0 : -1;   // 无显式值=首项（真 DOM）
             },
             set value(v) { this._val = String(v); },
             get value() {
               const i = this.selectedIndex;
               return i >= 0 ? this._children[i].value : "";
             },
             set innerHTML(v) { this._children = []; this._val = null; },
             appendChild(o) { this._children.push(o); }};
  return s;
}
const sels = {};
global.$ = id => sels[id] || (sels[id] = mkSelect(id));
global.document = {createElement: tag => mkOption()};

(0, eval)([
  cut("function fillPortSel(", "\nfunction portSelVal("),
  "global.__fill = fillPortSel;"
].join("\n"));
const fillPortSel = global.__fill;

global.kbData = {ports: {}, junoIn: "", junoInDev: 0};
const fails = [];
const ok = (name, cond, detail) => {
  console.log((cond ? "PASS " : "FAIL ") + name
      + (detail ? " | " + detail : ""));
  if (!cond) fails.push(name);
};
const selOf = () => sels["#kb-jin"];
const picked = () => {                 // 当前选中项 (name, dev)
  const s = selOf(), i = s.selectedIndex;
  return i < 0 ? null
      : {name: s.options[i].title, dev: s.options[i].dataset.dev,
         label: s.options[i].textContent};
};

// ---- ① 枚举序变化：身份按 (name,dev) 对保留（修复有效性） ----
global.kbData.ports.in = [
  {label: "USB-Midi · 第1个", name: "USB-Midi", dev: 0},
  {label: "USB-Midi · 第2个", name: "USB-Midi", dev: 1},
  {label: "JUNO-DS", name: "JUNO-DS", dev: 0}];
fillPortSel("kb-jin", "in", "USB-Midi", 2);   // 首建
selOf().value = "1";                          // 用户手选「USB-Midi · 第2个」
global.kbData.ports.in = [                     // winmm 枚举序变化（JUNO 抢先）
  {label: "JUNO-DS", name: "JUNO-DS", dev: 0},
  {label: "USB-Midi · 第1个", name: "USB-Midi", dev: 0},
  {label: "USB-Midi · 第2个", name: "USB-Midi", dev: 1}];
fillPortSel("kb-jin", "in", "USB-Midi", 2);
ok("① 重排后选中身份仍=USB-Midi/dev1（跟随二元组不跟随下标）",
   JSON.stringify(picked()) === JSON.stringify(
       {name: "USB-Midi", dev: "1", label: "USB-Midi · 第2个"}),
   "picked=" + JSON.stringify(picked()));

// ---- ② 手选项掉线回落 × pos 新鲜/陈旧 ----
// 三只同名 P，已存配置 P/dev2（第3只）。用户手选第3只后拔掉第1只：
// 剩余枚举=[P(原第2),P(原第3)]，手选的 dev2 条目消失 → 回落到已存配置。
function scene(pos) {
  global.kbData.ports.in = [
    {label: "P · 第1个", name: "P", dev: 0},
    {label: "P · 第2个", name: "P", dev: 1},
    {label: "P · 第3个", name: "P", dev: 2},
    {label: "Q", name: "Q", dev: 0}];
  fillPortSel("kb-jin", "in", "P", pos);
  selOf().value = "2";                        // 用户手选「P · 第3个」
  global.kbData.ports.in = [                   // 拔掉第1只+Q（枚举重排）
    {label: "P · 第1个", name: "P", dev: 0},
    {label: "P · 第2个", name: "P", dev: 1}];
  fillPortSel("kb-jin", "in", "P", pos);       // pos=portStatus 快照（可陈旧）
  return picked();
}
const fresh = scene(2);                        // 快照已刷新：clamp 后 pos=2
ok("②a pos 新鲜（=2）→ 回落选中 dev1（原第3只=现第2只，clamp 正确）",
   fresh && fresh.dev === "1", "picked=" + JSON.stringify(fresh));
const stale = scene(3);                        // 快照未刷（3s 缓存内）：pos=3
ok("②b pos 陈旧（=3）→ dev===2 找不到 → 回落同名第 1 只=错档",
   stale && stale.dev === "0" && stale.label === "P · 第1个",
   "picked=" + JSON.stringify(stale));
// 错档后果：用户此刻点保存 → pick() 落 {name:"P", dev:0}（原存 dev=2）
global.kbData.junoIn = "P"; global.kbData.junoInDev = 2;
const saved = (() => {                         // app.js pick() 同构（1974-1983）
  const sel = selOf(), v = sel.value, i = Number(v);
  if (v === "-1" || v === "" || isNaN(i))
    return {name: global.kbData.junoIn, dev: global.kbData.junoInDev || 0};
  const entries = global.kbData.ports[sel.dataset.list] || [];
  const e = entries[i];
  return e ? {name: e.name, dev: e.dev}
           : {name: global.kbData.junoIn, dev: global.kbData.junoInDev || 0};
})();
ok("②c 错档窗内保存 → 落盘 dev=0（错绑枚举位 0，丢原 dev=2）",
   JSON.stringify(saved) === JSON.stringify({name: "P", dev: 0}),
   "saved=" + JSON.stringify(saved));

// ---- ③ 幽灵项身份重建重锚 ----
global.kbData.ports.in = [];
fillPortSel("kb-jin", "in", "GHOST-1", 0);     // 已存名不在场 → 幽灵项
selOf().value = "-1";                          // portSelVal 会选中幽灵
global.kbData.ports.in = [{label: "JUNO-DS", name: "JUNO-DS", dev: 0}];
fillPortSel("kb-jin", "in", "GHOST-1", 0);     // 他口上电仍重建（幽灵仍在）
ok("③ 幽灵身份跨重建保留（keepDev=ghost 重锚）",
   picked() && picked().dev === "ghost"
   && picked().label === "GHOST-1 · 未接入",
   "picked=" + JSON.stringify(picked()));

console.log(fails.length ? "存在反例: " + fails.join(" ; ")
                         : "全部场景按推演落地");
process.exit(fails.length ? 1 : 0);
