# Vaultwarden 懒猫应用适配器

将 [Vaultwarden](https://github.com/dani-garcia/vaultwarden) 的正式版本自动适配为懒猫官方应用商店的 `cloud.lazycat.app.vaultwarden`。不修改上游项目，不重新编译镜像。

## 更新流程

每天北京时间 **12:47** 由 GitHub Actions 检查上游 `x.y.z` 稳定 Git tag（无 `v` 前缀），不监控 `main`、`testing` 或 `latest`。GitHub 调度可能延迟。发现新版本后使用对应的 `docker.io/vaultwarden/server:x.y.z` 官方 Debian 镜像，锁定 `linux/amd64` 摘要。

首次升级基线是商店已发布的 **1.37.0**。旧 LPK 镜像的 `origin_hash` 已与脚本中固定的官方镜像摘要核对一致。Runner 创建临时账号、加密密码条目和加密附件，升级到候选版本并重建容器，验证登录、同步、解密、SQLite 完整性、管理员配置及 RSA 签名密钥。临时数据和凭据在测试结束后删除。

测试成功后转存镜像到懒猫官方仓库，再生成 `.lpk`、提交审核并将版本、Manifest 和状态锁提交回本仓库。构建脚本会核对被转存镜像的摘要与测试证明；两者不一致时禁止打包和送审。仅有健康检查成功不算升级测试通过。

GitHub Release 发布与应用商店送审是独立任务；Release 失败不会阻止送审。Release 仅上传带应用版本号的 `.lpk`，不上传 `.sha256`。GitHub 自带的 Source code ZIP/TAR 是平台自动生成的。

`lazycat-action@v1` 记录上游版本、镜像摘要和审核状态；不会定时重复提交同一个已被拒绝的上游版本。修复适配后可手动开启 `retry-rejected`。新上游版本可以替代旧的待审核版本。

## 使用

1. 在本仓库 **Settings → Secrets and variables → Actions → Repository secrets** 配置 `LZC_API_TOKEN`，值为懒猫开发者 PAT。该开发者必须拥有 `cloud.lazycat.app.vaultwarden` 的维护/协作者权限。
2. 仓库 Actions 需要 `contents: write` 权限，以写回版本和状态、发布 Release。
3. 打开 **Actions → Update Vaultwarden for LazyCat → Run workflow**。
   - 勾选 `dry-run`：仅检查版本及更新日志，不转存、打包或送审。
   - 取消 `dry-run`：验证升级、转存、打包并真实送审。
   - `retry-rejected` 默认不勾选，仅用于修复后主动重试。

普通代码 push 会先运行升级测试，再执行只读版本检查，不会提交审核。定时任务发现新版本后才执行实际更新；无新版本不重复启动升级测试。

## 安装与数据兼容

- 数据位置沿用旧版：`/lzcapp/var/data:/data`。数据库、附件、Send 文件、`config.json` 和认证密钥都在这个目录内。升级前应备份。
- 管理面板继续使用 `lzc-deploy-params.yml` 的 `admin_token` 安装参数，**不提供共享默认管理员密码**。管理员 Key 与个人密码库主密码不同。
- 保留密码库及管理面板两个入口、后台运行和原有注册开关。管理员保存到 `config.json` 的设置沿用上游优先级，不主动覆盖。
- HTTPS 公网地址使用安装时的 `{{ .S.AppDomain }}`。`public_path: /` 沿用旧包以兼容原生客户端，Vaultwarden 自身的认证和密码库加密仍然有效。
- 沿用旧包 `unsupported_platforms: [android, ios]`，这是懒猫客户端的应用入口限制，不表示 Bitwarden 手机客户端不能连接。原生客户端应填写该实例的 HTTPS 地址；需使用与新服务端兼容的客户端版本。
- 初始构建目标为 `linux/amd64`，不因上游提供 ARM 镜像就自动宣称已完成 ARM 适配。

## 文件

`package.yml` 是商店元数据；`lzc-manifest.yml` 是运行配置；`lzc-build.yml` 显式收集图标和部署参数；`lazycat-action.yml` 是版本监控、测试及送审配置。`.lazycat-action.lock.yml` 由首次成功打包/送审产生并提交，用于下次比较。

`dist/`、`.lazycat-action/`、测试凭据、临时数据及 `.lpk` 不进入 Git。仓库图标直接取自现有商店 LPK。

上游文档：[镜像与版本](https://github.com/dani-garcia/vaultwarden/wiki/Which-container-image-to-use)、[数据备份](https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault)。
