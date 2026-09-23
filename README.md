# 工程播放台（CubeSetlistManager）

演出用控制台：素材库编排播放列表 → 一键切换 Cubase 工程（先关后开）→
播完自动切换下一首 → JUNO-DS 音色自动切换 + CC 踩钉快捷键 + OBS 外屏视频联动。
深色大字界面，演出暗场可读（深色标题栏，Win11 圆角）。

## 两个程序的关系

| 程序 | 定位 |
|---|---|
| `dist\工程播放台\工程播放台.exe` | **演出主程序**：歌单编排、切歌、走带、自动推进、音色/踩钉/VJ 全联动（本 README 主角） |
| `dist\MIDI视频桥\MIDI视频桥.exe` | 独立 VJ 桥（只做 MIDI 音符→OBS 视频），不需要歌单管理时单用 |

两个 exe 的 `config.json`、`playlist.json` 都放各自 exe 同目录（onedir 版本文件夹内）。

## 启动自检

启动时检查三大后端，**未运行的自动拉起**，状态写日志：

- **loopMIDI**：端口不在就拉起程序并等虚拟端口就绪；
- **OBS**：进程不在就自动启动（含等 WebSocket 就绪，至多 30 秒）；
- **Cubase**：进程不在就冷启动到 Hub（约 30 秒，之后切歌走单实例转交）。

任一后端拉起失败只记日志，不阻断其余功能（缺什么补什么）。

## 主界面

- **NOW/NEXT 横幅**：当前工程大字 + 下一首 + 剩余时间，演出一眼可读；
  下方**全宽进度条**随走带活跃时长推进（回零归零、无工程隐藏）。
- **素材库**：双击加入播放列表；支持 Ctrl/Shift 多选批量加入；右上搜索框过滤。
- **播放列表**：双击切换工程；**有工程在开时**默认弹确认防误触（设置页
  「切换工程需确认」可关），**无打开工程时双击直接打开、不弹确认**；
  当前行深色高亮，"←" 标记当前曲；编排按钮随选中启停。
- **监控栏**：VJ 自动化与键盘自动化两栏同构，各四格——端口名称/端口状态 +
  （VJ：OBS 状态/走带跟随 ｜ 键盘：音色映射/最近切换），状态格带 ● 圆点 +
  绿/黄/红语义色。
- **播放组**：开始=回零从头播 / 暂停 / 继续 / 回零（=停止+回零，停在零点、
  已播计时归零，之后「继续」从零点起播）/ 上一首 / 下一首 / **全停**（向所有
  Cubase 工程窗口发停止 + 熄屏 OBS）。暂停/继续随走带状态自动启停。
- **播放配置（底部独立一行）**：配置目标（标蓝选中曲）、时长三态（绿=自动识别 / 黄=手动值 /
  红=未知需手填）、写入/重新识别、「设置」入口（自动切换工程/连续播放/保持软件前台/
  切换工程需确认/端口名/目录）。时长写入目标=两列表中唯一标蓝项。

## 自动切换（两段式）

Cubase 播到头不会自己停（实测）：程序累计走带时钟已播时长 ≥ 工程时长 → 主动发停止键
（3 秒重试至多 3 次）→ 时钟断流 → 自动切播放列表下一首。中途手动停/暂停（已播不足）不切换。
时长来自 .cpr 定位条解析（启动全量探测+打开工程时重测），未设定位条的手填兜底。

## 键盘自动化 / 踩钉

- **键盘自动化**：Cubase 向 loopMIDI「Keyboard Automation」端口发音符，按当前工程
  映射切音色——C3-A3(60-69) → **JUNO-DS**，C4-F4(72-77) → **AX-09 Lucina**。
  映射录制=窗口里点「录制」，JUNO 在琴上按 Favorite、AX-09 在琴上选中音色
  （**先把 AX-09 的 MIDI 设置 Bn 开为 ON**：SHIFT+V-LINK 连按 5 次，改完 SHIFT+WRITE），
  软件从各自 MIDI 输入捕获 BS/PC，存工程文件夹 `keyboard_automation.json`
  （"slots"=JUNO 段，"ax"=AX-09 段）。AX-09 只能经 USB 接收（DIN 口 OUT-only）；
  BS+PC 直发 144 个常规音色（MSB 恒 87；1-128 号 LSB=0、129-144 号 LSB=1），
  Favorite/Special Tone 不在 MIDI 映射表里。
- **踩钉**：MIDI CC 上升沿触发（瞬时/开关踩钉通吃）。窗口里点「学习」踩一下即完成绑定，
  存 `config.json` 的 pedal 段；支持热插拔（断开每 10 秒自动重连）。

## config.json 字段

| 段 | 字段 | 说明 |
|---|---|---|
| obs | host / port / password | obs-websocket 地址（OBS 内置服务器需启用，真配置在 `%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`） |
| obs | obsExe / autoStart | OBS 未运行时自动拉起 |
| obs | mediaInput / videoRoot | 媒体源名（缺源自动补建「舞台视频」）/ 视频库根目录 |
| cubase | cubaseExe / projectsRoot / autoSave | Cubase 路径、工程库根目录（`<队伍>/<歌>/<歌>.cpr`）、切换时自动保存 |
| juno | inHint / outHint / patchCh / perfCh / deviceId | JUNO-DS MIDI 端口提示与通道 |
| ax09 | inHint / outHint / ch | AX-09 USB MIDI 端口提示与接收通道（默认 1；琴上 SHIFT+V-LINK×4 可查改） |
| pedal | deviceHint / bindings | 踩钉设备名提示、动作→CC 号 |
| autoAdvance | （顶层） | 「自动切换工程（播完自动切下一首）」勾选持久化 |
| autoPlay / topMost / switchConfirm | （顶层） | 连续播放 / 保持软件前台 / 切换工程需确认（默认开，设置页可改） |
| vjPortHint / kbPortHint | （顶层） | VJ 与键盘自动化的 loopMIDI 端口名提示（设置页可改，保存即热切换监听） |

## 文件架构

```
├─ setlist_gui.py        主程序（工程播放台）
├─ midi_bridge.py / midi_bridge_gui.py   VJ 桥核心 / 其 GUI（独立 exe）
├─ cubase_ctrl.py        Cubase 切歌/走带/进程（先关后开 + 键注入）
├─ obs_ctrl.py / obs_ws.py   OBS websocket 控制（进程枚举/launch_detached 在此）
├─ advance.py            自动推进看门狗（两段式）
├─ kbd_auto.py / pedal.py    键盘音色自动化 / CC 踩钉
├─ dpi.py                DPI 感知 + 深色主题 token（darkify/dark_title）
├─ cpr_meta.py           .cpr 时长解析
├─ 工程播放台.spec / MIDI视频桥.spec / build.bat   打包
├─ test_bridge.py        桥自检（离线，无 OBS/loopMIDI）
├─ night_test.py         夜测编排器（gui 离线 56 项等分阶段）
├─ _render_check.py      离线渲染断言 + 五窗截图（落 _render\，可删可再生）
├─ e2e_test.py / probe_kb_pipeline.py    真机分阶段 E2E / 键盘链路探针
├─ config.json / playlist.json   dist 同源恢复副本（重打包事故的恢复源）
├─ _bak_dist\            重打包前 dist 数据备份（确认新版正常后可删）
├─ dist\                 打包产物（exe 同目录放运行时真实数据）
└─ docs\                 设计审查报告 / 夜测报告 / M0 赛前验证清单
```

## 开发

- 直接运行：`python setlist_gui.py`。
- 验证：`python test_bridge.py`（离线自检）→ `python night_test.py gui`
  （离线 GUI 回归 56 项）→ `python _render_check.py`（版式断言+截图落
  `_render\`）；真机分阶段：`python e2e_test.py`。
- **重新打包一律用 `build.bat`（原生 cmd 或双击跑，Git Bash 调它会乱码）**：
  先备份 exe 目录两份 json → 双 spec 打包 → 数据原样放回，失败保留 .bak。
  **勿裸跑 `pyinstaller --noconfirm`**：它会先清空版本文件夹，dist 里是
  运行时真实数据（歌单/时长/设置），历史上因此丢过数据。杀毒偶发锁
  `_internal` 里的 DLL：等几秒删掉版本文件夹重跑即可。
- onedir 而非 onefile（%TEMP% 清理失败会弹窗）。

## 演出前

看 **[docs/M0_验证清单.md](docs/M0_验证清单.md)**：赛前自检步骤 + 现场故障恢复路径 + 待真机验证项。

## 已知平台坑（改代码前先读）

- python-rtmidi 在本机 Python 3.14 import 即崩 → MIDI 走 ctypes+winmm，勿引入 mido/rtmidi
- winmm `CALLBACK_FUNCTION` 回调的 wMsg 本机驱动走 `MM_MIM_*` 族（数据=0x3C3，非文档值 2）
- MME devcaps 枚举必须用 A 版（W 版对 teVirtualMIDI/Rubix 驱动返回 INVALPARAM）
- 强杀 OBS 会留崩溃标记弹窗，别强杀；强杀 Cubase 同理
- Cubase 窗口标题版本名随工程最后保存版本变（`Cubase Pro 工程 - 名` / `Cubase Version 13.0.40 工程 - 名`），
  匹配只认固定标记 `" 工程 - "`+后缀全等
- 无 console 的 exe 崩溃栈落 exe 同目录 `crash.log`（faulthandler）
