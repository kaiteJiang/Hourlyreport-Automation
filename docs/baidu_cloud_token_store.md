# 百度 API 云端集中 Token 存储

本文说明“SCF + COS 集中刷新百度 OAuth token”的部署和使用方式。

## 目标

- 桌面端默认从云端获取百度 `access_token`。
- `refresh_token` 只保存在腾讯云 COS 内，不再依赖每台同事电脑各自刷新。
- 云函数统一使用百度 `secretKey` 刷新 token，并把新 token 写回对应授权的独立 COS 对象。
- 云端失败时，桌面端 API 流程仍会按现有策略失败后降级浏览器抓数。

## SCF 接口

现有回调函数新增两个内部签名接口：

```text
POST /baidu/oauth/token
POST /baidu/oauth/store-profile
```

`/token` 用于桌面端取可用 `access_token`。  
`/store-profile` 用于本机导入 `.baidu-auth` 后，把该授权同步写入云端 COS。

两个接口均使用现有 HMAC 请求头：

```text
X-Baidu-Refresh-Timestamp
X-Baidu-Refresh-Signature
```

## 腾讯云环境变量

在 SCF 函数中保留原有变量，并新增：

```text
BAIDU_TOKEN_STORE_BUCKET=hourlyreport-1300869225
BAIDU_TOKEN_STORE_REGION=ap-nanjing
BAIDU_TOKEN_STORE_KEY=baidu-oauth/token-store/baidu_oauth_tokens.json
TENCENT_SECRET_ID=腾讯云访问密钥 SecretId
TENCENT_SECRET_KEY=腾讯云访问密钥 SecretKey
TENCENT_TOKEN=临时密钥 token（仅临时密钥需要）
```

COS 存储桶必须保持私有读写。

实际对象布局：

```text
baidu-oauth/token-store/baidu_oauth_tokens.json
baidu-oauth/token-store/baidu_oauth_tokens/profiles/<api_profile>.json
```

旧的 `baidu_oauth_tokens.json` 保留为只读兼容来源。某个授权首次重新同步或刷新后，会写入自己的
`profiles/<api_profile>.json`。各授权使用独立对象，多个项目并发刷新时不会再互相覆盖。

SCF 的 COS 权限必须覆盖 `BAIDU_TOKEN_STORE_KEY` 以及同名前缀下的 `profiles/*`，至少允许
`GetObject` 和 `PutObject`。

百度返回的 `expiresTime` / `refreshExpiresTime` 如果不带时区，程序固定按北京时间
`UTC+08:00` 解释；带 `Z` 或显式偏移的值按其自身时区解释。

## 桌面端网关配置

每台电脑的 `secrets/secrets.json` 中，`baidu_api_gateway` 需要包含：

```json
{
  "app_id": "百度应用 appId",
  "client_key": "桌面端与 SCF 共享的 HMAC 密钥",
  "refresh_url": "https://.../baidu/oauth/refresh",
  "token_url": "https://.../baidu/oauth/token",
  "store_profile_url": "https://.../baidu/oauth/store-profile"
}
```

`token_url`、`client_key`、网关 `app_id` 或对应授权 profile 的 `app_id` 缺失/不匹配时，
云端 Token 模式视为未就绪。程序不会静默退回旧的本地 Token 刷新链路；单项目会快速进入
现有浏览器降级，多项目会跳过该项目并记录明确原因。

在线更新包和完整安装器都不会携带真实 `client_key`。因此，在云端 Token 模式上线前已安装的
电脑，即使程序已更新，也必须由管理员提供当前 `.baidu-secrets`，在 GUI“系统 → 导入授权配置”
中完成一次安全迁移。不能从开发机手工复制单个 Token 字段，也不能把配置包放入发布包。

快速预检出现“云端 Token 配置未就绪，请导入管理员最新授权配置”时，应先重新导入当前配置包，
再执行 `test-baidu-api-readiness`。预检报告只记录配置项是否存在和是否匹配，不输出 URL、密钥或 Token。

## 导入并同步授权

本机拿到 `.baidu-auth` 文件后执行：

```cmd
.venv\Scripts\python.exe main.py --mode import-baidu-oauth --file "D:\Downloads\baidu_oauth_xxx.baidu-auth" --api-profile auto --sync-cloud-token-store
```

成功后会同时完成两件事：

1. 写入本机 `secrets/secrets.json`。
2. 上传对应 `api_profile` 到 COS 集中 token store。

导入完成后立即人工删除下载目录中的 `.baidu-auth` 文件。

## 敏感信息边界

- `.baidu-auth`、`secrets/secrets.json`、COS 中的总 store 和 `profiles/*.json` 都是敏感文件。
- 不得提交 Git，不得写入日志，不得打入发布包。
- `/token` 返回给桌面端的结果只包含 `access_token`，不会返回 `refresh_token`。

## 部署后验收

重新上传 SCF 包后执行：

```cmd
.venv\Scripts\python.exe main.py --mode test-baidu-api-readiness
```

该命令只读百度数据，不启动 Chrome、不读写业务 Excel。必须确认九个项目、十一个授权全部通过。

每台同事电脑首次迁移后也应执行一次。若开发机通过而同事端失败，优先比较预检报告里的
`api_profiles.cloud_ready` 与安全布尔字段，不要交换或查看真实密钥值。
