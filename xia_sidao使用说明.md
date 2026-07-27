# 夏思道（HERMES）使用说明

入口同步标记：`HERMES-20260710`

当前产品名为“蚁之力 · 竞价数据自动化”。夏思道使用 HERMES 固定入口执行任务，自动代执行只允许调用 `run_hermes_*.bat`。

## 当前能力

- 小时报：读取百度展现、点击、消费，默认读取快商通本地 API，写入 `时段数据` sheet。
- 日报：读取百度日报和快商通本地 API，写入 `百度` sheet；不传日期时默认昨天。
- 正式项目：昆明牛、南京牛、宁波牛、长沙牛、沈阳牛、青岛白、深圳白、南京白、沈阳白。
- 双百度来源：沈阳牛、沈阳白。
- 南京牛已包含 `baidu-华厦npx6`，推广 ID `85492975`，沿用现有 Excel 字段映射。
- 当前不做 QQ、截图、自动发消息，也不自动操作快商通客户端或网页。

## 唯一自动入口

进入本程序根目录后执行。

小时报：

```cmd
run_hermes_hourly.bat 11点
run_hermes_hourly.bat 15点
run_hermes_hourly.bat 18点
```

日报：

```cmd
run_hermes_daily.bat
run_hermes_daily.bat 2026-07-09
```

- 不带日期的日报处理昨天。
- 固定窗口标题包含 `HERMES`、任务类型和 `20260710`。
- 小时报窗口标题保持 `HERMES Hourly - fixed entry - 20260710`，日报窗口标题保持 `HERMES Daily - fixed entry - 20260710`。
- BAT 固定工作目录、UTF-8 和 `.venv` Python。
- `.venv` 不存在时，BAT 会调用 `install_env.bat` 自动准备环境。
- 小时报先执行 `preflight --quick`，日报先执行 `preflight --task daily --quick`；失败立即停止。
- 禁止绕过 BAT 自己拼 `main.py --mode run` 或拆分阶段代替完整任务。

## 百度数据源模式

HERMES 与 GUI、命令行完整任务共享同一应用级偏好 `baidu_data_source_preference`，BAT 文件名、参数和窗口规则不变：

- `A` / `api`：默认 API 优先；Token、网络或完整性异常先在 20 秒总预算内有限自修复，仍失败则自动整项目降级浏览器。
- `B` / `browser`：强制浏览器，不调用 API，是紧急回退入口。

九个项目、十一个授权已导入，正式发布前必须由开发人员显式运行 `.venv\Scripts\python.exe main.py --mode test-baidu-api-readiness`。该入口只读百度数据，不读写 Excel；Token 过期时可按生产规则备份并原子更新 `secrets/secrets.json`，原文件和备份均为敏感文件。

沈阳牛、沈阳白必须两路 API 全部成功后才合并；任一路失败时丢弃 API 临时结果并整项目降级，禁止混合 API 与浏览器的部分数据。多项目并行尚未投入生产。

API 模式的 preflight 不提前启动 Chrome；只有 API 最终失败、实际降级时才延迟启动 Chrome。普通 GUI 不得自动调用 `test-baidu-api`、`test-baidu-api-readiness`、`simulate-baidu-api-hourly` 或 OAuth 导入等开发探测入口。

## 执行前

1. 人工在 GUI 或菜单中把“当前项目”切换到本次项目。
2. 目标 Excel 必须存在，并关闭 WPS/Excel 中已打开的同一文件。
3. 默认 API 模式需保持快商通客户端登录且 GUI 的 `KST` 状态为绿色；只有显式切到“人工导出对话”时才需要导出文件。
4. 仅 30 分钟内的最新快商通文件参与本次任务；没有符合条件的文件时按 0 对话继续，不作为程序故障。
5. 不向用户索要、展示或记录真实百度账号密码；凭据只从本机 `secrets/secrets.json` 读取。

## Chrome 与登录

- 只使用 Google Chrome 调试端口 `9222`，不自动改用 Edge。
- `A` 模式预检不接触 Chrome，实际降级后才复用或延迟启动项目专用实例；`B` 模式预检继续检查 Chrome 9222。
- 自动切换账号、清 cookie 和 CAS 登录默认静默，不抢前台。
- 页面退出失败时，程序会清理当前上下文 cookie/storage 后重登当前项目账号。
- 遇到验证码、滑块、安全验证或明确要求人工确认时，停止并说明需要人工处理。

## 数据与 Excel 安全

- Excel 写入前必须备份原文件，不重建工作簿。
- 只写动态识别出的账户和字段区域，不写死单元格坐标。
- 不修改无关 sheet、公式区、汇总区、截图区或“每日时段统计数据”。
- 百度日报必须连续快照稳定且通过总计校验后才允许写入；不稳定时整次失败，不写部分结果。
- API 任一路失败先丢弃临时结果并整项目降级浏览器；仅 API 与浏览器均失败时停止，禁止写 Excel。
- 失败后禁止手工补数字。

## 成功与失败判断

小时报成功重点：

```text
reports/final_run_report.json
reports/write_report.json
logs/run.log
```

日报成功重点：

```text
reports/daily_final_run_report.json
reports/daily_write_report.json
logs/run.log
```

退出码 `0` 才算完成。GUI 成功后会自动打开当前项目 Excel。

## 常见异常与防复发

以下按「现象→原因→处理→防复发」整理，避免同一错误反复出现。

| 现象 | 原因 | 处理 | 防复发 |
|------|------|------|--------|
| 当前项目不对 | 默认配置是昆明牛，切其他项目时未改配置 | `--project` 标志临时覆盖，或人工切项目后重跑 | 接到任务先确认当前项目，用 `--project` 不碰配置 |
| 做完额外项目后忘了跑当前任务 | 切项目后注意力未回当前任务 | 以当前任务为准，做完一个项目回到用户要求 | 多项目任务做完后立刻回到用户当前要求，不凭记忆补流程 |
| 绕过BAT自己拼main.py | 不知道BAT或为省事 | 交互式用 `--project` 直连main.py（API模式推荐）；自动执行必须走BAT | 区分：交互式→main.py+--project，自动执行→BAT |
| 用定时任务入口代替BAT，导致多跑一轮 | 误用非固定入口手动触发 | 日报/小时报固定走BAT或main.py直连，不用其他入口手动触发代替 | 定时入口只用于计划任务，不用于手动触发 |
| BAT从bash直接跑报编码错 | bash解析bat语法导致乱码 | 用 venv python 直接跑 main.py，或 `cmd.exe /c` 包装 | 记住：bash不能直接跑.bat |
| Chrome 9222 test-browser-connect误判 | 代理网络导致err_proxy_connection_failed假阴性 | 用 `curl -s http://localhost:9222/json/version` 验证，跳过test直接preflight | 确认方法：curl返回"Chrome/..."即正常，不要信test-browser-connect |
| 预检包含Chrome导致15分钟延迟 | 旧版预检必须检查Chrome（浏览器模式） | API模式预检自动跳过Chrome检查，只有降级时才启动Chrome | 默认用API模式，无需单独检查Chrome |
| 百度要求验证码/滑块 | 登录态过期或异常 | 停止，等待人工完成验证后整次重跑 | 不跳过验证，不手工补数据 |
| 快商通没有30分钟内文件 | 忘记导出或导出路径不对 | 正常按0对话继续，不当作BUG | 导出文件放 `kst_exports/` 或项目配置目录 |
| Excel被占用 | WPS/Excel打开中 | 关闭对应WPS/Excel文件后整次重跑 | 执行前先查tasklist确认wps.exe/et.exe/EXCEL.EXE已关闭 |
| Excel文件名猜错 | 各项目文件名不同，习惯性猜文件名 | 从 `reports/*_final_run_report.json` 的 `excel_path` 字段读取 | 永不允许猜文件名，必须从report JSON读 |
| 忘记打开Excel | 流程遗漏 | 写入后必须 `os.startfile(excel_path)` 打开 | 写入操作后立刻打开Excel，自检项 |
| 打开Excel报文件不存在/中文路径乱码 | 用cmd //c start导致编码问题 | 用 `os.startfile(path)` 或 `powershell Start-Process` | 禁止cmd //c start中文路径 |
| Excel筛选按钮丢失 | openpyxl的wb.save清空所有sheet的autoFilter | 写入后从备份恢复所有sheet的filter/protection元数据 | 恢复函数必须覆盖所有sheet（时段数据/百度/大夜数据） |
| 百度数据不一致且3轮重试一致 | 百度后端数据问题，非临时错误 | 不盲重试——3轮重试100%一致说明是百度后端数据延迟 | 报告用户手动在百度后台验证，不加重试 |
| API多来源某一路失败 | 沈阳牛/沈阳白双来源任一路异常 | 程序自动丢弃API临时结果并整项目降级浏览器 | 不混合API与浏览器的部分数据，不写部分结果 |
| 回复太啰嗦/重复/AI味重 | 输出工具结果后又补解释 | 一行格式："项目+时段已做完，已打开，请检阅。" | 只给结果，不复述流程，一句话 |
| 做完报告后输出数据表格 | 误以为用户要看到具体数字 | 微信消息不展示展现/点击/消费数据 | 数字在Excel里，消息只说状态 |
| 百度日报数据不稳定时报错 | 页面加载未完成就读取 | 等待页面/API稳定后整次重跑 | 不手工补Excel |
| 手工编造或补填数据 | 写入失败后补救心理 | 失败后整次重跑，不手工改Excel数字 | 只允许程序写入，禁止手动干预 |

## 不得做

同见AGENTS.md 禁止行为清单。

- 不操作 QQ，不截图，不自动发送消息。
- 不自动操作商务通网页或客户端。
- 不手工编造或补填百度、商务通数据。
- 不在 Excel 打开时强行写入。
- 不修改无关 sheet、汇总区、公式区、截图区。
- 不提交 secrets、运行报告、日志、备份或本地项目选择状态。
- 不在消息或文档中记录真实账号密码。
- 不绕过固定 BAT 自己拼 `main.py --mode run`（交互式例外：API模式下`--project`直连main.py可接受）。
- 不猜测 Excel 文件名，必须从 report JSON 读取。
- 不手工补 Excel 数字。
- 不输出数据表格到微信消息。
- 不重复解释或复述已经给出的结果。

## 对应 SOP

- `docs/hermes_hourly_sop.md`
- `docs/hermes_daily_sop.md`
- `AGENTS.md`
