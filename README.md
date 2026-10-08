<div align="center">

<img src="docs/logo.png" width="128" alt="Cube Setlist Manager Logo"/>

# Cube Setlist Manager

**Cubase / Studio One 演出歌单控制台 · 移动端遥控 · 谱面自动翻页**

[![Release](https://img.shields.io/github/v/release/Hab1nA/CubeSetlistManager)](https://github.com/Hab1nA/CubeSetlistManager/releases/latest)
[![CI](https://github.com/Hab1nA/CubeSetlistManager/actions/workflows/ci.yml/badge.svg)](https://github.com/Hab1nA/CubeSetlistManager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/Windows-10%2F11-0078D6)
![Android](https://img.shields.io/badge/Android-8.0%2B-3DDC84)
![Python](https://img.shields.io/badge/Python-3.14-3776AB)
![Cubase](https://img.shields.io/badge/Cubase-13--15-673AB4)
![Studio One](https://img.shields.io/badge/Studio%20One-7%20Pro-0099D8)

[下载安装包](https://github.com/Hab1nA/CubeSetlistManager/releases/latest)
· [安卓 APP](https://github.com/Hab1nA/CubeSetlistManager/releases/latest)
· [演出验证清单](docs/演出验证清单.md)

</div>

---

演出自动化套装：两个完整版 + 一个简化版 + 一个安卓 APP（三个 Windows 程序
可并存安装）。主线 **Cube Setlist Manager**（Cubase / Studio One 双底座）——
素材库编排播放列表 → 一键切换 DAW 工程 → 播完自动切换下一首 → JUNO-DS 音色
自动切换 + CC 踩钉快捷键 + OBS 外屏视频联动 + 平板/手机遥控 + 谱面自动翻页；
**Cube Automator Studio One** 为去掉歌单/切歌的自动化简化版；**Cube Remote**
安卓 APP 负责遥控与谱面自动翻页。深色大字界面，演出暗场可读（深色标题栏，
Win11 圆角）。全程本地通信，不经互联网。

## 功能一览

- **歌单编排**：素材库双击/多选批量加入播放列表，搜索过滤，拖动顺序编排；
- **工程切换**：双击歌单一键切换 DAW 工程（Cubase 先关后开；S1 同窗换歌），有工程在开时弹确认防误触；
- **自动推进**：工程播完自动停止并切下一首（DAW 播到头不会自己停，Cubase 实测）；
- **音色自动切换**：DAW 发音符 → JUNO-DS / AX-09 Lucina 按工程映射切音色；
- **踩钉快捷键**：MIDI 踩钉 CC 绑定切歌/走带等动作，支持热插拔；
- **移动端遥控**：电脑开 Windows 热点，平板/手机连热点后用浏览器或
  **Cube Remote 安卓 APP** 遥控走带/切歌/全停（与桌面同权）；全程本地通信
  不经互联网；
- **谱面自动翻页**：DAW 发翻谱音符 → 平板 APP 按**本机翻页方法**
  （点按/双击/滑动/媒体键四通道）在谱面 App 自动翻页——手势与坐标由 APP
  组装，推送走持久连接 + Wi-Fi 低时延锁，翻谱链路不经浏览器（网页被冻结
  翻谱照常）；
- **VJ 视频联动**：走带跟随自动播/停 OBS 视频，熄屏一键黑场；
- **节目投影**：把 OBS 节目画面全屏投影到指定显示器（设置页选择，重连自动恢复）；
- **VJ 静音播放**：视频静音 + 关监听，或开「监视器并输出」出声，设置页切换；
- **启动自检**：loopMIDI / OBS / DAW（Cubase 或 Studio One）未运行自动拉起，缺什么补什么；
- **收尾可选**：退出时按序优雅关闭 DAW / OBS / loopMIDI。

## 产品组成

**安装版（推荐）**：Release 下载对应产品的安装包双击安装——按用户安装到
`%LOCALAPPDATA%\Programs\` 下各产品独立目录（免管理员），自动创建开始菜单/
桌面快捷方式，自带卸载器；三个安装包独立 AppId/目录，**可并存安装**；升级
直接装新版，`config.json` 等数据保留（Studio One 完整版首装预置 studioone
配置、简化版预置 automator 配置，已有配置不被覆盖）。

| 产品 | 端 / 底座 | 定位 |
|---|---|---|
| **Cube Setlist Manager** | Windows · Cubase | 演出主程序：歌单编排、切歌、走带、自动推进、音色/踩钉/VJ 全联动（本 README 主线） |
| **Cube Setlist Manager Studio One** | Windows · Studio One 7 Pro | 同上功能的 Studio One 版 |
| **Cube Automator Studio One** | Windows · Studio One 7 Pro | 自动化简化版：无歌单/切歌/走带遥控，保留其余全部自动化（见下节） |
| **Cube Remote** | Android 8.0+ | 遥控/翻谱 APP：控制页 + 谱面自动翻页（详见下文「移动端遥控 / 谱面自动翻页」） |

对应 Release 资产：`CubeSetlistManager-Cubase-Setup-x.y.z.exe` /
`CubeSetlistManager-StudioOne-Setup-x.y.z.exe` /
`CubeAutomator-StudioOne-Setup-x.y.z.exe` / `CubeRemote-vX.Y.apk`
（APP 亦可装好电脑端后连热点经网页「下载 APP」获取）；安装目录分别为
`%LOCALAPPDATA%\Programs\` 下的 `CubeSetlistManagerCubase` /
`CubeSetlistManagerStudioOne` / `CubeAutomatorStudioOne`。

`config.json`、`playlist.json` 放 exe 同目录（安装根目录）。

### 双底座（完整版：Cubase / Studio One）

同一套代码双后端（`daw_ctrl.py` 事实表）。底座由 config 顶层
`"daw": "cubase" | "studioone"` 决定；DAW 路径在 `dawSettings`
段（旧 `cubase` 段仍兼容读取）。S1 完整版已与 Cubase 版功能对齐：工程时长从
`.song`（ZIP/XML 容器）按事件终点自动解析（`song_meta.py`）、键盘自动化音色
槽按工程路径旁挂 JSON、退出保存框按窗口样式判别自动确认。走带时钟链路已
真机实证（S1 需以「**新建乐器**」类型建外部设备并勾 Send MIDI Clock，键盘类
设备进不了音轨输出）；每首歌需有输出到 VJ/Keyboard/Score 外部设备的乐器轨
（本机库内两首已接好）。

### Cube Automator Studio One（S1 简化版）

面向不需要歌单/切歌遥控的场景（与完整版并存安装，按需选用）：**去掉歌单/切歌/
走带遥控，保留其余全部自动化**——
VJ 视频跟随、键盘音色/移调/延音踏板（CC64）自动化、翻谱推送。入口程序
`automator_gui.py`（`app.lite=True`），与完整版共用全部底层模块。

- **自动识别当前工程**：轮询 S1 窗口标题（`Studio One - 歌名`），在工程库里
  精确匹配歌名、失败退唯一子串匹配（S1 标题取工程元数据名，可能与文件名不同），
  识别后自动加载该歌的音色槽——键盘自动化设置窗口无需手动选歌；
- **走带只看不控**：完整版的时钟跟随/三态显示/已播时长保留，但网页端
  `/cmd` 一律 403；启动不拉起、退出不关闭任何其它软件（OBS 只连接不启动，
  每 5 秒自动重连），loopMIDI 缺席时提示手动启动；
- **移动端双页面简化**：浏览器页=「简化版无遥控」提示+下载 APP 按钮；
  APP 页=完整版设置面板直接作为主页（谱面翻页功能不变）；
- S1 工程侧需为每首歌把 3 条乐器轨分别接到 `VJ Automation` /
  `Keyboard Automation` / `Score Automation` 外部设备（新建乐器类型，参考
  调研文档 §九）。

## 环境要求

- Windows 10/11（深色标题栏依赖 Win11 圆角特性）；
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)（虚拟 MIDI 端口）；
- OBS Studio（obs-websocket 5.x 已内置于 OBS 28+，需在「工具→WebSocket 服务器设置」启用）；
- DAW 按所装产品二选一：Steinberg Cubase（实测 Cubase 15 / Pro 13.0.40 窗口标题均兼容）
  或 PreSonus Studio One 7 Pro（实测 7.2）；
- 硬件可选：Roland JUNO-DS88、Roland AX-09 Lucina（仅 USB 可接收）、MIDI 踩钉；
- 移动端遥控（可选）：安卓平板/手机 8.0+ 安装 Cube Remote，或任意现代浏览器。

### 开发 / 构建

- Python **3.14.x**（实测版本）。第三方运行时依赖两项：UI 主题库 sv-ttk
  （`py -m pip install sv_ttk`）——全套控件迁移 ttk 后以 sv-ttk dark 换取
  Win11 原生深色观感（演出暗场远距离可读）；BLE MIDI 后端 pywinrt 系
  （`py -m pip install winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections
  winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Midi winrt-Windows.Storage.Streams`，
  键盘自动化走蓝牙 MIDI 用，包缺失自动降级纯 winmm）；MIDI/OBS 协议仍为
  标准库手写（python-rtmidi 在 3.14 下 import 即崩，协议层零依赖的初衷不变）；
- 安卓 APP：Kotlin + Gradle（`mobile/` 工程，`gradlew assembleDebug`）；
- 改码后重打包：`build.bat`（备份 dist 真实数据 → PyInstaller → 还原；装了
  Inno Setup 6 会顺带出按用户安装包并拷入最新 APK，版本取最近 git tag）；
- 离线自检：`py tests\test_bridge.py`，或 `py -m pytest tests\test_bridge.py`
  （CI 每次 push 自动跑）；
- 真机 E2E：`py -u tests\e2e_test.py <phase>`（库根默认本机路径，换机设环境变量
  `CUBE_PROJECTS_ROOT` 覆盖）。

## 快速开始

1. **首次配置**：把 `config.example.json` 复制为 exe 同目录的 `config.json`，
   改 OBS 地址/密码、DAW 路径、工程库与视频目录；
2. **准备素材**：工程按 `<队伍>/<歌>/<歌>.cpr`（Studio One 为 `.song`）放进
   工程库根目录，视频放进 VJ 视频目录；歌名来源=工程文件名；
3. **启动主程序**：启动自检会自动拉起三大后端并写日志——
   - **loopMIDI**：端口不在就拉起程序并等虚拟端口就绪；
   - **OBS**：进程不在就自动启动（含等 WebSocket 就绪，至多 30 秒）；
   - **DAW**：进程不在就冷启动（Cubase 约 30 秒到 Hub，之后切歌走单实例转交；
     Studio One 为同实例同窗换歌，约 1–2 秒）。

   任一后端拉起失败只记日志，不阻断其余功能（缺什么补什么）。
4. **移动端遥控/自动翻谱**：设置页勾「启用移动端遥控」→ 平板/手机连热点 →
   浏览器遥控即开即用；翻谱则在平板安装 Cube Remote APP（网页右上角
   「下载 APP」或 Release 下载），在 APP 内完成认领/翻页方法/无障碍授权；
5. **音色/踩钉绑定**：见下文[键盘自动化](#键盘自动化--踩钉)与[踩钉](#踩钉)两节。

## 界面与操作

### 主界面

- **NOW/NEXT 横幅**：当前工程大字 + 下一首 + 剩余时间，演出一眼可读；
  下方**全宽进度条**随走带活跃时长推进（回零归零、无工程隐藏）。
- **播放状态**：控件栏最左「当前状态：未在播放/播放中/已暂停」，随状态
  变色；移动端（网页/APP）状态行在 NOW 歌名右侧，切歌期间显示「切换中…」，
  NOW/NEXT 保持切歌前画面不跳动。
- **素材库**：双击加入播放列表；支持 Ctrl/Shift 多选批量加入；右上搜索框过滤。
- **播放列表**：双击切换工程；**有工程在开时**默认弹确认防误触（设置页
  「切换工程需确认」可关），**无打开工程时双击直接打开、不弹确认**；
  当前行深色高亮，"←" 标记当前曲；编排按钮随选中启停。
- **监控栏**：VJ 自动化与键盘自动化两栏同构，各四格——端口名称/端口状态 +
  （VJ：OBS 状态/走带跟随 ｜ 键盘：音色映射/最近切换），状态格带 ● 圆点 +
  绿/黄/红语义色；走带跟随格播放中会显示当前视频名。
- **播放组**：开始=回零从头播 / 暂停 / 继续 / 回零（=停止+回零，停在零点、
  已播计时归零，之后「继续」从零点起播）/ 上一首 / 下一首 / **全停**（向所有
  DAW 工程窗口发停止 + 熄屏 OBS）。暂停/继续随走带状态自动启停。
- **播放配置（底部独立一行）**：配置目标（标蓝选中曲）、时长三态（绿=自动识别 / 黄=手动值 /
  红=未知需手填）、写入/重新识别、「设置」入口。时长写入目标=两列表中唯一标蓝项。

### 自动切换（两段式）

DAW 播到头不会自己停（Cubase 实测）：程序累计走带时钟已播时长 ≥ 工程时长 → 主动发停止键
（3 秒重试至多 3 次）→ 时钟断流 → 自动切播放列表下一首。中途手动停/暂停（已播不足）不切换。
时长来自工程文件解析（Cubase .cpr 定位条 / Studio One .song 事件终点；启动全量
探测+打开工程时重测），未识别的手填兜底。

### 键盘自动化 / 踩钉

- **键盘自动化**：DAW 向 loopMIDI「Keyboard Automation」端口发音符，按当前工程
  映射切音色——C4-A4(60-69) → **JUNO-DS**，C5-F5(72-77) → **AX-09 Lucina**。
  映射录制=窗口里点「录制」，JUNO 在琴上按 Favorite、AX-09 在琴上选中音色
  （**先把 AX-09 的 MIDI 设置 Bn 开为 ON**：SHIFT+V-LINK 连按 5 次，改完 SHIFT+WRITE），
  软件从各自 MIDI 输入捕获 BS/PC，存工程文件夹 `keyboard_automation.json`
  （"slots"=JUNO 段，"ax"=AX-09 段）。AX-09 只能经 USB 接收（DIN 口 OUT-only）；
  BS+PC 直发 144 个常规音色（MSB 恒 87；1-128 号 LSB=0、129-144 号 LSB=1），
  Favorite/Special Tone 不在 MIDI 映射表里。
  琴侧 MIDI 口在窗口「MIDI 设备」下拉里选（收发两向同名同选，在线枚举
  零写死；同名多口以（第N个）区分）。蓝牙无线接法（BLE MIDI，如琴上插
  CME WIDI U-Host 连电脑内置蓝牙）：BLE 端点以「名（BLE）」出现在同一
  下拉里，选中即收发全走 BLE（`midi_ble.py`，走 Windows WinRT，winmm
  看不到 BLE 端点）；有线的同名 USB 口恒排在前，手写宽 hint（如 JUNO）
  优先落 USB。
- **踩钉**：MIDI CC 上升沿触发（瞬时/开关踩钉通吃）；蓝牙键盘型踩钉（HID 按键）
  同套学习。可学动作六项：**暂停/继续**（按播放状态一键切换）、开始、回零、
  全停、上一首、下一首；手法两种：单击、快踩两下（双踩）。窗口里点「学习」
  踩一下即完成绑定，存 `config.json` 的 pedal 段；支持热插拔（断开每 10 秒
  自动重连）。
- **踩钉冗余路·平板转发**（0.12.2 新增）：踏板 USB-C 有线连平板 → Cube Remote
  APP 经 WiFi 转发电脑（`/pedal/event` 端点），与蓝牙直连互为冗余、失效域解耦
  （10-02 演出射频全灭事故的踩钉侧整改）。APP 侧「踩钉转发」开关+电脑踩钉控制页
  「允许平板转发踩钉」双开生效；蓝牙学到的绑定对转发路自动生效；电脑端关闭时
  按键透传给平板前台 App；断网丢包不重放陈旧走带动作，心跳 5 秒、失联 15 秒
  状态行警示，电脑端恢复后自动接回。

### 移动端遥控 / 谱面自动翻页

- **网页遥控**（即开即用）：平板/手机连热点后访问 `http://<热点IP>:8765`，
  与桌面同权控制走带/切歌/全停；播放状态行 + 进度条随设备尺寸自适应
  （手机竖屏单列紧凑版）。
- **Cube Remote APP**（推荐）：控制页与网页同源，另承载翻谱设置——认领设备、
  翻页方法四选一（点按/双击/滑动/媒体键）、谱面 App 指定、测试翻页（自动切回
  谱面 App 并回传诊断）；DAW 发翻谱音符即按本机方法自动翻页。
- **翻谱音符协议**：C2/C#2（36/37）=上一/下一页，C4–A4=选设备槽位；推送语义
  指令到各设备，互不阻塞；端口全局统一（默认 8766，APP 内可改需两端同步）。
- 首次配置顺序：APP 控制页认领设备 → 选翻页方法 → 手机系统设置里开启无障碍授权。

### 设置页

保存即应用，写 `config.json` 持久化：

- **联动端口**：VJ / 键盘自动化 / 翻谱信号的 loopMIDI 端口下拉（列当前在线端口），保存即热切换监听；选「无」停用该自动化（两联动都停用时 loopMIDI 仍会拉起——DAW 工程时钟端口靠它承载）；已存设定当前不在场（设备未上电/端口改名）时显示「（当前不可用）」，不动它保存则保留原设定，改选其它项即替换；
- **移动端遥控**：总开关（开=自动开热点→起网页服务→开翻谱端口，关=全停；
  热点是本程序开的退出时自动关掉）、网页端口（默认 8765）、APP 页面端口
  （默认 8767）、翻谱接收端口（默认 8766，全局统一）、热点状态行（开/关、
  SSID、密码、本机 IP）。平板/手机连热点后：浏览器访问 `http://<状态行IP>:8765`
  即网页遥控；Cube Remote APP 连接地址 `http://<IP>:8767`——遥控走带/切歌/
  全停，「设置」面板认领翻谱设备、选翻页方法、试翻一页、下载 APK。首次监听
  Windows 会弹防火墙放行，允许一次即可；
- **VJ显示位置**：列本机显示器（Windows 枚举，不依赖 OBS 在线），选中即把
  OBS 节目画面全屏投影过去（换屏先关旧投影不留双份；屏名对不上 OBS 命名时按
  屏幕排列排名兜底；OBS 重连后自动恢复）；选「无」关闭投影；
- **VJ静音播放**：勾选=媒体源静音+关监听；不勾=开「监视器并输出」，声音进
  OBS「设置→音频→高级→监视输出设备」（默认=系统播放设备）；
- **目录**：DAW 工程库变更触发热重扫，VJ 视频目录热生效（均支持文件夹
  选择对话框）；
- **行为开关**：自动切换工程 / 连续播放 / 保持软件前台 / 切换工程需确认 /
  **退出时关闭被控软件**（DAW → OBS → loopMIDI 按序优雅关闭：Cubase 未保存
  确认框回车=保存、S1 保存框默认钮=「是」=保存；OBS 走 WM_CLOSE 不强杀；
  loopMIDI 先关后终止）。

## 配置参考

`config.json` 字段（exe 同目录，参照 `config.example.json`）：

| 段 | 字段 | 说明 |
|---|---|---|
| obs | host / port / password | obs-websocket 地址（OBS 内置服务器需启用，真配置在 `%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json`） |
| obs | obsExe / autoStart | OBS 未运行时自动拉起 |
| obs | mediaInput / videoRoot | 媒体源名（缺源自动补建「舞台视频」）/ 视频库根目录 |
| obs | vjMute | VJ 静音播放：勾选=静音+关监听；不勾=开监听「监视器并输出」（设置页可改，默认勾选） |
| obs | projectorMonitor | VJ 显示位置屏名（空=不投影），设置页可改 |
| daw | （顶层） | 底座选择：`cubase`（默认）或 `studioone` |
| dawSettings | dawExe / projectsRoot / autoSave | DAW 路径、工程库根目录（`<队伍>/<歌>/<歌>.cpr`，Studio One 为 `.song`）、切换时自动保存 |
| juno | inHint / outHint / dev / patchCh / perfCh / deviceId | JUNO-DS MIDI 端口提示与通道；dev=同名端口序号（两台同型号无线 MIDI 盒时在「键盘自动化」窗下拉选择，0 基，一般勿手改） |
| ax09 | inHint / outHint / ch / dev | AX-09 USB MIDI 端口提示与接收通道（默认 1；琴上 SHIFT+V-LINK×4 可查改）；dev 同 juno |
| pedal | deviceHint / bindings / hidBindings / hidDeviceHint / intercept / gestures / doubleWindow | 踩钉：MIDI 设备名提示与动作→CC、HID 动作→虚拟键码、所选设备身份与拦截开关、动作→手势（single/double，「学习」时踩出即自动分类）与双踩窗（默认 0.35s） |
| pedal | remoteEnabled | 允许平板转发踩钉（踩钉控制页开关持久化；APP 侧另有「踩钉转发」开关，双开生效） |
| webRemote | enabled / serverPort / appPort / taskerPort | 移动端遥控总开关（设置页可改，保存即整套起停）、网页服务端口（8765）、APP 页面端口（8767）、翻谱接收端口（8766） |
| webRemote | midiIn / devices | 翻谱信号 loopMIDI 端口名；已认领翻谱设备表（槽位/名字/IP/启停/分辨率——由 APP 网页认领自动维护，翻页方法存 APP 本机，勿手改） |
| autoAdvance | （顶层） | 「自动切换工程（播完自动切下一首）」勾选持久化（默认开） |
| autoPlay / topMost / switchConfirm | （顶层） | 连续播放 / 保持软件前台 / 切换工程需确认（默认开，设置页可改） |
| vjPortHint / kbPortHint | （顶层） | VJ 与键盘自动化的 loopMIDI 端口名提示（设置页可改，保存即热切换监听；空串=停用该联动） |
| clockPortHint | （顶层） | MIDI 时钟监听端口（设置页「时钟端口名称」，独立于 VJ 音符口；DAW 侧需把时钟发到该端口——Cubase 工程设置的时钟发送端口、S1 外部设备勾 Send MIDI Clock；空串=停用，走带三态/已播/自动推进不可用） |
| exitCloseApps | （顶层） | 「退出时关闭被控软件（DAW/OBS/loopMIDI）」勾选持久化 |

## 文件架构

```
├─ setlist_gui.py        主程序（Cube Setlist Manager）
├─ automator_gui.py      简化版主程序（Cube Automator Studio One，app.lite 分流共用底层模块）
├─ midi_bridge.py        MIDI 音符→OBS 视频桥（主程序内嵌 VJ 联动）
├─ daw_ctrl.py           DAW 底座控制器（Cubase/Studio One 事实表双后端：切歌/走带/进程）
├─ obs_ctrl.py / obs_ws.py   OBS websocket 控制（投影器/静音/熄屏/进程管理在此）
├─ advance.py            自动推进看门狗（两段式）
├─ dpi.py                DPI 感知 + 主题层（apply_theme 集中命名 style/paint_tree/bind_hint/dark_title）
├─ ui_text.py            跨窗复用文案唯一来源（配套 docs/UI文案与信息设计规范.md）
├─ stallguard.py         主线程停摆黑匣子（看门狗 stall.log + 栈哨兵 stall_stack.log）+ 指令队列治理
├─ kbd_auto.py / pedal.py    键盘音色自动化 / CC+HID 踩钉
├─ web_remote.py / hotspot.py   移动端遥控（网页服务+语义推送+设备表）/ Windows 热点（WinRT）
├─ cpr_meta.py / song_meta.py   .cpr 与 .song 工程时长解析（RIFF 定位条 / ZIP+XML 事件终点）
├─ mobile\               Cube Remote 安卓工程（Kotlin：WebView 壳+无障碍手势+NanoHTTPD 接收器+踩钉转发）
├─ app.ico               应用图标（exe 内嵌 + 窗口/任务栏）
├─ Cube Setlist Manager Cubase.spec / Cube Setlist Manager Studio One.spec / Cube Automator Studio One.spec / build.bat / installer.iss   打包 + 安装包（三产品各一份 spec、一个安装包，命名按底座对称）
├─ tests\                test_bridge（离线自检）/ test_stability / test_pedal_wire（线级黑盒）/ test_dialog_drain / test_manual_adopt / pedal_sim / e2e_test（真机分阶段）
├─ tools\                _render_check（离线渲染断言+截图）/ probe_* 真机探针 / _snapshot_bak（打包数据快照）
├─ config.example.json / config.studioone.json / config.automator.json   各产品预置配置样例
├─ config.json / playlist.json   仓库根副本（重打包事故的恢复源）
├─ _bak_dist\            重打包前 dist 数据备份（确认新版正常后可删）
├─ dist\                 打包产物 + 安装包（exe 同目录放运行时真实数据，不入库）
└─ docs\                 演出验证清单 / 调研文档（舞台射频频段·JUNO 延音·苹果端迁移）/ logo
```

## 开发与构建

- 直接运行：`python setlist_gui.py`。
- 验证链：`py -m pytest tests\`（离线自检）→ `py tools\_render_check.py`（版式断言+截图落
  `_render\`）；真机分阶段：`py tests\e2e_test.py`（Cubase）与
  `s1_*` 五阶段（Studio One，库根 `CUBE_S1_PROJECTS_ROOT`）。
- **重新打包一律用 `build.bat`（原生 cmd 或双击跑，Git Bash 调它会乱码）**：
  先备份 exe 目录两份 json → 打包 → 数据原样放回 → 拷入最新 APK → 编译安装包
  `CubeSetlistManager-Cubase-Setup-<版本>.exe` /
  `CubeSetlistManager-StudioOne-Setup-<版本>.exe` /
  `CubeAutomator-StudioOne-Setup-<版本>.exe`（版本取最近 git tag；未装
  [Inno Setup 6](https://jrsoftware.org/isinfo.php) 时跳过安装包只出绿色版），
  失败保留 .bak。
  **勿裸跑 `pyinstaller --noconfirm`**：它会先清空版本文件夹，dist 里是
  运行时真实数据（歌单/时长/设置），历史上因此丢过数据。杀毒偶发锁
  `_internal` 里的 DLL：等几秒删掉版本文件夹重跑即可。
- 安卓 APP：`cd mobile` 后 `gradlew assembleDebug`（产物由 build.bat 自动拷为
  `CubeRemote.apk` 随 dist 与安装包分发）。
- onedir 而非 onefile（%TEMP% 清理失败会弹窗）。

## 演出前

看 **[docs/演出验证清单.md](docs/演出验证清单.md)**：赛前自检步骤 + 现场故障恢复路径 + 待真机验证项。

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
- 无 console 的 exe 崩溃栈落 exe 同目录 `crash.log`（faulthandler）；主线程停摆现场与
  全线程栈分别落 `stall.log` / `stall_stack.log`（栈哨兵按窗连拍，卡点一次停摆即裁决）
- WinRT 热点 API 实名（PS 5.1 投影实测）：能力查询=`GetTetheringCapabilityFromConnectionProfile`
  （不是文档里的 `TetheringCapability`，那个在投影类型上不存在）；热点需本机有
  已连接的网络作共享来源；热点空闲一段时间会被 Windows 自动关闭；HTTP 服务
  首次监听会弹防火墙放行（一次即可）

## 版本历史

见 [CHANGELOG.md](CHANGELOG.md)，图文版见
[GitHub Releases](https://github.com/Hab1nA/CubeSetlistManager/releases)。

## License

[MIT](LICENSE)
