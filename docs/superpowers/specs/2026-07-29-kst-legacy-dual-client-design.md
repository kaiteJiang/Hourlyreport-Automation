# v116 快商通新旧双客户端兼容设计

## 目标

版本 `2026.7.29.116` 在保留现有快商通 Electron 客户端读取链路的同时，兼容
`OnlineCS.exe 7.03.17` 代表的 Java/JCEF 老客户端。程序必须自动识别客户端代际，
以只读方式读取对应本机数据，继续复用现有小时报、日报标签聚合口径，并修复本地
API 无条件启动、无限刷屏和停止阶段卡顿的问题。

## 已确认的兼容矩阵

| 客户端家族 | 识别特征 | 数据根目录 | 主要数据库 |
| --- | --- | --- | --- |
| `electron` | `OnlineWebCS.exe` 或 `OnlineWebCSNew.exe`，存在 `resources/app/package.json` | `%LOCALAPPDATA%\OnlineWebCSNew` | `VISITOR*.db` |
| `legacy_java` | `OnlineCS.exe`，安装目录存在 `config/DBCOMPANY.dll` 和内置 Java/JCEF | Windows“文档”目录下 `KuaiShangDataNew` | `<公司身份>_HIS.cdb`、`*-onlie/*_CS.pdb` |

老版安装目录中的 `config/*.dll` 实际是空 SQLite 建库模板，不能作为业务数据源。
真实数据位于 `KuaiShangDataNew/db`。已验证老版 `.cdb/.pdb` 为标准 SQLite 3，
可由 Python 标准库以只读连接打开，不引入新的发布依赖。

未知客户端结构不得猜测兼容，也不得返回伪成功或静默使用旧导出文件。

## 选定架构

采用“统一注册表、独立适配器”：

1. 保留现有 Electron 发现、日志凭证、SQLCipher 桥和服务端只读接口链路。
2. 新增隔离的 `legacy_java` 发现器和 SQLite 读取器。
3. 身份注册表只消费统一的安装身份协议，通过客户端家族分派读取、就绪检查和运行时
   构建，不把老版条件塞进 Electron SQL 和日志解析器。
4. 两个适配器都输出现有 `KstConversation` 安全模型，再复用
   `aggregate_kst_export_rows` 和 `aggregate_kst_daily_rows`。
5. 本地 HTTP API、项目路由、推广 ID 白名单、令牌和报告格式保持不变。

不采用以下方案：

- 只增加 `KST_INSTALLATION_ROOT`：老版没有 Electron、`package.json` 和
  `VISITOR*.db`，必然仍被拒绝。
- 把老版判断直接堆入现有 `db_reader.py`：会混合两套数据库语义，增加新版回归风险。
- 启动或控制快商通客户端：超出本产品只读边界。程序只启动自己的
  `127.0.0.1:18766` 本地 API。

## 本机路径设置

新增受 Git 和发布包排除的 `runtime/kst_machine_settings.json`，只允许保存：

```json
{
  "installation_root": "D:\\Program Files (x86)\\KuaishangSoft\\OnlineCustomerService",
  "data_root": "D:\\Documents\\KuaiShangDataNew"
}
```

不得在该文件保存账号、密码、Token、聊天正文、推广 ID 或项目绑定。

发现优先级：

1. 本机设置中的显式路径；
2. 兼容保留的 `KST_INSTALLATION_ROOT` 环境变量；
3. 正在运行的受支持快商通进程路径；
4. 现有 Electron 常见目录；
5. Windows 注册表的“个人文档”重定向目录及常见文档目录下的
   `KuaiShangDataNew`。

显式路径无效时必须报告具体缺失能力，不得回退到另一条含糊路径后假装成功。

“系统 → 快商通模式”菜单增加：

- 选择快商通程序目录；
- 选择快商通数据目录；
- 重新扫描快商通。

选择器接受目录，不接收单个数据库文件。路径保存后触发异步重新扫描，不阻塞 GUI。

## 老版身份发现

`legacy_java` 身份仅在以下条件全部满足时可用：

1. `OnlineCS.exe` 正在运行，且进程路径属于已识别安装根目录；
2. 数据根目录包含 `db` 和 `logs`；
3. `logs` 中存在最近十五分钟内更新的活动日志；
4. 每个候选公司目录具有 `<公司身份>_HIS.cdb`；
5. 至少存在一个 `*-onlie/*_CS.pdb` 对话分片；
6. 数据库通过只读能力探测，具有规定表和字段。

客户端未运行时不得读取可能过期的落盘结果。一个数据根目录可发现多个公司身份，
每个公司目录形成独立身份候选。项目仍通过推广 ID 唯一反向绑定；歧义、多项目命中、
同项目多身份命中或无命中都不得猜测。

## 老版数据可信边界

老版主历史库可能包含人工历史查询缓存，因此不能单独把
`OC_HDVISITORINFO` 当作权威会话清单。

允许进入统计的 `recId` 必须先出现在任一 `*-onlie/*_CS.pdb` 的
`DIALOGRECORD_VISITOR` 中，且该分片记录的 `addTime` 属于目标日期。该分片是老客户端
由服务器推送到本机的即时会话凭证。随后仅用
`<公司身份>_HIS.cdb` 的 `OC_HDVISITORINFO` 补充：

- 开始时间；
- 访客消息数；
- 推广 ID；
- 搜索词与竞价词；
- 标签字段。

主历史库中存在、但没有即时分片凭证的人工历史记录必须丢弃。即时分片存在但主历史
记录尚未同步完整时，本项目快商通读取判定为不完整，不允许静默少算；按既有规则该
项目快商通指标置零、百度数据继续。

数据库连接必须满足：

- URI `mode=ro`；
- `busy_timeout` 最多 500 毫秒；
- 禁止执行 `CREATE`、`INSERT`、`UPDATE`、`DELETE`、`VACUUM`、`PRAGMA`
  写操作；
- 单身份查询总截止时间五秒，并在每个数据库与查询阶段检查协作取消事件；
- 不复制数据库到发布目录、日志、报告或诊断包。

## 老版字段归一化

老版会话由以下字段构造统一模型：

- `recId` → `rec_id`；
- `curEnterTime`，缺失时使用 `diaStartTime` → `start_time`；
- `visitorSendNum` → `visitor_messages`；
- `visitorCustomField` 中的“推广 ID” → `promotion_id`；
- `keyword` → `keyword`；
- `bidWord` → `bid_word`；
- `talkGrade`、`dialogClassification`、`classifyTag`、`cusTypeTag`、`aiTags`
  合并为标签集合。

标签字段允许 JSON 数组、JSON 对象、中文顿号、逗号、分号、竖线和换行分隔。归一化
后去空白、去重，但不改写标签文字。`有效-三句话`、`有效-一般`、`转潜-有效` 等标签
继续交给现有聚合器处理，因此小时报和日报口径与新版完全一致。

缺失推广 ID、推广 ID 不属于项目白名单、负消息数、非法时间或无法解析的关键字段
均视为不完整，不把记录分配给其他项目。

## GUI 与服务生命周期

本地 API 管理器只在全局 `kst_data_source=local_api` 时启动：

- GUI 启动且模式为 API：异步启动或接管本地服务；
- 切换到人工导出：立即停止重试并异步释放自有服务；
- 切回 API：重新扫描并启动；
- GUI 真正退出：发出停止信号，清理服务；托盘隐藏不停止。

停止动作不得在 GUI 线程等待最长五秒。工作线程和数据库扫描在阶段边界检查停止事件；
HTTP 本地请求超时固定为十五秒。用户停止业务任务后，快商通阶段必须在十五秒内返回，
不得无限等待数据库锁或本地服务。

## 错误信息、重试和防刷屏

启动异常不得被统一吞成“暂不可用”。对用户输出脱敏分类：

- 未找到支持的客户端；
- 客户端未运行；
- 程序目录与运行进程不匹配；
- 未找到 `KuaiShangDataNew`；
- 数据库结构不兼容；
- 没有活动身份；
- 项目推广 ID 无法唯一绑定；
- 本地端口被不兼容进程占用；
- 数据库忙或读取超时。

重试使用 `5s → 15s → 30s → 60s` 上限退避。相同错误只在首次、错误变化和五分钟
状态提醒时写实时日志；状态灯和悬停详情仍可随时反映最新原因。成功后重置退避与去重
状态。

## 测试设计

新增完全合成、不含真实业务数据的 SQLite fixture，覆盖：

1. 老版程序与数据目录自动/显式发现；
2. 显式无效路径不静默回退；
3. 未运行 `OnlineCS.exe` 时拒绝陈旧数据；
4. 多公司身份和推广 ID 唯一绑定；
5. 只接受即时分片授权的 `recId`；
6. 人工历史库孤立记录不进入统计；
7. 主历史详情缺失时判定不完整；
8. 标签多格式归一化及五项小时报、日报口径；
9. 数据库只读、锁等待有界和取消；
10. Electron 现有发现、身份映射和报告结果不变；
11. 人工导出模式不启动 API；
12. 模式切换正确启停；
13. 相同失败不会每五秒刷屏；
14. 错误变化立即记录；
15. 停止不阻塞 GUI。

不得把本机 `D:\Documents\KuaiShangDataNew` 或真实数据库复制进测试、Git 或发布包。
本机样本只用于已完成的结构确认，不作为自动化测试依赖。

## 版本、文档和发布

发布版本固定为 `2026.7.29.116`。发布时必须：

1. 更新 `gui/version.py`；
2. 同步 `README.md`、`README_同事使用说明.md`、`docs/kst-local-api.md`；
3. 新增 `docs/releases/2026.7.29.116.md`；
4. 重新构建 `hourlyreport_automation.exe`；
5. 生成 `Hourlyreport_automation_v2026.7.29.116.zip`；
6. 生成 `Hourlyreport_automation_setup_v2026.7.29.116.exe`；
7. 验证更新包不覆盖或携带 `configs/`、`runtime/`、`secrets/`、`logs/`、
   `reports/`、`backups/`、`diagnostics/`、`kst_exports/` 和
   `browser_profile/`；
8. 验证发布包不包含真实快商通数据库、日志、路径设置或用户数据；
9. 用更新器逻辑验证 v116 Release 元数据；
10. 提交并推送 GitHub；Release 发布仍由用户手动完成。

## 验收标准

- v115 已支持的 Electron `9.86.21` 链路回归通过；
- 老版 `OnlineCS.exe 7.03.17` 能通过程序路径和数据目录建立本地 API；
- 老版合成样本的有效、一般有效、有效转潜、总转潜与现有标签聚合器结果一致；
- 老版人工历史孤立记录不进入统计；
- 无效或不完整数据按零继续百度流程，不导致 GUI 卡死；
- 人工导出模式没有后台 KST API 重试；
- 连续失败不刷屏，错误原因可诊断；
- 全量测试、安装包构建、更新包审计和安装器审计全部通过。
