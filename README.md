# 记忆导演 · Memory Director

**0.1.1-preview / Windows 本地主机 + iPhone 上传客户端工程**

把日子，留成故事。电脑保存和处理照片、视频、人物分组、备注与回忆影片；手机只向配对的家用电脑上传用户选择的素材。模型在本机推理，应用不调用云端大模型。

这是首个公开预览包。Windows 一键脚本和模拟素材测试用于验证部署；iPhone 工程需要在 Mac/Xcode 编译、签名和真机验收后才能安装分发。这里没有预签名 IPA。

## Windows 快速开始

1. 解压到独立的程序目录；不要覆盖已有个人资料或自用安装。双击 **安装记忆导演.cmd**。
2. 选择一个新的空数据目录。按需启用 iPhone 局域网连接。脚本为项目建立独立环境，必要时把 Python 官方安装到项目 `.runtime`，不修改全局 PATH。
3. 安装需要联网下载依赖和模型（约数百 MB）。下载模型逐一核对 SHA-256；首次完成后，正常识别、选片和影片制作支持断开外网使用。
4. 双击 **启动记忆导演.cmd**，在电脑本机网页创建账号。添加家庭账号也在电脑完成；没有默认密码。选择电脑中的照片或视频上传即可，不会自动扫描磁盘。
5. 需要 iPhone：先启用安全局域网服务，在电脑“连接手机”查看配对码，使用已签名安装的 iPhone App 扫码并登录。

Agent 部署可使用 [memory-director-setup Skill](skills/memory-director-setup/SKILL.md)。也可在解压目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File install.ps1 -DataDir "D:\MemoryDirectorData" -EnableLan -IncludeMusic
.venv\Scripts\python.exe doctor.py
.venv\Scripts\python.exe smoke.py
```

安装器支持寻找 Python 3.10–3.12 x64；发布前验证环境记录见 RELEASE-CHECKLIST.md。Windows 缺少微软 VC++ 运行库时，需在本机按错误提示安装微软官方运行库；脚本不会关闭安全软件或自动提升权限。详细安装输出留在本机 work/install.log，不能直接发给 Agent 或公共 Issue。

电脑网页默认 `http://localhost:7860`（仅本机）；手机安全端口默认 7861。端口可在本地 `local-config.json` 调整；同时运行另一份安装时使用不同端口。启用局域网后需重启服务；如防火墙阻止，用户可明确以管理员身份运行 enable-lan.ps1，仅放行专用网络、本地子网的 HTTPS 端口。不要设置公网端口转发。

## iPhone 首版

见 [Mac 编译与安装说明](ios/README.md)。iOS 17+；原生 SwiftUI，系统相册选择器、二维码配对、证书核对、家庭账号登录、前台上传进度和重复文件提示。首版未实现后台全相册自动备份、手机端完整图库和影片编辑。

## 功能

- 照片/视频时间线、原尺寸图片显示、视频兼容播放、备注、珍藏、家人分享和回收站。
- YuNet/SFace 本地人物分组，人工命名、纠错、同名快捷选择和批量合并。识别不会确认真实身份。
- 回忆影片：本地 CLIP 语义匹配，中文导演要求、备注、时间活动分组、明显闭眼筛查、相似照片择优。预览后按原片制作 720p/1080p MP4，三种模板，可用个人配乐或可选 CC0 音乐。
- 人脸与闭眼检测可能误判，侧脸、墨镜、小脸和年龄变化尤其需要人工确认。选片不足不会用随机内容凑数。

## 文件存储与更新

程序文件与数据目录分离。数据目录中 `uploads` 存原片，`faces` 存人脸裁切图，`memory.sqlite` 存账号、特征及备注，`models` 存公开模型，`films` 存成片，`display`/`playback` 存可复用显示缓存。不要只备份数据库而遗漏原片。

升级前停止已确认属于该安装的服务，完成数据备份；重试安装保留 `local-config.json` 和既有数据。不要复制别人的数据库、secret.key 或 transport 私钥。卸载默认保留数据，程序不提供自动删库或清空外部相册操作。

## 隐私、开源与反馈

先阅读 [PRIVACY.md](PRIVACY.md)。安装 Skill 是操作规范，不是隔离 Agent 的安全沙箱；拥有相同系统权限的软件仍可能读取本机文件。不要让 Agent 预览家庭素材或读取数据库来“验证安装”。

源代码采用 MIT，依赖与模型遵循各自许可证，见 [THIRD_PARTY.md](THIRD_PARTY.md)。没有复制 Immich 源码；模型不随源码 ZIP 分发，由安装器从发布者下载。发布构建只允许清单中的公开文件；运行后产生的任何目录均不能整夹上传 GitHub。问题反馈只提交脱敏信息和模拟复现，使用仓库中的 Issue 模板。
