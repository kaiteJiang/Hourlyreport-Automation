# 在线更新发布规则

## 基线

`2026.7.27.114` 是当前标准安装器基线。新电脑只分发 `Hourlyreport_automation_setup_v2026.7.27.114.exe`，用户选择目录后一次安装完成。

`Hourlyreport_automation_v*.zip` 只供客户端在线更新，故意排除 `configs/` 和其他用户数据，不能作为新电脑安装包。若误启动不完整的更新目录，GUI 应给出安装提示并安全退出，不显示 Python 异常堆栈。

更新源固定为公开仓库 `kaiteJiang/Hourlyreport-Automation` 的 `releases/latest`。`2026.7.19.104` 是新仓库更新基线；同事电脑先完整安装一次该内部包，104 及后续版本只从新仓库获取标准资产，不承担旧仓库增量迁移。

## 版本号

格式：

```text
发布年.月.日.永久累计序号
```

累计序号从 `100` 起，只增不减，跨日期也不重置：

```text
2026.7.15.101
2026.7.16.102
2026.7.16.103
2026.7.20.104
```

构建代码提供 `next_online_version()` 计算下一序号，并拒绝无效日期、错误格式和小于 `100` 的累计序号。

## 发布步骤

1. 查询 GitHub 上一次正式 Release 的版本号。
2. 使用当天日期，并将上一次版本号最后一段加 `1`。
3. 同步更新 `gui/version.py` 中的 `CURRENT_VERSION`。
4. 运行统一发布构建；裸 `hourlyreport_automation.exe` 只进入 `build/release_<version>_staging/`，不得留在 `dist/`。
5. 运行基础测试，不通过则停止发布。
6. 生成包含默认配置但不含真实凭据的标准安装器，以及只含程序文件的在线更新包：

```cmd
.venv\Scripts\python.exe tools\build_publish_release.py --version 2026.7.27.114
```

7. 验证 `dist/` 只包含 `Hourlyreport_automation_v2026.7.27.114.zip` 和 `Hourlyreport_automation_setup_v2026.7.27.114.exe`。
8. GitHub Release tag 使用 `v2026.7.27.114`，在线更新资产名为 `Hourlyreport_automation_v2026.7.27.114.zip`；完整安装器名为 `Hourlyreport_automation_setup_v2026.7.27.114.exe`，两者用途不同。
9. 读取 GitHub `releases/latest` 的真实响应，用上一正式版本号验证 tag、资产名、SHA-256、大小和下载链接均能被更新器识别。
10. 在一台已安装上一版本的测试电脑上验证“发现更新、下载、安装、重启、保留配置”完整流程，并把完整安装器安装到临时目录，验证重复安装不会覆盖已有配置。
11. 为每个版本保存中文更新说明，至少包含用户可感知改动、稳定性修复和兼容性注意事项，并复制到 GitHub Release 正文。

## 更新助手与重启

- 打包版 GUI 会复制当前 `hourlyreport_automation.exe` 到本机更新缓存，作为临时更新助手运行；不依赖 `.venv` 单独安装 PySide6。
- 临时更新窗口完成初始化并显示后，必须写出启动握手标记；主 GUI 收到标记后才允许退出。
- 更新文件覆盖完成后，更新助手会启动新版主程序并短时检查进程是否存活；立即退出时最多重试 3 次。
- 更新和重启结果写入 `%LOCALAPPDATA%\HourlyreportAutomation\updates\update_apply.log`。
- 更新助手未启动、初始化超时或提前退出时，旧 GUI 必须保持运行并显示明确错误，不得直接消失。

## 不得覆盖

在线更新包不得包含或覆盖：

- `configs/`
- `secrets/`
- `logs/`、`reports/`、`backups/`
- `kst_exports/`
- `browser_profile/`
- 同事本机 Excel 和其他业务数据

## API 开发关系

在线更新与百度 API 数据源是两条独立能力。生产默认使用应用级 `baidu_data_source_preference=api`；API 在有限自修复后仍失败时整项目降级浏览器。更新失败不得改变数据模式、项目配置或 OAuth 授权。
