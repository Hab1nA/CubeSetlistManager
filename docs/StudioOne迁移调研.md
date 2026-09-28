# Studio One 7 底座迁移调研

调研日期：2026-09-26。
问题：电脑端软件底座从 Cubase 15 换成 Studio One 7（另立版本，非替换），各 Cubase 相关
功能迁移是否困难、工作量大不大。调研方式：代码耦合面盘点 + S1 7 能力核查（网络证据
标注强度，未真机验证项单列）。

## 一、结论摘要

1. **可行，总体工作量中等偏小：估算一周日历工作量（含真机校准），且多数组件零改动。**
   Cubase 耦合集中在 4 个文件（cubase_ctrl/cpr_meta/advance/TransportSync），其余
   （OBS 联动、热点、网页遥控、移动端、键盘音色自动化、踩钉、打包）全部 DAW 无关。
2. 五个关键发现：
   - **走带跟随可平移**：S1 外部设备设置可勾 Send MIDI Clock（主时钟，含 Start/Stop/SPP），
     指向 loopMIDI 端口即复刻现机制，代码近零改动；
   - **走带控制有更优通道**：S1 原生支持走带 MIDI Learn 与 Mackie Control 控制面——
     可以用 loopMIDI 发 MIDI 控走带，**替代 SendInput 键注入**（不吃焦点、无 IME 干扰、
     不受弹窗遮挡），且 Mackie 控制面会回传播放位置，有机会顺带替换"时长解析+播到头
     自己停"两套 hack；
   - **S1 6.6+ 有官方 JavaScript 脚本引擎**（getHostAPI/控制台命令，Macro Toolbar 管理），
     Cubase 没有的文明通道，可用来导出工程时长等宿主内信息；
   - **多 Song 同开=多独立窗口**（Pro 版功能，Ctrl+Tab/Window 菜单切换，无标签页）——
     与现有窗口枚举模型同构；且**无 Hub/空框架环节**，Cubase 那套"先关后开+模态放行+
     补发+退出再冷启动"的复杂状态机在 S1 上很可能大幅缩短（切歌或许只需打开新窗口），
     **待真机验证**；
   - **.song 是私有容器**，无公开规格、无现成 Python 解析库——时长获取改走 S1 脚本导出
     JSON 或沿用现有手填兜底。
3. 主要风险不在代码在**校准**：S1 的弹窗序列、保存确认、CLI 打开的单实例转交行为、
   窗口标题格式都是未知量——用既有的 M0 真机流程逐项闭（与 Cubase 当年同量级，
   但踩坑清单更短）。

## 二、现有 Cubase 耦合面盘点（迁移面）

| 模块 | 行数 | Cubase 耦合点 | 迁移定性 |
|---|---|---|---|
| cubase_ctrl.py | 422 | 进程拉起/CLI 转交、窗口类与标题匹配、激活模型（工程激活≠聚焦）、WM_CLOSE 关闭、弹窗识别+仿人回车、SendInput 走带键、focus 管理 | **主战场**：基础设施（进程/窗口/SendInput/弹窗排水）可复用，Cubase 事实表换成 S1 事实表 |
| cpr_meta.py | 57 | .cpr（RIF2/RIFF）时长解析 | .song 私有格式，改脚本导出或手填 |
| advance.py | 111 | 播完自动推进（时钟活跃×时长阈值+主动停） | 逻辑 DAW 无关，仅停止键序换表 |
| midi_bridge.py TransportSync | 部分 | MIDI 时钟脉冲断流=暂停判定 | S1 同样可发时钟，**近零改动** |
| setlist_gui.py | 82 处引用 | 调用面+projName 标题正则+config cubase 段 | 引入 daw 后端抽象后改动很小 |
| e2e_test/night_test | — | Cubase 窗口/弹窗 fixtures | 移植一套 S1 fixtures |
| 其余全部 | ~4500 | 无 | **零改动** |

## 三、逐项能力对照

| 能力 | Cubase 现实现 | Studio One 7 对应 | 证据强度 | 难度 |
|---|---|---|---|---|
| 冷启动 | CLI 带路径+引号，48s 含弹窗序列 | `"Studio One.exe" "x.song"`（文件关联机制，无官方开关文档） | 社区经验 | 低，转交行为待验 |
| 切歌 | 先关后开+模态放行+补发+Hub 兜底（30s+） | 多 Song 同开独立窗口：打开新 Song 窗口即可，旧 Song 留场 | 官方手册（多 Song=Pro 版功能） | **可能反而更简单**，待验 |
| 窗口/状态识别 | SteinbergWindowClass+「工程 - 」标记 | 每窗口一 Song；标题格式待真机采样（教训沿用：只认固定标记） | 待验 | 低 |
| 走带键注入 | SendInput 空格/NUM0/NUM1/ESC 序列 | 默认 Space 切换、NumEnter 回零、Num* 录制；**全可自定义**；走带还可 **MIDI Learn** | 官方文档 | 低（换键序表或零改键映射对齐） |
| 走带跟随 | 工程发 MIDI 时钟→loopMIDI→脉冲判活 | External Devices→Send MIDI Clock（主时钟+Start/Stop+SPP） | 官方功能在册 | 极低（配置级） |
| 播放状态观测 | 无客观手段（ASIO 盲区），靠时钟推断 | 时钟同上；**Mackie Control 回传播放位置/状态**（升级机会） | Mackie 为 S1 标准控制面 | 中（新通道，需 spike） |
| 工程时长 | .cpr RIFF 解析 Cycle Right−Left | .song 私有容器；替代=S1 JS 脚本（getHostAPI）导出时长 JSON / 手填兜底（已有 UI） | 脚本引擎官方在册（6.6+），API 细节社区级 | 低-中 |
| 弹窗处理 | 排除法+仿人回车（未找到端口/保存/激活…） | 同技术换 S1 弹窗集（保存确认/缺内容等），需真机校准 | 待验 | 中（校准工作量） |
| 激活模型 | 显式激活≠聚焦（最深的坑） | 多窗口多 Song，无"激活"概念？打开新窗口即前台——**待真机确认语义** | 待验 | 待验 |
| 音色自动化/kbd_auto | loopMIDI 端口+音色表 | 端口机制不变，S1 里挂 MIDI 轨发同样音符即可 | 机制通用 | 零 |

## 四、待真机验证清单（M0 式，一步一测）

1. CLI 打开：S1 运行中再传 .song 路径=转交当前实例还是新实例？多开行为？
2. 窗口标题格式采样（版本号/歌名顺序/保存后变化）；
3. 切歌实操序列：开新 Song 时旧 Song 是否弹保存确认、耗时、有无模态阻塞；
4. 弹窗全集采样：缺失插件/内容、首次启动向导、音频接口切换等；
5. Send MIDI Clock 到 loopMIDI：脉冲速率/断流行为与 Cubase 是否一致（TransportSync 判活
   阈值是否沿用）；时钟是否只送单端口（多设备场景）；
6. 走带 MIDI Learn：loopMIDI 端口学 Play/Stop/回零键可行性（替代键注入的决策依据）；
7. Mackie Control spike：注册虚拟控制面后回传内容（位置/状态）是否够替代时钟+时长推断；
8. JS 脚本导出时长：getHostAPI 能否读到 Song 长度并写文件。

## 五、架构建议

- **做 daw 后端抽象，不做复制粘贴分叉**：`daw: "cubase" | "studioone"` 进 config；
  抽象接口约六个方法（launch/open_song/current_title/transport(seq|midi)/close/drain_dialogs）。
  第二个后端恰好验证抽象面是否正确，避免两个 exe 两套代码漂移。
- 走带通道优先级建议：先键注入（最快跑通，键序表换掉即可）→ 再评估 MIDI Learn/Mackie
  （更稳，摆脱焦点与弹窗依赖）→ TransportSync 时钟机制保持不动。
- 时长：先手填兜底上线 → S1 脚本导出作为增强。
- 版本策略：同一代码库双后端、同一安装包；发布时按用户底座选 config 默认值。

## 六、工作量汇总（估算）

| 项 | 估计 |
|---|---|
| daw 抽象层抽取+config 化（原 Cubase 逻辑不动） | 0.5-1 天 |
| S1 后端实现（进程/窗口/弹窗/键序） | 2-3 天（含真机校准） |
| 时长方案（手填沿用+脚本导出增强） | 0.5 天 |
| 走带跟随迁移（时钟配置+验证） | 0.5 天 |
| 测试移植（e2e/night 的 S1 fixtures） | 1 天 |
| Mackie/MIDI Learn 可选升级 spike | 1-2 天（可选，不阻塞上线） |
| **合计（不含可选项）** | **约 5-6 个工作日** |

对比参照：Cubase 底座当年从零到稳定历时多轮真机（激活模型/弹窗风暴/Hub 兜底都是踩出来
的）。S1 的已知未知量更少（无 Hub、多窗口直切），但"未知量必须真机闭"的纪律不变。

## 七、参考链接

- S1 MIDI 时钟（External Devices / Send MIDI Clock）: https://beatkitchen.io 及 Arturia/Elektron 社区实操帖
- S1 走带 MIDI Learn / 控制器指派: https://support.presonus.com
- S1 走带快捷键（Sound on Sound 讲座）: https://www.soundonsound.com
- S1 脚本引擎（6.6+ JS getHostAPI，社区文档）: https://s1toolbox.com/macrodocumentation
- S1 多 Song 同开（Reference Manual）: https://www.scribd.com （Studio One Reference Manual）
- .song 私有格式（无公开规格佐证）: https://studiooneforum.com/

## 八、M0 真机校准结果（2026-09-27 凌晨，全部实测）

> 实施记录见 CHANGELOG 0.11.0-prerelease.1/2 与 git log（a93adf4、721d0cf、836fce6）。
> 本节为§四清单的逐项对账 + 新增事实。

### 功能状态矩阵（迁移交付现状一览）

| 功能 | Cubase 版 | S1 版 | 未实现原因/难点 |
|---|---|---|---|
| 素材库/播放列表/编排/搜索 | ✓ | ✓ | 同一代码，零差异 |
| 切歌（确认/排队/防重/恢复） | ✓（30-48s） | ✓（**2s**） | S1 同窗换歌远比 Cubase 简单 |
| 走带键（开始/暂停/继续/回零/全停） | ✓ | ✓ | 键序表真机实测 |
| 走带时钟→VJ 跟随/播放状态/进度条 | ✓ | ✓（2026-09-28 实证，§九） | 前提=S1 外部设备必须「新建乐器」类型（键盘类进不了音轨输出） |
| NOW 识别/防错警报 | ✓ | ✓ | 标题格式已校准 |
| 工程时长自动解析 | ✓（.cpr） | ✗ 手填 | `.song` 私有容器无公开规格；后续 JS 脚本导出可解（⑧） |
| 键盘自动化音色切换 | ✓ | ✗ | 音色槽存于工程旁 JSON，由 .cpr 解析 → `.song` 无解析 |
| 退出连带关闭 | ✓ | 部分 | WM_CLOSE 退出可行；未保存时 S1 退出保存框标题=`Studio One`（正文才有"保存"），标题匹配的自动确认失效，需人工点一次 |

> 结论：S1 版除依赖「.song 解析」的功能（时长手填兜底）外全部可用。

### 校准闭环（①②③⑤ 已闭）

| 项 | 实测结论 |
|---|---|
| ① CLI 打开 | **同实例、同窗口换歌**：标题原地翻转，旧 Song 不保留、不新开实例（Pro 的多 Song 同开默认不发生）。ShellExecute=42 正常提交 |
| ② 窗口标题 | `Studio One - <歌名>`（歌名在标记**后**，与推断相反）；脏工程=歌名尾加 `*`；Start 页=光杆 `Studio One`（不含标记，不误判） |
| ③ 切歌序列 | **全程零弹窗**；有未保存修改时**静默丢弃**（.song mtime 不变实证）→ 无需先关后开（`close_before_open=False`）、autoSave 无实际作用（切歌前须自行保存）；Start 页态递交被静默丢弃（同 Cubase 空框架）→ `frame_title` 退出重启特例生效，全流程 9.0s、日常切换 **2 秒** |
| ⑤ 走带键 | 回零=**小键盘 `.`**（NUMDOT；NumEnter/主 Enter 证伪）；Space=播放/停止切换（双向实测）；S1 无独立暂停键，stop/pause/resume 同键序 |
| 退出保存框 | 标题就叫 `Studio One`（"保存"字样在正文）→ 标题匹配的弹窗排水看不到，且被 Start 页同名 dialog_ignores 排除——**S1 线 exitCloseApps 已知缺陷** |
| 焦点坑 | 用户机 Xbox GameInputServiceWindow（无标题隐形窗）霸占前台致 focus() 成片失败；绕法=SendInput 真实点击目标窗口空白区激活 |

### 校准过程中的新未知量/后续项

- ⑥ 走带 MIDI Learn / ⑦ Mackie spike：待验；
- ⑧ JS 脚本导出时长：getHostAPI（QtScript）可行性未验，手填兜底已可用；
- ④ 弹窗全集：当前仅遇退出保存框（标题 `Studio One`），切歌全流程零弹窗，
  继续随用随采；
- S1 退出保存框标题陷阱、Start 页递交丢弃等行为已固化进 `daw_ctrl.py` 事实表。

### 工作量对账（§六 估算 vs 实际）

- daw 抽象 + S1 后端 + 双安装包 + 校准：**约 1.5 个工作日**（估算 5-6 天，因
  S1 行为远比 Cubase 简单：零弹窗、同窗换歌、9s 冷启动）。

## 九、MIDI 时钟链路闭环（2026-09-28 二次调查，§四-5 至此闭环）

> 首轮时钟排查记录已按用户要求整体抹除（防思维锚定）；本节为抹除后**全新**
> 调查的闭环结论，不含旧叙事。

- **正控制先行**：Cubase 底座全链实测通过（播放时 VJ Automation 口稳定收
  F8=76/s，即 190BPM×24ppqn 分毫不差；停止即断流；winmm 环回 24/24）——
  机器的 loopMIDI（1.0.16.27，三端口）↔winmm↔新旧 MIDI 栈并存层全部健康，
  与 Windows MIDI Services 新栈的兼容层（KSA 桥）互不干扰。
- **S1 侧根因=外部设备建错了类型**：首轮遗留设备是「新建键盘」类
  （`KeyboardDevice`）——键盘类是输入控制器，只出现在音轨**输入**下拉、
  不进「外部乐器」栏，音轨输出永远路由不到它；其时钟字段存在但非官方
  发送路径。**删除重建为「新建乐器」（`InstrumentDevice`，Send To=VJ
  Automation，勾 Send MIDI Clock + MIDI Clock Start）后实测全链通**：
  《3.21演出》播放收 F8=48.0/s（=120BPM×24），65 秒 3056 个零丢失。
- **S1 时钟配置事实**（一手逆向+官方对照）：开关存于全局
  `%APPDATA%\PreSonus\Studio One 7\MusicDevices.settings` 的
  `midiClockActive`/`midiClockStart`（设备级，**不随 .song 保存**）；端口
  前缀 `WinMidi/`=走 WinMM（与 Cubase 同通道）；官方确认 6.2 起键盘类也有
  时钟复选框但完整文档流程在乐器类；同端口只应在一台设备勾时钟（防重复
  注入）。**SPP 历来不发**（勿设计依赖 SPP 的功能）；时钟仅播放时发送。
- **待接线收尾**（时钟地基已通，功能待接）：.song 里建 Instrument 轨、
  输出选 VJ 设备，VJ 触发音符/音色切换即与 Cubase 版同构。
