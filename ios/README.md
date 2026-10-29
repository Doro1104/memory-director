# 在 Mac 编译与安装 iPhone 客户端

此目录是原生 SwiftUI 工程，目标 iOS 17+，无第三方 iOS SDK。当前发布环境为 Windows，不能代替 Xcode 编译、苹果签名或真机测试；工程交付状态与验收结果见 RELEASE-CHECKLIST.md。用户拥有 Mac 后可按下列步骤完成。

## 先构建模拟器版本

安装支持 iOS 17+ SDK 的 Xcode，完成首次启动组件安装，并在“设置 → Locations → Command Line Tools”选择当前 Xcode。终端进入仓库根目录：

```sh
bash ios/build-simulator.sh
```

这会无签名构建并生成 `work/ios-build/MemoryDirector-simulator.zip`，只适用于 Mac 上的 iOS 模拟器。可启动一个模拟器后用 `xcrun simctl install booted work/ios-build/Build/Products/Debug-iphonesimulator/MemoryDirector.app` 安装。它不能作为 IPA 安装到真实 iPhone。

## 安装到自己的 iPhone

1. 用 Xcode 打开 `ios/MemoryDirector.xcodeproj`，选择 MemoryDirector target。
2. 在 Signing & Capabilities 中由用户自己选择 Team；必要时修改 Bundle Identifier。仓库没有 Apple ID、Team ID、签名证书或描述文件。
3. 连接并信任 iPhone，按系统提示开启开发者模式，选择真机，点击 Run。个人开发测试与安装有效期受苹果账号和签名规则限制。
4. 电脑主机用 `install.ps1 -EnableLan` 配置并启动，手机与电脑在可互访的同一家庭网络。电脑本机网页先创建账号，点击“连接手机”。
5. App 扫码或粘贴配对 JSON，确认家庭电脑证书，允许系统“本地网络”权限，再登录已有家庭账号。仅选择一张无隐私测试图片，验证上传、重复提示和退出登录。

首次选择相册中的文件时，若原片仅在 iCloud，系统可能需要先下载。上传阶段保持 App 前台打开、电脑保持唤醒。传送失败可重新选择文件；服务端检查重复。配对后更换电脑证书时必须重新扫码，不能绕过证书错误。

## 让别人安装 / TestFlight

先完成真机验收和隐私说明核对，再使用 Apple Developer Program、App Store Connect 与 Xcode Organizer 的正式流程签名、上传和审核。源码开源不等于可免签安装。本仓库的 GitHub Actions 仅做无签名编译，不读取或上传任何开发者签名秘密。

不要把 Apple ID 密码、Keychain、p12、mobileprovision、真实设备照片、配对信息或安装后的 App 容器交给 Agent。签名操作由用户在自己的 Mac 中完成。

## 真机验收清单

- 允许/拒绝本地网络和相机权限时均有可理解的结果；不支持扫码时可粘贴配对信息。
- 正确指纹可连接，错误指纹、非局域网地址及重定向均拒绝。
- 错误账号、电脑未初始化、断网、电脑休眠和超过 4 GB 文件有明确提示。
- 普通图片、HEIC、所选视频和 Live Photo 静态导出实测正确；不导入未经用户选择的项目。
- 顺序上传、进度、失败重选、重复检查以及临时文件清理符合预期。
- 应用切后台不承诺持续上传；没有全相册后台备份。

苹果参考：[签名与能力](https://help.apple.com/xcode/mac/current/en.lproj/dev60b6fbbc7.html)、[局域网权限](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)、[分发方式](https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases)。
