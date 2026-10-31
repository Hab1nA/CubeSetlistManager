# MIDI Remote 直控部署（v1）

走带/全停/关工程从键盘模拟（SendInput 抢焦点）升级为 Cubase 官方 MIDI
Remote API 直控：App 经 loopMIDI 虚拟口发 CC，Cubase 内的 driver 脚本把
CC 转成键令（runCommand），全程不聚焦不抢焦点。v1 范围与不变项：

| 动作 | 直控序列（CC） | 效果 |
| --- | --- | --- |
| 播放 play | 103 停 → 104 回零 → 102 播 | 从头播放 |
| 停止 stop / 暂停 pause | 103 停 | 原地停 |
| 继续 resume | 102 播 | 原位置续播 |
| 回零 rewind | 103 停 → 104 回零 | 停在零点 |
| 全停 panic | 103 停（单窗时） | 多窗错乱态仍走键盘逐窗停 |
| 关工程（切歌链路内） | 103 停 → 105 存 → 106 关 | WM_CLOSE 老路保留兜底 |

不变项：打开工程仍走 CLI 单实例转交；定位点 GOTO 仍走 MMC SysEx；
Studio One 底座完全不涉及（直控仅 Cubase）。

## 回落链（为什么可以放心先部署）

- 口「CubeSetlist Remote」不存在/打不开 → 每个动作自动回落原键盘路径，
  行为与升级前一致（设置零改动，装好口才生效）。
- 单条 CC 发送失败 → 同上，弃口 10 秒冷却后自愈重试。
- **已知限制（v1 无心跳）**：口在、但 driver 未在 Cubase 里映射（或
  Cubase 没开）时，CC 发进虚拟口无人受理且不报错——走带会「没反应」。
  部署完成后务必跑下面的验收三步；正式演出口不删、映射不动即可长期生效。

## 部署三步（真机一次性）

1. **建虚拟口**：loopMIDI（App 启动会自动拉起）里新建端口，命名
   `CubeSetlist Remote`（与检测单元严格相等匹配，勿加后缀）。
2. **装 driver**：把仓库 `mr_driver/Local/CubeSetlistManager/
   CubeSetlistRemote/CubeSetlistManager_CubeSetlistRemote.js` 按同样目录
   层级拷到：
   `C:\Users\<用户>\Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\
   Local\CubeSetlistManager\CubeSetlistRemote\`
3. **映射**：重启 Cubase → 菜单「Studio → MIDI Remote Manager」→ 列表
   出现 `CubeSetlistManager CubeSetlistRemote` → 点它右侧 Connect/
   端口列，输入/输出都选 `CubeSetlist Remote` → 确认映射保存。

## 验收（三步全绿才算部署完成）

```
py -u -X utf8 tools/probe_mr_ctrl.py --selftest     # ① 离线：CC 表一致性
py -u -X utf8 tools/probe_mr_ctrl.py play 12        # ② play：POS 持续推进
py -u -X utf8 tools/probe_mr_ctrl.py rewind 8       # ③ rewind：POS 跳 0
```

②③ 的 POS 来自 MTC 时码（前提=工程已勾 MTC 时码输出，同定位点功能前提）。
探针结论行显示「直控生效/已归零」即通；「位置未推进」= 映射没做或 Cubase
没开（见上节已知限制）。App 内同样可验：走带按钮点按后日志出现
「走带 …（MR 直控）」且 MTC 三态跟随，即为直控路径在工作。

## 配置

`config.json` 顶层 `mrPortHint`（缺省 `CubeSetlist Remote`）：口名改过
才需要填；其余无需配置，v1 无设置页。

