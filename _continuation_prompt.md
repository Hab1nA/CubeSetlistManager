# MIDI 时钟问题隔夜修复 — 续跑提示（给重启后新起的 zcode 代理）

## 背景速读
读记忆文件：`C:\Users\XKZ\.zcode\cli\memories\projects\cubesetlistmanager-03f3209be9dfa9cc\memory\cubase-setlist-project-status.md` 最后三节（S1 音符输出判定 / 对称命名+工作区分层 / 时钟修复新路线），以及仓库 `docs/StudioOne迁移调研.md` §八。

## 已确认的事实（不要重复验证）
1. 本机 Windows 25H2 (26200.9550)，Windows MIDI Services（midisrv）已随 CFR 滚装激活。
2. loopMIDI 1.0.16.27 的四个虚拟口（VJ Automation / Keyboard Automation / Score Automation / +Rubix24 硬件）在 WinMM 层枚举、打开、**自环收发全部正常**。
3. **两个 DAW（Cubase 15 与 Studio One 7.2.3）的 MIDI 输出都到不了虚拟口**：Cubase 播放配置过时钟的《優しい彗星》、S1 播放带音符的 VJ 轨，WinMM 监听器全程挂—均为零。WinMM 自环正常=驱动/口/监听侧无责。
4. 已无效的手段：midisrv 重启（UAC 已批过）、loopMIDI 重启、S1 重启×N、官方 SDK 1.0.16-rc.3.7 安装+卸载、原生环回端点对（CubeClock，已随卸载移除）、走带同步按钮。
5. 已写入 HKCU `Software\Microsoft\Windows NT\CurrentVersion\Drivers32` 的 `UseLegacyMidi`=1（DWORD）——**重启后生效，强制 WinMM MIDI 走老栈直连**。
6. SDK 已卸载（runtime/tools 均已移除；`C:\Program Files\Windows MIDI Services\` 已不存在）。winget 包 `Microsoft.MIDI.SDK` 可随时重装。

## 重启后要做的验证（按顺序）
1. `py -c "import sys; sys.path.insert(0,'.'); import midi_bridge as mb; print(mb._in_devices())"` —— 确认四口仍在。
2. WinMM 自环：Python 向 VJ Automation 输出口发 note-on，同口输入监听应收 1 条（对照基准，此前一直正常）。
3. 启动 S1（若安全模式弹窗：选「正常启动」→开始；若弹「MIDI配置已改变」：选「是」重连）。
4. 打开《lingo5.16演出》，VJ 轨画一个音符，光标置音符前，空格播放。
5. 全端口监听 15 秒：
   - **收到** → UseLegacyMidi 修复生效！立即：a) 验证 S1 时钟（probe 或直接听 F8）；b) 继续 0fa0dd9 之后的子项目开发（见下）。
   - **仍零** → S1 即便走老栈也不发 → S1 侧缺陷实锤 → 按 `tools/fix_s1_midi_clock.bat` 里的思路重启 midisrv（需管理员，可通过 Start-Process -Verb RunAs 弹 UAC，但用户睡觉无人点）→ 改为向 PreSonus 论坛 + microsoft/MIDI GitHub 写 bug 报告（证据链在本文件与调研文档 §八），并把子项目触发方式定为「时间线+踩钉手动」（不依赖 S1 MIDI 输出），继续开发。

## 子项目（简化版自动化发生器）开发要点
用户已拍板：同仓库新入口、纯自动化发生器（不做切歌/走带/NOW）、MIDI 轨触发设计（环境修复后自动生效）+ 需要时的应用自触发兜底。
- 复用：kbd_auto（ToneSwitcher 音色/submit_shift 移调）、pedal（踩钉）、web_remote.push_turn（翻谱推送平板）、daw_ctrl 的事实表模式。
- 新增：每歌预设（音色 PC/BS+移调半音+翻谱点 mm:ss）的 JSON 结构与加载；MIDI 口监听分发（Keyboard Automation 口=音色 60-69/移调 36-39；Score Automation 口=翻谱 C2/C#2）；无 UI 的最小状态窗。
- 验证：S1 播放带音符的 VJ 轨 → 全链自动触发；WinMM 自环做单元回归。

## 环境备注
- 用户在睡觉，不会点 UAC——所有需要管理员的操作改为「写入 startup 文件夹的 .bat，用户醒来登录时自动触发」。
- 重启由 `shutdown /r /t 60`（本命令链）发起；S1 若有未保存修改会被丢弃（仅测试性绘图，已与用户确认可弃）。
- loopMIDI 开机自启（HKCU Run）✓；CSM 两个底座应用不会自启（验证时手动或脚本启动）。
