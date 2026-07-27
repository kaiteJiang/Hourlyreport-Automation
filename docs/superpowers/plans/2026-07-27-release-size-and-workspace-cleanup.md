# Release Size and Workspace Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 114 版 GUI EXE 恢复到不携带 pandas/numpy 的轻量构建，并让仓库与 `dist/` 只保留当前有效内容。

**Architecture:** 将快商通日报纯统计逻辑从 pandas 文件读取模块拆出，使 GUI 本地 API 的静态导入图保持轻量；所有桌面构建物进入版本化 `build/release_<version>_staging`，发布编排器从暂存区生成两个最终资产并清空 `dist/` 旧文件。清理采用明确白名单，保留浏览器状态、备份、开发环境、配置、授权和运行令牌。

**Tech Stack:** Python 3.14、pytest、PyInstaller、Inno Setup 6、PowerShell、Git

## Global Constraints

- 目标版本固定为 `2026.7.27.114`。
- `browser_profile/`、`backups/`、`.venv/`、`secrets/`、`configs/`、`runtime/` 必须保留。
- 不运行真实 `run`、`run-daily`，不写任何业务 Excel。
- 在线更新包和安装器不得包含配置、授权、日志、报告、备份、诊断、快商通导出、浏览器资料或运行令牌。
- `dist/` 最终只能有 114 在线更新 ZIP 和 114 完整安装器。
- 不回滚当前用户对项目配置及 `docs/releases/2026.7.22.109.md` 的未提交修改。

---

### Task 1: 拆分快商通日报纯统计模块

**Files:**
- Create: `modules/kst_daily_aggregation.py`
- Modify: `modules/kst_daily_parser.py`
- Modify: `modules/kst_local/service.py`
- Modify: `modules/kst_local/source.py`
- Modify: `tests/test_kst_packaging.py`

**Interfaces:**
- Produces: `DAILY_KST_METRICS`、`default_daily_kst_date(today=None)`、`classify_daily_dialog_by_tags(tags)`、`empty_daily_kst_account_row()`、`empty_daily_kst_accounts(accounts=None)`、`aggregate_kst_daily_rows(rows, config)`。
- Consumes: `modules.kst_parser` 的行字段常量、账户映射和访客消息判断。
- Compatibility: `modules.kst_daily_parser` 继续重新导出上述名称，现有调用方无需改变。

- [ ] **Step 1: 写入失败的导入图回归测试**

```python
def test_gui_kst_api_import_graph_does_not_load_tabular_stack():
    command = [
        sys.executable,
        "-c",
        (
            "import sys; import gui.kst_api_manager; "
            "blocked={'pandas','numpy'} & set(sys.modules); "
            "raise SystemExit(','.join(sorted(blocked))) if blocked else None"
        ),
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_kst_packaging.py::test_gui_kst_api_import_graph_does_not_load_tabular_stack -q`

Expected: FAIL，子进程退出信息包含 `pandas` 或 `numpy`。

- [ ] **Step 3: 移动纯统计逻辑并切换本地 API 导入**

`kst_daily_aggregation.py` 只允许导入标准库、`kst_parser`、`text_normalizer` 和 `validators`。`kst_daily_parser.py` 保留 `kst_export_parser` 文件读取逻辑，并从新模块导入/重新导出统计接口。`kst_local/service.py` 和 `kst_local/source.py` 直接从新模块导入。

- [ ] **Step 4: 运行 GREEN 与快商通相关测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_kst_packaging.py tests\test_kst_local_service.py tests\test_kst_local_source.py tests\test_kst_local_http_server.py tests\test_kst_multi_identity_http.py -q`

Expected: PASS，且导入图测试不再看到 pandas/numpy。

- [ ] **Step 5: 提交依赖边界变更**

```powershell
git add modules/kst_daily_aggregation.py modules/kst_daily_parser.py modules/kst_local/service.py modules/kst_local/source.py tests/test_kst_packaging.py
git commit -m "refactor: keep KST API free of pandas runtime"
```

### Task 2: 将裸 EXE 移出 dist 并增加最终发布编排

**Files:**
- Modify: `tools/build_desktop_exe.py`
- Modify: `tools/build_release.py`
- Modify: `tools/build_windows_installer.py`
- Create: `tools/build_publish_release.py`
- Modify: `tests/test_basic.py`
- Modify: `tests/test_kst_packaging.py`

**Interfaces:**
- `desktop_staging_dir(root, version=None) -> Path`
- `build_desktop_exe(root, *, output_dir=None) -> int`
- `build_release(..., artifact_dir=None, output_dir=None) -> Path`
- `build_windows_installer(..., artifact_dir=None, output_dir=None) -> Path`
- `build_publish_release(root, version, *, compiler=None) -> tuple[Path, Path]`

- [ ] **Step 1: 写失败测试**

测试必须证明：

```python
assert desktop_staging_dir(root, "2026.7.27.114") == (
    root / "build" / "release_2026.7.27.114_staging"
)
```

并用临时目录和假构建函数验证 `build_publish_release` 在开始时清空 `dist/`，结束时只留下：

```python
{
    "Hourlyreport_automation_v2026.7.27.114.zip",
    "Hourlyreport_automation_setup_v2026.7.27.114.exe",
}
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_kst_packaging.py tests\test_basic.py -k "staging_dir or publish_release or online_update_build or first_install_build" -q`

Expected: FAIL，因为新接口尚不存在或仍从 `dist/hourlyreport_automation.exe` 读取。

- [ ] **Step 3: 实施版本化暂存目录**

`build_desktop_exe` 默认把 EXE 和 manifest 写入 `build/release_<source_version>_staging/`。`build_release` 和安装器构建显式接收 `artifact_dir`，将该目录中的 EXE写入包根目录，不再扫描 `dist/hourlyreport_automation.exe`。

- [ ] **Step 4: 实施最终发布编排器**

核心行为：

```python
def build_publish_release(root, version, *, compiler=None):
    dist = Path(root) / "dist"
    _clear_directory_files(dist)
    staging = desktop_staging_dir(root, version)
    if build_desktop_exe(root, output_dir=staging) != 0:
        raise RuntimeError("桌面 EXE 构建失败")
    update = build_release(
        root,
        version=version,
        online_update=True,
        artifact_dir=staging,
        output_dir=dist,
    )
    installer = build_windows_installer(
        root,
        version,
        compiler=compiler,
        artifact_dir=staging,
        output_dir=dist,
    )
    assert {item.name for item in dist.iterdir()} == {update.name, installer.name}
    return update, installer
```

删除动作只允许发生在已解析并验证等于 `<root>/dist` 的目录内。

- [ ] **Step 5: 运行构建工具测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_basic.py tests\test_kst_packaging.py -k "release or installer or desktop_build or publish" -q`

Expected: PASS。

- [ ] **Step 6: 提交发布编排变更**

```powershell
git add tools/build_desktop_exe.py tools/build_release.py tools/build_windows_installer.py tools/build_publish_release.py tests/test_basic.py tests/test_kst_packaging.py
git commit -m "build: stage desktop artifacts outside dist"
```

### Task 3: 清理 Git 跟踪内容与忽略规则

**Files:**
- Modify: `.gitignore`
- Modify: `tests/test_basic.py`
- Delete from Git: `.claude/settings.local.json`
- Delete: `create_config.bat`
- Delete: `run_11.bat`
- Delete: `run_15.bat`
- Delete: `run_18.bat`
- Delete: `run_fetch_baidu.bat`
- Delete: `run_fetch_baidu_15.bat`
- Delete: `run_inspect.bat`
- Delete: `run_mock_write.bat`
- Delete: `run_parse_kst_export_15.bat`
- Delete: `run_test_browser_connect.bat`
- Delete: `setup_env.bat`
- Delete: `START_HERE.bat`

**Interfaces:**
- `.gitignore` 必须忽略 `.claude/settings.local.json`、`diagnostics/`、`configs/multi_project_selection.json`、`credentials.local.json.lock`、构建与运行输出。
- 有效入口列表固定为 `install_env.bat`、`run_menu.bat`、`run_desktop_gui.bat`、`run_hermes_hourly.bat`、`run_hermes_daily.bat`。

- [ ] **Step 1: 写失败的仓库卫生测试**

```python
def test_obsolete_root_entry_bats_are_removed():
    obsolete = {"run_11.bat", "run_15.bat", "START_HERE.bat", ...}
    assert not [name for name in obsolete if (root / name).exists()]
```

并扩展 Git ignore 测试，校验本机文件规则存在且 `!samples/.gitkeep` 与 `reports/*` 分行。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_basic.py -k "obsolete_root or gitignore" -q`

Expected: FAIL，因为旧 BAT 仍存在且忽略规则不完整。

- [ ] **Step 3: 删除旧入口并修复忽略规则**

使用 `apply_patch` 删除受 Git 跟踪的旧 BAT；执行：

```powershell
git rm --cached -- .claude/settings.local.json
```

仅移出 Git 索引，本机文件保留。

- [ ] **Step 4: 运行仓库卫生测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_basic.py -k "obsolete_root or gitignore or windows_user_entry_bats" -q`

Expected: PASS。

- [ ] **Step 5: 提交仓库卫生变更**

```powershell
git add .gitignore tests/test_basic.py
git add -u -- .claude/settings.local.json create_config.bat run_11.bat run_15.bat run_18.bat run_fetch_baidu.bat run_fetch_baidu_15.bat run_inspect.bat run_mock_write.bat run_parse_kst_export_15.bat run_test_browser_connect.bat setup_env.bat START_HERE.bat
git commit -m "chore: remove obsolete repository entry points"
```

### Task 4: 同步 114 版本和发布文档

**Files:**
- Modify: `gui/version.py`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `README_同事使用说明.md`
- Modify: `docs/online_update_sop.md`
- Create: `docs/releases/2026.7.27.114.md`

**Interfaces:**
- `CURRENT_VERSION = "2026.7.27.114"`
- 当前标准安装器、下载文件名和发布基线全部指向 114。

- [ ] **Step 1: 更新已有版本断言为 114 并确认 RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_basic.py -k "current_version or online_update_build" -q`

Expected: FAIL，当前源码仍是 113。

- [ ] **Step 2: 更新版本和文档**

发布说明需包含：移除 GUI 中 pandas/numpy/OpenBLAS、裸 EXE 移出 dist、仓库清理、用户配置与浏览器/备份数据不受影响。

- [ ] **Step 3: 运行版本测试**

Run: `.venv\Scripts\python.exe -m pytest tests\test_basic.py -k "version or online_update" -q`

Expected: PASS。

- [ ] **Step 4: 提交版本文档**

```powershell
git add gui/version.py AGENTS.md README.md README_同事使用说明.md docs/online_update_sop.md docs/releases/2026.7.27.114.md tests/test_basic.py
git commit -m "release: prepare v2026.7.27.114"
```

### Task 5: 清理本地边角料并重建最终资产

**Files/directories removed locally:**
- Old contents of `dist/`
- Old contents of `build/`
- `__pycache__/`, `tests/__pycache__/`, `tools/__pycache__/`, `.pytest_cache/`
- `.playwright-cli/`, `.superpowers/`
- `logs/*` except `.gitkeep`
- `reports/*` except `.gitkeep`
- `diagnostics/`
- `.ignore`, `_verify_excel.py`, `nul`
- Root untracked legacy `.spec` files
- Stale `credentials.local.json.lock` only after confirming no owning process

**Preserved:** `browser_profile/`, `backups/`, `.venv/`, `secrets/`, `configs/`, `runtime/`, `kst_exports/`.

- [ ] **Step 1: 验证每个删除目标位于仓库根目录内**

PowerShell 必须对每个目标执行 `Resolve-Path`/`GetFullPath`，确认其前缀为仓库绝对路径；不得使用未解析通配符执行递归删除。

- [ ] **Step 2: 关闭仅用于验证的本程序实例**

只终止可执行路径明确等于本仓库旧 `dist/hourlyreport_automation.exe` 的进程，不影响其他安装目录中的用户程序。

- [ ] **Step 3: 清理可再生成内容**

使用原生 PowerShell `Remove-Item -LiteralPath`，逐个删除已验证目标。运行输出目录删除文件后重新保留 `.gitkeep`。

- [ ] **Step 4: 运行全量测试**

Run: `.venv\Scripts\python.exe -m pytest`

Expected: 0 failed。

- [ ] **Step 5: 构建两个最终资产**

Run: `.venv\Scripts\python.exe tools\build_publish_release.py --version 2026.7.27.114`

Expected: `dist/` 仅生成更新 ZIP 和完整安装器。

- [ ] **Step 6: 检查 EXE 依赖、包内容和体积**

从在线更新 ZIP 临时提取 EXE并执行：

```powershell
.venv\Scripts\pyi-archive_viewer.exe -l <extracted-exe>
```

输出不得包含 `pandas`、`numpy`、`openblas`。ZIP 不得含用户状态目录；体积应接近 112 的 38.84 MB 基线。

- [ ] **Step 7: 只读运行验证**

启动最终 EXE，验证带本地令牌的 `/health` 返回 200 且未鉴权返回 401；运行快商通小时报/日报只读 API 校验，不运行完整任务，不写 Excel。

- [ ] **Step 8: 清理构建暂存区并复核工作区**

删除当前 `build/` 暂存内容。运行：

```powershell
git diff --check
git status --short
Get-ChildItem dist -File
```

确认 `dist/` 恰好两个文件，Git 状态只含计划内变更和用户原有配置变更。

- [ ] **Step 9: 提交遗漏的清理元数据并推送**

```powershell
git add -A -- <计划内路径>
git commit -m "chore: finalize clean v2026.7.27.114 release"
git push origin main
```

不得暂存项目配置、secrets、报告、日志、备份、诊断或运行时文件。
