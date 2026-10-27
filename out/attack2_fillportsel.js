// attack2_fillportsel.js —— 攻击面四：同名多口下 fillPortSel 的 keepName/回落错绑
// 从 webapp/app.js 原文抽取 fillPortSel / portSelVal（运行时 brace-match，非手抄），
// 配最小 DOM shim，验证三场景是否「静默错绑到第 1 个同名口」。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");

function extractFn(name) {
  const i = SRC.indexOf("function " + name + "(");
  if (i < 0) throw new Error(name + " not found");
  const j = SRC.indexOf("{", i);
  let depth = 0;
  for (let k = j; k < SRC.length; k++) {
    if (SRC[k] === "{") depth++;
    else if (SRC[k] === "}") {
      depth--;
      if (depth === 0) return SRC.slice(i, k + 1);
    }
  }
  throw new Error("unbalanced");
}

// ---- 最小 <select> shim（浏览器语义：重建后自动选第 1 项；value 不存在→-1）----
function mkSel() {
  return {
    _opts: [], dataset: {}, selectedIndex: -1,
    get options() { return this._opts; },
    set innerHTML(_) { this._opts = []; this.selectedIndex = -1; },
    appendChild(o) {
      this._opts.push(o);
      if (this.selectedIndex === -1) this.selectedIndex = this._opts.length - 1;
    },
    get value() { return this.selectedIndex >= 0
        ? this._opts[this.selectedIndex].value : ""; },
    set value(v) { this.selectedIndex =
        this._opts.findIndex(o => o.value === String(v)); },
  };
}

const sel = mkSel();
global.kbData = {};                      // 被抽取函数引用的全局
global.$ = () => sel;                    // 单 select 测试
global.document = { createElement: () =>
    ({value: "", textContent: "", title: "", dataset: {}}) };
const kbData = global.kbData;

(0, eval)(extractFn("fillPortSel"));     // 间接 eval → 全局作用域
(0, eval)(extractFn("portSelVal"));
const fillPortSel = global.fillPortSel, portSelVal = global.portSelVal;

// kb-save 的 pick（app.js:1950-1959 原文照抄，仅换上下文变量名绑定）
function savePick(hint, dev) {
  const i = Number(sel.value);
  if (sel.value === "-1" || sel.value === "" || isNaN(i))
    return {name: hint, dev: dev || 0};
  const entries = ((kbData.ports || {})[sel.dataset.list] || []);
  const e = entries[i];
  return e ? {name: e.name, dev: e.dev} : {name: hint, dev: dev || 0};
}
const entry = (label, name, dev) => ({label, name, dev});
const U1 = entry("USB-Midi · 第1个", "USB-Midi", 0);
const U2 = entry("USB-Midi · 第2个", "USB-Midi", 1);

function show(tag, expect) {
  const e = sel.options[sel.selectedIndex];
  const got = {label: e.textContent, name: e.title, dev: e.value};
  const wrong = got.label !== expect.label;
  console.log((wrong ? "  [错绑]" : "  [正常]") + " " + tag
      + " → 选中「" + got.label + "」(dev=" + got.dev + ")"
      + (wrong ? "，期望「" + expect.label + "」" : ""));
  return wrong;
}

let bad = 0;

// ---------- 场景 A：手选第 2 口 → 任意外部口拓扑变化触发重建 → 静默落回第 1 口 ----------
console.log("场景A：同名双口在线，用户手选「第2个」，第三方端口(loopMIDI)上线触发重建");
sel._opts = []; sel.dataset = {}; sel.selectedIndex = -1;
kbData.ports = {in: [U1, U2]};
fillPortSel("kb-jin", "in", "USB-Midi");          // 首建（saved hint 在线）
sel.value = "1";                                   // 用户手选 USB-Midi · 第2个
console.log("  用户手选后: " + sel.options[sel.selectedIndex].textContent);
kbData.ports = {in: [entry("loopMIDI", "loopMIDI", 0), U1, U2]};  // 拓扑变化
fillPortSel("kb-jin", "in", "USB-Midi");          // sig 变 → 重建，keepName=「USB-Midi」
bad += show("重建后", {label: "USB-Midi · 第2个"});
const saved = savePick("USB-Midi", 1);
console.log("  点「应用」落盘: {name:" + saved.name + ", dev:" + saved.dev + "}"
    + (saved.dev === 0 ? "   ← 用户选的是第2口(dev=1)，保存成第1口(dev=0)！错绑成立" : ""));
if (saved.dev === 0) bad += 1;

// 对照组：唯一名端口同样流程不丢
console.log("对照组：唯一名口（FP-80）同样手选+重建");
sel._opts = []; sel.dataset = {}; sel.selectedIndex = -1;
const F1 = entry("FP-80", "FP-80", 0), F2 = entry("FP-90", "FP-90", 0);
kbData.ports = {in: [F1, F2]};
fillPortSel("kb-jin", "in", "FP-80");
sel.value = "1";                                   // 手选 FP-90
kbData.ports = {in: [entry("loopMIDI", "loopMIDI", 0), F1, F2]};
fillPortSel("kb-jin", "in", "FP-80");
bad += show("重建后", {label: "FP-90"}) ? 0 : 1 - 1;   // 唯一名应保留
show("重建后", {label: "FP-90"});

// ---------- 场景 B：手选的口掉线 → 回落须带 pos 消歧（与 portSelVal 同口径） ----------
console.log("场景B：已存配置=第2口(dev=1)；用户手选 FP-80 后 FP-80 掉线触发回落");
sel._opts = []; sel.dataset = {}; sel.selectedIndex = -1;
kbData.ports = {in: [U1, U2, entry("FP-80", "FP-80", 0)]};
fillPortSel("kb-jin", "in", "USB-Midi", 2);          // hint=已存名，pos=2
sel.value = "2";                                    // 用户手选 FP-80
kbData.ports = {in: [U1, U2]};                      // FP-80 拔掉
fillPortSel("kb-jin", "in", "USB-Midi", 2);         // keepName 未命中 → 回落
bad += show("回落", {label: "USB-Midi · 第2个"});   // 注释宣称回落到「已存配置对应项」
const savedB = savePick("USB-Midi", 1);
console.log("  点「应用」落盘: dev=" + savedB.dev
    + (savedB.dev === 0 ? "   ← 已存 dev=1 被静默改绑 dev=0！" : ""));
if (savedB.dev === 0) bad += 1;

// 对照：force 路径 portSelVal 有 pos 消歧，会正确选第 2 口
console.log("对照组：force 路径 portSelVal(id, hint, pos=2) 有 pos 消歧");
sel.dataset.list = "in";
sel._opts = []; sel.dataset = {}; sel.selectedIndex = -1;
kbData.ports = {in: [U1, U2]};
fillPortSel("kb-jin", "in", "USB-Midi");
portSelVal("kb-jin", "USB-Midi", 2);
show("portSelVal pos=2", {label: "USB-Midi · 第2个"});

// ---------- 场景 C：幽灵项保名 → 双口上电 → 幽灵身份不得吸回第 1 口 ----------
console.log("场景C：已存 dev=1；双口曾全掉线（幽灵项），用户停在幽灵项，双口重新上电");
sel._opts = []; sel.dataset = {}; sel.selectedIndex = -1;
kbData.ports = {in: []};
fillPortSel("kb-jin", "in", "USB-Midi", 2);         // 幽灵项「USB-Midi · 未接入」
console.log("  当前选中: " + sel.options[sel.selectedIndex].textContent);
kbData.ports = {in: [U1, U2]};                      // 双口上电
fillPortSel("kb-jin", "in", "USB-Midi", 2);         // 幽灵消失 → pos 消歧回落
bad += show("上电重建", {label: "USB-Midi · 第2个"});

console.log(bad > 0
    ? "\n推翻成立：fillPortSel 在同名多口下共 " + bad + " 处静默错绑/误回落（保存即错绑）"
    : "\n未能推翻：回落逻辑在同名多口下保持正确");
