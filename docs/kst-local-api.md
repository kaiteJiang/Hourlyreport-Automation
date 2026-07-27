# 商务通本地 API

## 当前支持范围

- 首期项目：昆明牛。
- 已验证商务通版本：`9.86.21`。
- 数据用途：小时报。
- 数据来源：服务器自动转历史事件或客户端启动自动同步。
- 人工点击历史查询产生、但没有自动来源凭证的本地记录不会进入统计。
- 商务通数据库仅以只读方式补充推广 ID；会话时间、访客消息数和名片标签通过当前登录态查询服务器。

## 前置条件

1. 商务通客户端已经启动并登录昆明牛账号。
2. 当前配置身份为 `733875_1269870`。
3. 商务通安装根目录可访问。
4. 小时报工具与本地 API 在同一台 Windows 电脑运行。

昆明牛当前配置：

```json
{
  "kst": {
    "data_source": "local_api",
    "installation_root": "D:\\Program Files (x86)\\KuaishangSoftx64\\OnlineWebCSNew",
    "identity": "733875_1269870",
    "local_api_url": "http://127.0.0.1:18766",
    "local_api_token_env": "KST_LOCAL_API_TOKEN"
  }
}
```

安装位置变化时，只需要修改 `installation_root`。删除该字段后，程序会检查环境变量和常见安装目录。身份有多个时建议显式填写 `identity`，避免误读其他项目。

## 启动本地 API

在小时报程序目录运行：

```powershell
.\.venv\Scripts\python.exe main.py `
  --mode serve-kst-local `
  --project kunming_niu `
  --kst-root "D:\Program Files (x86)\KuaishangSoftx64\OnlineWebCSNew"
```

服务固定监听：

```text
http://127.0.0.1:18766
```

不能绑定 `0.0.0.0` 或局域网地址。

如需访问令牌，在启动服务和运行小时报的同一用户环境中设置：

```powershell
$env:KST_LOCAL_API_TOKEN = "由管理员生成的随机值"
```

令牌不写入项目配置或日志。

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:18766/health
```

关键字段：

- `status=ok`：安装、日志、身份和必需接口已发现。
- `required_endpoints_available=true`：当前日志包含必需接口地址。
- `automatic_conversation_count`：日志中发现的自动来源会话数量。
- `database_count`：当前库和轮换 `VISITOR_*.db` 的数量。

健康检查不返回访客个人信息、聊天正文或登录令牌。

## 单独读取小时数据

无需启动 HTTP 服务，也可以直接执行一次只读采集：

```powershell
.\.venv\Scripts\python.exe main.py `
  --mode fetch-kst-local `
  --project kunming_niu `
  --date 2026-07-26 `
  --period 15点 `
  --kst-root "D:\Program Files (x86)\KuaishangSoftx64\OnlineWebCSNew"
```

输出：

```text
reports/kst_dialog_data.json
```

该模式不抓百度数据、不合并、不写 Excel。

## 小时报接入

当昆明牛配置为 `kst.data_source=local_api` 时，小时报步骤 2 会调用：

```text
GET /v1/kst/hourly?project_id=kunming_niu&date=YYYY-MM-DD&period=15点
```

本地 API 结果写成原有 `kst_dialog_data.json` 结构，后续百度合并与 Excel 逻辑无需改变。

人工导出回退必须显式改为：

```json
{
  "kst": {
    "data_source": "export"
  }
}
```

程序不会自动混合或自动回退，避免重复统计或把旧导出误当成实时结果。

## 版本和路径诊断

发现器检查：

- `resources/app/package.json`
- `OnlineWebCS.exe` 或 `OnlineWebCSNew.exe`
- `better-sqlite3-multiple-ciphers`
- `%LOCALAPPDATA%\OnlineWebCSNew\log\<identity>`
- `%LOCALAPPDATA%\OnlineWebCSNew\db\<identity>\VISITOR*.db`

数据库桥会检查访客表和必需列。结构不兼容时停止运行，不输出成功零数据。

后续 GUI 可以把 `installation_root` 接成目录选择框；底层发现接口已经支持显式根目录和自动探测。

## 2026-07-26 验收基准

- 本地 API 自动来源：26 条。
- 人工完整导出：26 条。
- 未归属：双方均为 0。
- 账户分布：
  - 银康01：14。
  - 银康银屑02：1。
  - 银康03：11。
- 五项小时指标逐账户完全一致。

## GUI 自动托管与来源切换

启动小时报 GUI 后，程序会在后台自动检查 `127.0.0.1:18766`：

- 如果已有兼容服务，直接复用，不在退出时关闭外部服务。
- 如果没有服务，GUI 自动启动本地 API；启动失败后每 15 秒重试。
- 隐藏到托盘不会停止 API；真正退出程序时只停止 GUI 自己创建的服务。
- 实时日志右上角显示 `● KST  ● 实时`。KST 圆点绿色表示 API 正常，灰色表示尚未启动或不可用。

点击 `● KST` 可在两种互斥来源间切换：

- `API 自动获取`：小时报直接读取服务器自动推送来源，不使用人工历史查询缓存。
- `人工导出`：继续使用原有商务通导出文件流程，作为应急回退。

昆明牛启用 `allow_zero_on_unavailable=true`。当选择 API 自动获取但 API 暂时不可用时，商务通五项指标按 0 继续，百度数据仍可生成小时报；不会自动读取旧人工导出文件。日志会明确提示“商务通 API 不可用，已按 0 继续”。

## 安全边界

- 不修改商务通数据库、安装目录或进程内存。
- SQLCipher 连接强制 `readonly` 和 `fileMustExist`。
- 认证信息仅从当前日志载入内存，不持久化。
- API 默认不返回聊天正文、姓名、手机或微信。
- 本地 API 只允许回环地址。
- 任一白名单会话查询失败时整次采集失败，不伪装为零数据。
