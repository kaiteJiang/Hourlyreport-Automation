# 发布包瘦身与开发目录清理设计

## 目标

在不影响快商通本地 API、手工导出文件解析、百度授权、Excel 安全写入和在线更新的前提下：

1. 消除 GUI 单文件 EXE 对 `pandas`、`numpy` 和 OpenBLAS 的非必要打包依赖。
2. 让 `dist/` 在发布结束后只保留当前版本的在线更新 ZIP 和完整安装器。
3. 清理开发目录中的旧发布物、可再生成缓存、过期入口和本机专属文件。
4. 让 GitHub 仓库不再跟踪本机配置或已废弃入口。

新版本为 `2026.7.27.114`。

## 依赖边界

快商通统计规则属于纯 Python 业务逻辑，不应依赖文件读取库。现有引用链为：

`GUI -> KstApiManager -> KstIdentityRegistry -> KstConversationService -> kst_daily_parser -> kst_export_parser -> pandas`

这导致 PyInstaller 将 `pandas`、`numpy` 和 OpenBLAS 收入 GUI EXE。设计调整为：

- 新建轻量统计模块，承载小时报和日报的行聚合逻辑。
- 快商通本地 API 服务只依赖轻量统计模块。
- 手工 Excel/CSV 解析模块继续保留 `pandas`，供 `.venv` Python 任务使用。
- 现有统计函数保留兼容导入，避免调用方接口变化。

不得仅依靠 PyInstaller `excludes` 强制移除依赖，因为这可能掩盖真实的静态耦合并在运行时触发缺模块错误。

## 构建与发布目录

PyInstaller 中间 EXE 和构建清单写入 `build/release_<version>_staging/`。在线更新包和完整安装器从该受控暂存区取 EXE。

发布完成后：

- `dist/Hourlyreport_automation_v2026.7.27.114.zip`
- `dist/Hourlyreport_automation_setup_v2026.7.27.114.exe`

是 `dist/` 中仅有的两个文件。裸 `hourlyreport_automation.exe`、构建清单、旧版本包和兼容内部包不得留在 `dist/`。

发布包仍必须排除 `configs/`、`secrets/`、`logs/`、`reports/`、`backups/`、`diagnostics/`、`kst_exports/`、`browser_profile/` 和 `runtime/` 中的用户状态。

## 清理范围

### 保留

- `browser_profile/`：保留百度登录状态。
- `backups/`：保留 Excel 和 Token 安全备份。
- `.venv/`：保留开发和生产任务依赖。
- `secrets/`、`configs/`、`runtime/`：保留授权、项目配置、用户选择和本地 API 令牌。
- 当前用户对配置文件和旧发布说明的未提交修改。

### 删除或重建

- `dist/` 中除 114 两个最终发布包外的所有文件。
- `build/` 中所有旧 PyInstaller 产物、暂存包和构建日志；发布时只允许当前暂存区短暂存在。
- `__pycache__/`、`.pytest_cache/`、`.playwright-cli/` 和 `.superpowers/` 等可再生成缓存。
- `logs/`、`reports/` 中的运行输出，仅保留 `.gitkeep`。
- `diagnostics/` 和明确的临时锁文件。
- 根目录未跟踪的旧 `.spec`、`.ignore`、`nul`、`_verify_excel.py`。
- 已被发布脚本标记为旧入口的 BAT：
  `create_config.bat`、`run_11.bat`、`run_15.bat`、`run_18.bat`、
  `run_fetch_baidu.bat`、`run_fetch_baidu_15.bat`、`run_inspect.bat`、
  `run_mock_write.bat`、`run_parse_kst_export_15.bat`、
  `run_test_browser_connect.bat`、`setup_env.bat`、`START_HERE.bat`。

继续保留当前有效入口：GUI、`menu.py`、`run_menu.bat`、
`run_hermes_hourly.bat`、`run_hermes_daily.bat`、`run_desktop_gui.bat`
和 Chrome 调试维护入口。

## Git 仓库卫生

- 修复 `.gitignore` 中 `samples/.gitkeep` 与 `reports/*` 粘连的问题。
- 忽略诊断目录、运行时选择文件、锁文件和本机 Claude 设置。
- 从 Git 索引移除 `.claude/settings.local.json`，但保留本机文件。
- 删除 Git 中已废弃的 BAT；相关测试改为验证这些入口不再存在。
- 不提交任何真实 Token、密钥、报告、日志、备份或浏览器数据。

## 验证标准

1. 先用回归测试证明 GUI 模块图不应包含 `pandas` 或 `numpy`。
2. 快商通小时报、日报、HTTP 服务和身份路由相关测试通过。
3. 全量测试通过。
4. PyInstaller 构建成功，归档清单中不存在 `pandas`、`numpy` 或 OpenBLAS。
5. 新 EXE 能启动，带鉴权的 `/health` 返回正常，未鉴权请求返回 401。
6. 在线更新 ZIP 和完整安装器成功生成，且包内容不含用户状态。
7. `dist/` 最终恰好两个文件，在线更新包体积恢复到接近 112 版本基线。
8. `git status` 仅保留用户原有、本次明确不提交的本机配置修改。
