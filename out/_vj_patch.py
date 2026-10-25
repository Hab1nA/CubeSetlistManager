# -*- coding: utf-8 -*-
"""VJ 页「音符 ↔ 视频对应」展示：/vjmap 端点 + 前端键帽网格。"""
import io


def patch(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    for old, new, tag in pairs:
        assert old in s, path + " NOT FOUND: " + tag
        assert s.count(old) == 1, path + " NOT UNIQUE: " + tag
        s = s.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print(path, "ok", len(pairs))


# ================= web_remote.py：/vjmap 端点 =================
WR = [
    ('''        elif path == "/monitor":''',
     '''        elif path == "/vjmap":
            self._h_vjmap()
        elif path == "/monitor":''',
     "route"),
    ('''    def _h_playlist(self):''',
     '''    def _h_vjmap(self):
        """VJ 音符 ↔ 视频对应表：NOTE_MAP 固定规则（音符 N → N-59.mp4，
        F#6=熄屏）+ 视频库实际文件宽松匹配（精确 basename 或「编号+空格」
        前缀）。只读快照，无 App 状态，HTTP 线程直出。"""
        app = self.server.app
        if getattr(app, "lite", False):
            self._json(403, {"error": "简化版无 VJ 联动"})
            return
        import re as _re
        ctl = getattr(app, "ctl", None)
        root = (ctl.cfg.get("videoRoot", "") if ctl is not None else "") or ""
        files = (obs_ctrl.scan_videos(root)
                 if root and os.path.isdir(root) else [])
        by_base, by_num = {}, {}
        for rel in files:
            b = os.path.basename(rel)
            by_base.setdefault(b, rel)
            m = _re.match(r"(\\d+)\\s", b)
            if m:
                by_num.setdefault(m.group(1), rel)
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A",
                 "A#", "B")
        rows = []
        for n in range(60, 91):
            f = __import__("midi_bridge").NOTE_MAP[n]
            nm = names[n % 12] + str(n // 12 - 1)
            if f is None:
                rows.append({"note": n, "name": nm, "file": "熄屏",
                             "blackout": True, "hit": None})
                continue
            base = os.path.basename(f)
            num = _re.match(r"(\\d+)", base)
            hit = by_base.get(base) or (by_num.get(num.group(1))
                                        if num else None)
            rows.append({"note": n, "name": nm, "file": hit or base,
                         "blackout": False, "hit": hit})
        self._json(200, {"videoRoot": root, "files": len(files),
                         "rows": rows})

    def _h_playlist(self):''',
     "handler"),
]
patch("web_remote.py", WR)

# ================= webapp/index.html：映射卡 =================
HTML = [
    ('''      <div class="card"><h3>视频联动</h3>''',
     '''      <div class="card"><h3>音符 ↔ 视频
        <span class="h3-note" id="vjmap-note">触发规则：音符 60 起，第
        N 个音对应 N.mp4（支持「N 描述」命名）；F#6 = 熄屏</span></h3>
        <div id="vjmap" class="keymap vjmap"></div>
      </div>
      <div class="card"><h3>视频联动</h3>''',
     "vjmap card"),
]
patch("webapp/index.html", HTML)

# ================= webapp/app.css =================
CSS = [
    ('''.keychip b { color: var(--fg); font-family: Consolas, monospace;''',
     '''.vjmap .keychip.live { border-color: var(--accent); color: var(--fg);
  box-shadow: 0 0 0 1px var(--accent); }
.vjmap .keychip.miss { opacity: .5; }
.vjmap .keychip.blackout b { color: var(--err); }
.vjmap .keychip .f { max-width: 180px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.keychip b { color: var(--fg); font-family: Consolas, monospace;''',
     "vjmap css"),
]
patch("webapp/app.css", CSS)

# ================= webapp/app.js =================
JS = [
    # 拉映射：进页/保存后/videoRoot 变化时（不进 1.5s 轮询）
    ('''// ---- VJ 联动 ----
async function refreshVj() {''',
     '''// ---- VJ 联动 ----
let vjMapRows = null, vjMapRoot = null;
async function refreshVjMap(root) {
  if (root === vjMapRoot && vjMapRows) return;   // 目录没变不重扫
  try {
    const r = await fetch("/vjmap");
    if (!r.ok) return;
    const d = await r.json();
    if (d.videoRoot !== (root ?? vjMapRoot) && root !== undefined) return;
    vjMapRoot = d.videoRoot;
    vjMapRows = d.rows;
    const box = $("#vjmap");
    box.innerHTML = "";
    if (!vjMapRoot) {
      box.innerHTML = "<span class='dim sm-txt'>未设置视频目录，"
          + "先在下方「视频联动」里选择</span>";
      return;
    }
    d.rows.forEach(row => {
      const chip = document.createElement("span");
      chip.className = "keychip"
          + (row.blackout ? " blackout" : "")
          + (row.hit ? "" : " miss");
      chip.dataset.file = row.file;
      chip.title = row.blackout ? "F#6：熄灭投影画面"
          : (row.hit ? "视频库：" + row.hit
             : "视频库中未找到 " + row.file + "（可按「N 描述」命名放入视频目录）");
      const b = document.createElement("b");
      b.textContent = row.name;
      const f = document.createElement("span");
      f.className = "f";
      f.textContent = row.blackout ? "熄屏" : row.file;
      chip.append(b, f);
      box.appendChild(chip);
    });
  } catch { /* 瞬断 */ }
}

async function refreshVj() {''',
     "refreshVjMap"),
    # 进页/保存后拉映射
    ('''    const ms = $("#vj-media-st");
    ms.textContent = d.obs.mediaInput || "未配置";
    ms.className = "stat-big" + (d.obs.mediaInput ? "" : " dim");''',
     '''    const ms = $("#vj-media-st");
    ms.textContent = d.obs.mediaInput || "未配置";
    ms.className = "stat-big" + (d.obs.mediaInput ? "" : " dim");
    refreshVjMap((d.obs.videoRoot || "").trim());''',
     "refreshVj call"),
    # 当前在播高亮（走已有 /state 轮询，零额外请求）
    ('''  // 视频名只在播放/暂停时挂出（同旧监控格；stopped/播完不带）
  $("#vj-video").textContent =
      (vj.follow === "playing" || vj.follow === "paused") && vj.video
      ? "视频：" + vj.video : "";''',
     '''  // 视频名只在播放/暂停时挂出（同旧监控格；stopped/播完不带）
  $("#vj-video").textContent =
      (vj.follow === "playing" || vj.follow === "paused") && vj.video
      ? "视频：" + vj.video : "";
  // 映射网格：在播视频对应的音符芯片高亮
  if (vjMapRows) {
    const cur = (vj.video || "").split("/").pop();
    $$("#vjmap .keychip").forEach(chip => {
      const f = chip.dataset.file || "";
      chip.classList.toggle("live",
          !!cur && (f === cur || cur.endsWith(f)
                    || f.replace(/^\\d+/, "") !== ""
                       && cur.split(" ").length > 1
                       && cur.split(" ")[0] === f.replace(/^\\d+/, "")));
    });
  }''',
     "live highlight"),
]
patch("webapp/app.js", JS)
print("ALL OK")
