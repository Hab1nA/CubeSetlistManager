# 构建 iOS 版并上传 TestFlight

本文针对本 fork 的 `ios/` 工程（Bundle ID 默认 `com.kngtnpt.cuberemote`，
版本 `0.1.0`，build `1`，最低 iOS 16，iPhone/iPad 通用）。

> TestFlight 没有独立的“账号”：它是 App Store Connect 里的测试分发功能，
> 需要 **Apple Developer Program**（每年 99 美元，个人/公司均可）。
> 免费 Personal Team 只能真机调试，不能上传 TestFlight。

## 0. 前置条件

- 一台装了 **Xcode 16 或更新版本** 的 Mac（本仓库的工程由 XcodeGen 生成，
  已提交 `.xcodeproj`，不装 XcodeGen 也能直接打开）；
- 已加入 Apple Developer Program 的 Apple ID；
- 在 [App Store Connect](https://appstoreconnect.apple.com) 有 App Manager
  或 Admin 权限；
- 一台 iPhone/iPad 用于自测（可选，但强烈建议）。

先确认 Team ID（10 位，例如 `ABCDE12345`）：

- <https://developer.apple.com/account> → Membership details → Team ID；
- 或 Xcode → Settings → Accounts → 选中账号 → Team ID。

## 1. 改成你自己的签名与 Bundle ID

Bundle ID 必须全球唯一，且和 App Store Connect 里的记录一致。默认值
`com.kngtnpt.cuberemote` 属于本 fork 作者，**换到你的账号时务必改掉**。

编辑 `ios/project.yml`：

```yaml
settings:
  base:
    DEVELOPMENT_TEAM: "ABCDE12345"          # 你的 Team ID
...
targets:
  CubeRemote:
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.yourname.cuberemote
```

然后在 `ios/` 下重新生成工程：

```bash
cd ios
xcodegen generate          # 没装：brew install xcodegen
```

如果不想装 XcodeGen，也可以直接在 Xcode 里改：选中 `CubeRemote` target →
**Signing & Capabilities** → Team 选你的团队、Bundle Identifier 改成你的。
但下次 `xcodegen generate` 会覆盖，所以建议改 `project.yml` 为准。

`project.yml` 里已有：

```yaml
CURRENT_PROJECT_VERSION: "1"     # build 号，每次上传必须 +1
MARKETING_VERSION: "0.1.0"      # 对外版本号
```

## 2. 在 App Store Connect 建 App 记录

1. 打开 <https://appstoreconnect.apple.com> → **My Apps** → 左上角 **+** →
   **New App**；
2. Platform 选 **iOS**；
3. Name 填 `Cube Remote`（可改，30 字符内，全局唯一）；
4. Primary Language 选 **Chinese (Simplified)**；
5. Bundle ID 选第 1 步注册的那个；如果下拉里没有，先去
   <https://developer.apple.com/account/resources/identifiers> 注册一个
   **App ID**（Bundle ID 填同一个，Capabilities 不需要额外勾选）；
6. SKU 随便填一个内部编号，例如 `cuberemote-001`；
7. User Access 选 Full Access。

## 3. Archive（生成发布包）

### 方式 A：Xcode 图形界面（推荐第一次用）

1. `open ios/CubeRemote.xcodeproj`；
2. 顶部 scheme 选 **CubeRemote**，运行目标选 **Any iOS Device (arm64)**；
3. 菜单 **Product → Archive**（会跑 Release 配置，等几分钟）；
4. 弹出 **Organizer** → 选中刚生成的 Archive →
   **Distribute App** → **App Store Connect** → **Upload** →
   一路 Next（Automatic signing）→ **Upload**。

### 方式 B：命令行

```bash
cd ios

xcodebuild -project CubeRemote.xcodeproj \
  -scheme CubeRemote \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/CubeRemote.xcarchive \
  -allowProvisioningUpdates \
  DEVELOPMENT_TEAM=ABCDE12345 \
  archive
```

导出并直接上传（Xcode 15+ 支持 `destination=upload`）：

```bash
cp ExportOptions.plist.example ExportOptions.plist
# 编辑 ExportOptions.plist，把 REPLACE_WITH_TEAM_ID 换成你的 Team ID

xcodebuild -exportArchive \
  -archivePath build/CubeRemote.xcarchive \
  -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export \
  -allowProvisioningUpdates
```

如果 Xcode 较旧、不支持 `destination=upload`：把 `ExportOptions.plist` 里的
`destination` 改成 `export`，导出 `build/export/CubeRemote.ipa`，再用
**Transporter**（Mac App Store 免费下载）拖进去上传。

> 不想用命令行上传，也可以只做到 archive，然后在 Xcode Organizer 里点
> Distribute App 上传；两种方式等价。

## 4. TestFlight 里分发

1. 上传成功后，App Store Connect → 你的 App → **TestFlight**；
2. 等构建从 **Processing** 变成可用（通常 5–30 分钟，偶尔更久）；
3. 首次可能提示 **Missing Compliance**：本工程已在 `Info.plist` 写了
   `ITSAppUsesNonExemptEncryption = NO`，正常会自动通过；若仍提示，
   在构建页回答“仅使用豁免加密（HTTPS）”即可；
4. **Internal Testing**：建一个内部组，添加测试员（必须是 App Store
   Connect 团队成员，最多 100 人），分配构建。内部测试**不需要 Beta App
   Review**，最适合自己/乐队成员先用；
5. **External Testing**：建外部组、添加构建，首次需要 **Beta App Review**
   （比正式上架轻，但仍会检查），通过后最多 10,000 人，可生成公开链接；
6. 测试员在 App Store 装 **TestFlight**，接受邮件邀请或兑换公开链接，
   即可安装。构建 **90 天** 后过期，过期后需重新上传。

## 5. 这个 App 特有的注意事项

- **必须和电脑在同一局域网**：电脑端开启“网页遥控”，手机连同一 Wi-Fi/
  热点，APP 页默认端口 `8767`。TestFlight 测试员如果没有电脑端，APP 只能
  停在连接页，这是预期行为。
- **本地网络权限**：首次连接会弹“允许访问本地网络”，拒绝后无法连接；
  可在 iOS 设置 → 隐私与安全 → 本地网络里重新打开。
- **明文 HTTP**：工程为局域网直连放了 ATS 例外
  （`NSAllowsArbitraryLoads`）。外部 TestFlight 审核时如果被问，说明
  “仅用于用户自己局域网内的电脑，不经过互联网”；后续可收窄为
  `NSAllowsLocalNetworking` + `NSAllowsArbitraryLoadsInWebContent` 再验证。
- **后台音频保活**：设置里的“音频保活”默认关闭。它只是为了延长后台存活，
  外部审核可能质疑滥用 `audio` 背景模式；送 External Testing 前建议保持
  关闭，并在“What to Test”里说明。
- **App 隐私**：本 App 不收集数据（相机仅本地扫码、本地网络仅连电脑），
  App Store Connect 的 App Privacy 可选 **Data Not Collected**。
- **每次上传 build 号必须 +1**：改 `project.yml` 的
  `CURRENT_PROJECT_VERSION`（或 Xcode target → General → Build），否则会报
  “The bundle version must be higher than the previously uploaded version”。

## 6. 常见报错

| 报错 | 处理 |
|---|---|
| `No profiles for 'com.xxx' were found` | Xcode → Settings → Accounts → Manage Certificates 生成 Apple Distribution；确认 Bundle ID 已在开发者后台注册 |
| `The bundle identifier is not available` | 换一个 Bundle ID，或确认这个 ID 属于当前 Team |
| `ITMS-90076` / Missing Compliance | 确认 `ITSAppUsesNonExemptEncryption` 为 `NO`，或在构建页补答加密问题 |
| `The bundle version must be higher...` | `CURRENT_PROJECT_VERSION` +1 后重新 archive |
| 上传卡住 / 网络错误 | 换网络，或用 Transporter；公司网络常拦上传端口 |
| TestFlight 一直 Processing | 等；超过 1 小时可检查 App Store Connect 的邮箱通知，多为图标/隐私清单问题 |

## 7. 本机验证边界

本仓库作者的环境只做到：

- `swiftc -typecheck` 通过；
- `CubeRemoteCore` 的纯逻辑测试通过；
- `Info.plist` / `PrivacyInfo.xcprivacy` / `project.pbxproj` 通过 `plutil -lint`。

本机验证边界以 [ios.md §6](ios.md) 为准（较本文旧快照更新）：已产出未签名
archive 并在 Simulator 完成编译/安装/启动截图；**未做**真机签名与
TestFlight 上传——需付费 Apple Developer Team。
上面第 1–4 步必须在你自己有 Apple Developer 权限的 Mac 上完成。
