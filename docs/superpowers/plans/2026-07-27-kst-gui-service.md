# KST GUI-Managed Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GUI 自动托管昆明牛商务通本地 API，显示 `● KST  ● 实时` 状态，支持 API/人工来源切换，并在 API 不可用时以 0 商务通数据继续百度小时报。

**Architecture:** 新增 Qt 生命周期管理器，在后台线程探测、启动、复用和停止现有回环 HTTP 服务；新增独立状态控件负责精确显示和来源菜单。小时报来源适配器增加显式零数据降级，项目配置模块负责持久化来源选择，主窗口只协调信号和界面。

**Tech Stack:** Python 3.14、PySide6、Python `ThreadingHTTPServer`、标准库 `threading/urllib`、pytest。

## Global Constraints

- API 只能监听 `127.0.0.1:18766`。
- GUI 主线程不得执行数据库扫描或网络健康检查。
- GUI 隐藏到托盘时服务继续；真正退出时只停止 GUI 自己创建的服务。
- 显示文本必须为 `● KST  ● 实时`，不再出现“已就绪”。
- `local_api` 与 `export` 始终互斥，不自动混合。
- API 不可用时仅在 `allow_zero_on_unavailable=true` 下生成零商务通报告并继续百度。
- 不输出认证令牌、请求头、聊天正文或访客个人信息。
- 开发期间主目录昆明牛保持 `export`；验收通过后才切回 `local_api`。
- 不运行正式 Excel 写入。

---

### Task 1: 项目来源持久化与 API 零数据降级

**Files:**
- Modify: `modules/project_config.py`
- Modify: `modules/kst_local/source.py`
- Modify: `modules/run_pipeline.py`
- Modify: `configs/projects/project_template.json`
- Test: `tests/test_kst_project_source.py`
- Test: `tests/test_kst_local_source.py`
- Test: `tests/test_kst_local_pipeline.py`

**Interfaces:**
- Produces: `get_project_kst_data_source(root: str | Path, project_id: str) -> str`
- Produces: `set_project_kst_data_source(root: str | Path, project_id: str, value: str) -> dict[str, Any]`
- Produces: `write_unavailable_zero_result(config: dict[str, Any], root: Path, period: str | None, target_date: str | None, reason: str) -> dict[str, Any]`
- Consumes: existing `fetch_kst_local_report(...)` and `empty_kst_accounts(...)`.

- [ ] **Step 1: Write failing source-mode persistence tests**

```python
def test_set_project_kst_source_preserves_unrelated_fields(tmp_path):
    project = _write_project(tmp_path, data_source="export", marker={"keep": 1})
    saved = set_project_kst_data_source(tmp_path, "kunming_niu", "local_api")
    assert saved["kst"]["data_source"] == "local_api"
    assert saved["marker"] == {"keep": 1}
    assert get_project_kst_data_source(tmp_path, "kunming_niu") == "local_api"

def test_set_project_kst_source_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="local_api.*export"):
        set_project_kst_data_source(tmp_path, "kunming_niu", "mixed")
```

- [ ] **Step 2: Run persistence tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_project_source.py
```

Expected: import failure for the two new functions.

- [ ] **Step 3: Implement atomic project source persistence**

Implement both functions in `modules/project_config.py`. Resolve the project path through `configs/projects/<project_id>.json`, parse UTF-8 JSON, validate against `{"local_api", "export"}`, write to a `.tmp` sibling with `ensure_ascii=False, indent=2`, then `replace()` the original.

- [ ] **Step 4: Write failing zero-fallback tests**

```python
def test_api_unavailable_can_emit_zero_kst_report(tmp_path):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True
    result = fetch_kst_local_report(
        config, tmp_path, "15点", target_date="2026-07-27",
        transport=lambda *_: (_ for _ in ()).throw(OSError("offline")),
    )
    assert result["parse_report"]["passed"] is True
    assert result["dialog_data"]["source"] == "kst_local_api_unavailable_zero"
    assert result["dialog_data"]["summary"]["api_unavailable"] is True
    assert all(
        value == 0
        for account in result["dialog_data"]["accounts"].values()
        for value in account.values()
    )

def test_api_unavailable_still_raises_without_opt_in(tmp_path):
    with pytest.raises(KstLocalSourceError):
        fetch_kst_local_report(
            _config(), tmp_path, "15点",
            transport=lambda *_: (_ for _ in ()).throw(OSError("offline")),
        )
```

- [ ] **Step 5: Run zero-fallback tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_source.py
```

Expected: unavailable request still raises even with opt-in.

- [ ] **Step 6: Implement zero report and pipeline warning**

Add `write_unavailable_zero_result`. Its `dialog_data` must contain `source="kst_local_api_unavailable_zero"`, zero accounts from `get_required_accounts`, `summary.api_unavailable=True`, and a warning without raw exception details. `parse_report.passed=True`. Update `fetch_kst_local_report` to use it only when the config flag is true. Update `run_half_auto_pipeline` to log and display “商务通 API 不可用，已按 0 继续” when this source is returned.

- [ ] **Step 7: Run Task 1 tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_project_source.py tests/test_kst_local_source.py tests/test_kst_local_pipeline.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add modules/project_config.py modules/kst_local/source.py modules/run_pipeline.py configs/projects/project_template.json tests/test_kst_project_source.py tests/test_kst_local_source.py tests/test_kst_local_pipeline.py
git commit -m "feat: persist KST source and continue with zero fallback"
```

### Task 2: GUI-owned API lifecycle manager

**Files:**
- Create: `gui/kst_api_manager.py`
- Modify: `modules/kst_local/http_server.py`
- Test: `tests/test_kst_api_manager.py`

**Interfaces:**
- Produces: `class KstApiManager(QObject)`
- Produces signals: `status_changed = Signal(bool, str)`, `log_message = Signal(str)`
- Produces methods: `start() -> None`, `stop() -> None`, `is_ready() -> bool`, `status_detail() -> str`
- Consumes: `build_live_runtime`, `create_server`, `load_project_config`, `build_runtime_config_from_project`.

- [ ] **Step 1: Write failing lifecycle tests with injected dependencies**

```python
def test_manager_starts_owned_server_and_stops_it(qapp, tmp_path):
    server = FakeServer()
    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=20,
    )
    manager.start()
    assert wait_until(manager.is_ready)
    manager.stop()
    assert server.shutdown_calls == 1
    assert server.close_calls == 1

def test_manager_reuses_external_server_without_stopping_it(qapp, tmp_path):
    manager = KstApiManager(tmp_path, probe=lambda *_: True)
    manager.start()
    assert wait_until(manager.is_ready)
    manager.stop()
    assert manager.owns_server() is False

def test_failed_start_stays_gray_and_retries(qapp, tmp_path):
    attempts = []
    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: attempts.append(1) or (_ for _ in ()).throw(OSError("busy")),
        retry_interval_ms=20,
    )
    manager.start()
    assert wait_until(lambda: len(attempts) >= 2)
    assert manager.is_ready() is False
```

The test module creates a session-local `QApplication` fixture and a `wait_until` helper that calls `QApplication.processEvents()` without adding `pytest-qt`.

- [ ] **Step 2: Run manager tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_api_manager.py
```

Expected: `gui.kst_api_manager` import failure.

- [ ] **Step 3: Implement threaded lifecycle manager**

`start()` starts a Qt retry timer and calls `_ensure_service_async()`. The worker first calls the injected `probe`; a compatible existing service sets ready without ownership. Otherwise it builds the Kunming runtime, creates the server, stores it under a lock, emits ready, and runs `serve_forever()` in the worker thread. Failures emit gray status and return. `stop()` stops the timer, sets `_stopping`, calls `shutdown/server_close` only for the owned server, joins the worker for at most five seconds, and is idempotent.

- [ ] **Step 4: Add production health probe**

Implement `probe_kst_health(url: str, token: str, timeout: float = 1.5) -> tuple[bool, str]`. It calls `/health`, accepts only JSON with `status="ok"` and `required_endpoints_available=true`, and returns a sanitized reason without response bodies.

- [ ] **Step 5: Run manager tests**

Run Task 2 command again. Expected: all pass and no live port access.

- [ ] **Step 6: Commit**

```powershell
git add gui/kst_api_manager.py modules/kst_local/http_server.py tests/test_kst_api_manager.py
git commit -m "feat: manage KST API with GUI lifecycle"
```

### Task 3: `● KST  ● 实时` control and source menu

**Files:**
- Create: `gui/kst_status_control.py`
- Modify: `gui/main_window.py`
- Test: `tests/test_kst_status_control.py`

**Interfaces:**
- Produces: `class KstStatusControl(QWidget)`
- Produces signal: `source_selected = Signal(str)`
- Produces methods: `set_api_ready(ready: bool, detail: str) -> None`, `set_source_mode(mode: str) -> None`
- Consumes: `get_project_kst_data_source`, `set_project_kst_data_source`.

- [ ] **Step 1: Write failing offscreen widget tests**

```python
def test_status_control_exact_text_and_colors(qapp):
    control = KstStatusControl()
    assert control.kst_button.text() == "● KST"
    assert control.live_label.text() == "● 实时"
    control.set_api_ready(False, "未启动")
    assert control.kst_button.property("apiReady") is False
    control.set_api_ready(True, "127.0.0.1:18766")
    assert control.kst_button.property("apiReady") is True

def test_status_control_emits_exclusive_source_mode(qapp):
    control = KstStatusControl()
    values = []
    control.source_selected.connect(values.append)
    control.manual_action.trigger()
    control.api_action.trigger()
    assert values == ["export", "local_api"]
    assert control.api_action.isChecked()
```

- [ ] **Step 2: Run widget tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_status_control.py
```

Expected: module import failure.

- [ ] **Step 3: Implement status control**

Build a transparent horizontal widget containing a `QToolButton("● KST")` and `QLabel("● 实时")`. The KST button owns an exclusive menu with `API 自动获取` and `人工导出`. Dynamic property `apiReady` selects green `#34c759` or gray `#9aa5b1`; the live label remains green. `set_api_ready` refreshes the style and tooltip.

- [ ] **Step 4: Replace LogConsole ready overlay**

In `LogConsole.__init__`, remove `ready_dot` and `ready_label`, add `KstStatusControl` to the existing overlay, preserve the right-aligned resize behavior, and expose it as `self.status_control`. In `MainWindow`, store `self.kst_status_control`.

- [ ] **Step 5: Connect source menu**

On startup read Kunming mode, set the checked action, and connect `source_selected` to a handler that persists the mode, appends `商务通来源已切换为 API 自动获取/人工导出`, and does not restart the current task.

- [ ] **Step 6: Run widget and relevant GUI tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_status_control.py tests/test_basic.py -k "gui or log or project_config" --maxfail=1
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```powershell
git add gui/kst_status_control.py gui/main_window.py tests/test_kst_status_control.py
git commit -m "feat: show KST and realtime status in log panel"
```

### Task 4: Main window lifecycle integration

**Files:**
- Modify: `gui/main_window.py`
- Modify: `gui/app.py`
- Test: `tests/test_kst_gui_lifecycle.py`

**Interfaces:**
- Consumes: `KstApiManager.start/stop/status_changed/log_message`
- Produces: `MainWindow.start_kst_api()`, `MainWindow.stop_kst_api()`, `MainWindow.on_kst_api_status_changed(bool, str)`.

- [ ] **Step 1: Write failing integration tests with fake manager**

```python
def test_window_starts_manager_and_updates_status(qapp, app_root):
    fake = FakeKstApiManager()
    window = MainWindow(app_root, kst_api_manager_factory=lambda *_: fake)
    process_events()
    assert fake.start_calls == 1
    fake.status_changed.emit(True, "ready")
    assert window.kst_status_control.kst_button.property("apiReady") is True
    window.stop_kst_api()
    assert fake.stop_calls == 1

def test_window_exit_stops_manager_once(qapp, app_root):
    fake = FakeKstApiManager()
    window = MainWindow(app_root, kst_api_manager_factory=lambda *_: fake)
    window.stop_kst_api()
    window.stop_kst_api()
    assert fake.stop_calls == 1
```

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_gui_lifecycle.py
```

Expected: constructor does not accept the manager factory.

- [ ] **Step 3: Integrate manager**

Add an optional factory argument to `MainWindow` and `create_window`. After `_build_ui`, construct the manager, connect its two signals, and schedule `start_kst_api` with `QTimer.singleShot(0, ...)`. Status changes call `set_api_ready`; log messages go through `append_log`. Make stop idempotent.

- [ ] **Step 4: Connect application shutdown**

In `gui/app.py`, connect `app.aboutToQuit` to `window.stop_kst_api` before `instance_guard.close`. Keep tray hide behavior unchanged.

- [ ] **Step 5: Run lifecycle tests**

Run Task 4 command again. Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add gui/main_window.py gui/app.py tests/test_kst_gui_lifecycle.py
git commit -m "feat: bind KST API service to GUI lifetime"
```

### Task 5: Live verification, final API default, and documentation

**Files:**
- Modify: `configs/projects/kunming_niu.json`
- Modify: `docs/kst-local-api.md`
- Modify: `README_同事使用说明.md`
- Test: all files from Tasks 1–4.

**Interfaces:**
- Consumes the completed manager, status control, source adapter, and project setting.
- Produces the user-ready default `kst.data_source="local_api"` with `allow_zero_on_unavailable=true`.

- [ ] **Step 1: Run all focused tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$tests = Get-ChildItem tests -Filter 'test_kst*.py' | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest -q @tests
```

Expected: all pass.

- [ ] **Step 2: Perform offscreen visual capture**

Create a `QApplication`, instantiate `KstStatusControl`, render gray and green states to `work/kst-status-gray.png` and `work/kst-status-green.png`, and inspect both images. Verify exact text, right alignment suitability, circle visibility, and no clipping.

- [ ] **Step 3: Perform live owned-service lifecycle test**

Start `KstApiManager` with the real Kunming config, wait until `is_ready()`, query `/health`, then call `stop()` and verify port `18766` no longer accepts connections when the manager owned it. If an external compatible service existed before the test, verify the manager reports external ownership and leaves it running.

- [ ] **Step 4: Perform live data equality test**

Build the live API report for `2026-07-26` without writing Excel. Compare it with `数据统计_网页记录_20260727085840-0.xlsx` and assert:

```text
rows=26
accounts=14/1/11
unmatched=0
all five account metrics equal
```

- [ ] **Step 5: Verify zero fallback with unavailable port**

Point a temporary runtime config at a closed loopback port with `allow_zero_on_unavailable=true`. Assert source is `kst_local_api_unavailable_zero`, all KST metrics are zero, and a fake merge receives intact Baidu data. Do not call the Excel writer.

- [ ] **Step 6: Switch final default back to API**

Set in `configs/projects/kunming_niu.json`:

```json
{
  "data_source": "local_api",
  "allow_zero_on_unavailable": true
}
```

Keep `export_dir` unchanged for manual switching.

- [ ] **Step 7: Update docs**

Document `● KST  ● 实时`, click-to-switch behavior, GUI-owned lifecycle, gray/green semantics, zero fallback, and manual export recovery.

- [ ] **Step 8: Run final verification**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$tests = Get-ChildItem tests -Filter 'test_kst*.py' | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest -q @tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_basic.py -k "kst or runtime_config or run_pipeline or gui" --maxfail=1
.\.venv\Scripts\python.exe -m compileall -q gui modules/kst_local main.py
git diff --check
```

Expected: all focused tests pass, related legacy tests pass, compile succeeds, and diff check is clean.

- [ ] **Step 9: Commit**

```powershell
git add configs/projects/kunming_niu.json docs/kst-local-api.md README_同事使用说明.md
git commit -m "docs: enable GUI-managed KST API for Kunming"
```

## Self-review

- Spec coverage: lifecycle ownership/reuse/retry/stop is Task 2 and Task 4; exact UI and switch are Task 3; zero fallback and manual mode are Task 1; live equality and final default are Task 5.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, or unspecified error-handling steps.
- Type consistency: manager and control method/signal names match across Tasks 2–4; project source values are consistently `local_api` and `export`.
- Scope: all tasks serve the single GUI-managed KST objective; no unrelated GUI refactor or Excel behavior change is included.
