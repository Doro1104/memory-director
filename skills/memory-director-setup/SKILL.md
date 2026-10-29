---
name: memory-director-setup
description: 安装、检查或升级记忆导演 Windows 本地主机，或指导用户在 Mac 构建配套 iPhone 客户端。使用项目安装脚本和模拟样本，不浏览用户的相册、人物数据库或私人媒体。
---

# 记忆导演本地部署

用于用户明确要部署本项目的任务。先确认取得的发布包/仓库位置、用户选择的新数据目录，以及是否需要 iPhone 局域网连接；已有答案无需重复询问。安装范围限定于该应用目录和专用数据目录。

## 素材边界

- 安装和验证不需要真实照片、视频、语音、人物头像或备注。不得扫描相册目录、预览真实媒体，或把它们交给当前 Agent、云端模型、第三方分析服务。
- 不读取或输出 memory.sqlite、secret.key、server.key、账号密码、Cookie、证书私钥、原始日志；也不从历史聊天中提取这些内容制作配置。
- 只检查公开代码、公开模型、安装状态和脱敏 doctor 输出。安装脚本应将详细输出留在本地 work/install.log；失败时优先使用 doctor.py 和模拟样本复现，不把原日志直接带入上下文。
- 不替用户选择整个磁盘、Pictures/DCIM、云盘或已有相册作为数据目录。安装器只接受空目录或已标记的本应用数据目录。真实素材随后由用户在应用中主动选择上传。
- 不提交 data、work、.venv、.runtime、local-config.json、签名文件或安装后的目录。发布必须使用白名单打包器，并在包内运行隐私检查。
- Skill 是 Agent 操作规范，不是操作系统沙箱；它不能阻止拥有同一账号权限的其他软件读取文件。不要向用户承诺“任何 Agent 都绝对读不到”。如用户要求改变真实素材边界，先说明相关内容可能进入当前 AI 会话，取得明确授权后再处理。

## Windows 安装

1. 在用户指定的解压目录读取 README.md 和 PRIVACY.md；不要在其家庭素材目录中运行搜索。
2. 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File install.ps1 -DataDir <新数据目录> -EnableLan -IncludeMusic`。仅当用户需要手机连接时加 -EnableLan。不修改全局 PATH，不关闭防火墙或杀掉其他 Python 进程。
3. 安装器建立独立 .venv，必要时安装 Python 到项目 .runtime，下载官方公开模型并核对 SHA-256。可以在已授权的安装范围内自动重试下载；校验不符就停止，不绕过校验或改用未指定镜像。
4. 执行 `.venv\Scripts\python.exe doctor.py` 和 `.venv\Scripts\python.exe smoke.py`。smoke 只生成临时图形，在隔离数据库中验证；不连接用户真实图库。
5. 启动 `启动记忆导演.cmd`，让用户自己在本机网页创建账号；不要代填或索取其密码。需要放行端口时使用 enable-lan.ps1，仅专用网络、本地子网、所选 HTTPS 端口，需系统管理员权限时说明原因后按宿主权限机制执行。
6. 指导 iPhone App 扫描电脑“连接手机”中的配对码。配对码含局域网地址与公开证书指纹，不含密码；不要把它写进公共 Issue、示例、GitHub 或模型请求。

## 升级与排错

保留已有 local-config.json、data 和 transport 证书，不替换账号或密钥。先停止已确认属于该安装的服务，再由用户本机完成数据备份；备份内容不进入 Agent 上下文。新程序在公开模拟样本上验证后再切换。卸载只移除程序和运行环境，默认保留全部用户数据，不能递归删除用户选择的素材目录。

## Mac / iPhone

读取 ios/README.md。在 Mac 用 Xcode 打开 ios/MemoryDirector.xcodeproj；先运行 ios/build-simulator.sh 做无签名编译，再由用户自己选择 Team、连接 iPhone 并签名安装。不得读取其 Apple ID 密码、Keychain、签名证书或手机照片。真实设备测试由用户选择非敏感测试图执行。

GitHub Actions 只提供无签名构建；模拟器包不能直接装到 iPhone。TestFlight/App Store 的签名和审核是单独步骤，不得把未签名源码宣称为可安装的 iPhone App。
