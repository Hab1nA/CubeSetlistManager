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
| 全停 panic | 103 停 | 作用激活工程 |
| 关工程（切歌链路内） | 103 停 → 105 存 → 106 关 | 窗口消亡轮询验收 |

不变项：打开工程仍走 CLI 单实例转交；定位点 GOTO 仍走 MMC SysEx；
Studio One 底座完全不涉及（直控仅 Cubase）。

## 失效行为（完整迁移，无回落）

- 走带/全停/关闭工程只有 MIDI Remote 一条路：口「CubeSetlist Remote」
  不存在/打不开、或 Cubase 未开 → 动作失败并在日志报错（切歌在关闭步
  失败并中止）。
- 部署三步是演出前置条件；演出前跑一遍下面的验收三步。
- **存活探针（11-11 落地，v1 无心跳欠账已清）**：动作发送前查 Cubase
  进程+界面在场（ daw_ctrl 注入）；Cubase 已退出/退出空壳时报错弃口进
  冷却，不再静默假成功。
- **回声自动判死不可行（真机实证 11-11）**：loopMIDI 同缆自听使口上
  回声无法区分 driver 反馈，且 driver 对外来 CC 不回发 feedback
  （活/死 Cubase 口上字节全同）。driver 是否被装载的核验用
  `py tools/probe_mr_echo.py`（线缆层自证 + Cubase 界面人工确认效果）。

## 部署三步（真机一次性）

1. **建虚拟口**：loopMIDI（App 启动会自动拉起）里新建端口，命名
   `CubeSetlist Remote`（与检测单元严格相等匹配，勿加后缀）。
2. **装 driver**：把仓库 `mr_driver/Local/CubeSetlistManager/
   CubeSetlistRemote/CubeSetlistManager_CubeSetlistRemote.js` 按同样目录
   层级拷到：
   `C:\Users\<用户>\Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\
   Local\CubeSetlistManager\CubeSetlistRemote\`
3. **确认自动连接**：重启 Cubase → 菜单「Studio → MIDI Remote Manager」→
   列表出现 `CubeSetlistManager CubeSetlistRemote` 且端口列已自动填
   `CubeSetlist Remote`（检测单元严格同名自动连接，真机实证零手工映射；
   罕见未连时手选该口）。

## 验收（三步全绿才算部署完成）

```
py -u -X utf8 tools/probe_mr_ctrl.py --selftest     # ① 离线：CC 表一致性
py -u -X utf8 tools/probe_mr_ctrl.py play 12        # ② play：POS 持续推进
py -u -X utf8 tools/probe_mr_ctrl.py rewind 8       # ③ rewind：POS 跳 0
```

②③ 的 POS 来自 MTC 时码（前提=工程已勾 MTC 时码输出，同定位点功能前提）。
探针结论行显示「直控生效/已归零」即通；「位置未推进」= driver 未装载或
Cubase 没开（见上节已知限制）。App 内同样可验：走带按钮点按后日志出现
「走带 播放」类条目且 MTC 三态跟随，即为直控路径在工作。

## 配置

`config.json` 顶层 `mrPortHint`（缺省 `CubeSetlist Remote`）：口名改过
才需要填；其余无需配置，v1 无设置页。

