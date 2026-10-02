# JUNO 延音自动化调研

日期：2026-10-01。结论经过两轮独立调查（主线程调查 + 不带预置结论的独立子代理盲调），两条线结论一致。

## 需求

现场演出时一手在 JUNO-DS88 上手弹、一手让 Cubase 播放工程伴奏。希望由电脑在工程时间轴的指定位置**自动控制琴的延音开/关**（替代脚踩延音踏板），作用对象是**手弹的音**。

## 结论（TL;DR）

**直接做不到。** JUNO-DS88 的延音状态在「本地音符流」与「MIDI 输入音符流」之间是**架构性双向隔离**的：

- 外部发来的 CC64（Hold 1）只延音 **MIDI 进来的音**，不延音手弹的音；
- 琴自己的踏板插孔只延音手弹的音，不延音 MIDI 进来的音。

延音踏板自动化功能（kbd_auto：E2(40)→JUNO、A2(45)→AX-09 触发，发 CC64 到 patchCh）现行设计只能覆盖 MIDI 进来的音——与上述需求错位属硬件架构限制，**不是应用 bug**。

要满足需求必须绕行，可行路线按推荐排序：**5-pin 回环 + Local OFF** > **Matrix Control 伪延音** > **软件转发回环**（见下）。

## 硬件行为证据

| 证据 | 可信度 | 内容 |
|---|---|---|
| [JUNO-DS MIDI Implementation（官方 PDF）](https://static.roland.com/assets/media/pdf/JUNO-DS_MIDI_Imple_eng02_W.pdf) | 官方原文 | CC64 接收=O（受 Patch Rx Hold-1 / Per-Part HOLD 门控）；Redamper Sw=ON 支持半踏；**无延音 SysEx**；MIDI Local On/Off 消息**不被识别** |
| [Roland Clan t=53349（2017，DS88）](https://forums.rolandclan.com/viewtopic.php?t=53349) | 用户实证 | Arduino 向 MIDI IN 发 CC64：手弹的音不被延，仅 MIDI 音被延；外部 CC64 经 MTRX CTL/Effects CTL **可**影响键盘部分 |
| [Roland Clan t=60968（2021，DS88）](https://forums.rolandclan.com/viewtopic.php?t=60968) | 用户实证 | 反向印证：琴自身踏板插孔只延手弹音、不延 MIDI 音 → 双向隔离实锤 |
| [JUNO-DS Parameter Guide（官方 PDF）](http://cdn.roland.com/assets/media/pdf/JUNO-DS_ParamGuide_e01_W.pdf) | 官方原文 | Local Switch（SYSTEM:MIDI，仅面板可设）、MTRX CTRL 源/目的表、DAW CONTROL 双 USB 口分工 |
| Roland 官方 KB / 客服 | 未证实 | 未检索到外部延音自动化相关条目 |

补充事实：JUNO-DS 两个 USB MIDI 口中，「JUNO-DS DAW CTRL」是 Mackie Control 控台仿真口，延音控制应走「JUNO-DS」口（两口的延音行为差异无任何记载）。

## 可行路线

### 路线 1（推荐）：5-pin 回环线 + Local Control OFF

一根 5-pin MIDI 线把琴的 **MIDI OUT 直连它自己的 MIDI IN**，面板 SYSTEM:MIDI 把 Local Switch 关掉。手弹的音绕一圈变成「MIDI 进来的音」，落入 CC64 管辖，应用现有踏板自动化即可真正延手弹的音。

- 优点：延迟约 1ms；演奏链路不依赖电脑（纯硬件回环）；电脑只负责在时间点注入 CC64（走 USB「JUNO-DS」口）；真踏板（CC64 经回环）照常可用，与应用自动化可叠加。
- 坑（两条都必须落实）：
  - **Soft Thru 必须保持关闭**——否则消息经 OUT→IN 无限回声，重演 2026-10-01 的 loopMIDI feedback 风暴（见监控记录）；
  - **Local OFF 是系统级设置**：回环线一旦松脱，手弹不出声，现场须保证线接牢。

### 路线 2：Matrix Control 伪延音

PATCH EDIT:MTRX CTRL 源=CC64、目的=TVA-RELEASE。外部 CC64 经参数通路可作用到**手弹的音**（t=53349 实测），无需改 Local 和布线。

- 代价：是「松键后拖长尾音」的近似，不是真延音；逐音色设置；该音色上 MIDI 音的延音语义也被一并改掉。

### 路线 3（末选）：软件转发回环

手弹音经无线盒 / Cubase Thru 绕电脑一圈回琴（Local OFF + 转发）。延迟高、现场依赖电脑稳定性，仅在前两条不可行时考虑。

### 死路清单

SysEx 直控延音（不存在）；DAW CTRL 口发延音（无佐证）；MIDI 消息切 Local（实现表标记不识别，只能面板设置）。

## 待办 / 验收

1. **本机 A/B 实测**（探针已跑过，听感待回报）：程序弹音踩延音→松键应延续；延音踩住期间手弹→松键无延音。此为路线验收基线。
2. 走路线 1 前的琴端检查清单：Local Switch=OFF、Soft Thru=OFF、（按需）Patch Rx Hold-1 / Per-Part HOLD 接收开、Redamper Sw 按需。
3. AX-09（A2 触发）行为未真机验证，固件与 JUNO 两套，不受本文结论约束。

## 关联事件

2026-10-01 loopMIDI feedback 风暴：Cubase 触发轨 All MIDI Inputs+录音武装+输出同口自环、JUNO Soft Thru 回显经无线盒回流成永动机——诊断记录见监控存档。路线 1 实施时的 Soft Thru 关闭要求即源于该事件的教训。
