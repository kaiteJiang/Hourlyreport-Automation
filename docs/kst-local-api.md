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

1. 自动识别 Electron `OnlineWebCS.exe` / `OnlineWebCSNew.exe`（安装目录含 `resources/app/package.json`）与旧 Java/JCEF `OnlineCS.exe`（安装目录含 `config/DBCOMPANY.dll`）；也兼容 `KST_INSTALLATION_ROOT` 指定程序目录；
2. 确认受支持的快商通客户端进程正在运行。Electron 枚举 `%LOCALAPPDATA%\OnlineWebCSNew` 下近期仍在写日志且具有 `VISITOR*.db` 的登录身份；旧客户端读取 Windows“文档”目录下 `KuaiShangDataNew` 的身份数据。人工指定旧版数据目录时应选择 `KuaiShangDataNew`，不要选择其下的 `db\<身份>`；
3. 以只读方式从每个身份的当前库和轮换库提取历史推广 ID；
4. 根据九个正式项目配置中的推广 ID 建立全局反向索引；
5. 仅在“一个身份只命中一个项目、一个项目只命中一个身份”时建立绑定。

推广 ID 在正式项目间必须唯一。以下情况均不猜测、不绑定：

- 同一推广 ID 出现在多个项目；
- 一个身份命中多个项目；
- 一个项目命中多个身份；
- 身份没有命中任何项目。

每次请求只接受 `project_id`，调用方不能指定身份。注册表会把项目请求路由到其唯一绑定身份的日志、认证快照和数据库，避免跨账号读取。

活跃日志窗口默认 300 秒，可由管理员通过 `KST_ACTIVE_LOG_MAX_AGE_SECONDS` 调整；GUI 运行期间会周期刷新注册表，因此新增登录、退出或切换账号不需要重启小时报程序。

自动发现失败或安装位置变更时，展开用户可见的 `系统 > 快商通模式`，直接选择“选择快商通程序目录”“选择快商通数据目录”，或点击“重新扫描快商通”。重新扫描入口在人工导出模式下也可用，点击后会切回本地 API 并执行扫描。目录状态只在界面中提示，不会自动弹出并可能被其他窗口遮挡的选择框。选择器只接受目录；人工保存的数据目录会参与 Electron 与旧客户端的后续发现。指定旧版目录时仍会自动扫描 Electron 新版；新旧客户端可以并存，某一路未运行只保留诊断，不会屏蔽另一路的健康身份。旧客户端的数据根目录为 `KuaiShangDataNew`，真实业务数据在其 `db` 下；不得复制、迁移或手动选择其中的数据库文件。

## 数据边界

进入统计的会话必须具有自动来源凭证。Electron 会话须在快商通日志中具有服务器自动推送或启动自动同步凭证；旧 Java/JCEF 会话须先出现在目标日期的 `*-onlie/*_CS.pdb` 即时分片。仅由人工历史查询下载到本地、没有这些凭证的记录不会进入统计。

两类客户端均为只读：

- Electron 的 SQLCipher 桥使用 `readonly` 和 `fileMustExist`，推广 ID 桥只读取 `visitorCustomField` 与 `info`；会话详情和标签通过对应登录身份的只读服务端接口查询。
- 旧 Java/JCEF 的 `.cdb/.pdb` 为标准 SQLite，以 `mode=ro` 打开；读取每个 `*-onlie` 下标准命名的 `*CS.pdb`（包括编号分片，排除带 `(1)` 的复制副本），仅以 `recType=1` 的真实访客消息授权会话，再从根历史库和 `his\YYYY-MM_HIS.cdb` 月归档补充字段、推广 ID 与标签。推广 ID 优先读取 `visitorCustomField`，并兼容旧版 `info` 字段；明确的直接来源会话不进入百度项目统计。
- 不执行 INSERT、UPDATE 或 DELETE，不返回聊天正文、姓名、手机、微信或认证令牌。

Electron 客户端运行期间会持续更新现有数据库及 WAL/SHM/journal 旁路文件。身份校验允许同一数据库路径的文件状态正常变化，并由运行态状态变化触发缓存重建；安装根、登录身份或数据库路径集合变化仍会使绑定失效。旧 Java/JCEF 数据库继续执行完整文件指纹比较，读取期间变化时安全拒绝绑定。

## 性能与认证安全

Electron 运行时可以从同一登录身份的历史日志恢复服务端接口 URL，以避免因当天日志没有再次记录 URL 而等待；但公共查询参数、请求头和 Token 只允许从当天日志取得，不得跨日复用。当天认证材料缺失或不完整时，该身份不会进入可用注册表，KST 状态保持灰色。

Electron 日报采集按不同会话最多 4 路并发。日志具备 `visitor_info` 时，单个会话内部仍严格依次调用 `visitor_info`、`visitor_card`；个别身份未记录 `visitor_info`、但当前认证和其余必需接口完整时，改用只读数据库中已校验的开始时间、推广 ID、访客消息数和标签 ID，不猜测接口地址。两类客户端任一会话查询失败或返回不完整时，均丢弃该项目的全部快商通临时结果，整项目快商通指标按 0 继续，不输出部分统计。

## 小时报调用

```text
GET /v1/kst/hourly?project_id=<项目ID>&date=YYYY-MM-DD&period=15点
```

`project_id` 必填。响应的项目 ID 必须与请求项目完全一致，否则小时报把该响应视为不可用，禁止合并。

API 模式下，某项目出现未绑定、歧义、服务不可用、客户端未运行、程序目录或数据目录无效、数据库结构不兼容、端口冲突或响应不完整时：

- 该项目快商通五项指标按 0；
- 百度数据继续；
- 不检查、也不隐式读取旧人工导出文件。

人工恢复只能显式选择 `系统 > 快商通模式 > 人工导出对话`。人工模式继续使用原有最近导出文件流程，并且不会调用本地 API。

本地 API 的脱敏状态类别包括：认证配置无效、客户端目录无效、数据目录无效、客户端未运行、数据库结构不兼容、身份映射未就绪、端口被占用与启动失败。旧版历史库或消息库缺少核心表/字段时，实时日志会显示脱敏后的具体缺失项。持续未就绪时按 `5 秒 → 15 秒 → 30 秒 → 60 秒` 上限退避；错误类别日志仍去重，但每次失败都会显示当前问题和下一次重试时间，成功后重置。切到“人工导出对话”会停止本地 API 的启动与重试。

客户端请求地址固定为 `http://127.0.0.1:18766`。令牌优先读取 `KST_LOCAL_API_TOKEN`；未设置时自动生成到被 Git 和发布包排除的 `runtime/kst_local_api_token`，供同一安装目录下的 GUI 与 HERMES/CLI 安全复用。健康检查与数据接口都必须通过令牌认证，项目配置不能把令牌转发给其他本机端口。

## 健康检查

```powershell
$kstToken = (Get-Content runtime\kst_local_api_token -Raw).Trim()
Invoke-RestMethod http://127.0.0.1:18766/health -Headers @{Authorization = "Bearer $kstToken"}
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

- Electron：`OnlineWebCS.exe` 或 `OnlineWebCSNew.exe`、`resources/app/package.json`、`better-sqlite3-multiple-ciphers`、`%LOCALAPPDATA%\OnlineWebCSNew\log\<identity>`、`%LOCALAPPDATA%\OnlineWebCSNew\db\<identity>\VISITOR*.db`
- 旧 Java/JCEF：`OnlineCS.exe`、`config/DBCOMPANY.dll`、Windows“文档”下 `KuaiShangDataNew\db` 的 `<公司身份>_HIS.cdb` 与 `*-onlie\*_CS.pdb`

Electron `9.86.21` 已完成既有只读验证；旧 Java/JCEF `7.03.17` 已用真实只读副本验证根历史库、月归档和编号即时分片。旧版历史库缺少关键词、竞价词或标签类可选字段时以空值兼容；会话编号、会话时间、访客消息数、推广 ID 来源，或消息库会话编号/消息时间/消息类型等核心字段缺失时，仍安全停止该身份绑定并让对应项目按 0 继续。新登录产生的空壳身份会保留脱敏诊断，但不会阻断同一数据根下其他已完整验证的身份；如果没有任何完整身份，仍整体保持未就绪。程序不会写入或迁移快商通文件；真实数据口径仍应由业务同事按本机环境验收。

## 昆明牛验收基准

2026-07-26 的只读验证：

- API 自动来源 26 条；
- 人工完整导出 26 条；
- 未归属双方均为 0；
- 账户分布 14 / 1 / 11；
- 五项小时指标逐账户完全一致。
