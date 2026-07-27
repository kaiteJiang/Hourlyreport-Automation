# 快商通日报本地 API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让单项目和多项目日报与小时报一样通过严格项目路由的快商通本地 API 自动获取数据。

**Architecture:** 在现有 `KstConversationService` 内复用自动推送会话集合并调用既有日报聚合器；HTTP 服务公开独立日报端点；客户端将响应落到既有日报文件契约；日报流水线仅按全局模式选择 API 或人工导出。

**Tech Stack:** Python 3.14、标准库 `http.server`/`urllib`、pytest、PyInstaller、PySide6。

## Global Constraints

- 本地 API 固定绑定 `127.0.0.1:18766`。
- 每个请求必须携带 `project_id`，响应项目必须完全一致。
- `local_api` 失败时只允许生成当前项目全零数据，不得隐式读取人工导出。
- `export` 模式保持现有人工日报导出兼容。
- 复用推广 ID 唯一映射，不新增手工身份关系表。

---

### Task 1: 服务端日报聚合

**Files:**
- Modify: `modules/kst_local/service.py`
- Test: `tests/test_kst_local_service.py`

**Interfaces:**
- Consumes: `KstConversationService.collect(target_date)` 与 `modules.kst_daily_parser.aggregate_kst_daily_rows(rows, config)`。
- Produces: `KstConversationService.build_daily_report(target_date: str | None) -> dict[str, Any]`。

- [ ] **Step 1: Write the failing test**

在 `tests/test_kst_local_service.py` 构造与小时报相同的自动会话，断言 `build_daily_report("2026-07-26")` 返回正确项目、日期、`source` 和六项日报账户统计。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_service.py -q`

Expected: FAIL because `KstConversationService` has no `build_daily_report`.

- [ ] **Step 3: Write minimal implementation**

将会话转换为现有日报解析器接受的行，调用 `aggregate_kst_daily_rows`，返回：

```python
{
    "project_id": self._config.get("project_id"),
    "project_name": self._config.get("project_name"),
    "date": resolved_date,
    "source": "kst_local_api",
    "accounts": aggregate["accounts"],
    "summary": {**aggregate["summary"], "automatic_rows": len(conversations)},
    "errors": aggregate.get("errors", []),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_service.py -q`

Expected: PASS.

### Task 2: 日报 HTTP 接口

**Files:**
- Modify: `modules/kst_local/http_server.py`
- Test: `tests/test_kst_local_http_server.py`

**Interfaces:**
- Consumes: `service_factory(project_id, target_date)` 和 `service.build_daily_report(target_date)`。
- Produces: `GET /v1/kst/daily?project_id=<id>&date=<date>`。

- [ ] **Step 1: Write the failing tests**

新增成功请求测试，并新增 `/v1/kst/daily` 缺少 `project_id` 返回 400 的测试。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_http_server.py -q`

Expected: FAIL with 404 or missing daily call.

- [ ] **Step 3: Write minimal implementation**

将 `/v1/kst/daily` 加入必须校验 `project_id` 的路径集合，并在服务创建后调用 `build_daily_report(target_date)`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_http_server.py -q`

Expected: PASS.

### Task 3: 日报本地 API 客户端

**Files:**
- Modify: `modules/kst_local/source.py`
- Test: `tests/test_kst_local_source.py`

**Interfaces:**
- Produces: `fetch_kst_local_daily_report(config, root, *, target_date, transport=_default_transport) -> dict[str, Any]`。
- Writes: `kst_daily_data.json`、`kst_daily_parse_report.json`、`kst_daily_unmatched_rows.json`、`kst_daily_account_dialog_details.json`。

- [ ] **Step 1: Write the failing tests**

覆盖成功响应落盘、响应项目不一致时拒绝、`allow_zero_on_unavailable` 时写六项全零且 `passed=True`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_source.py -q`

Expected: FAIL because `fetch_kst_local_daily_report` does not exist.

- [ ] **Step 3: Write minimal implementation**

复用回环地址、令牌、超时和项目校验；使用 `validate_daily_kst_counts`/日报账户完整性规则校验响应；通过日报解析器的写文件契约输出成功或全零结果。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_source.py -q`

Expected: PASS.

### Task 4: 日报流水线模式切换

**Files:**
- Modify: `modules/run_pipeline.py`
- Test: `tests/test_kst_local_pipeline.py`
- Test: `tests/test_multi_project.py`

**Interfaces:**
- Consumes: `config["kst"]["data_source"]` 和 `fetch_kst_local_daily_report`。
- Preserves: `run_daily_pipeline(..., parse_kst_func=...)` 的人工导出测试注入能力。

- [ ] **Step 1: Write the failing tests**

新增 API 模式不调用 `find_latest_kst_export`/人工解析、API 全零可继续合并、人工模式仍调用导出解析，以及多项目日报给每个项目传递自身配置的测试。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_pipeline.py tests/test_multi_project.py -q`

Expected: FAIL because `run_daily_pipeline` always scans exports.

- [ ] **Step 3: Write minimal implementation**

为 `run_daily_pipeline` 增加 `fetch_kst_local_func` 注入参数；初始化时只在 `export` 模式查找文件；第二步按模式调用日报 API 或保留原解析分支，并把 `kst_data_source` 写入最终报告。

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_pipeline.py tests/test_multi_project.py -q`

Expected: PASS.

### Task 5: 回归、打包和实机验收

**Files:**
- Verify: `tools/hourlyreport_automation.spec`
- Verify: `dist/hourlyreport_automation.exe`

**Interfaces:**
- Verifies: 小时报、日报、全局模式、GUI 服务生命周期与打包内容。

- [ ] **Step 1: Run focused regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_kst_local_service.py tests/test_kst_local_http_server.py tests/test_kst_local_source.py tests/test_kst_local_pipeline.py tests/test_multi_project.py -q`

Expected: PASS.

- [ ] **Step 2: Run full regression**

Run: `$env:QT_QPA_PLATFORM='offscreen'; $env:KST_INSTALLATION_ROOT='C:\__codex_test_missing_kst__'; .venv\Scripts\python.exe -m pytest -q --tb=short`

Expected: all tests PASS.

- [ ] **Step 3: Build packaged application**

Run: `.venv\Scripts\python.exe tools\build_desktop_exe.py`

Expected: `dist/hourlyreport_automation.exe` rebuilt with exit code 0.

- [ ] **Step 4: Verify live API**

启动最终 EXE，确认 `/health` 为 `status=ok`；请求昆明牛 `/v1/kst/hourly` 与 `/v1/kst/daily`，核对项目 ID、日期、自动来源和账户汇总；退出后确认 18766 端口释放。
