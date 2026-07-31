# 2026.7.31.118 最小修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 不改现有业务架构，只修复已由日志和代码证实的快商通、百度诊断和 GUI 可用性断点。

**Architecture:** 保留现有只读本地 API、严格项目绑定、百度 20 秒自修复和浏览器降级。只在原函数内补参数、容错、状态判断和安全日志，不新增服务、不改 Excel 流程。

**Tech Stack:** Python 3.14、PySide6、pytest。

## Constraints

- 不执行真实 `run` / `run-daily`，不写业务 Excel。
- 不记录 Token、Secret、原始推广 ID、对话内容或数据库路径。
- 每项先写失败测试，再做最小修改。
- 不提交主目录现有配置和运行文件。

### Task 1: 快商通路径、重新扫描与隐藏窗口

**Files:**
- Modify: `modules/kst_local/discovery.py`
- Modify: `gui/main_window.py`
- Test: `tests/test_kst_dual_backend.py`
- Test: `tests/test_kst_global_menu.py`
- Test: `tests/test_kst_gui_lifecycle.py`

- [ ] Electron 扫描真正使用机器设置中的 `data_root`，兼容 `OnlineWebCSNew` 本身和父目录。
- [ ] 显式 Electron/Legacy 路径只运行对应后端，避免无关错误遮蔽。
- [ ] 状态回调不再自动弹目录框。
- [ ] 人工导出模式下点击“重新扫描”先保存切换到本地 API，再启动并扫描一次。
- [ ] 运行上述三个测试文件。

### Task 2: 快商通身份读取与当前项目状态

**Files:**
- Modify: `modules/kst_local/db_reader.py`
- Modify: `modules/kst_local/log_source.py`
- Modify: `modules/kst_local/backend.py`
- Modify: `modules/kst_local/identity_registry.py`
- Modify: `gui/kst_api_manager.py`
- Test: `tests/test_kst_local_db_reader.py`
- Test: `tests/test_kst_local_log_source.py`
- Test: `tests/test_kst_identity_registry.py`
- Test: `tests/test_kst_api_manager.py`

- [ ] 单个数据库失败不丢弃同身份其他数据库已经读到的合法 ID；全部失败仍报错。
- [ ] Electron 身份可用当天结构化 `visitorCustomField` 日志补充正式配置 ID；多项目冲突继续拒绝绑定。
- [ ] API 状态同时检查当前项目是否在 `bound_project_ids`，不再以任意项目成功代表当前项目成功。
- [ ] 运行上述四个测试文件。

### Task 3: 快商通安全错误与百度失败诊断

**Files:**
- Modify: `modules/kst_local/http_server.py`
- Modify: `modules/kst_local/source.py`
- Modify: `modules/baidu_report_api.py`
- Test: `tests/test_kst_multi_identity_http.py`
- Test: `tests/test_kst_local_source.py`
- Test: `tests/test_basic.py`

- [ ] 快商通 HTTP 与置零报告保留白名单错误类别，不再统一吞成“不可用”。
- [ ] `baidu_api_attempt_report.json` 增加失败来源、profile 和白名单安全详情。
- [ ] 不改变百度请求次数、20 秒预算、原子降级和标准报告提交规则。
- [ ] 运行相关快商通测试和百度 API/多来源测试。

### Task 4: 百度无输出提醒、版本和发布

**Files:**
- Modify: `gui/task_runner.py`
- Modify: `gui/main_window.py`
- Modify: `gui/version.py`
- Modify: `README.md`
- Modify: `README_同事使用说明.md`
- Modify: `docs/kst-local-api.md`
- Create: `docs/releases/2026.7.31.118.md`
- Test: `tests/test_basic.py`

- [ ] 百度阶段 45 秒无输出只提示一次，新输出重置计时；停止按钮行为不变。
- [ ] 更新版本与用户文档。
- [ ] 运行相关测试、完整 `test_basic.py`、发布包审计和 GUI 冒烟。
- [ ] 生成 118 在线更新包与完整安装器。
- [ ] 审查、提交、合并 main 并推送；GitHub Release 仍由用户手动发布。
