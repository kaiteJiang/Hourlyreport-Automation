# AGENTS.md

本文件面向所有参与本仓库开发、排障、维护、构建和发布的 AI Agent / 自动化助手 / 人工协作者。无论使用 Codex、Claude、HERMES（夏思道）或其他平台，都必须优先遵守本文件。

## 项目概览

- 产品名：`蚁之力 · 竞价数据自动化`
- 平台：Windows 10/11 本地运行
- 当前标准版本：`2026.7.28.115`
- 主要用途：百度竞价日报/小时报自动化
- 核心流程：百度数据读取、快商通本地只读 API（可切人工导出）、本地 Excel 安全写入、日志与报告输出
- 用户入口：桌面 GUI、控制台菜单、HERMES 固定 BAT

当前版本不做 QQ/微信自动发送，不操作快商通客户端，不做 OCR，不自动截图发送，不把业务数据交给外部 AI 分析服务。

## 全局协作原则

- 全程使用中文回复。
- 先读代码和配置，再动手改。
- 尽量做最小范围修改，避免顺手重构。
- 不要回滚用户或其他 Agent 的已有改动；无关脏文件只忽略。
- 不要运行真实 `run` / `run-daily` 或写目标 Excel，除非用户明确要求。
- 每次修改后说明改了哪些文件、改了什么、做了哪些验证。
- 每次运行后说明日志、报告或测试结果。
- 不向用户索要真实百度密码，不输出、不记录、不提交 secrets。

## 最高优先级硬规则

1. 分阶段开发，不允许一次性做全流程大改。
2. Excel 写入前必须先备份原文件。
3. 不允许重建目标 Excel。
4. 不允许修改无关 sheet。
5. 不允许修改公式区、汇总区、截图区、非目标区域和用户模板样式。
6. Excel 区域识别必须扫描表头、账户区域和字段名称；禁止写死固定坐标。
7. 遇到不确定的 Excel 结构，不要猜测写入，必须中断并输出诊断信息。
8. 浏览器自动化不允许依赖绝对屏幕坐标，优先使用 URL、文本、表头、表格结构、选择器。
9. 浏览器自动化必须优先使用 Google Chrome，不允许默认启动 Edge。
10. Chrome 启动失败时必须输出明确错误并等待人工确认，不允许静默降级到 Edge。
11. API 与浏览器均失败时必须停止，不得继续解析、合并或写 Excel。
12. 发布包不得包含真实凭据、OAuth Token、日志、报告、备份、诊断包、浏览器数据或快商通导出数据。
13. `.baidu-secrets`、`secrets/secrets.json` 和本机授权数据禁止提交 Git。
14. 不要手工补 Excel 数字；失败后输出原因并让流程重跑。

## Superpowers 使用优先级

默认采用轻量开发流程，不要机械套用完整 Superpowers。

- 微小修改：文案、字号、间距、单个明确条件、局部说明文档等低风险改动，直接做最小修改，查看 diff，必要时跑相关测试。
- 普通修改：先做简短分析，再实施，并运行相关测试。
- 高风险修改：启用完整 Superpowers，包括诊断、计划、测试驱动、代码审查和完成前验证。

以下情况无论代码量大小，都按高风险处理：

- 安全、权限、账号授权、secrets
- Excel 写入、备份、恢复、筛选/保护元数据
- 在线更新、安装器、发布包覆盖规则
- 百度 API 主通道、Token 自修复、浏览器降级
- 并发、多项目执行、数据迁移、破坏性文件操作
- 公共接口变化、跨模块重构、难以回退的行为变化

不使用完整 Superpowers 不等于降低质量：仍必须保持最小 diff、不回滚无关改动、查看最终差异、运行与风险匹配的验证。

## 版本与发布规则

- 当前标准安装器基线：`2026.7.28.115`
- 新电脑只分发：`Hourlyreport_automation_setup_v2026.7.28.115.exe`
- 在线更新仓库：`kaiteJiang/Hourlyreport-Automation`
- tag 格式：`v<版本号>`
- 在线更新包：`Hourlyreport_automation_v<版本号>.zip`
- 完整安装器：`Hourlyreport_automation_setup_v<版本号>.exe`
- 桌面主程序：`hourlyreport_automation.exe`

版本号固定为：

```text
发布年.月.日.永久累计序号
例如：2026.7.22.108
```

累计序号从 `100` 起，跨日期永久递增，不得按天归零。

每次发布必须：

1. 更新 `gui/version.py` 中的 `CURRENT_VERSION`。
2. 重新构建 `hourlyreport_automation.exe`。
3. 生成在线更新包和完整安装器。
4. 生成 `docs/releases/<version>.md` 中文更新说明。
5. 验证在线更新包不包含用户配置和运行数据。
6. 用更新器逻辑验证最新 Release 元数据可被识别。
7. 提交源码变更，再推送到 GitHub。

在线更新包和普通发布包不得覆盖：

- `configs/`
- `secrets/`
- `logs/`
- `reports/`
- `backups/`
- `diagnostics/`
- `kst_exports/`
- `browser_profile/`

完整安装器可包含默认项目配置，但不得包含真实 `secrets/secrets.json` 或 `.baidu-secrets`。

## 百度 API 规则

- 服务商应用：`openBD`
- 当前为九个项目、十一个授权。
- 生产任务统一读取应用级 `baidu_data_source_preference`。
- 缺失或无效时按 `api`。
- `A` / `api` 表示 API 优先。
- `B` / `browser` 表示强制浏览器。

API 主通道自修复预算：

- Token 最多刷新一次。
- 网络错误最多额外重试两次。
- 完整性错误最多额外读取一次。
- 总预算 20 秒。
- 仍失败时整项目降级现有浏览器抓数。

关键边界：

- `B` 模式不得发起 API 请求。
- API 与浏览器均失败时必须停止。
- 百度应用 secretKey 只允许保存在腾讯云 SCF 环境变量。
- 桌面端只保存独立 HMAC 客户端密钥和 OAuth Token。
- 日志、报告、诊断包不得包含令牌或密钥。
- `test-baidu-api-readiness` 只读百度数据，不读写 Excel，不启动 Chrome。
- Token 过期时可按生产规则备份并原子更新 `secrets/secrets.json`，原文件和备份均为敏感文件。
- 沈阳牛、沈阳白必须两路 API 全部成功后才合并；任一路失败则丢弃 API 临时结果并整项目降级浏览器。
- 禁止混合 API 与浏览器的部分数据。
- 多项目模式只允许 API 并行准备；快商通解析、合并和 Excel 写入必须按选择顺序串行。
- 多项目模式不得降级浏览器。单项目 API 失败只跳过该项目，并在 `reports/multi_project_run_report.json` 汇总。
- 多项目停止请求不得中断当前项目；只允许从下一个排队项目开始停止。
- 多项目最多 3 个、最少 1 个；重复项目和重复 Excel 路径必须在发起 API 前拒绝。

## Chrome 与浏览器规则

默认只连接 Chrome 调试端口：

```text
http://127.0.0.1:9222
```

优先使用：

```python
chromium.connect_over_cdp("http://127.0.0.1:9222")
```

约束：

- 禁止默认启动 Edge。
- 禁止在 `connect_existing` 模式下另起普通 Chrome。
- 不要关闭老 Chrome 后新开普通 Chrome 来跑任务。
- `A` 模式 preflight 不提前启动 Chrome；只有 API 实际降级时才延迟启动并检查 Chrome。
- `B` 模式 preflight / run 继续检查 Chrome 9222，未启动时自动尝试启动项目专用调试 Chrome。
- 自动切换百度账号、清 cookie、CAS 登录不应把 Chrome 抢到前台。
- 只有验证码、安全校验、滑块或人工确认等确实需要人工介入的场景，才显示 Chrome。

## 快商通数据规则

默认使用快商通本地只读 API；可在“系统 → 快商通模式”显式切换为人工导出。两种模式都不读网页、不读桌面控件、不做 OCR、不自动操作快商通软件。

本地 API 只绑定 `127.0.0.1:18766`，令牌自动保存在被 Git 和发布包排除的 `runtime/`，并按推广 ID 将登录身份严格绑定到唯一项目。只统计具有服务器自动推送或启动自动同步凭证的会话；项目未绑定、映射歧义、接口失败或结果不完整时，该项目快商通指标按 0 继续，百度数据照常处理，不得偷偷读取旧导出文件。

人工导出模式继续从 Excel/CSV 文件读取，文件放入项目配置目录或 `kst_exports/`，也可通过 `--file` 指定。

小时报字段口径：

- `总对话`：有访客消息的有效行。
- `有效对话`：`有效-三句话` + `转潜-有效`。
- `一般有效`：`有效-一般`。
- `有效转潜`：`转潜-有效`。
- `总转潜`：包含 `转潜-`。

日报字段口径：

- `有效对话` 不包含 `有效-一般`。
- `一般有效对话` 独立统计 `有效-一般`。
- `转潜-有效` 同时计入 `有效对话` 和 `有效转潜`。

字段识别必须通过表头，不允许写死列号。无法归属账户的行必须输出到报告，不得静默丢弃。

## Excel 写入规则

小时报常用流程：

```cmd
.venv\Scripts\python.exe main.py --mode inspect-excel
.venv\Scripts\python.exe main.py --mode fetch-baidu-auto --period 15点
.venv\Scripts\python.exe main.py --mode parse-kst-export --period 15点
.venv\Scripts\python.exe main.py --mode merge-data
.venv\Scripts\python.exe main.py --mode write-excel --period 15点
.venv\Scripts\python.exe main.py --mode run --period 15点 --yes
```

日报常用流程：

```cmd
.venv\Scripts\python.exe main.py --mode inspect-daily-excel
.venv\Scripts\python.exe main.py --mode fetch-baidu-daily --date 2026-05-26
.venv\Scripts\python.exe main.py --mode parse-kst-daily --date 2026-05-26
.venv\Scripts\python.exe main.py --mode merge-daily --date 2026-05-26
.venv\Scripts\python.exe main.py --mode write-daily --date 2026-05-26
.venv\Scripts\python.exe main.py --mode run-daily --date 2026-05-26 --yes
```

`run-daily` 不传 `--date` 时默认处理昨天。

日报抓取不能把“DOM 元素出现”当作“数据已加载完成”。必须等表格快照稳定，并做基础完整性校验。`networkidle` 超时后不得静默使用早期残值。

## 运行入口

普通用户优先运行 GUI 或菜单：

```cmd
hourlyreport_automation.exe
.venv\Scripts\python.exe menu.py
```

HERMES / 夏思道 / 自动代执行必须走固定 BAT：

```cmd
run_hermes_hourly.bat 11点
run_hermes_hourly.bat 15点
run_hermes_hourly.bat 18点
run_hermes_daily.bat
run_hermes_daily.bat 2026-07-09
```

固定窗口规则：

- 小时报窗口标题：`HERMES Hourly - fixed entry - 20260710`
- 日报窗口标题：`HERMES Daily - fixed entry - 20260710`
- BAT 会固定工作目录、UTF-8 环境和 `.venv` Python。
- BAT 会先跑 preflight，失败则停止。
- 不要自行拆分 `fetch/parse/merge/write` 代替完整任务。
- 失败后不要手工补 Excel 数字。

## Preflight 规则

快速预检：

```cmd
.venv\Scripts\python.exe main.py --mode preflight --quick
.venv\Scripts\python.exe main.py --mode preflight --task daily --quick
```

快速预检检查：

- 项目根目录。
- 当前项目配置是否合法。
- Excel 路径是否存在。
- 快商通导出目录是否存在。
- `secrets/secrets.json` 是否为合法 JSON。
- 当前项目所需 `credential_profile` 是否存在且账号密码非空。
- `B` 模式检查 Chrome 9222。
- `A` 模式不提前启动 Chrome。

完整预检：

```cmd
.venv\Scripts\python.exe main.py --mode preflight
.venv\Scripts\python.exe main.py --mode preflight --task daily
```

完整预检只用于新项目上线、Excel 模板变更、结构识别异常或排障。

## 日志、报告与维护工具

常规输出：

- `logs/run.log`
- `logs/gui_history.log`
- `reports/*.json`
- 必要时输出 `reports/sheet_text_dump.csv`

重要报告：

- `reports/preflight_report.json`
- `reports/final_run_report.json`
- `reports/daily_final_run_report.json`
- `reports/baidu_account_data.json`
- `reports/baidu_daily_data.json`
- `reports/write_report.json`
- `reports/daily_write_report.json`

维护入口：

```cmd
.venv\Scripts\python.exe main.py --mode lock-dependencies
.venv\Scripts\python.exe main.py --mode diagnostic-bundle
.venv\Scripts\python.exe main.py --mode archive-logs
```

诊断包必须脱敏，并跳过 `secrets/`。日志归档默认只归档旧 `.log`，不改业务报告和 Excel。

## 测试与验证

修改代码后优先运行相关测试。改动跨模块、入口、安装器、发布包或 Excel 写入时，运行更宽的测试集。

基础命令：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_basic.py
```

不得在未验证时声称已修复。若测试失败，必须说明失败项是否与本次改动相关。

## 文档同步规则

修改以下内容时，至少同步检查相关文档：

- 固定入口
- Chrome 策略
- preflight
- 日报稳定性
- HERMES 执行方式
- 百度 API 模式
- 在线更新与发布规则
- 安装器和依赖安装

重点文件：

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `README_同事使用说明.md`
- `xia_sidao使用说明.md`
- `docs/hermes_hourly_sop.md`
- `docs/hermes_daily_sop.md`
- `docs/online_update_sop.md`
- `run_hermes_hourly.bat`
- `run_hermes_daily.bat`

如果只改其中一处，必须说明为什么其他文件无需同步。

## 禁止行为清单

- 禁止自动启动 Edge。
- 禁止关闭老 Chrome 后新开普通 Chrome 跑任务。
- 禁止使用绝对屏幕坐标操作百度页面。
- 禁止猜测 Excel 单元格坐标写入。
- 禁止重建目标 Excel。
- 禁止修改无关 sheet、公式区、汇总区、截图区。
- 禁止跳过备份直接写 Excel。
- 禁止失败后手工补 Excel 数字。
- 禁止把真实账号密码写入文档、日志、测试或示例。
- 禁止提交 `secrets/secrets.json`、`.baidu-secrets`、本机账号密码、个人导出数据。
- 禁止把 `diagnostics/`、`logs/`、`reports/`、`backups/`、`kst_exports/` 打进在线更新包。
- 禁止在 API 部分失败时混合浏览器部分数据继续写入。
