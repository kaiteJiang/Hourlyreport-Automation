# Global KST API Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让所有正式项目默认使用一个 GUI 托管的本地快商通 API，通过全局唯一推广 ID 自动把本机多个登录身份路由到对应项目，并保留系统菜单中的全局人工导出开关。

**Architecture:** 应用配置保存唯一全局快商通模式；身份发现层枚举本机全部身份并只读提取历史推广 ID；注册表用全局唯一反向索引建立严格一对一项目绑定；HTTP 服务按 `project_id` 路由到绑定身份。GUI 的系统菜单负责全局模式切换，日志区 KST 控件只显示服务健康状态。

**Tech Stack:** Python 3.14、PySide6、`ThreadingHTTPServer`、商务通 Electron/SQLCipher 只读桥、pytest、PyInstaller。

## Global Constraints

- 本地 API 只监听 `127.0.0.1:18766`。
- `API 自动获取` 与 `人工导出对话` 是全局互斥模式，默认 `local_api`。
- API 模式失败时该项目快商通五项指标按 0，百度数据继续；不得自动读取人工导出文件。
- 推广 ID 必须在全部正式项目间唯一；重复、跨项目、一个项目多身份或一个身份多项目均不得猜测绑定。
- 每个项目请求只能使用其绑定身份的日志、认证快照、数据库和推广 ID 白名单。
- 实时日志的 `● KST` 不可点击，只显示 API 健康状态。
- 快商通模式菜单必须复用系统菜单样式，不使用独立 Windows 原生菜单。
- 不写正式 Excel 进行前置验收。
- 不输出认证令牌、聊天正文或访客个人信息。

---

### Task 1: 全局快商通模式配置

**Files:**
- Modify: `modules/project_config.py`
- Modify: `configs/app_config.json`
- Modify: `configs/projects/project_template.json`
- Test: `tests/test_kst_global_mode.py`

**Interfaces:**
- Produces: `normalize_kst_data_source(value: Any) -> str`
- Produces: `get_kst_data_source(root: str | Path) -> str`
- Produces: `set_kst_data_source(root: str | Path, value: str) -> str`
- Consumes: existing `load_app_config`, `_write_json_atomically`, `build_runtime_config_from_project`.

- [ ] **Step 1: Write failing global-mode tests**

```python
def test_global_kst_mode_defaults_to_local_api(tmp_path):
    _write_app_and_project(tmp_path, app_extra={})
    assert get_kst_data_source(tmp_path) == "local_api"

def test_global_kst_mode_persists_without_changing_projects(tmp_path):
    project_path = _write_app_and_project(tmp_path)
    before = project_path.read_text(encoding="utf-8")
    assert set_kst_data_source(tmp_path, "export") == "export"
    assert get_kst_data_source(tmp_path) == "export"
    assert project_path.read_text(encoding="utf-8") == before

def test_runtime_uses_global_mode_for_every_project(tmp_path):
    project = load_project_config(tmp_path, "project_a")
    runtime = build_runtime_config_from_project(project, {})
    assert runtime["kst"]["data_source"] == "local_api"
    assert runtime["kst"]["allow_zero_on_unavailable"] is True
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_global_mode.py
```

Expected: import failure for `get_kst_data_source` and `set_kst_data_source`.

- [ ] **Step 3: Implement application-level mode**

Add `kst_data_source` defaulting to `local_api` in `load_app_config`. Implement atomic getter/setter accepting only `local_api` and `export`. In `build_runtime_config_from_project`, set:

```python
kst["data_source"] = normalize_kst_data_source(
    app_config.get("kst_data_source")
)
kst["allow_zero_on_unavailable"] = True
```

Project `kst.export_dir` remains unchanged for manual mode. Remove project-level `data_source` from the template so the global value is authoritative.

- [ ] **Step 4: Run Task 1 tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_global_mode.py tests/test_kst_project_source.py tests/test_kst_local_pipeline.py
```

Expected: all pass. Legacy project source helpers may remain for compatibility but runtime selection must use the global value.

- [ ] **Step 5: Commit**

```powershell
git add modules/project_config.py configs/app_config.json configs/projects/project_template.json tests/test_kst_global_mode.py
git commit -m "feat: add global KST data source mode"
```

### Task 2: 统一系统菜单与只读 KST 状态

**Files:**
- Modify: `gui/kst_status_control.py`
- Modify: `gui/main_window.py`
- Test: `tests/test_kst_status_control.py`
- Test: `tests/test_kst_global_menu.py`

**Interfaces:**
- Consumes: `get_kst_data_source`, `set_kst_data_source`
- Produces: `MainWindow.set_global_kst_data_source(mode: str) -> None`
- Changes: `KstStatusControl` no longer emits `source_selected`; `kst_button` becomes a non-interactive label or disabled-menu-free tool button.

- [ ] **Step 1: Write failing status and menu tests**

```python
def test_kst_status_has_no_menu_or_click_source_signal(qapp):
    control = KstStatusControl()
    assert control.kst_button.menu() is None
    assert control.kst_button.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert not hasattr(control, "api_action")
    assert not hasattr(control, "manual_action")

def test_system_menu_contains_global_kst_submenu(qapp, app_root):
    window = MainWindow(app_root, kst_api_manager_factory=lambda *_: FakeManager())
    assert window.kst_mode_menu.title() == "快商通模式"
    assert window.kst_api_action.text() == "API 自动获取"
    assert window.kst_export_action.text() == "人工导出对话"
    assert window.kst_api_action.actionGroup().isExclusive()
    assert window.kst_mode_menu.styleSheet() == window.excel_auto_open_menu.styleSheet()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_status_control.py tests/test_kst_global_menu.py
```

Expected: old KST control still has its own menu and `MainWindow` has no `kst_mode_menu`.

- [ ] **Step 3: Make KST status display-only**

Remove the KST source menu and source signal from `KstStatusControl`. Keep:

```text
● KST  ● 实时
```

`set_api_ready` continues to control gray/green state and tooltip. The KST widget must not change the mode.

- [ ] **Step 4: Add styled global system submenu**

Create an exclusive `QActionGroup` in the existing system menu:

```python
self.kst_mode_menu = QMenu("快商通模式", self.system_config_menu)
self.kst_api_action = QAction("API 自动获取", group)
self.kst_export_action = QAction("人工导出对话", group)
```

Apply `_style_menu(self.kst_mode_menu, 206)` exactly like the existing system submenus. Read the global mode on startup and persist changes through `set_global_kst_data_source`. Log one sanitized setting message.

- [ ] **Step 5: Run Task 2 tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_status_control.py tests/test_kst_global_menu.py tests/test_basic.py -k "menu or gui" --maxfail=1
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add gui/kst_status_control.py gui/main_window.py tests/test_kst_status_control.py tests/test_kst_global_menu.py
git commit -m "feat: move KST mode into system menu"
```

### Task 3: 枚举全部本机身份与历史推广 ID

**Files:**
- Modify: `modules/kst_local/discovery.py`
- Modify: `modules/kst_local/db_reader.py`
- Create: `modules/kst_local/resources/read_promotion_ids.js`
- Modify: `tools/hourlyreport_automation.spec`
- Test: `tests/test_kst_local_discovery.py`
- Test: `tests/test_kst_local_db_reader.py`
- Test: `tests/test_kst_packaging.py`

**Interfaces:**
- Produces: `discover_installations(explicit_root=None, local_app_data=None) -> list[KstInstallation]`
- Produces: `read_identity_promotion_ids(installation: KstInstallation, runner=subprocess.run) -> set[str]`
- Consumes: existing `KstInstallation`, `_validate_root`, SQLCipher key derivation and Electron node mode.

- [ ] **Step 1: Write failing multi-identity discovery test**

```python
def test_discover_installations_returns_every_identity(tmp_path):
    root, local = _make_installation_with_identities(
        tmp_path, ["100_aaa", "200_bbb", "300_ccc"]
    )
    found = discover_installations(root, local)
    assert [item.identity for item in found] == ["100_aaa", "200_bbb", "300_ccc"]
```

- [ ] **Step 2: Write failing promotion-ID reader test**

```python
def test_read_identity_promotion_ids_merges_all_rotated_databases(tmp_path):
    installation = _installation(tmp_path)
    outputs = iter([
        {"promotionIds": ["72828178", "72828179"]},
        {"promotionIds": ["72828179", "81509165"]},
    ])
    result = read_identity_promotion_ids(
        installation,
        runner=lambda *_args, **_kwargs: completed(next(outputs)),
    )
    assert result == {"72828178", "72828179", "81509165"}
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_discovery.py tests/test_kst_local_db_reader.py
```

Expected: missing `discover_installations` and `read_identity_promotion_ids`.

- [ ] **Step 4: Implement all-identity discovery**

Refactor the current single-identity scanner into a function returning every identity with both recent logs and at least one `VISITOR*.db`. Keep `discover_installation` as a compatibility wrapper that selects an explicit identity or the most recently active identity.

- [ ] **Step 5: Implement the historical promotion-ID bridge**

The new JavaScript bridge must:

- reuse the current SHA256/MD5 SQLCipher key derivation;
- open with `{readonly: true, fileMustExist: true}`;
- iterate only `visitorCustomField` and `info` columns;
- extract promotion IDs in memory and output only `{"promotionIds": [...]}`;
- execute no mutation SQL and return no visitor fields.

Python invokes it once per rotated database, merges IDs, and raises `KstDatabaseError` on invalid output.

- [ ] **Step 6: Package the bridge**

Add `read_promotion_ids.js` to the PyInstaller `datas` list beside `read_visitor_db.js`. Extend the packaging test to require both files.

- [ ] **Step 7: Run Task 3 tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_discovery.py tests/test_kst_local_db_reader.py tests/test_kst_packaging.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add modules/kst_local/discovery.py modules/kst_local/db_reader.py modules/kst_local/resources/read_promotion_ids.js tools/hourlyreport_automation.spec tests/test_kst_local_discovery.py tests/test_kst_local_db_reader.py tests/test_kst_packaging.py
git commit -m "feat: discover KST identities by promotion IDs"
```

### Task 4: 推广 ID 唯一注册表与防串读路由

**Files:**
- Create: `modules/kst_local/identity_registry.py`
- Modify: `modules/kst_local/runtime.py`
- Test: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Produces: `build_project_promotion_index(projects: list[dict[str, Any]]) -> dict[str, str]`
- Produces: `class KstIdentityRegistry`
- Produces: `KstIdentityRegistry.refresh() -> None`
- Produces: `KstIdentityRegistry.installation_for(project_id: str) -> KstInstallation`
- Produces: `KstIdentityRegistry.build_runtime(project_id: str, target_date: str) -> KstLiveRuntime`
- Produces: `KstIdentityRegistry.health() -> dict[str, Any]`
- Consumes: `list_projects`, `load_project_config`, `discover_installations`, `read_identity_promotion_ids`, `build_live_runtime`.

- [ ] **Step 1: Write failing uniqueness and mapping tests**

```python
def test_duplicate_promotion_id_across_projects_is_rejected():
    with pytest.raises(KstIdentityMappingError, match="重复"):
        build_project_promotion_index([
            project("a", ["1001"]),
            project("b", ["1001"]),
        ])

def test_three_identities_map_to_three_projects_by_promotion_id():
    registry = registry_for(
        projects=[project("a", ["1001"]), project("b", ["2001"]), project("c", ["3001"])],
        identities={"id-a": {"1001"}, "id-b": {"2001"}, "id-c": {"3001"}},
    )
    registry.refresh()
    assert registry.installation_for("a").identity == "id-a"
    assert registry.installation_for("b").identity == "id-b"
    assert registry.installation_for("c").identity == "id-c"
```

- [ ] **Step 2: Write failing ambiguity tests**

```python
@pytest.mark.parametrize("identities", [
    {"id-a": {"1001", "2001"}},
    {"id-a": {"1001"}, "id-b": {"1001"}},
])
def test_ambiguous_identity_mapping_is_never_guessed(identities):
    registry = registry_for(
        projects=[project("a", ["1001"]), project("b", ["2001"])],
        identities=identities,
    )
    registry.refresh()
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_identity_registry.py
```

Expected: module import failure.

- [ ] **Step 4: Implement strict reverse mapping**

Build a global unique `promotion_id -> project_id` index from formal project configs. For each identity:

- no known IDs: leave unbound;
- IDs resolve to exactly one project: candidate;
- IDs resolve to multiple projects: record identity conflict;
- more than one identity candidates for one project: record project conflict.

Only conflict-free one-to-one candidates enter `_bindings`. `installation_for` never tries another identity.

- [ ] **Step 5: Bind runtime to the selected installation**

Extend `build_live_runtime` with an optional `installation: KstInstallation`. When supplied, do not call discovery. `KstIdentityRegistry.build_runtime` loads the requested project runtime config and passes only the bound installation.

- [ ] **Step 6: Add safe health diagnostics**

Health returns counts and project IDs only:

```json
{
  "status": "ok",
  "required_endpoints_available": true,
  "identity_count": 3,
  "bound_project_ids": ["a", "b", "c"],
  "unbound_project_ids": [],
  "mapping_error_count": 0
}
```

Do not return raw promotion IDs or authentication data.

- [ ] **Step 7: Run Task 4 tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_identity_registry.py tests/test_kst_local_service.py tests/test_kst_local_discovery.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add modules/kst_local/identity_registry.py modules/kst_local/runtime.py tests/test_kst_identity_registry.py
git commit -m "feat: route KST projects through unique identities"
```

### Task 5: 多项目 HTTP 服务与 GUI 生命周期

**Files:**
- Modify: `modules/kst_local/http_server.py`
- Modify: `gui/kst_api_manager.py`
- Test: `tests/test_kst_local_http_server.py`
- Test: `tests/test_kst_api_manager.py`
- Test: `tests/test_kst_multi_identity_http.py`

**Interfaces:**
- Changes: `ServiceFactory = Callable[[str, str], Any]`
- Produces: factory call `service_factory(project_id, target_date)`
- Consumes: `KstIdentityRegistry`.

- [ ] **Step 1: Write failing project-routing HTTP test**

```python
def test_hourly_endpoint_routes_project_id_to_its_service():
    calls = []
    server = create_server(
        "127.0.0.1", 0,
        service_factory=lambda project_id, target_date: calls.append(
            (project_id, target_date)
        ) or FakeService(project_id),
    )
    payload = get_json(server, "/v1/kst/hourly?project_id=project_b&date=2026-07-27&period=15点")
    assert calls == [("project_b", "2026-07-27")]
    assert payload["project_id"] == "project_b"
```

- [ ] **Step 2: Write failing missing-project test**

```python
def test_hourly_endpoint_rejects_missing_project_id():
    status, payload = request("/v1/kst/hourly?date=2026-07-27")
    assert status == 400
    assert payload == {"error": "project_id_required"}
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_http_server.py tests/test_kst_multi_identity_http.py
```

Expected: factory receives only the date and missing `project_id` is not rejected.

- [ ] **Step 4: Route HTTP requests by project**

Parse and require `project_id` before creating a service. Never accept an identity parameter from the caller. The registry is the only project-to-identity authority.

- [ ] **Step 5: Replace Kunming-specific GUI manager runtime**

`KstApiManager` constructs one `KstIdentityRegistry`, refreshes it in the worker, publishes registry health, and passes:

```python
service_factory=lambda project_id, request_date: registry.build_runtime(
    project_id, request_date
).service
```

Remove hard-coded `project_id="kunming_niu"`. Green status means the API listener and base registry are healthy; individual unbound projects remain project-level warnings.

- [ ] **Step 6: Add three-project no-cross-read integration test**

Create three fake services with distinct sentinel account values and query all three project IDs. Assert each response contains only its own sentinel and that every factory call used the expected project.

- [ ] **Step 7: Run Task 5 tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_http_server.py tests/test_kst_multi_identity_http.py tests/test_kst_api_manager.py tests/test_kst_gui_lifecycle.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add modules/kst_local/http_server.py gui/kst_api_manager.py tests/test_kst_local_http_server.py tests/test_kst_multi_identity_http.py tests/test_kst_api_manager.py
git commit -m "feat: serve multiple KST projects from one API"
```

### Task 6: 流水线、多项目和最终验收

**Files:**
- Modify: `modules/kst_local/source.py`
- Modify: `modules/run_pipeline.py`
- Modify: `modules/multi_project_runner.py`
- Modify: `docs/kst-local-api.md`
- Modify: `README_同事使用说明.md`
- Test: `tests/test_kst_local_source.py`
- Test: `tests/test_kst_local_pipeline.py`
- Test: `tests/test_multi_project.py`

**Interfaces:**
- Consumes: global runtime `config["kst"]["data_source"]`
- Preserves: `fetch_kst_local_report(...)` and zero-fallback result shape.

- [ ] **Step 1: Write failing multi-project API-mode test**

```python
def test_three_project_run_keeps_each_kst_request_scoped(tmp_path):
    requested = []
    configs = {
        project_id: runtime(project_id, kst_mode="local_api")
        for project_id in ("a", "b", "c")
    }
    report = run_multi_project_pipeline(
        root=tmp_path,
        project_ids=["a", "b", "c"],
        runtime_config_loader=lambda _root, project_id: configs[project_id],
        hourly_pipeline=lambda config, **kwargs: requested.append(
            config["project_id"]
        ) or passed_report(config["project_id"]),
        **fake_baidu_dependencies(),
    )
    assert requested == ["a", "b", "c"]
    assert report["summary"]["success"] == 3
```

- [ ] **Step 2: Write failing global manual-mode test**

```python
def test_global_export_mode_keeps_all_projects_on_manual_parser(tmp_path):
    for project_id in ("a", "b", "c"):
        config = runtime(project_id, kst_mode="export")
        assert config["kst"]["data_source"] == "export"
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_kst_local_pipeline.py tests/test_multi_project.py -k "kst or three_project or global_export"
```

Expected: global source is not yet consistently propagated in all fixtures/paths.

- [ ] **Step 4: Preserve strict source behavior**

Ensure every single- and multi-project runtime gets the global mode. In API mode:

- unavailable, unbound, ambiguous or incomplete responses use `kst_local_api_unavailable_zero`;
- the API URL always includes the actual project ID;
- old export files are never inspected.

In export mode, retain existing recent-file behavior and never call the API.

- [ ] **Step 5: Update documentation**

Document:

- `系统 > 快商通模式`;
- global default API and manual recovery;
- promotion-ID reverse mapping;
- multi-identity and multi-project behavior;
- gray/green KST semantics;
- zero fallback and no implicit export fallback.

- [ ] **Step 6: Run focused regression**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$tests = Get-ChildItem tests -Filter 'test_kst*.py' | ForEach-Object FullName
.\.venv\Scripts\python.exe -m pytest -q @tests
.\.venv\Scripts\python.exe -m pytest -q tests/test_basic.py -k "kst or runtime_config or run_pipeline or gui" --maxfail=1
.\.venv\Scripts\python.exe -m compileall -q gui modules/kst_local main.py
git diff --check
```

Expected: all focused tests pass, compilation succeeds and diff check is clean.

- [ ] **Step 7: Perform read-only live validation**

Without writing formal Excel:

1. Detect all currently logged identities.
2. Print only identity count and derived project IDs.
3. Verify each binding is supported by globally unique promotion IDs.
4. Query each bound project independently.
5. For every available project with a matching manual complete export, compare row count, account ownership, unmatched count and five metrics.
6. Start the packaged EXE, require `/health` to be `ok`, then verify owned port release on exit.

- [ ] **Step 8: Rebuild desktop EXE**

Run:

```powershell
.\.venv\Scripts\python.exe tools\build_desktop_exe.py
.\.venv\Scripts\python.exe -m PyInstaller.utils.cliutils.archive_viewer -r -b dist\hourlyreport_automation.exe
```

Require both database bridges and all KST Python modules in the archive. Verify the build manifest source fingerprint matches the current source.

- [ ] **Step 9: Commit**

```powershell
git add modules/kst_local/source.py modules/run_pipeline.py modules/multi_project_runner.py docs/kst-local-api.md README_同事使用说明.md tests/test_kst_local_source.py tests/test_kst_local_pipeline.py tests/test_multi_project.py
git commit -m "docs: enable global multi-identity KST API"
```

## Self-review

- Spec coverage: Task 1 covers global mode; Task 2 covers unified menu and display-only status; Tasks 3–5 cover installation discovery, promotion-ID uniqueness, strict identity mapping and project HTTP routing; Task 6 covers pipelines, multi-project isolation, documentation, packaging and live validation.
- Placeholder scan: no `TBD`, `TODO`, “implement later” or unspecified error-handling steps.
- Type consistency: `KstIdentityRegistry.build_runtime(project_id, target_date)` feeds the two-argument HTTP `service_factory(project_id, target_date)`; global mode names remain `local_api` and `export`.
- Safety: every ambiguous mapping stops that project binding; the HTTP caller cannot choose an identity; API failure never reads a stale export.
