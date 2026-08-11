# HERMES 日报自动化执行手册

同步标记：`HERMES-20260710`

产品：竞价数据自动化控制台。适用对象：HERMES（夏思道）及负责代执行日报的内部同事。

## 固定入口

```cmd
run_hermes_daily.bat
run_hermes_daily.bat 2026-07-09
```

不传日期时处理昨天。不得直接拼接 `main.py --mode run-daily`。BAT 会固定根目录和 UTF-8 环境；缺少 `.venv` 时先自动安装；随后运行日报快速 preflight，通过后才执行完整日报。

窗口标题保持 `HERMES Daily - fixed entry - 20260710`，BAT 文件名和参数不变。

## 执行顺序

1. 确认 GUI/菜单中的当前项目和目标日期。
2. 确认目标 Excel 已关闭。
3. 默认 API 模式确认快商通客户端已登录、GUI 的 `KST` 状态为绿色；人工导出模式才检查日报导出文件。
4. 调用固定 BAT；指定日期时必须使用 `YYYY-MM-DD`。
5. 仅退出码 `0` 视为完成。
6. 核对 `reports/daily_final_run_report.json`、`reports/daily_write_report.json` 和 `logs/run.log`。

最终报告中的 `data_source` 会记录实际来源。HERMES 与 GUI、命令行完整任务共享同一应用级偏好 `baidu_data_source_preference`：`A` / `api` 为默认 API 优先，有限自修复后失败则自动整项目降级；`B` / `browser` 为强制浏览器。固定入口不变。

九个项目、十一个授权已导入，正式发布前必须由开发人员显式运行 `.venv\Scripts\python.exe main.py --mode test-baidu-api-readiness`。该入口只读百度数据，不读写 Excel；Token 过期时可按生产规则备份并原子更新 `secrets/secrets.json`，原文件和备份均为敏感文件。普通 GUI 不得自动调用该入口及其他开发探测入口。

沈阳牛、沈阳白必须两路 API 全部成功后才合并；任一路失败时丢弃本次 API 临时结果并整项目降级，禁止混合 API 与浏览器的部分数据。多项目并行尚未投入生产。

## 快速预检

入口内部执行：

```cmd
.venv\Scripts\python.exe main.py --mode preflight --task daily --quick
```

快速预检不扫描整张日报 Excel。API 模式的 preflight 不提前启动 Chrome，只有 API 最终失败并实际降级时才延迟启动 Chrome；强制浏览器模式继续在预检中检查 Chrome 9222。新项目、模板变化、结构识别异常或排障时才运行完整日报 preflight。

## 百度稳定性

- 不把表格 DOM 出现当作数据就绪。
- 连续快照的账号集合和指标必须稳定。
- 可识别总计行时，展现、点击、消费必须与账户求和一致。
- `networkidle` 超时后不得使用早期残值。
- API 任一路失败先丢弃临时结果并整项目降级浏览器；仅 API 与浏览器均失败时停止，禁止写 Excel。
- 浏览器降级读取的稳定性校验未通过时，按浏览器失败处理，不继续合并或写 Excel。

## 安全边界

- 写入前备份，不重建工作簿。
- 只写扫描识别出的日期、账户和字段区域。
- 不改无关 sheet、公式区、汇总区或截图区。
- 不索要或输出密码。
- 失败后不手工补 Excel。
