# 设计：VJ 视频循环开关（点击展示块切换「播一遍停 / 循环」）

定案日期：2026-11-09。状态：已实现（含测试；第 8 节真机验收清单待跑——
其中 8.3 为唯一真机待验点：OBS 中途合并 looping 是否即时生效）。

## 1. 需求与语义定案

在 webapp VJ 页的触发槽位网格（`#vjmap`）里，**点击某个视频格子 = 切换该槽位
「播一遍后停止（定格末帧）」⇄「循环播放」**。设置按槽位编号持久化，重启保留；
琴键触发（MIDI 音符）按当下设置起播。

- 槽位（slot）= 编号 N（1..30），与文件名约定「N 描述.mp4」的 N 一致；标记挂
  在**槽位**而非具体文件——换文件（同编号改名/替换）标记不丢。
- 默认值 = 播一遍停（现状行为，零迁移）。
- 熄屏槽（F#6/音符 90）无循环语义，不可点击。
- 改动只做 webapp 端；Tk 主窗/平板遥控页/安卓壳本无视频格子，不扩展。

## 2. 现状事实（实现的地基，全部已核验）

| 事实 | 位置 |
|---|---|
| 播放器是 OBS 媒体源（ffmpeg_source），`set_media(file_rel, loop)` 已把 loop 映射为 OBS `looping` 设置，连日志「（循环）」都写好 | obs_ctrl.py:263-278 |
| **全仓唯一写死点**：琴键触发热路径 `ctl.set_media(target, False)` | midi_bridge.py:678 |
| 播完行为：定格末帧（OBS_MEDIA_STATE_ENDED），无自动下一动作、无 EOS 事件订阅——两种模式都不需要 EOS 处理 | obs_ws.py:157（eventSubscriptions=0） |
| 走带暂停跟随对 looping 源天然兼容（循环源恒 PLAYING，不进 ENDED 不复活分支） | midi_bridge.py:639-647 |
| 同音符重按=从头重播（两种模式语义一致）；连发取最新音符 | midi_bridge.py:666-681 |
| 熄屏=stop_media 清空文件，独立路径，与循环无交集 | midi_bridge.py:672-676、obs_ctrl.py:280-295 |
| 唯一格子渲染处：webapp `refreshVjMap`，纯展示无点击、`cursor:default` | webapp/app.js:2276-2316、app.css:672-686 |
| 格子 5 秒 force 重建（即放即扫），`.live` 在播高亮由 500ms 轮询靶向补挂 | app.js:2939-2940、2365-2374 |
| `/vjmap` 只读快照：rows[{note,name,file,blackout,hit}]，行内已解析编号 | web_remote.py:1659-1696 |
| 设置保存两条路径（web/Tk）对 obs 段均为**按键合并**，未知键保留——新增 `videoLoops` 键不会被设置保存洗掉 | setlist_gui.py:2512-2526、5058-5067 |
| lite 模式 `/vjmap` 返回 403 | web_remote.py:1664-1666 |

## 3. 数据模型与持久化

```json
// config.json → obs 段新增键（与 videoRoot/vjMute 同段、同 camelCase）
"videoLoops": {"3": true, "17": true}
```

- **键 = 槽位编号字符串**（`str(int(N))`，无前导零），**值恒 true，删键=回到播一遍停**（稀疏存储，只记例外）。
- 规范化口径与现有编号解析一致：`re.match(r"(\d+)", basename)` 取前导数字再 `str(int(...))`（/vjmap 行、`_abs_path` 宽松匹配同一套）。
- 运行时唯一真源 = `ctl.cfg["videoLoops"]`（ObsController 的 cfg 字典）：启动由 `load_obs_cfg()` 自然带入（midi_bridge.py:696-705），切换即时改它，琴键触发即时读它。不另设镜像集合，杜绝双源漂移。
- 为什么不放 videoRoot 旁 sidecar（points.py 式）：videoRoot 是即放即扫的内容投放目录，应用从不在里面持久化任何东西；而 videoLoops 是应用级设置，obs 段同族键先例充分。
  ponytail: 若将来多机共用同一 videoRoot 要同步标记，升级为 videoRoot 内 sidecar，迁移点只在读写两函数。

## 4. 后端改动明细（三处小改 + 一个新端点）

### 4.1 obs_ctrl.py：查找 + 热应用两个小函数

```python
def loop_for(self, file_rel):
    """槽位编号查循环标记：file_rel 取 basename 前导数字（与 _abs_path
    宽松匹配同一编号口径）；无标记/无编号 = False（播一遍停）。"""
    m = re.match(r"(\d+)", os.path.basename(str(file_rel or "")))
    loops = self.cfg.get("videoLoops") or {}
    return bool(m and loops.get(str(int(m.group(1)))))

def set_looping(self, on):
    """热应用循环标记到在播媒体源（仅合并 looping 设置，不 RESTART——
    正在播的画面零中断）。"""
    def do(ws):
        ws.request("SetInputSettings", {
            "inputName": self.cfg.get("mediaInput", "舞台视频"),
            "inputSettings": {"looping": bool(on)}, "overlay": True})
    return self._act("循环%s" % ("开" if on else "关"), do)
```

`set_media` 签名不动（显式参数语义清晰，预热路径继续传 False）。

### 4.2 midi_bridge.py：写死点一行

```python
ok = ctl.set_media(target, ctl.loop_for(target))    # 原: (target, False)
```

standalone（midi_bridge.main）与 lite（automator_gui）宿主零改动即获得该行为
（cfg 由 load_obs_cfg 带入）。

### 4.3 web_remote.py：`/vjmap` 行加 `loop` + 新写端点

- `_h_vjmap` 每行（非熄屏）追加 `"loop": ctl.loop_for(f)`（ctl 缺席=false）；
  熄屏行 `"loop": false`。契约是**加性**扩展，测试同步钉。
- 新端点，仿 `/slotset` 的 `_run_on_app` 派发风格：

```
POST /vjloop   {"note": 73, "on": true}
→ 200 {"ok": true, "loop": true}
→ 400 note ∉ 72..101（102=熄屏拒绝）或 on 非布尔
→ 403 lite 模式（与 /vjmap 同口径）
```

### 4.4 setlist_gui.py：App 侧切换方法（Tk 线程内执行）

```python
def _set_video_loop(self, note, on):
    """VJ 槽位循环开关：改运行时 cfg（琴键热路径即时生效）→ 持久化 →
    在播槽位热应用（OBS 网络动作按惯例走工作线程，不占主线程）。"""
```

- `n = note - 71`；`loops = dict(self.ctl.cfg.get("videoLoops") or {})`；
  on → `loops[str(n)] = True`，off → `loops.pop(str(n), None)`；
  写回 `self.ctl.cfg["videoLoops"] = loops`。
- 持久化走 `_settings_apply_web` 同款合并（读 cfg → obs 段改单键 → 落盘），
  绝不整段替换。
- 热应用条件：`self.sync.current_video` 的 basename 前导编号 == n 且
  `sync.video_state` 为 playing/paused → 起工作线程调 `ctl.set_looping(on)`。
- 返回 `(True, on)` 供端点回包。

### 4.5 Tk 主窗监控格小润色（可选 1 行）

「走带跟随」格在播视频名后追加 `（循环）` 后缀（setlist_gui.py:3914-3926
拼接处查 `self.ctl.cfg`），与 obs_ctrl 日志同文案。

## 5. 前端改动明细（webapp 三件）

### 5.1 app.js `refreshVjMap`

- 构格时非熄屏行：`cell.className += row.loop ? " loop" : ""`；
  `cell.dataset.note = row.note`；`aria-pressed` 同步；
  title 追加「·点击切换循环/播一遍停」。
- 每格挂 click（熄屏格不挂）：in-flight 防抖（`vjLoopBusy` 按 note 记账，
  仿 `ptOnPending` 防轮询回弹）→ 乐观切 `.loop` 类与 aria-pressed →
  `post("/vjloop", {note, on: !row.loop})` → 成功回写 `row.loop`（5 秒重建
  保真）；失败回滚 + toast 错误。
- 键盘可达：`tabindex="0"` + Enter/Space 触发同 click（a11y 基线）。

### 5.2 app.css

```css
.vj-cell:not(.blackout) { cursor: pointer; }
.vj-cell.loop { border-color: var(--ok-line); }        /* 与 .tgl.on 同族绿色语义 */
.vj-cell.loop::after { content: "∞";
    position: absolute; top: 4px; right: 6px;
    color: var(--ok); font-weight: 600; }              /* 角标不占布局 */
.vj-cell.blackout { cursor: default; }                 /* 熄屏不可点 */
```

`.slot` 已有 `position: relative` 则直接用；无则补（实现时核对）。
`.loop` 与 `.live`（在播高亮 ring）可叠加：ring=正在播，∞=循环档，语义不撞。
缺视频（`.miss`）格仍可点——标记挂槽位，文件后放也能预配置。

### 5.3 index.html

零结构改动（网格容器已在）。

## 6. 边界与已知取舍

1. **OBS 中途改 looping 是否立即生效 = 唯一真机待验点**。设计按 obs-websocket
   `SetInputSettings overlay:true` 合并、ffmpeg_source 在 EOS 时查 looping 的
   预期行为（等价于 OBS 属性面板勾 Loop 不打断播放）。真机探针：短视频播至
   尾声前 2 秒切 ON，观察能否无缝进圈。若证伪：降级口径=「标记作用于下次触
   发」，移除 set_looping 热应用与 4.4 的条件分支（各一处，改动退坡干净）。
2. ENDED 定格后再切 ON 不会复活画面（OBS 已收播）——语义=下次触发生效，
   重按琴键即从头循环。文案不承诺「点了立即续圈」。
3. 乐观更新与 5 秒 force 重建的竞态：POST 成功回写 `row.loop` 是权威，重建
   从行数据渲染，天然收敛；多端（双浏览器）最迟 5 秒一致。
4. 快速连点多格：in-flight 按 note 记账互不阻塞；同格连点在途忽略第二次。
5. `_persist_config` 顶层 update 不合字典——obs 段必须走 4.4 的合并路径，
   否则会把 host/port 等洗掉（实现时用现成模式，勿自创）。
6. 编号超界（如手改 config 塞 "99"）：loop_for 查不到即 false，无危害；
   端点侧 note 校验挡住常规脏数据，不做配置清洗（YAGNI）。

## 7. 测试计划

- tests/test_bridge.py：note_handler 尊重 videoLoops——假 ctl（cfg 带
  {"3": true}）喂音符 62（→3.mp4）断言 set_media 收 loop=True，音符 61 断言
  False；加源锚断言 midi_bridge.py:678 不再是字面 `set_media(target, False)`。
- tests/test_webapp.py：`test_vjmap_endpoint` 扩断言行含 `loop` 且与
  videoLoops 对应；新 `POST /vjloop` 用例（200 切换+cfg 落点、400 note 90、
  403 lite）。
- obs_ctrl 单测：loop_for 编号解析（"3.mp4"→3、"03 x.mp4"→3、无数字→False）。
- e2e/真机：见验收清单（含第 6.1 探针）。

## 8. 真机验收清单

1. 点格 ≤0.5s 出现/消失 ∞ 角标（乐观更新）。
2. ∞ 档按琴键：视频循环；再点掉：下次触发播一遍定格末帧。
3. **探针**：在播临近尾声切 ON → 无缝进圈；循环中切 OFF → 本圈播完即停。
4. 设置页保存（web 与 Tk 各一次）后 videoLoops 不丢；重启应用标记保留。
5. 双浏览器同开 VJ 页，一端切换另一端 5 秒内跟上。
6. 熄屏格无手势无角标；`.live` 在播高亮与 ∞ 角标同格共存不串色。
7. lite 版：无 /vjloop（403）、无格子，琴键触发仍按已存标记执行。
8. 走带暂停/恢复对循环源行为不变（暂停定格、恢复续圈）。

## 9. 明确不做（YAGNI 留档）

- Tk 主窗/平板页/安卓壳的视频格子（本无格子；遥控触发视频是另一个需求）。
- 每视频显示名/时长元数据、点击格子即触发视频、循环次数上限。
- videoLoops 的设置表单入口（格子即编辑器，表单只管目录/监视器等全局项）。
