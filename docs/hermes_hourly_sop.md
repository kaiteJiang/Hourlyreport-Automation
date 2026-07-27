# HERMES 小时报自动化执行手册

同步标记：`HERMES-20260710`

产品：蚁之力 · 竞价数据自动化。适用对象：HERMES（夏思道）及负责代执行小时报的内部同事。

## 固定入口

```cmd
run_hermes_hourly.bat 11点
run_hermes_hourly.bat 15点
run_hermes_hourly.bat 18点
```

不得直接拼接 `main.py --mode run`。BAT 会固定根目录和 UTF-8 环境；缺少 `.venv` 时先自动安装；随后运行快速 preflight，通过后才执行完整小时报。

窗口标题保持 `HERMES Hourly - fixed entry - 20260710`，BAT 文件名和参数不变。

## 执行顺序

1. 确认 GUI/菜单中的当前项目。
2. 确认目标 Excel 已关闭。
3. 默认 API 模式确认快商通客户端已登录、GUI 的 `KST` 状态为绿色；人工导出模式才检查 30 分钟内文件。
4. 调用对应时段的固定 BAT。
5. 仅退出码 `0` 视为完成。
6. 核对 `reports/final_run_report.json`、`reports/write_report.json` 和 `logs/run.log`。

最终报告中的 `data_source` 会记录实际来源。HERMES 与 GUI、命令行完整任务共享同一应用级偏好 `baidu_data_source_preference`：`A` / `api` 为默认 API 优先，有限自修复后失败则自动整项目降级；`B` / `browser` 为强制浏览器。HERMES 始终使用同一个固定 BAT，不要自行改拼 API 命令。

九个项目、十一个授权已导入，正式发布前必须由开发人员显式运行 `.venv\Scripts\python.exe main.py --mode test-baidu-api-readiness`。该入口只读百度数据，不读写 Excel；Token 过期时可按生产规则备份并原子更新 `secrets/secrets.json`，原文件和备份均为敏感文件。普通 GUI 不得自动调用该入口及其他开发探测入口。

沈阳牛、沈阳白必须两路 API 全部成功后才合并；任一路失败时丢弃本次 API 临时结果并整项目降级，禁止混合 API 与浏览器的部分数据。多项目并行尚未投入生产。

## 快速预检

入口内部执行：

```cmd
.venv\Scripts\python.exe main.py --mode preflight --quick
```

快速预检检查项目配置、Excel 路径、快商通目录和当前项目凭据，不执行耗时的 Excel 全结构扫描。API 模式的 preflight 不提前启动 Chrome，只有 API 最终失败并实际降级时才延迟启动 Chrome；强制浏览器模式继续在预检中检查 Chrome 9222。新项目、模板变化或结构异常才使用完整 preflight。

## 安全边界

- Excel 写前先备份，不重建工作簿。
- 只写扫描识别出的目标账户/字段，不改无关区域。
- 只使用 Chrome，不自动降级 Edge。
- 不索要或输出密码。
- API 任一路失败先丢弃临时结果并整项目降级浏览器；仅 API 与浏览器均失败时停止，禁止写 Excel。
- 禁止写入任一 API 来源的部分结果，也禁止混合 API 与浏览器的部分数据。
- 失败后不手工补 Excel。

## 需要人工介入

遇到验证码、滑块、安全校验、Excel 占用或结构无法识别时停止，报告原因，处理后整次重跑。
