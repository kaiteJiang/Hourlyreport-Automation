# v116 快商通新旧双客户端兼容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布 `2026.7.29.116`，让同一套本地 API 自动兼容 Electron `OnlineWebCSNew` 和 Java/JCEF `OnlineCS.exe 7.03.17`，并消除无条件启动、无限刷屏和停止卡顿。

**Architecture:** 保留现有 Electron 适配链路，新增隔离的老版发现器、标准 SQLite 只读读取器和老版聚合服务。身份注册表通过统一安装类型分派推广 ID 读取、就绪检查、状态指纹和运行时构建；GUI 只在 API 模式托管服务，并把本机路径保存到受更新保护的 `runtime/`。

**Tech Stack:** Python 3.11、标准库 `sqlite3`/`threading`/`winreg`、PySide6、pytest、现有 PyInstaller/Inno Setup 发布脚本。

## Global Constraints

- 发布版本固定为 `2026.7.29.116`，累计序号不得重置。
- 不新增第三方运行依赖，不运行真实 `run`/`run-daily`，不写目标 Excel。
- 所有老版数据库连接必须只读，锁等待最多 500 毫秒，单身份查询截止时间五秒。
- 本地 HTTP 请求超时固定为十五秒，停止不得在 GUI 线程执行五秒 `join`。
- 不启动、不关闭、不操作快商通客户端；只检测受支持进程并启动自己的 `127.0.0.1:18766` 服务。
- 仅接受目标日期即时 `*-onlie/*_CS.pdb` 分片证明过的 `recId`，历史库孤立记录不得统计。
- 标签继续复用现有小时报和日报聚合口径，不修改“有效/一般有效/转潜”定义。
- `runtime/`、真实快商通数据库、日志、身份、Token 和业务数据不得提交或进入发布包。
- 保留用户当前 `configs/` 改动，不暂存、不回滚、不复制到工作树。

---

### Task 1: 本机路径设置与老版客户端发现

**Files:**
- Create: `modules/kst_local/machine_settings.py`
- Create: `modules/kst_local/legacy_discovery.py`
- Modify: `modules/kst_local/models.py`
- Modify: `modules/kst_local/discovery.py`
- Test: `tests/test_kst_machine_settings.py`
- Test: `tests/test_kst_legacy_discovery.py`

**Interfaces:**
- Produces:
  - `KstMachineSettings(installation_root: Path | None, data_root: Path | None)`
  - `load_kst_machine_settings(root: str | Path) -> KstMachineSettings`
  - `save_kst_machine_settings(root: str | Path, *, installation_root: str | Path | None, data_root: str | Path | None) -> KstMachineSettings`
  - `LegacyKstInstallation(root, executable, version, identity, log_dir, data_root, history_db, message_database_paths, client_family="legacy_java")`
  - `KstInstallationLike = KstInstallation | LegacyKstInstallation`
  - `discover_legacy_installations(*, explicit_root: str | Path | None = None, explicit_data_root: str | Path | None = None, process_paths: Iterable[Path] | None = None, now_timestamp: float | None = None, require_running_process: bool = True) -> list[LegacyKstInstallation]`
  - `discover_all_installations(root: str | Path, *, require_running_process: bool = True) -> list[KstInstallationLike]`
- Consumes: existing Electron `discover_installations()` without changing its validated semantics.

- [ ] **Step 1: Write machine-setting tests**

```python
def test_machine_settings_round_trip_uses_runtime_only(tmp_path):
    saved = save_kst_machine_settings(
        tmp_path,
        installation_root=r"D:\KST\OnlineCustomerService",
        data_root=r"D:\Documents\KuaiShangDataNew",
    )
    assert saved.installation_root == Path(r"D:\KST\OnlineCustomerService")
    assert load_kst_machine_settings(tmp_path) == saved
    assert not (tmp_path / "configs" / "app_config.json").exists()


def test_invalid_machine_settings_json_fails_closed(tmp_path):
    path = tmp_path / "runtime" / "kst_machine_settings.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(KstMachineSettingsError, match="本机路径设置"):
        load_kst_machine_settings(tmp_path)
```

- [ ] **Step 2: Run settings tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_machine_settings.py -q
```

Expected: collection fails because `modules.kst_local.machine_settings` does not exist.

- [ ] **Step 3: Implement atomic, non-sensitive machine settings**

```python
@dataclass(frozen=True)
class KstMachineSettings:
    installation_root: Path | None = None
    data_root: Path | None = None


def save_kst_machine_settings(root, *, installation_root, data_root):
    path = Path(root) / "runtime" / "kst_machine_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return load_kst_machine_settings(root)
```

Reject unknown keys and non-string path values. Empty strings normalize to `None`.

- [ ] **Step 4: Write synthetic legacy discovery tests**

```python
def make_legacy_tree(tmp_path):
    install = tmp_path / "OnlineCustomerService"
    data = tmp_path / "Documents" / "KuaiShangDataNew"
    (install / "config").mkdir(parents=True)
    (install / "OnlineCS.exe").write_bytes(b"MZ")
    sqlite_template(install / "config" / "DBCOMPANY.dll", history_schema=True)
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "260729090000.log").write_text("active", encoding="utf-8")
    sqlite_history(data / "db" / "company-a" / "company-a_HIS.cdb")
    sqlite_messages(
        data / "db" / "company-a" / "agent-a" / "07290900-onlie" / "agent-a_CS.pdb"
    )
    return install, data


def test_legacy_discovery_requires_running_matching_onlinecs(tmp_path):
    install, data = make_legacy_tree(tmp_path)
    assert discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[],
        now_timestamp=time.time(),
    ) == []


def test_legacy_discovery_returns_each_capable_company_identity(tmp_path):
    install, data = make_legacy_tree(tmp_path)
    found = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
    )
    assert [(item.client_family, item.identity) for item in found] == [
        ("legacy_java", "company-a")
    ]
```

- [ ] **Step 5: Run discovery tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_discovery.py -q
```

Expected: collection fails because `legacy_discovery` and `LegacyKstInstallation` do not exist.

- [ ] **Step 6: Implement capability-based legacy discovery**

Implement these helpers with the stated concrete behavior:

```python
def redirected_documents_candidates() -> tuple[Path, ...]:
    values = []
    if os.name == "nt":
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            values.append(Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0])))
    values.append(Path.home() / "Documents")
    return tuple(dict.fromkeys(path.resolve() for path in values))


def running_kst_process_paths() -> tuple[Path, ...]:
    completed = subprocess.run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='OnlineCS.exe'\" | "
            "Select-Object -ExpandProperty ExecutablePath",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        **hidden_subprocess_kwargs(),
    )
    return tuple(
        Path(line.strip()).resolve()
        for line in completed.stdout.splitlines()
        if line.strip()
    )
```

Use `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders\Personal`,
`Path.home() / "Documents"` and existing explicit paths. Require `OnlineCS.exe`,
SQLite-format `config/DBCOMPANY.dll`, recent logs within 900 seconds, history table
`OC_HDVISITORINFO`, message table `DIALOGRECORD_VISITOR`, and process path equality.
Explicit invalid paths raise `KstDiscoveryError` with a safe category; automatic candidates
may be skipped.

- [ ] **Step 7: Preserve Electron discovery and add combined discovery**

`discover_all_installations()` loads machine settings, calls the existing Electron discoverer
and the legacy discoverer, deduplicates by `(client_family, root, identity)`, and never converts
an explicit legacy path into an Electron path.

- [ ] **Step 8: Run Task 1 tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_machine_settings.py tests\test_kst_legacy_discovery.py tests\test_kst_local_discovery.py -q
```

Expected: all pass.

- [ ] **Step 9: Commit Task 1**

```powershell
git add modules/kst_local/machine_settings.py modules/kst_local/legacy_discovery.py modules/kst_local/models.py modules/kst_local/discovery.py tests/test_kst_machine_settings.py tests/test_kst_legacy_discovery.py
git commit -m "feat: discover legacy KST clients safely"
```

---

### Task 2: 老版 SQLite 可信读取与标签归一化

**Files:**
- Create: `modules/kst_local/legacy_db_reader.py`
- Test: `tests/test_kst_legacy_db_reader.py`

**Interfaces:**
- Consumes: `LegacyKstInstallation`, existing `KstConversation`.
- Produces:
  - `KstLegacyDatabaseError`
  - `normalize_legacy_tags(*values: Any) -> tuple[str, ...]`
  - `read_legacy_promotion_ids(installation, *, cancel_event=None, deadline_seconds=5.0) -> set[str]`
  - `read_legacy_conversations(installation, target_date, *, cancel_event=None, deadline_seconds=5.0) -> list[KstConversation]`

- [ ] **Step 1: Write RED tests for source isolation**

```python
def test_history_only_record_is_not_counted(legacy_installation):
    insert_history(
        legacy_installation.history_db,
        rec_id="history-only",
        start="2026-07-29 09:10:00",
        messages=3,
        promotion_id="10001",
        tags="有效-三句话",
    )
    assert read_legacy_conversations(legacy_installation, "2026-07-29") == []


def test_live_shard_authorizes_matching_history_record(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="live-1",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="live-1",
        start="2026-07-29 09:10:00",
        messages=3,
        promotion_id="10001",
        tags="有效-三句话",
    )
    rows = read_legacy_conversations(legacy_installation, "2026-07-29")
    assert [(row.rec_id, row.promotion_id, row.visitor_messages) for row in rows] == [
        ("live-1", "10001", 3)
    ]
```

- [ ] **Step 2: Run source-isolation tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_db_reader.py -q
```

Expected: import fails because `legacy_db_reader` does not exist.

- [ ] **Step 3: Implement read-only connections and deadlines**

```python
def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=0.5,
    )
    connection.execute("PRAGMA busy_timeout=500")
    return connection
```

Install a progress handler that aborts when `cancel_event.is_set()` or
`time.monotonic() >= deadline`. Never use `immutable=1` because the running client may append.
Allow only fixed `SELECT` statements and schema inspection.

- [ ] **Step 4: Implement authorized `recId` collection and history join**

For every message shard, select distinct `recId` where `date(addTime)=?`. Query history in
bounded parameter chunks:

```sql
SELECT recId, curEnterTime, diaStartTime, visitorSendNum,
       visitorCustomField, keyword, bidWord,
       talkGrade, dialogClassification, classifyTag, cusTypeTag, aiTags
FROM OC_HDVISITORINFO
WHERE recId IN ({one_parameter_marker_per_chunk_item})
```

Require every authorized `recId` to have a history row; otherwise raise
`KstLegacyDatabaseError("老版快商通会话尚未同步完整")`. Drop complete rows whose
`visitorSendNum <= 0`, matching the existing “有访客消息” rule.

- [ ] **Step 5: Write and pass tag-format tests**

```python
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (('["有效-三句话", "转潜-有效"]',), ("有效-三句话", "转潜-有效")),
        (({"1": "有效-一般"},), ("有效-一般",)),
        (("有效-三句话、有效-一般|转潜-有效",), ("有效-三句话", "有效-一般", "转潜-有效")),
    ],
)
def test_normalize_legacy_tags(values, expected):
    assert normalize_legacy_tags(*values) == expected
```

Parse JSON recursively; otherwise split on `、,，;；|\r\n`. Preserve first-seen order and
exact non-empty label text.

- [ ] **Step 6: Write and pass validation/cancellation tests**

Cover missing promotion ID, promotion ID regex, negative message count, invalid date,
database lock timeout, deadline expiration and pre-set cancellation. Assert each raises a
safe `KstLegacyDatabaseError` and source files remain byte-for-byte unchanged.

- [ ] **Step 7: Run Task 2 tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_db_reader.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add modules/kst_local/legacy_db_reader.py tests/test_kst_legacy_db_reader.py
git commit -m "feat: read legacy KST SQLite data safely"
```

---

### Task 3: 统一身份路由与老版报表服务

**Files:**
- Create: `modules/kst_local/legacy_service.py`
- Create: `modules/kst_local/backend.py`
- Modify: `modules/kst_local/runtime.py`
- Modify: `modules/kst_local/identity_registry.py`
- Modify: `modules/kst_local/__init__.py`
- Test: `tests/test_kst_legacy_service.py`
- Test: `tests/test_kst_dual_backend.py`
- Modify: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Produces:
  - `LegacyKstConversationService(config, installation, cancel_event=None)`
  - `LegacyKstRuntime(installation, service)` with `health()`
  - `read_installation_promotion_ids(installation) -> set[str]`
  - `installation_ready(installation, target_date) -> bool`
  - `installation_runtime_state(installation, target_date, snapshot=None) -> tuple[Any, ...]`
  - `build_installation_runtime(config, target_date, *, installation, snapshot=None) -> KstLiveRuntime | LegacyKstRuntime`
- Consumes: Electron `read_identity_promotion_ids`, `_required_endpoints_available`,
  `_runtime_input_state`, `build_live_runtime`; legacy Task 2 readers.

- [ ] **Step 1: Write legacy aggregation tests**

```python
def test_legacy_service_reuses_hourly_tag_rules(legacy_installation, project_config):
    seed_conversation(
        legacy_installation,
        rec_id="1",
        promotion_id="10001",
        visitor_messages=3,
        tags=("有效-三句话", "转潜-有效"),
    )
    report = LegacyKstConversationService(
        project_config,
        legacy_installation,
    ).build_hourly_report("2026-07-29", "11点")
    assert report["accounts"]["账户A"] == {
        "总对话": 1,
        "有效对话": 1,
        "一般有效": 0,
        "有效转潜": 1,
        "总转潜": 1,
    }
```

Add a daily assertion proving `有效-一般` stays out of daily `有效对话` and enters
`一般有效对话`.

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_service.py -q
```

Expected: import fails because `legacy_service` does not exist.

- [ ] **Step 3: Implement the legacy service**

Convert `KstConversation` to the exact normalized row keys already consumed by
`aggregate_kst_export_rows` and `aggregate_kst_daily_rows`. Filter promotion IDs through
`config["kst"]["promotion_id_accounts"]`; an out-of-project ID raises
`KstServiceError`, never reassigns the row.

- [ ] **Step 4: Write backend dispatch RED tests**

```python
def test_backend_dispatches_legacy_without_electron_snapshot(legacy_installation, config):
    runtime = build_installation_runtime(
        config,
        "2026-07-29",
        installation=legacy_installation,
    )
    assert isinstance(runtime, LegacyKstRuntime)


def test_backend_preserves_electron_builder(electron_installation, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(backend, "build_live_runtime", lambda *a, **k: sentinel)
    assert build_installation_runtime(
        {},
        "2026-07-29",
        installation=electron_installation,
        snapshot=AutomaticSourceSnapshot(
            sources_by_rec_id={},
            auth=KstAuthContext(),
        ),
    ) is sentinel
```

- [ ] **Step 5: Implement family dispatch**

`backend.py` uses `isinstance(installation, LegacyKstInstallation)`. Unknown types raise
`KstDiscoveryError("不支持的快商通客户端结构")`. Do not add family branches inside
Electron readers.

- [ ] **Step 6: Adapt identity registry without changing mapping semantics**

Replace default injected functions with backend dispatchers and combined discovery:

```python
installations_loader=lambda: discover_all_installations(root, require_running_process=True)
promotion_id_reader=read_installation_promotion_ids
runtime_builder=build_installation_runtime
runtime_state_reader=installation_runtime_state
endpoint_checker=installation_ready
```

For Electron, parse the existing snapshot before state/runtime construction. For legacy,
pass `snapshot=None` and use DB file size/mtime state. Cache keys include family and all
database paths.

- [ ] **Step 7: Run Task 3 and Electron regression tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_service.py tests\test_kst_dual_backend.py tests\test_kst_identity_registry.py tests\test_kst_local_runtime.py tests\test_kst_local_service.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add modules/kst_local/legacy_service.py modules/kst_local/backend.py modules/kst_local/runtime.py modules/kst_local/identity_registry.py modules/kst_local/__init__.py tests/test_kst_legacy_service.py tests/test_kst_dual_backend.py tests/test_kst_identity_registry.py
git commit -m "feat: route KST projects across client generations"
```

---

### Task 4: GUI路径设置、按模式启停、防刷屏与有界停止

**Files:**
- Modify: `gui/main_window.py`
- Modify: `gui/kst_api_manager.py`
- Modify: `modules/kst_local/source.py`
- Modify: `tests/test_kst_gui_lifecycle.py`
- Modify: `tests/test_kst_global_menu.py`
- Modify: `tests/test_kst_api_manager.py`
- Modify: `tests/test_kst_local_source.py`
- Modify: `tests/test_basic.py`

**Interfaces:**
- Consumes: `load_kst_machine_settings`, `save_kst_machine_settings`,
  `KstIdentityRegistry.refresh()`.
- Produces:
  - `MainWindow.choose_kst_installation_root()`
  - `MainWindow.choose_kst_data_root()`
  - `MainWindow.rescan_kst_api()`
  - restartable `MainWindow.start_kst_api()` / `stop_kst_api()`
  - `KstApiManager.rescan()`
  - categorized `KstApiManager.status_detail()`

- [ ] **Step 1: Write lifecycle RED tests**

```python
@pytest.mark.parametrize(("mode", "starts"), [("local_api", 1), ("export", 0)])
def test_window_starts_manager_only_in_api_mode(qapp, tmp_path, mode, starts):
    set_kst_data_source(tmp_path, mode)
    fake = FakeKstApiManager()
    window = MainWindow(tmp_path, kst_api_manager_factory=lambda *_: fake)
    QApplication.processEvents()
    assert fake.start_calls == starts


def test_mode_switch_stops_and_restarts_manager(qapp, tmp_path):
    fake = FakeKstApiManager()
    window = MainWindow(tmp_path, kst_api_manager_factory=lambda *_: fake)
    window.set_global_kst_data_source("export")
    window.set_global_kst_data_source("local_api")
    assert (fake.stop_calls, fake.start_calls) == (1, 2)
```

- [ ] **Step 2: Run GUI lifecycle tests and verify RED**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe -m pytest tests\test_kst_gui_lifecycle.py tests\test_kst_global_menu.py -q
```

Expected: export mode currently starts the manager and switching mode does not control it.

- [ ] **Step 3: Implement mode-aware restartable lifecycle**

Remove unconditional `QTimer.singleShot(0, self.start_kst_api)`. Schedule start only when
`self.kst_data_source == "local_api"`. Replace the permanent `_kst_api_stopped` latch with
separate `api_requested` and `application_exiting` state so an export→API switch can restart.
Mode changes call start/stop after the setting saves successfully.

- [ ] **Step 4: Add path and rescan menu tests, then implementation**

Assert the styled KST submenu contains, in order:

```text
API 自动获取
人工导出对话
---
选择快商通程序目录
选择快商通数据目录
重新扫描快商通
```

Use `QFileDialog.getExistingDirectory`. Cancel leaves settings unchanged. Successful selection
saves only the chosen field while preserving the other and calls `rescan_kst_api()`. Rescan is
disabled in export mode and does not start the server.

- [ ] **Step 5: Write retry-dedup RED tests**

```python
def test_identical_start_failure_logs_once_until_reminder(qapp, tmp_path):
    now = [0.0]
    messages = []
    manager = failing_manager(tmp_path, monotonic=lambda: now[0])
    manager.log_message.connect(messages.append)
    manager.start()
    trigger_attempts(manager, 4)
    assert len(messages) == 1
    now[0] = 301
    trigger_attempts(manager, 1)
    assert len(messages) == 2


def test_error_change_logs_immediately(qapp, tmp_path):
    manager = sequenced_failure_manager(tmp_path, ["客户端未运行", "数据库结构不兼容"])
    messages = collect_two_attempts(manager)
    assert messages == ["客户端未运行", "数据库结构不兼容"]
```

- [ ] **Step 6: Implement categorized failures and backoff**

Preserve safe exception messages from discovery/mapping/port errors. Store
`_last_error_key`, `_last_error_log_at`, `_retry_index`; schedule one-shot retry intervals
`[5000, 15000, 30000, 60000]`. Log on first occurrence, category change, or after 300 seconds.
Success clears state. `rescan()` clears delay and starts an immediate worker only in started state.

- [ ] **Step 7: Write non-blocking stop RED test and implement**

Use a fake worker blocked on an event. Measure `manager.stop()`:

```python
started = time.monotonic()
manager.stop()
assert time.monotonic() - started < 0.1
```

`stop()` sets a cancellation event, stops timers and requests server shutdown without joining
worker/server threads on the GUI thread. Worker finalizers close owned resources exactly once.
Application exit may poll completion through a short Qt timer but must not block the window.

- [ ] **Step 8: Bound local HTTP source timeout**

Change both hourly and daily defaults from 60 to 15 seconds and clamp configured values to
`1..15`. A fake transport appends its received timeout to `recorded_timeouts`; tests assert
`recorded_timeouts == [15]` for both an omitted setting and a configured value of `99`.

- [ ] **Step 9: Run Task 4 tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe -m pytest tests\test_kst_gui_lifecycle.py tests\test_kst_global_menu.py tests\test_kst_api_manager.py tests\test_kst_local_source.py tests\test_basic.py -q
```

Expected: all pass with no Qt warnings treated as failures.

- [ ] **Step 10: Commit Task 4**

```powershell
git add gui/main_window.py gui/kst_api_manager.py modules/kst_local/source.py tests/test_kst_gui_lifecycle.py tests/test_kst_global_menu.py tests/test_kst_api_manager.py tests/test_kst_local_source.py tests/test_basic.py
git commit -m "fix: make KST API lifecycle responsive"
```

---

### Task 5: v116版本、用户文档与发布边界

**Files:**
- Modify: `gui/version.py`
- Modify: `README.md`
- Modify: `README_同事使用说明.md`
- Modify: `docs/kst-local-api.md`
- Create: `docs/releases/2026.7.29.116.md`
- Modify: packaging manifest/spec files only if Task 6 audit finds missing new modules
- Modify: `tests/test_kst_packaging.py`
- Modify: `tests/test_basic.py`

**Interfaces:**
- Produces: `CURRENT_VERSION = "2026.7.29.116"` and user-facing dual-client instructions.

- [ ] **Step 1: Write/update version and packaging RED assertions**

Extend `test_desktop_spec_packages_kst_database_bridge` and
`test_online_update_build_contains_program_but_excludes_user_data` with assertions that:

```python
assert CURRENT_VERSION == "2026.7.29.116"
assert "modules/kst_local" in spec_source.replace("\\", "/")
assert not any(name.startswith("runtime/") for name in names)
assert not any(name.endswith((".db", ".cdb", ".pdb")) for name in names)
```

- [ ] **Step 2: Run targeted release tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_packaging.py tests\test_basic.py::test_online_update_build_contains_program_but_excludes_user_data tests\test_basic.py::test_online_release_version_counter_never_resets_with_date tests\test_basic.py::test_gui_uses_unified_hourlyreport_technical_names -q
```

Expected: version/package expectations fail before version and build metadata are updated.

- [ ] **Step 3: Update version and documentation**

Document:

- supported client families and executable names;
- how automatic detection works;
- “系统 → 快商通模式” path-selection actions;
- old data root `KuaiShangDataNew`;
- client must be running;
- safe zero-on-unavailable behavior;
- exact error categories and retry policy;
- manual export remains explicit and does not run local API.

Release notes title:

```markdown
# 蚁之力 · 竞价数据自动化 v2026.7.29.116
```

Describe the release as “双代际本地数据引擎”，without claiming unverified real-data parity.

- [ ] **Step 4: Run docs/version/package tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_packaging.py tests\test_basic.py::test_online_update_build_contains_program_but_excludes_user_data tests\test_basic.py::test_online_release_version_counter_never_resets_with_date tests\test_basic.py::test_gui_uses_unified_hourlyreport_technical_names -q
git diff --check
```

Expected: all pass; no whitespace errors.

- [ ] **Step 5: Commit Task 5**

```powershell
git add gui/version.py README.md README_同事使用说明.md docs/kst-local-api.md docs/releases/2026.7.29.116.md tests
git commit -m "docs: prepare v116 dual-client release"
```

---

### Task 6: 全量验证、代码审查、构建与发布审计

**Files:**
- Modify only files required by failing verification or review findings.
- Produce: `dist/Hourlyreport_automation_v2026.7.29.116.zip`
- Produce: `dist/Hourlyreport_automation_setup_v2026.7.29.116.exe`

**Interfaces:**
- Consumes all prior tasks.
- Produces a reviewed, reproducible v116 release commit and two audited release artifacts.

- [ ] **Step 1: Run focused KST suite**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe -m pytest tests\test_kst_machine_settings.py tests\test_kst_legacy_discovery.py tests\test_kst_legacy_db_reader.py tests\test_kst_legacy_service.py tests\test_kst_dual_backend.py tests\test_kst_identity_registry.py tests\test_kst_api_manager.py tests\test_kst_gui_lifecycle.py tests\test_kst_local_source.py -q
```

Expected: all pass.

- [ ] **Step 2: Run full suite**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures and zero collection errors.

- [ ] **Step 3: Request code review**

Use `superpowers:requesting-code-review` with:

- description: v116 legacy Java/JCEF KST adapter plus lifecycle hardening;
- requirements: the approved design document;
- base SHA: `5dff0eb`;
- head SHA: current feature branch HEAD.

Fix every Critical and Important finding through a new RED→GREEN test cycle. Re-run focused
and full suites after fixes.

- [ ] **Step 4: Build executable and release artifacts**

Use the repository's existing build entry points documented by `docs/online_update_sop.md`.
Build `hourlyreport_automation.exe`, then online update ZIP and full installer. Do not invoke
business `run` or write Excel.

- [ ] **Step 5: Audit online ZIP and installer staging**

Enumerate every member and fail on:

```text
configs/
runtime/
secrets/
logs/
reports/
backups/
diagnostics/
kst_exports/
browser_profile/
.worktrees/
.claude/worktrees/
*.db
*.cdb
*.pdb
```

Assert the update ZIP contains the required executable and new Python modules/resources exactly
once, and contains no loose duplicate `hourlyreport_automation.exe` outside the intended payload.

- [ ] **Step 6: Verify updater metadata logic**

Run the existing update-check fixture against tag/version `v2026.7.29.116` and asset name
`Hourlyreport_automation_v2026.7.29.116.zip`. Do not create the GitHub Release; the user handles it.

- [ ] **Step 7: Final fresh verification**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python.exe -m pytest -q
git status --short
git diff --check
```

Record test count, artifact byte sizes and SHA-256 hashes in
`docs/releases/2026.7.29.116.md`, then commit the verification record:

```powershell
git add docs/releases/2026.7.29.116.md
git commit -m "release: verify v2026.7.29.116 artifacts"
```

- [ ] **Step 8: Integrate and push**

Use `superpowers:finishing-a-development-branch`, present its required integration choices and
wait for the user's selection. If the user selects local merge to `main`, re-run the full suite
on the merged tree and then, under the existing GitHub delivery authorization:

```powershell
git push origin main
```

Do not publish a GitHub Release. Report the pushed commit, tag readiness, artifact paths, sizes,
hashes, tests and any remaining limitation requiring coworker real-data validation.
