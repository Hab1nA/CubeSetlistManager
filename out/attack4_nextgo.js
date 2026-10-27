// attack4_nextgo.js —— 四轮攻击面一：next-go 单一来源边界的混拍复活
// 攻击点：
//   S1 boot 期混拍：stState=null（/state 未回）时 next-go 回退 stCur（/playlist
//      拍）→ i 用 /playlist、s=null → 裸 index 发包；#next-go 初始 HTML 无
//      disabled（index.html:66），render 只在 poll 成功后跑（app.js:559 唯一
//      调用点）→ boot 窗口内按钮可点。
//   S2 稳态混拍（null→number）：卡面按 render 的 cur=-1 显示「第一首」，
//      点击按 stCur（/playlist 新拍，快照未追上）算 i=stCur+1 → 卡面显 A
//      实切 E。
//   S3 稳态混拍（number→null + 迟到 /playlist）：后端 cur 变 null（游离歌
//      路径 setlist_gui.py:3337），/playlist 旧响应（cur=5）晚于 null snap
//      落地且仍为最新 seq → stCur=5，卡面显示第一首，点击实切第 7 首。
//   S4 confirm 闭包：武装后 stState.cur 变化，第二击执行武装时捕获的
//      fire 闭包（武装时 key + 当前 plCache 重解析）→ 切武装目标（非新
//      next）；fire 不复核 i===cur。
//   S5 render 的 null 归一（typeof st.cur==="number"?st.cur:-1）核对：
//      不存在 rows[null+1]=rows[1]。
// 全部函数/关键行从 webapp/app.js 原文逐字抽取执行。
"use strict";
const fs = require("fs");
const SRC = fs.readFileSync(__dirname + "/../webapp/app.js", "utf8");
const HTML = fs.readFileSync(__dirname + "/../webapp/index.html", "utf8");

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
  cut("let swLastFire = 0;", "\nconst fmt ="),          // switchTo 全文
  cut("function armConfirm(", "\n// ---- popover"),      // armConfirm 全文
  cut("async function cmd(", "\nasync function post("),  // cmd 全文
  cut("async function post(", "\n// ---- 走带"),         // post 全文
  "let pollFails = 0;",
  cut("async function poll()", "\n// ================= 编排页"), // poll 全文
  cut("async function refreshPlaylist()", "\nasync function plcmd2("),
  // next-go 监听器体逐字（把 addEventListener 外壳换成可调函数）
  "global.__nextGo = () => {\n  "
    + cut("const cur = stState", "switchTo(i, $(\"#next-go\"), false, s ? s.key : undefined);")
    + "switchTo(i, $(\"#next-go\"), false, s ? s.key : undefined);\n};",
  // render 的 NEXT 卡段逐字（外壳换函数；行内容原样）
  "global.__card = st => {\n  "
    + cut("const rows = st.songs || [];", "$(\"#next-go\").disabled")
    + "$(\"#next-go\").disabled = !nx || !!st.busy;\n"
    + "  return {nextName: nx ? nx.name : (rows.length ? \"末尾\" : \"—\"),"
    + " btnDisabled: !nx || !!st.busy};\n};",
  "global.__st = () => stState; global.__stCur = () => stCur;"
  + " global.__pc = () => plCache;"
  + " global.__resetFire = () => { swLastFire = 0; };"
  + " global.__set = (st, pc, sc) => { stState = st; plCache = pc; stCur = sc; };"
].join("\n");

// ---- 最小 DOM/网络桩（refreshPlaylist/poll 引用的外围符号）----
function fakeEl() {
  return {dataset: {}, classList: {add() {}, remove() {}, toggle() {}},
          disabled: false, textContent: "", hidden: false, isConnected: true,
          addEventListener() {}, removeEventListener() {}, focus() {},
          appendChild() {}, append() {}, remove() {}, style: {}};
}
const els = {};
global.$ = s => els[s] || (els[s] = fakeEl());
els["#next"] = fakeEl(); els["#next-go"] = fakeEl(); els["#tstate"] = fakeEl();
global.window = {};
global.render = () => {};          // poll 内调用（display 语义另用 __card 验证）
global.drawLib = () => {}; global.drawPl = () => {};
global.popEl = null; global.popAnchor = null; global.closePop = () => {};
const toasts = [], sent = [];
global.toast = (m) => toasts.push(m);

// fetch 桩：/cmd POST 体记录进 sent；其余按场景分派
function recordFetch(inner) {
  return async (path, opts) => {
    if (String(path).includes("/cmd") && opts && opts.body)
      sent.push(JSON.parse(opts.body));
    return inner(path, opts);
  };
}

(0, eval)(CODE);
const resetFire = () => global.__resetFire();
const resetState = () => global.__set(null, null, null);

const SONGS = "ABCDEFGHIJ".split("").map(k => ({key: k, name: "歌" + k}));
const snapOf = (cur, songs) => ({cur, songs: songs || SONGS,
                                 confirm: false, open: true, busy: false});

const fails = [];
const ok = (name, cond, detail) => {
  console.log((cond ? "PASS " : "FAIL ") + name
      + (detail ? " | " + detail : ""));
  if (!cond) fails.push(name);
};

(async () => {
  // ==== 事实核对：#next-go 初始 HTML 是否 disabled / render 调用点 ====
  const nextBtnHtml = HTML.split("\n").filter(l => l.includes("next-go"))[0];
  ok("F1 index.html #next-go 初始无 disabled（boot 期可点）",
     /<button id="next-go"/.test(nextBtnHtml) && !/disabled/.test(nextBtnHtml),
     nextBtnHtml.trim());
  const renderCalls = SRC.split("\n").filter(l =>
      /[^.\w]render\(/.test(l) && !/function render\(/.test(l));
  ok("F2 render 仅 poll 成功后调用（app.js 唯一调用点 559）",
     renderCalls.length === 1 && renderCalls[0].includes("render(stState)"),
     renderCalls.map(l => l.trim()).join(" ; "));

  // ==== S1 boot 混拍：/playlist 先落地、/state 未回 ====
  global.fetch = recordFetch(async (path) => {
    if (String(path).includes("/state"))
      return new Promise(() => {});                    // 永不回（boot 窗口）
    if (String(path).includes("/playlist"))
      return {ok: true, json: async () =>
              ({lib: [], pl: SONGS.map(s => ({key: s.key})), cur: 5})};
    return {ok: true, json: async () => ({})};
  });
  await (0, eval)("refreshPlaylist()");
  ok("S1a boot 期 stState=null 且 stCur=5（/playlist 已落地）",
     global.__st() === null && global.__stCur() === 5,
     "stState=" + global.__st() + " stCur=" + global.__stCur());
  sent.length = 0; resetFire();
  global.__nextGo();
  await new Promise(r => setTimeout(r, 20));
  ok("S1b 点击发出裸 index switch（无 key）：index=stCur+1=6",
     sent.length === 1 && sent[0].index === 6 && sent[0].key === undefined,
     JSON.stringify(sent));
  ok("S1c 卡面此时尚未渲染（HTML 初值空，无显示/行动矛盾）",
     els["#next"].textContent === "");

  // ==== S2 稳态混拍（null→number：试音首切） ====
  resetState();
  global.fetch = recordFetch(async (path) =>
    String(path).includes("/state")
      ? {ok: true, json: async () => snapOf(null)}     // snap 落后：cur 仍 null
      : {ok: true, json: async () => ({})});
  await (0, eval)("poll()");
  const card1 = global.__card(global.__st());
  ok("S2a poll#1 后卡面 NEXT=第一首 A（cur=null 归一 -1）且按钮可用",
     card1.nextName === "歌A" && !card1.btnDisabled, JSON.stringify(card1));
  // 后端已切到 index=3；/playlist 新拍落地（snap 尚未追上）
  global.fetch = recordFetch(async (path) =>
    String(path).includes("/playlist")
      ? {ok: true, json: async () =>
         ({lib: [], pl: SONGS.map(s => ({key: s.key})), cur: 3})}
      : {ok: true, json: async () => snapOf(null)});
  await (0, eval)("refreshPlaylist()");
  ok("S2b /playlist 落地后 stCur=3 而 stState.cur=null（混拍成立）",
     global.__stCur() === 3 && global.__st().cur === null);
  sent.length = 0; resetFire();
  global.__nextGo();
  await new Promise(r => setTimeout(r, 20));
  ok("S2c 点击实切 index=4 key=E：卡面显「歌A」实切「歌E」",
     sent.length === 1 && sent[0].index === 4 && sent[0].key === "E",
     "sent=" + JSON.stringify(sent) + " card=" + card1.nextName);
  global.fetch = recordFetch(async () =>
    ({ok: true, json: async () => snapOf(3)}));
  await (0, eval)("poll()");
  ok("S2d 下一拍 poll 即归位（stCur=stState.cur=3，窗口≤1 个 poll 周期）",
     global.__stCur() === 3 && global.__st().cur === 3);

  // ==== S3 稳态混拍（number→null + 迟到 /playlist） ====
  resetState();
  global.fetch = recordFetch(async () =>
    ({ok: true, json: async () => snapOf(5)}));
  await (0, eval)("poll()");
  ok("S3a 卡面 NEXT=歌G（cur=5 → rows[6]）", global.__card(global.__st()).nextName === "歌G");
  // 游离歌路径：cur→null（列表不动）；poll#2 快照已重建 → 卡面变「第一首」
  global.fetch = recordFetch(async () =>
    ({ok: true, json: async () => snapOf(null)}));
  await (0, eval)("poll()");
  const card3 = global.__card(global.__st());
  ok("S3b cur=null 后卡面 NEXT=歌A 且按钮可用", card3.nextName === "歌A");
  // 迟到的 /playlist（过渡前生成 cur=5，仍是最新 seq → 接受）
  global.fetch = recordFetch(async (path) =>
    String(path).includes("/playlist")
      ? {ok: true, json: async () =>
         ({lib: [], pl: SONGS.map(s => ({key: s.key})), cur: 5})}
      : {ok: true, json: async () => snapOf(null)});
  await (0, eval)("refreshPlaylist()");
  ok("S3c 迟到 /playlist 被接受：stCur=5、stState.cur=null",
     global.__stCur() === 5 && global.__st().cur === null);
  sent.length = 0; resetFire();
  global.__nextGo();
  await new Promise(r => setTimeout(r, 20));
  ok("S3d 点击实切 index=6 key=G：卡面显「歌A」实切「歌G」",
     sent.length === 1 && sent[0].index === 6 && sent[0].key === "G",
     "sent=" + JSON.stringify(sent) + " card=" + card3.nextName);
  global.fetch = recordFetch(async () =>
    ({ok: true, json: async () => snapOf(null)}));
  await (0, eval)("poll()");
  ok("S3e 下一拍 poll 归位 stCur=null（窗口≤1 个 poll 周期）",
     global.__stCur() === null);

  // ==== S4 confirm 闭包：武装后 cur 变化 ====
  resetState();
  global.__set({confirm: true, open: true, cur: 1, songs: SONGS},
               {pl: SONGS.map(x => ({key: x.key, name: x.name}))}, 1);
  sent.length = 0; resetFire();
  const btn = els["#next-go"]; btn.dataset = {}; btn.classList =
      {add() {}, remove() {}, toggle() {}};
  global.__nextGo();                                   // 第一击：武装（目标 C）
  await new Promise(r => setTimeout(r, 5));
  ok("S4a 第一击武装不发包（confirm 路径）",
     sent.length === 0 && btn.dataset.armed === "1");
  // 武装窗内他端切到 C → cur=2；卡面 NEXT 变 D
  global.__set(Object.assign({}, global.__st(), {cur: 2}), global.__pc(), global.__stCur());
  const card4 = global.__card(global.__st());
  ok("S4b 卡面 NEXT 已变 D（cur=2）", card4.nextName === "歌D");
  resetFire();
  global.__nextGo();                                   // 第二击：确认
  await new Promise(r => setTimeout(r, 20));
  ok("S4c 第二击重算目标切新 next D（armConfirm 执行本次传入的 fire，"
     + "非武装时 C）——卡面与行动一致",
     sent.length === 1 && sent[0].key === "D" && sent[0].index === 3,
     "sent=" + JSON.stringify(sent));
  ok("S4d 武装态已清、无二次发包（武装保留的是确认窗，不是目标）",
     sent.length === 1 && btn.dataset.armed === undefined);

  // ==== S5 render null 归一核对 ====
  ok("S5 render 对 cur=null 取 rows[0]（无 rows[null+1]=rows[1] 问题）",
     global.__card(snapOf(null)).nextName === "歌A");

  console.log(fails.length ? "\n== 有 FAIL ==" : "\n== 全部通过 ==");
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
