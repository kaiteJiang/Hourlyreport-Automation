# 本地快商通 API 启动解决方案

> 适用版本：`2026.7.31.118` 及后续维护版
> 整理日期：2026-08-02
> 目标：让 WorkBuddy 或其他 Agent 在同事自己的电脑上，先识别本机快商通客户端和数据结构，再决定是否可以接入本地只读 API。

## 先说结论

快商通本地 API 不是“把数据库文件复制到新版客户端目录，再套一个固定 SQL”这么简单。它依赖三件必须同时成立的事实：

1. 本机正在运行的客户端家族和版本；
2. 该客户端原生生成的登录身份、日志和数据库目录；
3. 该版本数据库实际存在且可读的表、字段和数据语义。

站点 ID 可以作为项目身份映射的第一锚点，但不能解决数据库结构不兼容。推广 ID 仍用于账户级映射，数据库结构仍必须按本机版本验证。

截至目前，自己的机器上已经完成过客户端发现、站点 ID 映射、API 注册表和只读读取链路的代码验证；同事电脑上没有一次稳定的“登录客户端 → KST 灯变绿 → 小时报/日报 API 取数成功”的端到端验收案例。因此，本文件是可复用的落地排障方案，不应被理解为对所有快商通版本的兼容承诺。

## 1. 固定边界

- 只读快商通数据，不执行 `INSERT`、`UPDATE`、`DELETE`。
- 不启动、关闭或控制快商通客户端；客户端必须由用户自行启动并登录。
- 不复制、迁移、覆盖或改名快商通数据库；尤其不能把老客户端的 `db\<身份>` 复制进新版客户端目录。
- 不上传完整数据库、聊天正文、Token、Cookie、密码或登录日志。
- API 服务只监听 `http://127.0.0.1:18766`，不向局域网开放。
- API、身份映射或数据库能力检查失败时，停止该项目；不能猜列名、混用旧导出文件或手工补 Excel 数字。

## 2. 先判断是哪一类客户端

| 客户端家族 | 程序特征 | 安装根目录特征 | 原生数据位置 | 读取方式 |
| --- | --- | --- | --- | --- |
| 新版 Electron | `OnlineWebCS.exe` 或 `OnlineWebCSNew.exe` | `resources/app/package.json`、`better-sqlite3-multiple-ciphers` | `%LOCALAPPDATA%\OnlineWebCSNew`，身份目录通常同时有 `log\<identity>` 和 `db\<identity>\VISITOR*.db` | 使用客户端自带 Electron/SQLite Cipher 桥，只读读取 |
| 老版 Java/JCEF | `OnlineCS.exe` | `config\DBCOMPANY.dll` 且该文件是 SQLite 头 | `Documents\KuaiShangDataNew`，业务数据在 `db\<identity>` 下 | Python SQLite `mode=ro`，读取老版历史库和消息库 |

老版典型结构：

```text
KuaiShangDataNew\
├─ db\
│  └─ <站点身份>\
│     ├─ <身份>_HIS.cdb
│     ├─ his\YYYY-MM_HIS.cdb
│     └─ *-onlie\*_CS.pdb
└─ logs\
```

不要只看 `db\733875` 这样的文件夹名就认为已经可读。还必须确认客户端进程、活动日志、历史库、消息库和必要字段全部存在。

## 3. 两层身份映射规则

### 3.1 第一层：站点 ID 映射项目

站点 ID 是一个项目对应一个快商通站点的唯一值，建议写入项目配置：

```json
{
  "kst": {
    "site_id": "733875"
  }
}
```

当前已知参考值：

| 项目 | 站点 ID | 说明 |
| --- | --- | --- |
| 昆明牛 | `733875` | 曾出现身份映射失败 |
| 沈阳牛 | `981765` | 本机曾验证过站点 ID 映射 |
| 沈阳白 | `223699` | 同事电脑上出现同样失败 |
| 长沙牛 | `202668` | 本地配置中的站点 ID，曾出现 KST 灯不亮 |

程序从客户端身份串的数字前缀提取站点 ID，例如 `<site_id>_...`，再与项目配置严格比较：

- 站点 ID 匹配，才允许把身份候选归入该项目；
- 同一个站点 ID 配置到多个项目，直接报错；
- 站点 ID 缺失、无法提取或冲突，不猜项目。

### 3.2 第二层：推广 ID 映射账户

站点 ID 只能确定“这是哪个项目”，不能确定每一行属于哪个账户。账户级映射必须从本机真实数据库提取推广 ID，并与项目配置的 `kst_ids` 做一对一校验。

- Electron：优先从 `visitorCustomField`，兼容旧版 `info` 中提取推广 ID。
- 老版：从 `OC_HDVISITORINFO` 的 `visitorCustomField` 或 `info` 中匹配“推广 ID: 数字”。
- 一个身份命中多个项目、一个项目命中多个身份、推广 ID 跨项目重复，均不绑定。
- 只按账户名称相似度映射是不安全的，名称变更或同名会造成串数。

## 4. 在同事电脑上的正确落地顺序

### 第 0 步：建立只读现场快照

让 WorkBuddy 先输出本机清单，不要先修改代码：

- Windows 版本、Python 版本；
- 正在运行的快商通进程名和完整程序路径；
- 客户端版本号；
- 安装根目录；
- 数据根目录；
- 登录身份目录名；
- 站点 ID；
- 数据库文件名、大小、修改时间；
- 仅输出表名、字段名、行数和脱敏后的 ID，不输出聊天正文。

PowerShell 只读检查示例：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('OnlineCS.exe', 'OnlineWebCS.exe', 'OnlineWebCSNew.exe') } |
  Select-Object Name, ExecutablePath, CommandLine

Get-NetTCPConnection -LocalPort 18766 -State Listen -ErrorAction SilentlyContinue
```

### 第 1 步：启动并登录原生客户端

1. 关闭与当前项目无关的快商通客户端，避免多个身份同时竞争。
2. 使用同事自己的客户端登录目标站点。
3. 等待登录后日志和数据库至少产生一次新的修改时间。
4. 记录登录后出现的身份目录名及其数字前缀。

登录成功不等于 API 可用；必须继续做数据库能力检查和推广 ID 读取。

### 第 2 步：填写正确的两个根目录

- 安装根目录：包含 `OnlineCS.exe` 或 `OnlineWebCS.exe` 的目录；
- 数据根目录：老版选择 `KuaiShangDataNew`，不是 `KuaiShangDataNew\db\<身份>`，更不是某一个 `.cdb/.pdb` 文件；
- 新版实际数据根为 `%LOCALAPPDATA%\OnlineWebCSNew`；GUI 可选择该目录或其上级 `%LOCALAPPDATA%`，但不要把老版 `KuaiShangDataNew` 伪装成 Electron 目录。

历史排障中出现过的参考路径：

```text
D:\KuaishangSoft\OnlineCustomerService
D:\Documents\KuaiShangDataNew
D:\Documents\KuaiShangDataNew\db\155707
D:\Documents\KuaiShangDataNew\db\733875
```

这些路径只说明当时的机器布局，不能直接复制到另一台电脑使用。

### 第 3 步：做版本和结构指纹

#### Electron

确认以下文件存在并记录版本：

```text
<安装根目录>\resources\app\package.json
<安装根目录>\resources\app\node_modules\better-sqlite3-multiple-ciphers
<数据根目录>\log\<identity>
<数据根目录>\db\<identity>\VISITOR*.db
```

读取推广 ID 时必须调用该客户端对应的 Electron 和数据库模块，不能用普通 Python SQLite 猜解密库。

#### 老版 Java/JCEF

至少验证：

```text
<安装根目录>\OnlineCS.exe
<安装根目录>\config\DBCOMPANY.dll
<数据根目录>\db\<identity>\<identity>_HIS.cdb
<数据根目录>\db\<identity>\*-onlie\*_CS.pdb
```

消息库必须有 `DIALOGRECORD_VISITOR`，至少包含：

```text
recId, addTime, recType
```

历史库必须有 `OC_HDVISITORINFO`，至少包含：

```text
recId, visitorSendNum, visitorCustomField
```

并且必须存在 `curEnterTime` 或 `diaStartTime` 之一。缺表或缺字段就是数据库结构不兼容，不要靠改 SQL 绕过。

### 第 4 步：先验证身份，再验证 API

项目配置完成后，先做身份注册表刷新，再检查健康状态。健康状态至少满足：

```text
status=ok
required_endpoints_available=true
bound_project_ids 包含目标项目
mapping_error_count=0（或能解释的其他身份无关诊断）
```

API 监听端口打开只代表服务进程启动，不代表 KST 灯应该变绿。KST 变绿需要“客户端运行、活动日志、数据库可读、推广 ID 可提取、项目映射唯一、必要接口可用”全部成立。

### 第 5 步：用小范围请求验收

先调用一个目标日期、一个小时段，不要立即跑整套 Excel：

```text
GET /v1/kst/hourly?project_id=<项目ID>&date=YYYY-MM-DD&period=15点
GET /v1/kst/daily?project_id=<项目ID>&date=YYYY-MM-DD
```

响应必须同时满足：

- `source` 为 `kst_local_api`；
- `project_id`、日期、时段与请求完全一致；
- `accounts` 包含项目要求的全部账户；
- 没有 `errors`；
- 统计结果完整，不是部分数据库、部分浏览器或旧导出拼接。

只有这一步通过后，才允许进入 GUI 小时报/日报和 Excel 写入流程。

## 5. 当前失败案例和根因

| 案例 | 表现 | 实际根因 | 正确结论 |
| --- | --- | --- | --- |
| 把同事的老数据复制到本机新版目录 | 解析后仍不运行，或提示数据库不兼容 | 客户端家族、加密方式、目录约定和 schema 不一致；复制还会破坏“运行进程—数据目录”的对应关系 | 必须在同事原机读取原生客户端，不做跨机迁移 |
| 直接选择 `db\155707` 或 `db\733875` | 找不到身份、数据目录无效、KST 灯不亮 | 设置项要的是数据根目录，不是单个身份子目录或数据库文件 | 老版选择 `KuaiShangDataNew` |
| 登录老客户端后只看到新建的 `db\733875` | 认为已有数据库就应可解析 | 目录存在不代表历史库、消息库、日志和字段完整 | 继续做活动日志、表结构和字段能力检查 |
| 运行的是 `D:\KuaishangSoft\OnlineCustomerService`，配置却指向新版目录 | 提示客户端未运行或路径不匹配 | 运行进程路径与配置安装根目录不是同一程序 | 以正在运行的 `OnlineCS.exe` 完整路径为准 |
| 沈阳白站点 ID `223699` 在同事电脑上仍失败 | API 启动提示出现，但 KST 灯不绿或随后反复重试 | 站点 ID 只能改善项目路由，不能修复老版本 schema、活动日志或接口能力 | 先判定版本和 schema，不能继续堆映射条件 |
| 昆明牛 `733875` 曾身份映射失败 | 项目未绑定，指标按 0 | 旧判断过度依赖名称/推广 ID，未先使用唯一站点 ID，或本机身份没有可读推广 ID | 站点 ID 做项目第一层路由，推广 ID 做账户第二层校验 |
| 长沙牛同样重扫后 KST 不绿 | 日志显示“API 启动”，状态仍灰 | “端口启动”与“身份注册表就绪”是两个状态；后者可能因路径、登录身份、结构或映射失败 | 看 `/health` 和脱敏失败类别，不看启动提示单行文字 |
| 读取时频繁提示数据库忙、超时 | 老客户端登录后一直重试 | 客户端正在写数据库，或同时读取多个身份；老版只读连接需要短锁等待和总超时 | 登录稳定后重扫；仍超时就停止，不提高超时掩盖问题 |
| 直接按账户名称映射 | 某些账户能出数，另一些账户串数或为 0 | 名称会变更、同名或编码不同，名称不是唯一键 | 必须以本机数据库推广 ID 和项目配置 `kst_ids` 校验 |

### 当前同事电脑落地结论

到目前为止，没有同事电脑的端到端成功案例。最明确的共性是：同事使用的快商通版本与本地开发机不一致，老客户端数据库结构和新版 Electron 读取链路不能直接互换。后续 Agent 不应继续把“改一个路径、补一个 ID、再重建 EXE”当成通用修复，而应先完成本机版本/结构指纹。

## 6. 给 WorkBuddy/其他 Agent 的执行指令模板

可以把下面内容直接交给 Agent：

```text
目标：在当前 Windows 电脑上只读识别本机快商通客户端，并判断是否能接入 127.0.0.1:18766 本地 API。

硬约束：
1. 不复制、移动、重命名或修改快商通文件；不启动或控制快商通客户端。
2. 不上传完整数据库、聊天正文、Token、Cookie、密码和原始日志。
3. 先识别客户端家族和版本，再识别安装根目录、数据根目录、登录身份和站点 ID。
4. 老版数据根目录是 KuaiShangDataNew，不是 db\<身份>，更不是单个数据库文件。
5. 只有确认表、字段、活动日志、推广 ID 和项目一对一映射后，才能启动/验收 API。
6. 任一环节失败就停止并输出失败类别，不猜列名、不混用旧导出、不写 Excel。

请按以下顺序执行并逐步汇报：
A. 输出进程完整路径、客户端版本、安装根目录和数据根目录；
B. 输出登录身份目录名、站点 ID、数据库文件清单（仅文件名/大小/时间）；
C. 输出 schema 指纹（表名、字段名、行数，不输出聊天正文）；
D. 从本机数据库提取推广 ID，并与项目配置逐项比较；
E. 刷新身份注册表，检查 /health；
F. 用一个小时段和一个日期分别测试 hourly/daily API；
G. 只有 A-F 全部通过，才报告“本机 API 可用”。

最终输出四项：
1. machine_inventory：客户端家族、版本、安装根、数据根、身份、站点 ID；
2. schema_report：表/字段/能力检查结果；
3. mapping_report：站点 ID、推广 ID、项目/账户绑定结果；
4. api_acceptance：health、hourly、daily 的通过/失败和脱敏原因。
```

## 7. 最终验收标准

只有同时满足以下条件，才能说“这台同事电脑的商务通本地 API 落地成功”：

- 原生客户端版本、安装路径和数据根目录已记录；
- 客户端正在运行并已登录目标站点；
- 站点 ID 与项目配置唯一一致；
- 推广 ID 能从本机数据库读取，并与账户配置唯一一致；
- 所需数据库表和字段通过只读能力检查；
- KST 状态为绿色，健康检查 `status=ok`；
- 小时报和日报 API 均返回完整、可校验的 `kst_local_api` 结果；
- 没有复制数据库、混合浏览器/旧导出、手工补数或写入未知 Excel 区域。

如果只是“API 进程启动了”或“发现了一个 `db\<站点>` 文件夹”，不算成功。

## 8. 后续开发建议

如果未来还要继续支持同事电脑，正确方向不是继续扩大固定路径判断，而是建立“客户端指纹 → 适配器”机制：

1. 先按客户端家族、版本和 schema 指纹选择适配器；
2. 每个新版本先用脱敏 schema fixture 写只读回归测试；
3. 未知结构只生成诊断报告，不自动猜测兼容；
4. 站点 ID 继续作为项目路由第一锚点，推广 ID 作为账户路由第二锚点；
5. 直到拿到同事电脑的真实端到端验收记录，再考虑把该版本加入正式发布包。
