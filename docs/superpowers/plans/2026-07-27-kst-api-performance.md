# KST Local API Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate repeated full KST log scans and runtime reconstruction while preserving the automatic-push whitelist and project routing behavior.

**Architecture:** Add a process-local incremental log snapshot cache, throttle full identity refreshes while the owned API is healthy, and cache live runtimes by semantic input state. Every cache rebuilds automatically on log rotation, automatic-source changes, database changes, identity changes, or TTL expiry.

**Tech Stack:** Python 3.14, standard-library threading/pathlib/dataclasses, PySide6 `QTimer`, pytest, PyInstaller.

## Global Constraints

- Do not change the `msgType=48` automatic-source whitelist.
- Do not treat arbitrary local database rows or manual history-query responses as automatic sources.
- Keep `127.0.0.1:18766`, authentication, HTTP paths, and response schemas unchanged.
- Keep all caches in memory; do not persist credentials, tokens, conversations, or cache files.
- A failed refresh or runtime build must not replace the last successful cache with a zero or partial result.
- Preserve unrelated working-tree changes and do not run a production Excel write.

---

### Task 1: Incremental Log Snapshot Cache

**Files:**
- Modify: `modules/kst_local/log_source.py`
- Modify: `modules/kst_local/runtime.py`
- Modify: `modules/kst_local/identity_registry.py`
- Test: `tests/test_kst_local_log_source.py`

**Interfaces:**
- Produces: `IncrementalLogSnapshotCache.parse(log_dir, target_date, *, auth_date=None) -> AutomaticSourceSnapshot`
- Produces: `IncrementalLogSnapshotCache.diagnostics() -> dict[str, int]`
- Produces: `parse_cached_log_snapshot(log_dir, target_date, *, auth_date=None) -> AutomaticSourceSnapshot`
- Preserves: `parse_log_snapshot(log_dir, target_date, *, auth_date=None)` as the uncached full-scan compatibility entry point.

- [ ] **Step 1: Write failing append-only and truncation tests**

```python
def test_incremental_cache_reads_only_appended_log_bytes(tmp_path):
    log = tmp_path / "app.log"
    first = '[2026-07-27 09:00:00] websocket {"msgType":48,"msgContent":[101]}\n'
    second = '[2026-07-27 09:01:00] websocket {"msgType":48,"msgContent":[202]}\n'
    log.write_text(first, encoding="utf-8")
    cache = IncrementalLogSnapshotCache()
    assert set(cache.parse(tmp_path, "2026-07-27").sources_by_rec_id) == {"101"}
    before = cache.diagnostics()["bytes_read"]
    with log.open("a", encoding="utf-8") as stream:
        stream.write(second)
    assert set(cache.parse(tmp_path, "2026-07-27").sources_by_rec_id) == {"101", "202"}
    assert cache.diagnostics()["bytes_read"] - before == len(second.encode("utf-8"))


def test_incremental_cache_rebuilds_after_log_truncation(tmp_path):
    log = tmp_path / "app.log"
    log.write_text('[2026-07-27 09:00:00] websocket {"msgType":48,"msgContent":[101]}\n', encoding="utf-8")
    cache = IncrementalLogSnapshotCache()
    cache.parse(tmp_path, "2026-07-27")
    log.write_text('[2026-07-27 09:01:00] websocket {"msgType":48,"msgContent":[202]}\n', encoding="utf-8")
    assert set(cache.parse(tmp_path, "2026-07-27").sources_by_rec_id) == {"202"}
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_kst_local_log_source.py -q`

Expected: import or attribute failure because `IncrementalLogSnapshotCache` does not exist.

- [ ] **Step 3: Refactor line parsing into an accumulator and implement the cache**

Implement a locked cache keyed by `(resolved_log_dir, target_date, auth_date)`. Store each file's byte offset and pending unterminated bytes. If the file set changes or any file shrinks, rebuild the entry from zero; otherwise seek to the stored offsets and consume only appended complete lines. Build immutable `AutomaticSourceSnapshot` values from copied dictionaries and frozen source sets.

```python
class IncrementalLogSnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[tuple[Path, str, str | None], _CacheEntry] = {}
        self._bytes_read = 0
        self._full_rebuilds = 0

    def diagnostics(self) -> dict[str, int]:
        with self._lock:
            return {
                "bytes_read": self._bytes_read,
                "full_rebuilds": self._full_rebuilds,
                "entry_count": len(self._entries),
            }
```

Add `parse()` beside this diagnostics method. It must resolve the cache key, call `_log_files`, rebuild through `_new_cache_entry` when the path set changes or a current size is below its saved offset, otherwise call `_consume_appended_bytes` for each grown file, and finally return `_snapshot_from_accumulator`. Create one module-level cache for production and change `runtime.py` plus the identity endpoint checker to call `parse_cached_log_snapshot`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_kst_local_log_source.py tests/test_kst_identity_registry.py -q`

Expected: all tests pass, including multi-ID batches, manual-history exclusion, append-only reads, and truncation rebuilds.

- [ ] **Step 5: Commit the task**

```powershell
git add modules/kst_local/log_source.py modules/kst_local/runtime.py modules/kst_local/identity_registry.py tests/test_kst_local_log_source.py
git commit -m "perf: incrementally parse KST logs"
```

### Task 2: Throttle Healthy Owned-Registry Refreshes

**Files:**
- Modify: `gui/kst_api_manager.py`
- Modify: `tests/test_kst_api_manager.py`

**Interfaces:**
- Extends: `KstApiManager(root, *, probe=probe_kst_health, server_factory=create_server, registry_factory=KstIdentityRegistry, retry_interval_ms=15_000, registry_refresh_interval_ms=300_000, monotonic=time.monotonic, parent=None)`
- Preserves: the existing `retry_interval_ms=15_000` lightweight timer and immediate retries while not ready.

- [ ] **Step 1: Replace the periodic full-refresh expectation with throttle tests**

```python
def test_healthy_owned_server_skips_full_refresh_before_interval(qapp, tmp_path):
    now = [100.0]
    server = FakeServer()
    registries = []

    def registry_factory(*_args):
        registry = FakeRegistry()
        registries.append(registry)
        return registry

    manager = KstApiManager(
        tmp_path,
        registry_factory=registry_factory,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=20,
        registry_refresh_interval_ms=300_000,
        monotonic=lambda: now[0],
    )
    manager.start()
    assert wait_until(manager.is_ready)
    time.sleep(0.08)
    assert registries[0].refresh_calls == 1
    now[0] += 301
    assert wait_until(lambda: registries[0].refresh_calls == 2)
    manager.stop()
```

Keep the existing not-ready retry test to prove unavailable identities still retry every timer tick.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_kst_api_manager.py -q`

Expected: constructor rejects the new arguments or refresh count exceeds one before the interval.

- [ ] **Step 3: Implement the refresh deadline**

Record the monotonic time after each successful full refresh. In `_refresh_owned_registry`, use the existing registry health without calling `refresh()` until 300 seconds elapse. If the registry is missing or unhealthy, continue the existing full-refresh path every 15 seconds. Clear the deadline when ownership ends.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_kst_api_manager.py -q`

Expected: all manager lifecycle, retry, external-server, ownership, and throttle tests pass.

- [ ] **Step 5: Commit the task**

```powershell
git add gui/kst_api_manager.py tests/test_kst_api_manager.py
git commit -m "perf: throttle healthy KST registry refreshes"
```

### Task 3: Reuse Live Runtimes by Semantic Input State

**Files:**
- Modify: `modules/kst_local/identity_registry.py`
- Modify: `modules/kst_local/runtime.py`
- Modify: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Extends the existing `KstIdentityRegistry` keyword arguments with `runtime_cache_ttl_seconds=60` and `runtime_state_reader=_runtime_input_state`; retain `monotonic=time.monotonic`.
- Extends: `build_live_runtime(config, target_date, *, installation_root=None, installation=None, snapshot: AutomaticSourceSnapshot | None = None)`
- Runtime-state input includes bound installation identity, automatic-source snapshot contents, auth/tag contents, and `(path, size, mtime_ns)` for every database.

- [ ] **Step 1: Write failing reuse and invalidation tests**

```python
def test_registry_reuses_runtime_when_semantic_inputs_are_unchanged(tmp_path):
    now = [100.0]
    calls = []
    state = ["v1"]
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: calls.append(1) or HealthyRuntime(),
        runtime_state_reader=lambda *_args: state[0],
        runtime_cache_ttl_seconds=60,
        monotonic=lambda: now[0],
    )
    registry.refresh()
    assert registry.build_runtime("a", "2026-07-27") is registry.build_runtime("a", "2026-07-27")
    assert len(calls) == 1


def test_registry_rebuilds_runtime_on_state_change_or_ttl_expiry(tmp_path):
    now = [100.0]
    calls = []
    state = ["v1"]
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
        runtime_state_reader=lambda *_args: state[0],
        runtime_cache_ttl_seconds=60,
        monotonic=lambda: now[0],
    )
    registry.refresh()
    first = registry.build_runtime("a", "2026-07-27")
    state[0] = "v2"
    second = registry.build_runtime("a", "2026-07-27")
    now[0] += 61
    third = registry.build_runtime("a", "2026-07-27")
    assert first is not second
    assert second is not third
    assert len(calls) == 3
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_kst_identity_registry.py -q`

Expected: `registry_for` or `KstIdentityRegistry` rejects runtime-cache arguments and duplicate runtime builds occur.

- [ ] **Step 3: Implement semantic runtime caching**

Compute the state before building. Under a re-entrant lock, reuse the cached runtime only when its installation key, state value, and non-negative age below 60 seconds all match. Build first and only then replace the cache entry so failures never overwrite a valid runtime. Pass the already-read cached snapshot into `build_live_runtime` to avoid duplicate work.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_kst_identity_registry.py tests/test_kst_local_pipeline.py tests/test_kst_multi_identity_http.py -q`

Expected: runtime reuse/invalidation and all routing/API integration tests pass.

- [ ] **Step 5: Commit the task**

```powershell
git add modules/kst_local/identity_registry.py modules/kst_local/runtime.py tests/test_kst_identity_registry.py
git commit -m "perf: reuse unchanged KST live runtimes"
```

### Task 4: Regression, Benchmark, and Desktop Build

**Files:**
- Verify: `dist/hourlyreport_automation.exe`
- Verify: `dist/hourlyreport_automation.build.json`

**Interfaces:**
- Consumes the unchanged loopback API at `http://127.0.0.1:18766`.
- Produces a rebuilt desktop executable containing the three performance changes.

- [ ] **Step 1: Run KST regression**

Run: `python -m pytest tests -q -k kst --tb=short`

Expected: all KST tests pass.

- [ ] **Step 2: Run full regression**

Run: `python -m pytest -q`

Expected before rebuild: the only permitted failure is the build-manifest source fingerprint check; every functional test passes.

- [ ] **Step 3: Benchmark source-level caches**

Measure first and second cached log parses for both active identities. Assert snapshots have identical automatic IDs and the second parse reads only bytes appended since the first call. Benchmark two consecutive Kunming hourly requests and retain the row counts for comparison.

- [ ] **Step 4: Rebuild the GUI**

Run: `.venv\Scripts\python.exe tools/build_desktop_exe.py`

Expected: `dist/hourlyreport_automation.exe` and its build manifest are regenerated successfully.

- [ ] **Step 5: Verify the build-manifest test**

Run: `python -m pytest tests/test_basic.py::test_online_update_build_contains_program_but_excludes_user_data -q`

Expected: pass.

- [ ] **Step 6: Launch the rebuilt GUI and verify the production endpoint**

Request `/health` and `/v1/kst/hourly?project_id=kunming_niu&date=2026-07-27`. Verify health is `ok`, source is `kst_local_api`, and `raw_rows`, `matched_rows`, and `automatic_rows` agree with the current automatic-push data.

- [ ] **Step 7: Commit any remaining source/test changes**

Stage only files named by this plan. Do not stage local configuration, credentials, reports, diagnostics, logs, or user data.
