# 商务通本地 API 首期规格

## 目标

为“昆明牛”项目增加一个只读商务通数据源，绕过人工导出，但不绕过商务通自身的登录与授权。服务只处理由服务器自动推送或客户端启动自动同步确认过的会话，再复用当前客户端登录态查询会话详情，最终生成与现有 `kst_dialog_data.json` 相同的小时报统计结构。

## 首期范围

- 支持当前已验证客户端版本 `9.86.21`。
- 支持用户显式配置商务通安装根目录。
- 未配置时探测常见安装目录和正在运行进程的可执行文件路径。
- 自动发现当前用户的日志目录、身份目录和 `VISITOR.db`。
- 从日志识别两类允许来源：
  - WebSocket `msgType=48` 自动转历史事件；
  - `getLastVisitorList.do` 客户端启动自动同步响应。
- 仅从允许来源建立 `recId` 白名单。
- 通过只读 SQLCipher 查询补充推广 ID，不把本地数据库当成权威会话清单。
- 从日志内存解析当前服务地址、公共查询参数和请求头。
- 调用只读接口取得会话时间、访客消息数和名片标签。
- 按现有商务通统计口径生成小时报 JSON。
- 提供仅绑定 `127.0.0.1` 的本地 HTTP API。
- 现有人工导出流程保留为显式回退来源，不自动混合两类结果。

## 非目标

- 不修改商务通数据库、安装文件或进程内存。
- 不保存明文 `clientToken` 或完整认证请求头。
- 不抓取没有自动来源凭证的人工历史查询记录。
- 不在首期承诺所有商务通旧版本兼容。
- 不自动执行正式 Excel 写入。
- 本地 API 默认不返回聊天正文和访客个人信息。

## 数据流

1. `KstInstallationDiscovery` 根据显式根目录、进程路径和常见位置定位客户端。
2. `KstLogSource` 扫描当前身份日志，提取自动来源 `recId`、当前接口 URL、公共参数和请求头。
3. `KstDatabaseReader` 使用客户端自带 Electron/SQLCipher 模块，以只读模式查询指定日期的缓存候选行。
4. `KstConversationService` 对缓存候选行应用自动来源白名单，只保留可证明由服务器自动触发的 `recId`。
5. `KstApiClient` 对白名单会话调用 `visitorInfo/load` 和 `visitorCard/detail`，取得权威时间、访客消息数与标签 ID。
6. 服务把推广 ID、标签字典和权威字段组合成现有导出解析器可消费的标准行。
7. 复用 `aggregate_kst_export_rows` 生成账户统计。
8. CLI 可以直接写出 `reports/kst_dialog_data.json`；本地 API 可返回原始安全会话摘要或小时统计。

## 来源隔离规则

- 每个进入统计的 `recId` 必须包含 `websocket_msg_type_48` 或 `startup_auto_sync` 来源。
- 扫描数据库得到但没有上述来源的行必须丢弃。
- 来源集合与首次发现时间保留在内存结果和脱敏诊断中。
- 同一个 `recId` 可有多个来源，但只统计一次。
- 人工导出与本地 API 结果不能在一次运行中合并。

## 配置

项目配置新增可选字段：

```json
{
  "kst": {
    "data_source": "local_api",
    "installation_root": "D:\\Program Files (x86)\\KuaishangSoftx64\\OnlineWebCSNew",
    "local_api_url": "http://127.0.0.1:18766",
    "local_api_token_env": "KST_LOCAL_API_TOKEN"
  }
}
```

- `data_source` 缺省仍为 `export`，避免改变其他项目行为。
- `installation_root` 可省略，由发现器探测。
- API 访问令牌只从环境变量读取。
- 首期仅在昆明牛项目配置中启用 `local_api`。

## 本地 API

- `GET /health`
  - 不需要商务通数据正文；
  - 返回安装发现、登录态、版本支持和最近自动来源数量。
- `GET /v1/kst/conversations?date=YYYY-MM-DD`
  - 返回安全字段：`rec_id`、时间、推广 ID、访客消息数、标签、来源；
  - 不返回聊天正文、姓名、手机、微信或认证信息。
- `GET /v1/kst/hourly?project_id=kunming_niu&period=15点&date=YYYY-MM-DD`
  - 返回与 `kst_dialog_data.json` 相同的账户统计形状。
- 除 `/health` 外，配置了令牌时要求 `Authorization: Bearer ...`。
- 监听地址固定为 `127.0.0.1`，不得通过配置改为公网地址。

## 失败策略

- 未找到安装目录、日志、数据库或客户端 Electron：明确失败。
- 无当前登录态：明确失败，提示先登录商务通。
- 版本或数据库列不兼容：停止并输出能力诊断。
- API 请求失败：不生成“成功的零数据”结果。
- 某个会话字段缺失：记录该 `recId` 的脱敏错误并使本次采集失败。
- 没有自动来源记录但采集链路健康：允许返回真实零数据。

## 验收

- 单元测试覆盖目录发现、日志来源隔离、认证信息解析、API 响应适配、账户统计和 HTTP 鉴权。
- 固定样本验证人工历史查询记录不会因仅存在于数据库而进入统计。
- 2026-07-26 昆明牛回归样本保持 26/26 匹配。
- 运行时日志不出现 `clientToken` 值。
- 不修改商务通数据库。
- 不运行正式 Excel 写入。

## 后续版本兼容

首期的发现器和能力检查为后续版本扩展预留接口。后续 GUI 可让用户选择商务通根目录，然后递归搜索：

- `resources/app/package.json`
- `resources/app/node_modules/better-sqlite3-multiple-ciphers`
- 客户端 Electron 可执行文件
- `%LOCALAPPDATA%/OnlineWebCSNew/log/*`
- `%LOCALAPPDATA%/OnlineWebCSNew/db/*/VISITOR.db`

不同版本通过能力探测适配，而不是仅按版本号分支。
