# v117 KST Live-Write Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 v116 对 Electron 动态数据库写入的误判，并让人工路径选择在用户可见区域和明确路径错误时可直接使用。

**Architecture:** 在身份注册表内集中提供按客户端家族区分的指纹比较，所有刷新、健康检查和运行态入口复用该规则。GUI 复用既有目录选择与保存函数，只增加内嵌信号入口和每生命周期一次的路径错误引导。

**Tech Stack:** Python 3.14、PySide6、pytest、PyInstaller、Inno Setup

## Global Constraints

- 不运行真实 `run` / `run-daily`，不写业务 Excel。
- 不提交 `configs/` 本机改动、`runtime/`、数据库、日志、报告或 secrets。
- Electron 允许同一路径数据库动态变化，但路径集合变化必须失效。
- Java/JCEF 继续完整指纹 fail-closed。
- 路径自动引导每次 GUI 生命周期最多一次，且只响应明确路径错误。
- 发布版本固定为 `2026.7.29.117`。

---

### Task 1: Electron 动态写库一致性

**Files:**
- Modify: `modules/kst_local/identity_registry.py`
- Test: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Consumes: `KstInstallationLike`、`KstInstallation`、现有五段指纹元组。
- Produces: `_identity_fingerprint_matches(installation, baseline, current) -> bool`。

- [ ] **Step 1: 写失败测试**

增加测试，让 Electron 推广 ID 读取期间只改变指纹第五段文件状态，断言 `refresh()` 后健康且项目绑定成功；再修改现有数据库内容，断言健康仍为 `ok`。

- [ ] **Step 2: 验证 RED**

运行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_kst_identity_registry.py -k "electron and live_write" -q
```

预期：当前完整指纹比较将状态判定为 `database_busy_or_timeout` 或绑定 stale。

- [ ] **Step 3: 最小实现**

在 `identity_registry.py` 增加统一比较函数：Electron 比较指纹前四段，其他客户端比较完整指纹；替换刷新前后、发现指纹和已绑定指纹的直接 `!=`。

- [ ] **Step 4: 验证 GREEN 与严格边界**

运行新增测试，以及旧版变化拒绝、Electron 新路径失效测试。

- [ ] **Step 5: 提交**

```cmd
git add modules/kst_local/identity_registry.py tests/test_kst_identity_registry.py
git commit -m "fix: tolerate live Electron database writes"
```

### Task 2: 可见人工路径入口与单次引导

**Files:**
- Modify: `gui/main_window.py`
- Test: `tests/test_kst_global_menu.py`

**Interfaces:**
- Consumes: `choose_kst_installation_root()`、`choose_kst_data_root()`、`rescan_kst_api()`。
- Produces: `InlineConfigMenu` 三个请求信号和 `MainWindow` 单次路径引导状态。

- [ ] **Step 1: 写失败测试**

增加测试断言内嵌快商通区包含三个路径操作；信号分别调用既有处理函数；程序目录或数据目录错误只打开一次对应选择框；数据库超时不打开路径框。

- [ ] **Step 2: 验证 RED**

运行：

```cmd
.venv\Scripts\python.exe -m pytest tests\test_kst_global_menu.py -q
```

预期：缺少信号、操作行和单次引导状态而失败。

- [ ] **Step 3: 最小实现**

为 `InlineConfigMenu` 增加三个信号和操作行，在 `MainWindow` 连接现有处理函数；`on_kst_api_status_changed` 仅对两个明确路径错误调用一次对应目录选择函数。

- [ ] **Step 4: 验证 GREEN**

运行 `tests/test_kst_global_menu.py`、`tests/test_kst_gui_lifecycle.py` 和 `tests/test_kst_api_manager.py`。

- [ ] **Step 5: 提交**

```cmd
git add gui/main_window.py tests/test_kst_global_menu.py
git commit -m "fix: expose KST path recovery controls"
```

### Task 3: v117 发布与交付

**Files:**
- Modify: `gui/version.py`
- Create: `docs/releases/2026.7.29.117.md`
- Modify: `README.md`
- Modify: `README_同事使用说明.md`
- Modify: `docs/kst-local-api.md`

**Interfaces:**
- Produces: `Hourlyreport_automation_v2026.7.29.117.zip` 和 `Hourlyreport_automation_setup_v2026.7.29.117.exe`。

- [ ] **Step 1: 更新版本与文档**

记录 Electron 动态写入容错、旧版严格保护、路径入口和升级说明。

- [ ] **Step 2: 运行专项与全量测试**

运行所有 `tests/test_kst_*.py` 和完整 `pytest -q`。

- [ ] **Step 3: 构建与审计**

用包含 PySide6 的干净工作树构建 v117；检查 `dist` 仅有两个包、在线包无配置/运行数据/数据库、归档内包含 Qt GUI 模块。

- [ ] **Step 4: 更新识别验证**

用真实 v117 文件名、大小和 SHA-256 验证 v116 客户端能选择更新。

- [ ] **Step 5: 审查、合并、推送与本地同步**

审查通过后合并 `main`，保留主工作区本机配置，推送 GitHub，并将验证过的 v117 EXE 同步到开发目录根部。

