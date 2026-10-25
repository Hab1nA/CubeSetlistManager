# -*- coding: utf-8 -*-
"""遗留项清理批：五项值得做（switchTo 实时解析/exit 单确认/poll 超时/
空格键不劫持/checkpath 路径校验）。"""
import io


def patch(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    for old, new, tag in pairs:
        assert old in s, path + " NOT FOUND: " + tag
        assert s.count(old) == 1, path + " NOT UNIQUE: " + tag
        s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(path, "ok", len(pairs))


# ================= webapp/app.js =================
JS = [
    # 1) switchTo 陈旧索引根治：fire 前按 key 实时重解析 index
    ('''function switchTo(i, btn, dbl) {
  const st = stState;
  const fire = () => { swLastFire = Date.now(); cmd("switch", i); };''',
     '''function switchTo(i, btn, dbl, key) {
  const st = stState;
  const fire = () => {
    // 确认窗 3s 内列表可能被重排（多端操作）：按 key 实时重解析，
    // 不发武装时刻的旧下标（红队遗留：陈旧索引切错歌）
    if (key && plCache && plCache.pl) {
      const j = plCache.pl.findIndex(x => x.key === key);
      if (j >= 0) i = j;
    }
    swLastFire = Date.now();
    cmd("switch", i);
  };''',
     "switchTo key"),
    # consoleRow：行携带 key，三处调用传 key
    ('''  div.dataset.i = i;
  div.tabIndex = 0;              // 键盘可达：Enter=切歌（F3）''',
     '''  div.dataset.i = i;
  div.dataset.k = (plCache && plCache.pl && plCache.pl[i])
      ? plCache.pl[i].key : "";
  div.tabIndex = 0;              // 键盘可达：Enter=切歌（F3）''',
     "row dataset.k"),
    ('''  go.addEventListener("click", ev => {
    ev.stopPropagation();
    switchTo(i, go);
  });
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = s.dur;
  div.append(no, eq, nm, go, dur);
  div.addEventListener("dblclick", () => switchTo(i, go, true));
  div.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchTo(i, go);
    }
  });''',
     '''  go.addEventListener("click", ev => {
    ev.stopPropagation();
    switchTo(i, go, false, div.dataset.k);
  });
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = s.dur;
  div.append(no, eq, nm, go, dur);
  div.addEventListener("dblclick", () => switchTo(i, go, true, div.dataset.k));
  div.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      switchTo(i, go, false, div.dataset.k);
    }
  });''',
     "row calls"),
    # chips 传 key
    ('''    b.addEventListener("click", () => switchTo(x.i, b));''',
     '''    b.addEventListener("click", () => switchTo(x.i, b, false, x.key));''',
     "chips key"),
    ('''  plCache.pl.forEach((it, i) => {
    if (it.pt && it.key.startsWith("pt:")
        && it.key.slice(3).split("#")[0] === baseKey)
      items.push({i, name: it.name.split(" · ").pop(), cur: i === cur});
  });''',
     '''  plCache.pl.forEach((it, i) => {
    if (it.pt && it.key.startsWith("pt:")
        && it.key.slice(3).split("#")[0] === baseKey)
      items.push({i, key: it.key,
                  name: it.name.split(" · ").pop(), cur: i === cur});
  });''',
     "chips key data"),
    # pt-goto 传 key（ptGotoIndex 的 key 已知）
    ('''$("#pt-goto").addEventListener("click", () => {
  const i = ptGotoIndex();
  if (i !== null) {
    switchTo(i, $("#pt-goto"));''',
     '''$("#pt-goto").addEventListener("click", () => {
  const i = ptGotoIndex();
  if (i !== null) {
    switchTo(i, $("#pt-goto"), false,
        "pt:" + ptProj + "#" + ptSel);''',
     "pt-goto key"),
    # 3) poll 超时：HTTP 挂起可见（AbortSignal.timeout 渐进增强）
    ('''async function poll() {
  try {
    const r = await fetch("/state");''',
     '''async function poll() {
  try {
    // 5s 超时：进程活着但 HTTP 挂起时也走断连指示（挂起的 fetch
    // 不再永远 pending）；旧内核无 AbortSignal.timeout 则退化为原行为
    const opt = window.AbortSignal && AbortSignal.timeout
        ? {signal: AbortSignal.timeout(5000)} : {};
    const r = await fetch("/state", opt);''',
     "poll timeout"),
    # 4) 空格热键不劫持聚焦按钮的原生空格激活
    ('''  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT"
            || t.tagName === "TEXTAREA" || t.isContentEditable)) return;''',
     '''  if (t && (t.tagName === "INPUT" || t.tagName === "SELECT"
            || t.tagName === "TEXTAREA" || t.tagName === "BUTTON"
            || t.isContentEditable)) return;''',
     "space button"),
    # 6) 设置路径即时校验（失焦触发，红框+悬停原因）
    ('''$$(".browse").forEach(b =>
    b.addEventListener("click", () => pickDir(b.dataset.pick)));''',
     '''$$(".browse").forEach(b =>
    b.addEventListener("click", () => pickDir(b.dataset.pick)));

// ---- 设置路径即时校验（工程库根=目录 / Cubase 程序=文件）----
const PATH_KIND = {("st-root"): "dir", ("st-exe"): "file"};
async function checkPath(id) {
  const el = $("#" + id);
  const v = el.value.trim();
  if (!v || !PATH_KIND[id]) return;
  try {
    const r = await fetch("/checkpath", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: v})});
    if (!r.ok) return;
    const d = await r.json();
    const bad = !d.exists || (PATH_KIND[id] === "dir" && !d.isdir)
        || (PATH_KIND[id] === "file" && d.isdir);
    el.classList.toggle("invalid", bad);
    el.title = bad ? (!d.exists ? "路径不存在"
        : (PATH_KIND[id] === "dir" ? "该路径不是目录" : "该路径不是文件"))
        : "";
  } catch { /* 瞬断 */ }
}
["st-root", "st-exe"].forEach(id => {
  $("#" + id).addEventListener("change", () => checkPath(id));
});''',
     "checkpath fe"),
]
patch("webapp/app.js", JS)

# ================= webui.py：webui 模式标记 =================
WI = [
    ('''    ui_srv.shutdown()
    app.calls.put(app._on_exit)         # 窗口关=通知 Tk 线程退出全程序''',
     '''    ui_srv.shutdown()
    app.webui_mode = True               # Tk 确认框改由前端两段式承担
    app.calls.put(app._on_exit)         # 窗口关=通知 Tk 线程退出全程序''',
     "webui mode shutdown"),
]
patch("webui.py", WI)

# ================= setlist_gui.py =================
SG = [
    # /exit 单确认：webui 模式前端已两段式确认，跳过 Tk messagebox
    ('''    def _on_exit(self):
        """退出确认（busy 时文案含中断警示，恰弹一层）→ 关 MIDI 口/停后台线程。"""
        if self.ctrl is not None and self.ctrl.busy:''',
     '''    def _on_exit(self):
        """退出确认（busy 时文案含中断警示，恰弹一层）→ 关 MIDI 口/停后台线程。
        webui_mode（pywebview 壳）跳过 Tk 弹窗：前端侧栏退出钮已两段式确认，
        Tk 对话框跨窗口会沉底/触发 stallguard 误报（红队遗留）。"""
        if getattr(self, "webui_mode", False):
            ok = True
        elif self.ctrl is not None and self.ctrl.busy:''',
     "on exit webui"),
]
patch("setlist_gui.py", SG)

# ================= web_remote.py：/checkpath 端点 =================
WR = [
    ('''                          (self._h_kbd_record, "/kbdrecord"),
                          (self._h_slot_set, "/slotset")):''',
     '''                          (self._h_kbd_record, "/kbdrecord"),
                          (self._h_checkpath, "/checkpath"),
                          (self._h_slot_set, "/slotset")):''',
     "route"),
    ('''    def _h_exit(self, _body, ip):''',
     '''    def _h_checkpath(self, body, ip):
        """路径存在性探测（设置页即时校验）：仅限本机 loopback——目录
        存在性属主机信息，LAN 平板不得探测。"""
        if getattr(self.server.app, "lite", False) \\
                or not ip.startswith("127."):
            self._json(403, {"error": "仅限本机操作"})
            return
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            self._json(400, {"error": "path 必填"})
            return
        import os as _os
        self._json(200, {"ok": True, "exists": _os.path.exists(path),
                         "isdir": _os.path.isdir(path)})

    def _h_exit(self, _body, ip):''',
     "handler"),
]
patch("web_remote.py", WR)
print("ALL OK")
