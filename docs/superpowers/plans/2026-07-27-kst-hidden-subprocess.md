# KST 子进程无窗口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 KST 后台健康刷新和报表读取产生的 Windows 黑色控制台闪窗。

**Architecture:** 所有 KST `subprocess.run` 调用共享 Windows 无窗口参数。API 管理器复用身份注册表，注册表使用5分钟推广 ID 缓存减少 Electron 桥重复启动。

**Tech Stack:** Python 3.14、Windows subprocess、pytest、PyInstaller、PySide6。

## Global Constraints

- 保留15秒 API 健康刷新。
- 不改变商务通读取命令、日期筛选、身份映射和报表指标。
- Windows 必须使用 `CREATE_NO_WINDOW`；非 Windows 不传 Windows 专用标志。
- 推广 ID 缓存有效期固定为300秒，新身份或数据库路径必须形成新缓存键。

---

### Task 1: KST 子进程统一无窗口启动

**Files:**
- Create: `modules/kst_local/subprocess_utils.py`
- Modify: `modules/kst_local/discovery.py`
- Modify: `modules/kst_local/db_reader.py`
- Test: `tests/test_kst_local_discovery.py`
- Test: `tests/test_kst_local_db_reader.py`

**Interfaces:**
- Produces: `hidden_subprocess_kwargs() -> dict[str, int]`。
- Consumes: `subprocess.run(command, **hidden_subprocess_kwargs(), ...)`。

- [ ] **Step 1: Write failing tests**

使用记录关键字参数的真实调用边界替身，断言 Windows 下进程检查、推广 ID 读取和会话读取都包含非零 `creationflags`，且包含 `subprocess.CREATE_NO_WINDOW`。

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_kst_local_discovery.py tests/test_kst_local_db_reader.py -q`

Expected: FAIL because current runners receive no `creationflags`.

- [ ] **Step 3: Implement minimal shared helper**

```python
def hidden_subprocess_kwargs() -> dict[str, int]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
```

将返回值展开到三个 KST 子进程调用。

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_kst_local_discovery.py tests/test_kst_local_db_reader.py -q`

Expected: PASS.

### Task 2: 推广 ID 缓存与注册表复用

**Files:**
- Modify: `modules/kst_local/identity_registry.py`
- Modify: `gui/kst_api_manager.py`
- Test: `tests/test_kst_identity_registry.py`
- Test: `tests/test_kst_api_manager.py`

**Interfaces:**
- `KstIdentityRegistry(..., promotion_cache_ttl_seconds=300, monotonic=time.monotonic)`。
- `KstApiManager._refresh_owned_registry()` 优先复用 `self._registry`。

- [ ] **Step 1: Write failing tests**

连续两次刷新同一身份，断言推广 ID reader 只调用一次；推进单调时钟301秒后断言再次调用。API 管理器已有注册表时，断言刷新不创建新注册表。

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_kst_identity_registry.py tests/test_kst_api_manager.py -q`

Expected: FAIL because every refresh reread promotion IDs and manager recreates registry.

- [ ] **Step 3: Implement cache and reuse**

缓存键使用安装根目录、身份和数据库路径元组，值为 `(read_at, frozenset(promotion_ids))`。刷新时命中未过期缓存则复用；API 管理器从锁内取得现有注册表并调用 `refresh()`。

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_kst_identity_registry.py tests/test_kst_api_manager.py -q`

Expected: PASS.

### Task 3: 回归与成品验收

**Files:**
- Verify: `dist/hourlyreport_automation.exe`

- [ ] **Step 1: Run KST regression**

Run: `python -m pytest tests -q -k kst --tb=short`

Expected: PASS.

- [ ] **Step 2: Run full regression**

Run: `$env:QT_QPA_PLATFORM='offscreen'; $env:KST_INSTALLATION_ROOT='C:\__codex_test_missing_kst__'; python -m pytest -q --tb=short`

Expected: PASS.

- [ ] **Step 3: Rebuild and verify**

Run: `.venv\Scripts\python.exe tools\build_desktop_exe.py`

Expected: EXE and build manifest are regenerated; `/health` reports `status=ok` and port is released after exit.
