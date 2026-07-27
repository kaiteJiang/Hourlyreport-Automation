# KST Local API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** 为昆明牛项目实现来源可证明、只读、无需人工导出的商务通本地 API 数据源，并保持现有人工导出回退。

**Architecture:** 新增 `modules/kst_local` 包，将安装发现、日志来源解析、SQLCipher 只读桥接、服务器 API 查询、统计服务和回环 HTTP 服务分层。统计层把安全会话转换为现有 `aggregate_kst_export_rows` 标准行，避免复制小时报口径。`run_pipeline` 通过显式 `kst.data_source` 选择本地 API 或人工导出，绝不自动混合。

**Tech Stack:** Python 3.11 标准库、现有 requests/pandas 依赖、客户端自带 Electron 与 `better-sqlite3-multiple-ciphers`、pytest。

**Global Constraints:**

- 按 TDD 顺序：先写失败测试并确认失败，再写最小实现。
- 不修改商务通数据库或安装文件。
- 不持久化认证令牌，不在日志和异常中输出令牌。
- 不运行 `run`、`run-daily` 或正式 Excel 写入。
- 首期只以昆明牛和客户端 9.86.21 为验收基线。
- 当前主工作区有用户未提交改动；所有代码只在 `codex/kst-local-api` 工作树中修改。

---

### Task 1: 自动发现与日志来源白名单

**Files:**

- Create: `modules/kst_local/__init__.py`
- Create: `modules/kst_local/models.py`
- Create: `modules/kst_local/discovery.py`
- Create: `modules/kst_local/log_source.py`
- Test: `tests/test_kst_local_discovery.py`
- Test: `tests/test_kst_local_log_source.py`

**Step 1: Write the failing tests**

- 构造临时安装树、日志树和数据库身份目录。
- 断言显式 `installation_root` 优先并能定位 Electron、包版本、日志目录和 `VISITOR.db`。
- 断言无效根目录返回结构化诊断，不静默猜测。
- 构造含 `msgType=48`、启动同步响应、人工历史查询和 API 公共参数的日志。
- 断言只提取自动来源 `recId`，人工历史查询独有 `recId` 被排除。
- 断言认证上下文仅驻留模型对象，序列化的安全诊断不包含令牌。

**Step 2: Run tests to verify they fail**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_discovery.py tests/test_kst_local_log_source.py
```

Expected: imports fail because `modules.kst_local` does not exist.

**Step 3: Write minimal implementation**

- `KstInstallation` 保存根目录、Electron、版本、身份、日志目录和数据库列表。
- `discover_installation(explicit_root=None, local_app_data=None)` 按显式根目录、进程可执行路径、常见目录探测。
- `parse_log_snapshot(log_dir, target_date)` 返回 `AutomaticSourceSnapshot` 和内存认证上下文。
- 日志解析支持转义与未转义 JSON 片段。
- `safe_diagnostics()` 仅返回字段存在性，不返回字段值。

**Step 4: Run tests to verify they pass**

Run the Task 1 command again. Expected: all pass.

**Step 5: Commit**

```powershell
git add modules/kst_local tests/test_kst_local_discovery.py tests/test_kst_local_log_source.py
git commit -m "feat: discover KST installation and automatic sources"
```

### Task 2: SQLCipher 只读桥与安全候选行

**Files:**

- Create: `modules/kst_local/db_reader.py`
- Create: `modules/kst_local/resources/read_visitor_db.js`
- Test: `tests/test_kst_local_db_reader.py`

**Step 1: Write the failing tests**

- 用假 subprocess runner 验证命令只包含客户端 Electron、桥脚本、身份、数据库和日期。
- 断言设置 `ELECTRON_RUN_AS_NODE=1`。
- 断言重复 `recId` 选择推广 ID/标签字段更完整的候选行。
- 断言只接受 `visitorType=WEB` 且 `channelType=1`。
- 断言桥返回缺列或非 JSON 时结构化失败。
- 静态断言 JS 使用 `{ readonly: true, fileMustExist: true }`，没有 INSERT/UPDATE/DELETE。

**Step 2: Run tests to verify they fail**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_db_reader.py
```

Expected: import fails because `db_reader.py` does not exist.

**Step 3: Write minimal implementation**

- JS 从自身相对客户端根目录加载 `better-sqlite3-multiple-ciphers`，不硬编码 D 盘。
- 用 `MD5(SHA256(identity))` 派生现有客户端密钥，只对只读连接执行查询。
- 首先检查访客表和必需列，再查询指定日期安全字段。
- Python 合并多个数据库中的重复候选，返回 `KstCacheCandidate`。

**Step 4: Run tests to verify they pass**

Run the Task 2 command again. Expected: all pass.

**Step 5: Commit**

```powershell
git add modules/kst_local/db_reader.py modules/kst_local/resources/read_visitor_db.js tests/test_kst_local_db_reader.py
git commit -m "feat: read KST cache through readonly SQLCipher bridge"
```

### Task 3: 只读服务器客户端与会话组合

**Files:**

- Create: `modules/kst_local/api_client.py`
- Create: `modules/kst_local/service.py`
- Test: `tests/test_kst_local_api_client.py`
- Test: `tests/test_kst_local_service.py`

**Step 1: Write the failing tests**

- 假 HTTP transport 验证 URL、公共参数、POST 方法和超时。
- 断言异常文本不包含认证令牌。
- 用固定 API 响应验证时间、访客消息数、标签 ID 与推广 ID 组合。
- 构造数据库独有人工查询行，断言没有自动来源时被排除。
- 构造同一 `recId` 双来源，断言只统计一次。
- 用昆明牛推广 ID 映射验证生成账户统计与现有聚合器一致。
- 断言任一白名单会话查询失败时整体失败，不降级成零数据。

**Step 2: Run tests to verify they fail**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_api_client.py tests/test_kst_local_service.py
```

Expected: imports fail because client and service do not exist.

**Step 3: Write minimal implementation**

- `KstApiClient` 调用日志中发现的 `visitorInfo/load`、`visitorCard/detail` 和标签字典接口。
- 认证信息只保存在实例内存，异常只报告 endpoint path、HTTP 状态和 `recId`。
- `KstConversationService.collect(date)` 先求自动来源白名单与缓存候选交集，再逐条查询。
- 把结果转换为现有列名：`开始对话时间`、`备注说明`、`访客消息数`、`名片标签`、`搜索关键词`。
- 调用 `aggregate_kst_export_rows` 得到小时统计。

**Step 4: Run tests to verify they pass**

Run the Task 3 command again. Expected: all pass.

**Step 5: Commit**

```powershell
git add modules/kst_local/api_client.py modules/kst_local/service.py tests/test_kst_local_api_client.py tests/test_kst_local_service.py
git commit -m "feat: query KST conversations from automatic sources"
```

### Task 4: 回环本地 API 与 CLI

**Files:**

- Create: `modules/kst_local/http_server.py`
- Modify: `main.py`
- Test: `tests/test_kst_local_http_server.py`
- Test: `tests/test_kst_local_cli.py`

**Step 1: Write the failing tests**

- 通过本地临时端口启动服务，验证 `/health`、`/v1/kst/conversations` 和 `/v1/kst/hourly`。
- 断言服务拒绝非 `127.0.0.1` 绑定参数。
- 断言配置令牌时未授权请求返回 401，响应不泄露令牌。
- 断言 conversations 响应不含姓名、手机、微信、消息正文和认证字段。
- 断言 CLI 新模式 `serve-kst-local` 与 `fetch-kst-local` 可解析，但测试用依赖注入避免真实网络。

**Step 2: Run tests to verify they fail**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_http_server.py tests/test_kst_local_cli.py
```

Expected: imports or CLI choice assertions fail.

**Step 3: Write minimal implementation**

- 用 `ThreadingHTTPServer` 实现回环服务，不增加运行时依赖。
- `main.py` 新增 `serve-kst-local`、`fetch-kst-local`、`--kst-root`、`--host`、`--port`。
- host 固定校验为 `127.0.0.1`。
- `fetch-kst-local` 写现有报告 JSON，但不合并百度或写 Excel。

**Step 4: Run tests to verify they pass**

Run the Task 4 command again. Expected: all pass.

**Step 5: Commit**

```powershell
git add modules/kst_local/http_server.py main.py tests/test_kst_local_http_server.py tests/test_kst_local_cli.py
git commit -m "feat: expose KST data through loopback API"
```

### Task 5: 小时报数据源适配与昆明牛配置

**Files:**

- Create: `modules/kst_local/source.py`
- Modify: `modules/run_pipeline.py`
- Modify: `modules/project_config.py`
- Modify: `configs/projects/kunming_niu.json`
- Modify: `configs/projects/project_template.json`
- Test: `tests/test_kst_local_source.py`
- Test: `tests/test_kst_local_pipeline.py`

**Step 1: Write the failing tests**

- 断言 `data_source=export` 保持现有 `parse_kst_export_file` 行为。
- 断言 `data_source=local_api` 只调用本地 API，不查找导出文件。
- 断言本地 API 失败时流水线失败，不静默读取旧导出。
- 断言昆明牛运行配置保留自定义安装根目录和 API 配置。
- 断言本地 API 返回值写成现有 `kst_dialog_data.json` 形状，合并器无需修改。

**Step 2: Run tests to verify they fail**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_source.py tests/test_kst_local_pipeline.py
```

Expected: source adapter imports or behavior assertions fail.

**Step 3: Write minimal implementation**

- `fetch_kst_local_report` 调用回环 API并原子写报告文件。
- `run_half_auto_pipeline` 根据 `kst.data_source` 显式选择来源。
- 项目配置规范化保留新增字段。
- 昆明牛设为 `local_api`；项目模板仍默认 `export`。

**Step 4: Run tests to verify they pass**

Run the Task 5 command again. Expected: all pass.

**Step 5: Commit**

```powershell
git add modules/kst_local/source.py modules/run_pipeline.py modules/project_config.py configs/projects/kunming_niu.json configs/projects/project_template.json tests/test_kst_local_source.py tests/test_kst_local_pipeline.py
git commit -m "feat: integrate KST local API with Kunming hourly reports"
```

### Task 6: 本机只读验收与文档

**Files:**

- Create: `docs/kst-local-api.md`
- Modify: `README_同事使用说明.md`
- Create: `tests/fixtures/kst_local/README.md`

**Step 1: Run focused automated tests**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_kst_local_*.py
```

Expected: all pass.

**Step 2: Run legacy tests in bounded groups**

Run:

```powershell
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_multi_project.py
& 'D:\自动化脚本\hourly_report_bot_release_v0.4.4\.venv\Scripts\python.exe' -m pytest -q tests/test_basic.py --maxfail=1
```

Expected: pass or document pre-existing timeout/failure separately.

**Step 3: Run live read-only checks**

- 对当前商务通安装执行 discovery 与 `/health`。
- 对 2026-07-26 执行 `fetch-kst-local` 到临时输出目录。
- 与既有人工导出验证摘要比较 26 条、账户分布和标签计数。
- 不运行百度合并和 Excel 写入。

**Step 4: Secret and mutation audit**

Run:

```powershell
rg -n "clientToken|Authorization|GlobalCommonHeaders" modules tests docs
git diff --check
git status --short
```

Expected: 无硬编码令牌；只有预期文件变化；商务通数据库时间戳未变化。

**Step 5: Document usage and commit**

- 文档说明启动、健康检查、昆明牛启用、人工导出回退、版本诊断和安全边界。

```powershell
git add docs README_同事使用说明.md tests/fixtures/kst_local/README.md
git commit -m "docs: document KST local API validation and fallback"
```

## Self-review

- 规格覆盖：自动来源白名单、只读缓存、服务器查询、本地 API、小时报适配、路径发现和版本能力检查均有对应任务。
- 无实现占位符：每个任务均给出文件、接口、失败测试、最小实现、验证命令与提交点。
- 类型一致：核心模型均为 Python 数据类；HTTP 输出为 JSON；统计输出复用现有字典结构。
- 安全一致：令牌只驻留内存；API 回环绑定；不返回正文/个人信息；失败不伪装为零数据。
- 执行方式：本会话内联执行；当前开发者约束不允许主动创建子代理。
