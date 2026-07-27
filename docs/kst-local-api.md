# 快商通本地 API

## 使用方式

小时报 GUI 启动后会自动托管本机 API，固定监听：

```text
http://127.0.0.1:18766
```

默认使用 `API 自动获取`。全局模式在以下菜单切换：

```text
系统 > 快商通模式 > API 自动获取 / 人工导出对话
```

该设置对单项目和多项目共同生效，不再由各项目单独决定。实时日志右上角的 `● KST  ● 实时` 只显示服务状态，不可点击：

- 绿色：本机多项目 API 已启动；
- 灰色：API 未启动或基础身份注册表不可用。

## 多账号与项目映射

一台电脑可以同时登录多个快商通账号。程序会：

1. 自动查找常见快商通安装目录，也支持 `KST_INSTALLATION_ROOT` 指定根目录；
2. 枚举 `%LOCALAPPDATA%\OnlineWebCSNew` 下所有同时具有日志和 `VISITOR*.db` 的登录身份；
3. 以只读方式从每个身份的当前库和轮换库提取历史推广 ID；
4. 根据九个正式项目配置中的推广 ID 建立全局反向索引；
5. 仅在“一个身份只命中一个项目、一个项目只命中一个身份”时建立绑定。

推广 ID 在正式项目间必须唯一。以下情况均不猜测、不绑定：

- 同一推广 ID 出现在多个项目；
- 一个身份命中多个项目；
- 一个项目命中多个身份；
- 身份没有命中任何项目。

每次请求只接受 `project_id`，调用方不能指定身份。注册表会把项目请求路由到其唯一绑定身份的日志、认证快照和数据库，避免跨账号读取。

## 数据边界

进入统计的会话必须在快商通日志中具有服务器自动推送或启动自动同步凭证。仅由人工历史查询下载到本地、但没有自动来源凭证的数据库记录不会进入统计。

数据库连接强制使用 SQLCipher `readonly` 和 `fileMustExist`：

- 推广 ID 桥只读取 `visitorCustomField` 与 `info`；
- 不执行 INSERT、UPDATE 或 DELETE；
- 不返回聊天正文、姓名、手机、微信或认证令牌；
- 会话详情和标签通过对应登录身份的只读服务端接口查询。

## 小时报调用

```text
GET /v1/kst/hourly?project_id=<项目ID>&date=YYYY-MM-DD&period=15点
```

`project_id` 必填。响应的项目 ID 必须与请求项目完全一致，否则小时报把该响应视为不可用，禁止合并。

API 模式下，某项目出现未绑定、歧义、服务不可用或响应不完整时：

- 该项目快商通五项指标按 0；
- 百度数据继续；
- 不检查、也不隐式读取旧人工导出文件。

人工恢复只能显式选择 `系统 > 快商通模式 > 人工导出对话`。人工模式继续使用原有最近导出文件流程，并且不会调用本地 API。

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:18766/health
```

关键字段：

- `status=ok`
- `required_endpoints_available=true`
- `project_routing=true`
- `identity_count`
- `bound_project_ids`
- `unbound_project_ids`
- `mapping_error_count`

健康信息只包含数量和项目 ID，不包含原始推广 ID、身份目录名或认证数据。

## 版本与路径

发现器会检查：

- `resources/app/package.json`
- `OnlineWebCS.exe` 或 `OnlineWebCSNew.exe`
- `better-sqlite3-multiple-ciphers`
- `%LOCALAPPDATA%\OnlineWebCSNew\log\<identity>`
- `%LOCALAPPDATA%\OnlineWebCSNew\db\<identity>\VISITOR*.db`

已实机验证快商通 `9.86.21`。其他版本若文件结构或表结构不同，会安全停止该身份绑定并让对应项目按 0 继续，不会写入或迁移快商通文件。

## 昆明牛验收基准

2026-07-26 的只读验证：

- API 自动来源 26 条；
- 人工完整导出 26 条；
- 未归属双方均为 0；
- 账户分布 14 / 1 / 11；
- 五项小时指标逐账户完全一致。
