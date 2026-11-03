# Cube Remote for iOS / iPadOS

iPhone / iPad 版的 Cube Remote：加载电脑端同一张 APP 遥控页，并补上
iOS 能做的翻谱与踩钉转发。目标不是把 Android 版逐像素搬过来，而是把
**平台允许的那部分能力**做扎实，平台不允许的部分明确写清替代方案。

- 最低系统：iOS / iPadOS 16.0（iPhone、iPad 通用，支持竖屏与横屏）
- 语言 / UI：Swift 5 模式、SwiftUI + WKWebView、CoreMIDI、Network.framework
- Bundle ID：`com.kngtnpt.cuberemote`（可自行改）
- 工程：`ios/CubeRemote.xcodeproj`（由 `ios/project.yml` 经 XcodeGen 生成）
- 纯逻辑包：`ios/CubeRemoteCore`（SwiftPM，可独立 `swift test`）

## 1. 能力对照

| 能力 | Android（`mobile/`） | iOS（`ios/`） |
|---|---|---|
| 遥控页 / 走带 / 切歌 / 全停 | WebView + JS Bridge | 同一张 `PAGE_APP` + `window.CubeApp` 注入桥 |
| 电脑推来的翻谱 | 前台服务 NanoHTTPD `/turn` → 无障碍手势 / 媒体键 | 前台 `NWListener` `/turn` → CoreMIDI 虚拟源发音符 |
| 谱面 App 适配 | 点按 / 双击 / 滑动 / 媒体键，可指定目标 App | 只能走 MIDI：在谱面 App 里学习「Cube Remote」的音符 |
| 踩钉 | 无障碍 `onKeyEvent` 捕获 + Wi-Fi 转发 | CoreMIDI 输入（USB / 蓝牙 MIDI 踏板）+ Wi-Fi 转发 |
| 后台存活 | 前台服务 + Wi-Fi 高性能锁 | 仅前台；可选静音音频保活（best-effort，审核需说明） |
| 扫码 | GMS Code Scanner / zxing | AVFoundation（QR / Code128 / EAN13） |
| 配对令牌 | 地址里带 `?t=`（可选） | 连接页单独填，自动拼 `?t=` 并走 `X-Cube-Token` |

### iOS 明确做不到的（不要承诺）

1. **不能向其它 App 注入触摸 / 滑动**：没有 Android 的无障碍手势等价物。
2. **不能注入全局媒体键**，也不能枚举已安装 App / 读取使用情况。
3. **不能在后台长期监听端口**：App 进入后台后 `NWListener` 会被挂起，
   电脑端推送会失败；音频保活只能延长存活，不能保证。
4. **不能把本机模拟成 HID 键盘**给别的 App；CoreMIDI 是唯一合法的
   “向其它 App 发信号”通道。

因此 iOS 版的翻页是 **MIDI 翻页**，不是“模拟点屏”。谱面 App 必须支持
MIDI 翻页（forScore、Newzik、OnSong 等均支持；具体以各 App 设置为准）。

## 2. 架构

```
┌──────────────────────────── iPhone / iPad ────────────────────────────┐
│  SwiftUI Shell                                                        │
│   ├─ ConnectView（地址 / 端口 / 令牌 / 扫码 / 最近连接）              │
│   ├─ RemoteView → WKWebView(电脑 PAGE_APP)                            │
│   │     └─ document-start 注入 window.CubeApp（同步 getter + postMessage）│
│   ├─ SettingsView（MIDI 音符、本机端口、踩钉、保活）                  │
│   └─ AppModel（@MainActor，状态与生命周期）                           │
│                                                                       │
│  TurnHTTPServer (NWListener :8766) ── /turn {"dir":"next|prev"}        │
│        └─ MIDITurnService ── CoreMIDI 虚拟源 "Cube Remote" → 谱面 App  │
│                                                                       │
│  PedalMIDIService ── CoreMIDI 输入（USB / BLE 踏板）                   │
│        └─ PedalForwarder ── POST http://PC:8767/pedal/event            │
│                             （seq / sid / 心跳 / 403 停机 / 重试）      │
│                                                                       │
│  AudioKeepAlive（可选，静音循环）                                      │
└───────────────────────────────────────────────────────────────────────┘
```

电脑端不需要为 iOS 改协议：翻谱推送仍是 `POST /turn`，踩钉仍是
`POST /pedal/event`；iOS 的 `vk` 用 Linux keycode 183–194（F13–F24），
电脑端 `pedal.remote_key_to_vk()` 会原样转成 VK 0x7C–0x87。

## 3. 构建

### 3.1 直接打开工程

```bash
open ios/CubeRemote.xcodeproj
```

1. 选中 `CubeRemote` target → Signing & Capabilities → 选自己的 Team；
   如需上架，把 Bundle Identifier 改成自己的。
2. 选真机（iOS 16+）或模拟器，Run。
3. 首次运行会请求「本地网络」和「相机」权限；拒绝本地网络会导致连不上电脑。

> 工程文件已提交。只有改了 `ios/project.yml` 或增删源文件时才需要重新生成：
>
> ```bash
> brew install xcodegen
> cd ios && xcodegen generate
> ```

### 3.2 命令行编译 / 归档

```bash
xcodebuild -project ios/CubeRemote.xcodeproj \
  -scheme CubeRemote \
  -destination 'generic/platform=iOS' \
  -configuration Release archive
```

无签名 CI 可用：

```bash
xcodebuild -project ios/CubeRemote.xcodeproj -scheme CubeRemote \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

### 3.3 纯逻辑测试

```bash
cd ios/CubeRemoteCore && swift test
```

`CubeRemoteCore` 不依赖 UIKit / WebKit / CoreMIDI，覆盖：

- HTTP/1.1 请求解析（不完整、畸形、1MB 上限）、响应写出
- `/turn` 的 `{"dir":"next|prev","test":1}` 解析
- 踩钉包 JSON、音符 → LKC 映射、心跳包
- 连接串四种写法（`cube://`、`http://`、`ip:port`、纯 IP）
- 注入桥状态 JSON

## 4. 使用

### 4.1 连接电脑

1. 电脑：`Cube Setlist Manager` → 设置 → **移动端遥控** → 开启；
   记下「本机 IP」和 APP 页面端口（默认 8767）。
2. 手机与电脑连同一个 Wi-Fi / 电脑热点（路由器需允许客户端互访，
   很多公共 Wi-Fi 开了 AP 隔离，会连不上）。
3. iPhone：填地址（如 `192.168.1.5`）、端口 `8767`；
   若电脑端配置了 `webRemote.authToken`，把令牌一起填上。
4. 也可以扫二维码：内容支持
   `cube://connect?host=192.168.1.5&port=8767&token=xxx` 或
   `http://192.168.1.5:8767/?t=xxx`。

连接成功后就是电脑端同一张遥控页：曲目、走带、进度、全停、切歌都在。

### 4.2 翻谱（CoreMIDI）

1. 电脑端「网页遥控 → 平板转发端口」默认 `8766`；iOS 设置页「本机翻谱端口」
   必须与它一致。
2. 在谱面 App 的 MIDI 设置里选择输入源 **Cube Remote**，把「下一页 / 上一页」
   分别学习为 C2(36) / C#2(37)（可在 iOS 设置页改）。
3. 电脑在 DAW 时间线收到 C2 / C#2 时，会 `POST /turn` 到手机；
   手机通过 CoreMIDI 发出对应音符，谱面 App 翻页。
4. iOS 只在前台监听；锁屏/切后台后翻谱会停。需要长时间演出时：
   - 保持 App 在前台（引导式访问可防误触）；
   - 或打开「音频保活（实验）」，但不要把它当作可靠后台服务。

### 4.3 踩钉

1. 把 USB MIDI 踏板经相机转换头接到 iPhone，或配对蓝牙 MIDI 踏板。
2. iOS 设置 →「转发本机 MIDI 踏板」打开。
3. 电脑端「踩钉」页 →「平板转发链路」→ 启用。
4. 踩下踏板，电脑端学习到的键是 **F13–F24**（音符按音高类映射：
   C→F13、C#→F14……B→F24）。在电脑端把动作绑到这些键即可。
5. 电脑端关闭转发时返回 403，iOS 会自动停止转发并在设置页提示。

> iOS 不能像 Android 那样在系统层拦截踏板按键再透传；它只监听 MIDI，
> 不会影响踏板原本连接的其它 MIDI App。

### 4.4 令牌与安全

- `webRemote.authToken` 为空 = 保持旧行为（局域网内任意设备可调控制面）。
- 设置非空令牌后：
  - 回环地址（电脑本机）免令牌；
  - 手机页面必须带 `?t=<token>` 打开，服务端会种 `HttpOnly; SameSite=Strict`
    的 Cookie，之后页面里的 `fetch` 自动携带；
  - iOS 的原生请求（踩钉/诊断）带 `X-Cube-Token`。
- `webRemote.hosts` 可加额外可信 Host（默认只认 IP 字面量 / localhost，
  防 DNS 重绑定）。
- **已知折衷**：`/pedal/event` 与 `/diag` 对令牌豁免，以兼容未升级的
  Android 客户端；它们仍受「私网来源 + 频率限制 + seq 去重」保护。
  在不可信网络里请把 `authToken` 设上，并尽量只在可信 Wi-Fi / 电脑热点使用。

## 5. 与 Android 版的差异（产品口径）

| 场景 | Android | iOS |
|---|---|---|
| 谱面 App 不支持 MIDI | 可用点按/滑动/媒体键硬翻 | 不支持，只能换支持 MIDI 的谱面 App |
| 自动切回谱面 App | 有「使用情况访问」 | 无 |
| 锁屏后翻页 | 前台服务可继续 | 会停（保活仅延长） |
| 踏板按键拦截 | 可拦截，不透传给其它 App | 不拦截，MIDI 事件照常广播 |
| 安装 | APK 侧载 | Xcode 真机 / TestFlight / App Store |

## 6. 验证状态（诚实说明）

- `CubeRemoteCore` 的纯逻辑已用 `swiftc` 逐条跑过断言（151 项全过），
  并配有 XCTest；在装有完整 Xcode 的机器上 `swift test` 即可复跑。
- App 层（SwiftUI + WKWebView + CoreMIDI + NWListener）已通过
  `swiftc -typecheck`（iPhoneSimulator SDK，iOS 16 target）。
- **本仓库的开发环境未接受 Xcode 许可协议，`xcodebuild` 被系统拦下**，
  因此没有在本机产出 `.app`；首次在 Xcode 打开时请按 3.1 选 Team 后编译。
  这属于本机许可限制，不是工程缺文件。
- 真机验收清单：连接、翻谱（前台）、踩钉转发、令牌、扫码、iPad 分屏/旋转。

## 7. 故障排查

| 现象 | 处理 |
|---|---|
| 页面白屏 / 一直转圈 | 确认手机与电脑同网段；电脑防火墙放行 8767；路由器关闭 AP 隔离 |
| 401 / 提示需要令牌 | 连接页填 `authToken`，或用 `http://IP:8767/?t=令牌` 打开 |
| 403 Host 不被信任 | 用 IP 访问，不要用域名；或把域名加入 `webRemote.hosts` |
| 翻谱端口启动失败 | 换一个 ≥1024 的端口；确认没有其它 App 占用 |
| 谱面 App 不翻页 | 确认它选的是 `Cube Remote` 输入源、音符是 36/37；iOS 必须在前台 |
| 踩钉无反应 | 确认 iOS 开关 + 电脑「平板转发链路」都开；看设置页状态提示 |
| 锁屏后失效 | 预期行为；用引导式访问保持前台，或接受音频保活的不确定性 |
