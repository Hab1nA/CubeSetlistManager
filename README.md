# Cube Setlist Manager

演出用控制台：素材库编排播放列表 → 一键切换 Cubase 工程（先关后开）→
播完自动切换下一首 → JUNO-DS 音色自动切换 + CC 踩钉快捷键 + OBS 外屏视频联动。
深色大字界面，演出暗场可读（深色标题栏，Win11 圆角）。

## 功能一览

- **歌单编排**：素材库双击/多选批量加入播放列表，搜索过滤，拖动顺序编排；
- **工程切换**：双击歌单一键切换 Cubase 工程（先关后开），有工程在开时弹确认防误触；
- **自动推进**：工程播完自动停止并切下一首（Cubase 播到头不会自己停）；
- **音色自动切换**：Cubase 发音符 → JUNO-DS / AX-09 Lucina 按工程映射切音色；
- **踩钉快捷键**：MIDI 踩钉 CC 绑定切歌/走带等动作，支持热插拔；
- **VJ 视频联动**：走带跟随自动播/停 OBS 视频，熄屏一键黑场；
- **节目投影**：把 OBS 节目画面全屏投影到指定显示器（设置页选择，重连自动恢复）；
- **VJ 静音播放**：视频静音 + 关监听，或开「监视器并输出」出声，设置页切换；
- **启动自检**：loopMIDI / OBS / Cubase 未运行自动拉起，缺什么补什么；
- **收尾可选**：退出时按序优雅关闭 Cubase / OBS / loopMIDI。

## 程序组成

**安装版（推荐）**：Release 下载 `CubeSetlistManager-Setup-x.y.z.exe` 双击安装——
按用户安装到 `%LOCALAPPDATA%\Programs\CubeSetlistManager`（免管理员），自动创建
开始菜单/桌面快捷方式，自带卸载器；升级直接装新版，`config.json` 等数据保留。

| 程序 | 定位 |
|---|---|
| `Cube Setlist Manager\Cube Setlist Manager.exe` | **演出主程序**：歌单编排、切歌、走带、自动推进、音色/踩钉/VJ 全联动（本 README 主角） |

`config.json`、`playlist.json` 放 exe 同目录（安装目录的版本文件夹内）。

## 环境要求

- Windows 10/11（深色标题栏依赖 Win11 圆角特性）；
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)（虚拟 MIDI 端口）；
- OBS Studio（obs-websocket 5.x 已内置于 OBS 28+，需在「工具→WebSocket 服务器设置」启用）；
- Steinberg Cubase（实测 Cubase 15 / Pro 13.0.40 窗口标题均兼容）；
- 硬件可选：Roland JUNO-DS88、Roland AX-09 Lucina（仅 USB 可接收）、MIDI 踩钉。

### 开发 / 构建

- Python **3.14.x**（实测版本；零第三方运行时依赖是刻意设计——python-rtmidi
  在 3.14 下 import 即崩，故 MIDI/OBS 协议均为标准库手写）；
- 改码后重打包：`build.bat`（备份 dist 真实数据 → PyInstaller → 还原；装了
  Inno Setup 6 会顺带出按用户安装包，版本取最近 git tag）；
- 离线自检：`py test_bridge.py`，或 `py -m pytest test_bridge.py`（CI 每次
  push 自动跑）；
- 真机 E2E：`py -u e2e_test.py <phase>`（库根默认本机路径，换机设环境变量
  `CUBE_PROJECTS_ROOT` 覆盖）。

## 快速开始

1. **首次配置**：把 `config.example.json` 复制为 exe 同目录的 `config.json`，
   改 OBS 地址/密码、Cubase 路径、工程库与视频目录；
2. **准备素材**：Cubase 工程按 `<队伍>/<歌>/<歌>.cpr` 放进工程库根目录，
   视频放进 VJ 视频目录；歌名来源=`.cpr` 文件名；
3. **启动主程序**：启动自检会自动拉起三大后端并写日志——
   - **loopMIDI**：端口不在就拉起程序并等虚拟端口就绪；
   - **OBS**：进程不在就自动启动（含等 WebSocket 就绪，至多 30 秒）；
   - **Cubase**：进程不在就冷启动到 Hub（约 30 秒，之后切歌走单实例转交）。

   任一后端拉起失败只记日志，不阻断其余功能（缺什么补什么）。
4. **音色/踩钉绑定**：见下文[键盘自动化](#键盘自动化--踩钉)与[踩钉](#踩钉)两节。

## 界面与操作

### 主界面

- **NOW/NEXT 横幅**：当前工程大字 + 下一首 + 剩余时间，演出一眼可读；
  下方**全宽进度条**随走带活跃时长推进（回零归零、无工程隐藏）。
- **素材库**：双击加入播放列表；支持 Ctrl/Shift 多选批量加入；右上搜索框过滤。
- **播放列表**：双击切换工程；**有工程在开时**默认弹确认防误触（设置页
  「切换工程需确认」可关），**无打开工程时双击直接打开、不弹确认**；
  当前行深色高亮，"←" 标记当前曲；编排按钮随选中启停。
- **监控栏**：VJ 自动化与键盘自动化两栏同构，各四格——端口名称/端口状态 +
  （VJ：OBS 状态/走带跟随 ｜ 键盘：音色映射/最近切换），状态格带 ● 圆点 +
  绿/黄/红语义色；走带跟随格播放中会显示当前视频名。
- **播放组**：开始=回零从头播 / 暂停 / 继续 / 回零（=停止+回零，停在零点、
  已播计时归零，之后「继续」从零点起播）/ 上一首 / 下一首 / **全停**（向所有
  Cubase 工程窗口发停止 + 熄屏 OBS）。暂停/继续随走带状态自动启停。
- **播放配置（底部独立一行）**：配置目标（标蓝选中曲）、时长三态（绿=自动识别 / 黄=手动值 /
  红=未知需手填）、写入/重新识别、「设置」入口。时长写入目标=两列表中唯一标蓝项。

### 自动切换（两段式）

Cubase 播到头不会自己停（实测）：程序累计走带时钟已播时长 ≥ 工程时长 → 主动发停止键
（3 秒重试至多 3 次）→ 时钟断流 → 自动切播放列表下一首。中途手动停/暂停（已播不足）不切换。
时长来自 .cpr 定位条解析（启动全量探测+打开工程时重测），未设定位条的手填兜底。

### 键盘自动化 / 踩钉

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

### 设置页

保存即应用，写 `config.json` 持久化：

- **联动端口**：VJ / 键盘自动化的 loopMIDI 端口下拉（列当前在线端口），保存即热切换监听；选「无」停用该自动化（两联动都停用时 loopMIDI 仍会拉起——Cubase 工程时钟端口靠它承载）；已存设定当前不在场（设备未上电/端口改名）时显示「（当前不可用）」，不动它保存则保留原设定，改选其它项即替换；
- **VJ显示位置**：列本机显示器（Windows 枚举，不依赖 OBS 在线），选中即把
  OBS 节目画面全屏投影过去（换屏先关旧投影不留双份；屏名对不上 OBS 命名时按
  屏幕排列排名兜底；OBS 重连后自动恢复）；选「无」关闭投影；
- **VJ静音播放**：勾选=媒体源静音+关监听；不勾=开「监视器并输出」，声音进
  OBS「设置→音频→高级→监视输出设备」（默认=系统播放设备）；
- **目录**：Cubase 工程库变更触发热重扫，VJ 视频目录热生效；
- **行为开关**：自动切换工程 / 连续播放 / 保持软件前台 / 切换工程需确认 /
  **退出时关闭被控软件**（Cubase → OBS → loopMIDI 按序优雅关闭：Cubase 未保存
  确认框回车=保存；OBS 走 WM_CLOSE 不强杀；loopMIDI 先关后终止）。

## 配置参考

`config.json` 字段（exe 同目录，参照 `config.example.json`）：

| 段 | 字段 | 说明 |
|---|---|---|
| obs | host / port / password | obs-websocket 地址（OBS 内置服务器需启用，真配置在 `%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`） |
| obs | obsExe / autoStart | OBS 未运行时自动拉起 |
| obs | mediaInput / videoRoot | 媒体源名（缺源自动补建「舞台视频」）/ 视频库根目录 |
| obs | vjMute | VJ 静音播放：勾选=静音+关监听；不勾=开监听「监视器并输出」（设置页可改） |
| obs | projectorMonitor | VJ 显示位置屏名（空=不投影），设置页可改 |
| cubase | cubaseExe / projectsRoot / autoSave | Cubase 路径、工程库根目录（`<队伍>/<歌>/<歌>.cpr`）、切换时自动保存 |
| juno | inHint / outHint / patchCh / perfCh / deviceId | JUNO-DS MIDI 端口提示与通道 |
| ax09 | inHint / outHint / ch | AX-09 USB MIDI 端口提示与接收通道（默认 1；琴上 SHIFT+V-LINK×4 可查改） |
| pedal | deviceHint / bindings | 踩钉设备名提示、动作→CC 号 |
| autoAdvance | （顶层） | 「自动切换工程（播完自动切下一首）」勾选持久化 |
| autoPlay / topMost / switchConfirm | （顶层） | 连续播放 / 保持软件前台 / 切换工程需确认（默认开，设置页可改） |
| vjPortHint / kbPortHint | （顶层） | VJ 与键盘自动化的 loopMIDI 端口名提示（设置页可改，保存即热切换监听；空串=停用该联动） |
| exitCloseApps | （顶层） | 「退出时关闭被控软件（Cubase/OBS/loopMIDI）」勾选持久化 |

## 文件架构

```
├─ setlist_gui.py        主程序（Cube Setlist Manager）
├─ midi_bridge.py        MIDI 音符→OBS 视频桥（主程序内嵌 VJ 联动）
├─ cubase_ctrl.py        Cubase 切歌/走带/进程（先关后开 + 键注入 + 优雅退出）
├─ obs_ctrl.py / obs_ws.py   OBS websocket 控制（投影器/静音/熄屏/进程管理在此）
├─ advance.py            自动推进看门狗（两段式）
├─ kbd_auto.py / pedal.py    键盘音色自动化 / CC 踩钉
├─ dpi.py                DPI 感知 + 深色主题 token（darkify/flatten/dark_title）
├─ cpr_meta.py           .cpr 时长解析
├─ Cube Setlist Manager.spec / build.bat / installer.iss   打包 + 安装包
├─ test_bridge.py        桥自检（离线，无 OBS/loopMIDI）
├─ night_test.py         夜测编排器（gui 离线 55 项等分阶段）
├─ _render_check.py      离线渲染断言 + 四窗截图（落 _render\，可删可再生）
├─ e2e_test.py / probe_kb_pipeline.py / _probe_projector.py    真机分阶段 E2E / 键盘链路 / 投影屏名探针
├─ config.json / playlist.json   dist 同源恢复副本（重打包事故的恢复源）
├─ _bak_dist\            重打包前 dist 数据备份（确认新版正常后可删）
├─ dist\                 打包产物 + 安装包（exe 同目录放运行时真实数据，不入库）
└─ docs\                 设计审查报告 / 夜测报告 / M0 赛前验证清单
```

## 开发与构建

- 直接运行：`python setlist_gui.py`。
- 验证链：`python test_bridge.py`（离线自检）→ `python night_test.py gui`
  （离线 GUI 回归 55 项）→ `python _render_check.py`（版式断言+截图落
  `_render\`）；真机分阶段：`python e2e_test.py`。
- **重新打包一律用 `build.bat`（原生 cmd 或双击跑，Git Bash 调它会乱码）**：
  先备份 exe 目录两份 json → 打包 → 数据原样放回 → 编译安装包
  `dist\CubeSetlistManager-Setup-<版本>.exe`（版本取最近 git tag；未装
  [Inno Setup 6](https://jrsoftware.org/isinfo.php) 时跳过安装包只出绿色版），
  失败保留 .bak。
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
- 显示器枚举只用 user32 配置查询（EnumDisplayDevices/Monitors）；dxva2
  GetPhysicalMonitors* 在本机实测整屏黑死，勿引入
- 强杀 OBS 会留崩溃标记弹窗，别强杀；强杀 Cubase 同理
- OBS 媒体源无「启动不自动播放」开关：只要源里存着文件，随场景激活就会播；
  熄屏必须连文件一起清空才能根治下次启动续播
- obs-websocket 5.x 可开投影器（monitorIndex）但无关闭请求，只能按窗口标题
  （含「投影」/Projector）匹配后发 WM_CLOSE
- Cubase 窗口标题版本名随工程最后保存版本变（`Cubase Pro 工程 - 名` / `Cubase Version 13.0.40 工程 - 名`），
  匹配只认固定标记 `" 工程 - "`+后缀全等
- 无 console 的 exe 崩溃栈落 exe 同目录 `crash.log`（faulthandler）

## 版本历史

见 [CHANGELOG.md](CHANGELOG.md)。
