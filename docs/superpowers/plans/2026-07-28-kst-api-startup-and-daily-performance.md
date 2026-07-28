# KST API Startup and Daily Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持商务通自动来源隔离、当天认证门槛和整批失败语义不变的前提下，将本地 API 启动等待降到 5 秒级，并将 13 条会话的日报读取降到约 2～5 秒。

**Architecture:** 日志快照把接口 URL 与认证材料分开处理：接口 URL 可使用同一身份日志中的最近历史值，当天公共参数和请求头仍不可跨日复用。会话详情保持单条内部顺序，但不同会话通过最多 4 个线程并发读取；身份映射、single-flight 缓存和上层按 0 继续策略不变。

**Tech Stack:** Python 3.11、PySide6、`concurrent.futures.ThreadPoolExecutor`、`urllib.request`、pytest。

## Global Constraints

- 不运行真实 `run` / `run-daily`，不写目标 Excel。
- 本地服务继续只绑定 `127.0.0.1:18766`。
- 历史日志只允许复用接口 URL；公共查询参数、请求头和 Token 必须来自当天。
- 不持久化认证材料，不输出认证材料或访客隐私。
- 每条会话仍按 `visitor_info -> visitor_card` 顺序读取。
- 不同会话最大并发数固定为 4。
- 任一会话失败时整批失败，不返回、不缓存部分结果。
- 自动推送白名单、推广 ID 项目映射、小时报/日报统计口径和 Excel 规则不变。

---

### Task 1: 安全恢复历史接口 URL，同时收紧当天认证门槛

**Files:**
- Modify: `modules/kst_local/log_source.py`
- Modify: `modules/kst_local/identity_registry.py`
- Modify: `modules/kst_local/runtime.py`
- Test: `tests/test_kst_local_log_source.py`
- Test: `tests/test_kst_identity_registry.py`
- Create: `tests/test_kst_local_runtime.py`

**Interfaces:**
- Consumes: `parse_cached_log_snapshot(log_dir, target_date, auth_date: str | None) -> AutomaticSourceSnapshot`
- Produces: `_SnapshotAccumulator.consume()` 从所有日期保留最近接口 URL，但只从 `auth_date` 保存 `common_query` 和 `headers`。
- Produces: `_required_endpoints_available(installation, target_date) -> bool` 只有三个接口、当天公共参数和当天请求头全部存在时返回 `True`。
- Produces: `KstLiveRuntime.health() -> dict[str, Any]` 使用与注册表相同的就绪规则。

- [ ] **Step 1: 写失败测试，证明历史 URL 可恢复但历史认证不可复用**

在 `tests/test_kst_local_log_source.py` 将现有
`test_auth_date_excludes_historical_identity_endpoints` 替换为：

```python
def test_snapshot_reuses_historical_endpoint_urls_but_not_historical_auth(
    tmp_path: Path,
):
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(
            [
                '[2026-07-27 09:00:00] [Api] GlobalCommonParams {"query":{"old":"1"}}',
                '[2026-07-27 09:00:00] [Api] GlobalCommonHeaders {"clientToken":"old"}',
                "[2026-07-27 09:00:00] post https://old.example/OnlineHd/visitorInfo/load",
                "[2026-07-27 09:00:01] post https://old.example/OnlineCore/nv2012/func/ocVisitorCard/detail.do",
                "[2026-07-27 09:00:02] post https://old.example/OnlineCore/nv/visitorCard/custTypeQuery.do",
                '[2026-07-28 09:00:00] [Api] GlobalCommonParams {"query":{"today":"1"}}',
                '[2026-07-28 09:00:00] [Api] GlobalCommonHeaders {"clientToken":"today"}',
            ]
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(
        tmp_path,
        "2026-07-28",
        auth_date="2026-07-28",
    )

    assert snapshot.auth.common_query == {"today": "1"}
    assert snapshot.auth.headers == {"clientToken": "today"}
    assert set(snapshot.auth.endpoints) >= {
        "visitor_info",
        "visitor_card",
        "tag_dictionary",
    }
```

再增加当天 URL 覆盖历史 URL 的测试：

```python
def test_current_endpoint_url_overrides_historical_url(tmp_path: Path):
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(
            [
                "[2026-07-28 09:00:00] post https://new.example/OnlineHd/visitorInfo/load",
                "[2026-07-27 09:00:00] post https://old.example/OnlineHd/visitorInfo/load",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(
        tmp_path,
        "2026-07-28",
        auth_date="2026-07-28",
    )

    assert snapshot.auth.endpoints["visitor_info"].startswith(
        "https://new.example/"
    )
```

- [ ] **Step 2: 运行日志测试并确认失败**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_local_log_source.py::test_snapshot_reuses_historical_endpoint_urls_but_not_historical_auth `
  tests\test_kst_local_log_source.py::test_current_endpoint_url_overrides_historical_url -q
```

Expected: 第一项因历史接口 URL 未进入快照而失败。

- [ ] **Step 3: 最小修改日志累加器**

在 `modules/kst_local/log_source.py` 的 `_SnapshotAccumulator.consume()` 中保持公共参数和请求头的 `auth_line` 条件不变，把接口 URL 提取移出该条件：

```python
        for name, suffix in ENDPOINT_SUFFIXES.items():
            endpoint = _find_endpoint(line, suffix)
            if endpoint:
                self.endpoints[name] = endpoint
```

在 `_SnapshotAccumulator.__init__()` 增加：

```python
        self.endpoint_seen_at: dict[str, str] = {}
```

在 `consume()` 中从日志前缀提取可按字典序比较的
`YYYY-MM-DD HH:MM:SS`，并把接口 URL 提取移出 `auth_line` 条件：

```python
        line_timestamp = (
            line[1:20]
            if len(line) >= 21
            and line[0] == "["
            and line[20] == "]"
            else ""
        )
        for name, suffix in ENDPOINT_SUFFIXES.items():
            endpoint = _find_endpoint(line, suffix)
            previous_timestamp = self.endpoint_seen_at.get(name, "")
            if endpoint and (
                not previous_timestamp
                or not line_timestamp
                or line_timestamp >= previous_timestamp
            ):
                self.endpoints[name] = endpoint
                self.endpoint_seen_at[name] = line_timestamp
```

这样即使多个日志文件的消费顺序交错，当天较新的 URL 也不会被历史
URL 覆盖。

- [ ] **Step 4: 写失败测试，证明缺少当天认证时注册表不能就绪**

在 `tests/test_kst_identity_registry.py` 导入
`AutomaticSourceSnapshot`、`KstAuthContext` 和模块对象
`modules.kst_local.identity_registry as registry_module`，增加：

```python
@pytest.mark.parametrize(
    ("common_query", "headers", "expected"),
    [
        ({}, {"X-Client": "desktop"}, False),
        ({"compId": "1"}, {}, False),
        ({"compId": "1"}, {"X-Client": "desktop"}, True),
    ],
)
def test_required_endpoints_also_require_current_auth(
    monkeypatch,
    tmp_path,
    common_query,
    headers,
    expected,
):
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(
            common_query=common_query,
            headers=headers,
            endpoints={
                "visitor_info": "https://example/visitor",
                "visitor_card": "https://example/card",
                "tag_dictionary": "https://example/tags",
            },
        ),
    )
    monkeypatch.setattr(
        registry_module,
        "parse_cached_log_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    item = installation(tmp_path, "id-a")

    assert registry_module._required_endpoints_available(
        item,
        "2026-07-28",
    ) is expected
```

在新文件 `tests/test_kst_local_runtime.py` 增加：

```python
from pathlib import Path

import pytest

from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstInstallation,
)
from modules.kst_local.runtime import KstLiveRuntime


@pytest.mark.parametrize(
    ("common_query", "headers", "expected"),
    [
        ({}, {"X-Client": "desktop"}, False),
        ({"compId": "1"}, {}, False),
        ({"compId": "1"}, {"X-Client": "desktop"}, True),
    ],
)
def test_runtime_health_requires_current_auth(
    tmp_path: Path,
    common_query,
    headers,
    expected,
):
    root = tmp_path / "app"
    installation = KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity="id-a",
        log_dir=tmp_path / "log" / "id-a",
        database_paths=(tmp_path / "db" / "id-a" / "VISITOR.db",),
        sqlite_module_dir=root / "sqlite",
    )
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(
            common_query=common_query,
            headers=headers,
            endpoints={
                "visitor_info": "https://example/visitor",
                "visitor_card": "https://example/card",
                "tag_dictionary": "https://example/tags",
            },
        ),
    )
    runtime = KstLiveRuntime(
        installation=installation,
        snapshot=snapshot,
        service=object(),
    )

    health = runtime.health()

    assert health["required_endpoints_available"] is expected
    assert health["status"] == ("ok" if expected else "not_ready")
```

该测试确保 HTTP 健康信息与身份注册表使用同一认证门槛。

- [ ] **Step 5: 运行注册表测试并确认失败**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_identity_registry.py -q
```

Expected: 缺少当天公共参数或请求头时，现有实现仍只检查接口集合，测试失败。

- [ ] **Step 6: 收紧注册表和运行时就绪条件**

在 `modules/kst_local/identity_registry.py`：

```python
    required = {"visitor_info", "visitor_card", "tag_dictionary"}
    return (
        required.issubset(snapshot.auth.endpoints)
        and bool(snapshot.auth.common_query)
        and bool(snapshot.auth.headers)
    )
```

在 `modules/kst_local/runtime.py` 的 `health()` 中使用相同判断，并让 `status`、`required_endpoints_available` 都来自该布尔值。

- [ ] **Step 7: 运行 Task 1 测试**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_local_log_source.py `
  tests\test_kst_identity_registry.py `
  tests\test_kst_local_runtime.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交 Task 1**

```powershell
git add modules/kst_local/log_source.py modules/kst_local/identity_registry.py modules/kst_local/runtime.py tests/test_kst_local_log_source.py tests/test_kst_identity_registry.py tests/test_kst_local_runtime.py
git commit -m "perf: restore KST endpoints without reusing auth"
```

---

### Task 2: 缩短未就绪状态的后台重试周期

**Files:**
- Modify: `gui/kst_api_manager.py`
- Test: `tests/test_kst_api_manager.py`

**Interfaces:**
- Consumes: `KstApiManager(root, retry_interval_ms: int = 5_000)`
- Produces: 未显式传参时每 5 秒后台探测一次；测试传入的自定义间隔保持原行为。

- [ ] **Step 1: 写失败测试**

在 `tests/test_kst_api_manager.py` 增加：

```python
def test_manager_default_retry_interval_is_five_seconds(qapp, tmp_path):
    manager = KstApiManager(
        tmp_path,
        registry_factory=FakeRegistry,
        probe=lambda *_: False,
    )

    assert manager._retry_timer.interval() == 5_000
    manager.stop()
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_api_manager.py::test_manager_default_retry_interval_is_five_seconds -q
```

Expected: 实际值为 `15000`，测试失败。

- [ ] **Step 3: 修改默认值**

在 `gui/kst_api_manager.py`：

```python
        retry_interval_ms: int = 5_000,
```

不修改已经就绪时的 300 秒完整刷新间隔。

- [ ] **Step 4: 运行管理器测试**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_api_manager.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交 Task 2**

```powershell
git add gui/kst_api_manager.py tests/test_kst_api_manager.py
git commit -m "perf: retry KST readiness every five seconds"
```

---

### Task 3: 以最多 4 路并发读取不同会话详情

**Files:**
- Modify: `modules/kst_local/service.py`
- Test: `tests/test_kst_local_service.py`

**Interfaces:**
- Consumes: `KstConversationService(config, snapshot, candidates, client, max_workers: int = 4)`
- Produces: `_load_conversation(candidate, tag_map, allowed, promotion_map) -> KstConversation`
- Produces: `collect(target_date) -> list[KstConversation]` 保持 single-flight、稳定排序和整批失败语义。

- [ ] **Step 1: 写失败测试，证明不同会话并发且上限为 4**

在 `tests/test_kst_local_service.py` 增加一个包含 8 条白名单候选的测试客户端。客户端在 `load_visitor` 中用锁统计当前活动数，并用短暂 Event 屏障让工作线程重叠：

```python
def test_service_loads_different_conversations_with_bounded_parallelism():
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    class ParallelClient(FakeClient):
        def load_visitor(self, rec_id):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if peak >= 4:
                    release.set()
            release.wait(timeout=1)
            with lock:
                active -= 1
            return {
                "visitorId": f"visitor-{rec_id}",
                "curEnterTime": "2026-07-27 09:00:00",
                "visitorSendNum": 2,
            }

    rec_ids = [str(100 + index) for index in range(8)]
    snapshot_with_eight_allowed_ids = AutomaticSourceSnapshot(
        sources_by_rec_id={
            rec_id: frozenset({"websocket_msg_type_48"})
            for rec_id in rec_ids
        },
        auth=KstAuthContext(),
    )
    eight_candidates = [
        KstCacheCandidate(
            rec_id=rec_id,
            start_time="2026-07-27 09:00:00",
            promotion_id="72828178",
            visitor_messages=2,
        )
        for rec_id in rec_ids
    ]
    service = KstConversationService(
        config=_config(),
        snapshot=snapshot_with_eight_allowed_ids,
        candidates=eight_candidates,
        client=ParallelClient(),
    )

    conversations = service.collect("2026-07-27")

    assert len(conversations) == 8
    assert peak == 4
```

候选推广 ID 均使用 `_config()` 已允许的 `72828178`，避免测试混入项目映射失败。

- [ ] **Step 2: 写失败测试，证明单条内部调用顺序不变**

客户端记录每个 `rec_id` 的 `visitor` 和相应 `visitor_id` 的 `card` 调用，断言每个会话的 `visitor` 都先于自己的 `card`。不要断言不同会话之间的全局顺序：

```python
def test_service_preserves_visitor_then_card_order_per_conversation():
    calls = []
    lock = threading.Lock()

    class OrderedClient(FakeClient):
        def load_visitor(self, rec_id):
            with lock:
                calls.append(("visitor", rec_id))
            return {
                "visitorId": f"visitor-{rec_id}",
                "curEnterTime": "2026-07-27 09:00:00",
                "visitorSendNum": 2,
            }

        def load_card(self, visitor_id):
            rec_id = visitor_id.removeprefix("visitor-")
            with lock:
                calls.append(("card", rec_id))
            return {"cusTypeTag": '{"11":1}'}

    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=OrderedClient(),
    )

    service.collect("2026-07-27")

    assert calls.index(("visitor", "101")) < calls.index(("card", "101"))
```

- [ ] **Step 3: 写失败测试，证明并发错误不会缓存部分结果**

使用一个会让其中一个 `rec_id` 的 `load_visitor` 失败、其余成功的客户端：

```python
def test_parallel_failure_does_not_cache_partial_results():
    client = FakeClient(failing_rec_id="101")
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=client,
    )

    with pytest.raises(KstServiceError):
        service.collect("2026-07-27")
    first_call_count = len(client.visitor_calls)

    assert "2026-07-27" not in service._cache

    with pytest.raises(KstServiceError):
        service.collect("2026-07-27")

    assert len(client.visitor_calls) > first_call_count
```

- [ ] **Step 4: 运行新增服务测试并确认失败**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_local_service.py -q
```

Expected: 并发峰值仍为 1，新增并发测试失败。

- [ ] **Step 5: 提取单会话构建函数**

在 `modules/kst_local/service.py` 增加实例方法：

```python
    def _load_conversation(
        self,
        candidate: KstCacheCandidate,
        tag_map: dict[str, str],
        allowed: dict[str, frozenset[str]],
        promotion_map: dict[str, str],
    ) -> KstConversation:
        try:
            visitor = self._client.load_visitor(candidate.rec_id)
            visitor_id = str(visitor.get("visitorId") or "")
            if not visitor_id:
                raise ValueError("visitorId missing")
            card = self._client.load_card(visitor_id)
            start_time = str(
                visitor.get("curEnterTime")
                or visitor.get("dialogOpenTime")
                or ""
            )
            if not start_time:
                raise ValueError("curEnterTime missing")
            visitor_messages = int(
                visitor.get(
                    "visitorSendNum",
                    visitor.get("vsSendNum", 0),
                )
                or 0
            )
            promotion_id = (
                _promotion_id(visitor.get("visitorCustomField"))
                or candidate.promotion_id
            )
            if not promotion_id:
                raise ValueError("promotion id missing")
            if promotion_id not in promotion_map:
                raise ValueError("promotion id outside project mapping")
            tags = tuple(
                tag_map.get(tag_id, tag_id)
                for tag_id in _tag_ids(card.get("cusTypeTag"))
            )
            return KstConversation(
                rec_id=candidate.rec_id,
                start_time=start_time,
                promotion_id=promotion_id,
                visitor_messages=visitor_messages,
                tags=tags,
                sources=allowed[candidate.rec_id],
                keyword=candidate.keyword,
                bid_word=candidate.bid_word,
            )
        except Exception:
            raise KstServiceError(
                f"自动来源会话查询失败：recId={candidate.rec_id}"
            ) from None
```

同时在文件顶部增加：

```python
from concurrent.futures import ThreadPoolExecutor
```

- [ ] **Step 6: 增加固定并发上限并并行映射**

构造函数增加：

```python
        max_workers: int = 4,
```

保存：

```python
        self._max_workers = max(1, min(4, int(max_workers)))
```

在 `_collect_unlocked()` 中：

```python
        worker_count = min(self._max_workers, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            conversations = list(
                executor.map(
                    lambda candidate: self._load_conversation(
                        candidate,
                        tag_map,
                        allowed,
                        promotion_map,
                    ),
                    selected,
                )
            )
```

空 `selected` 直接返回并缓存空元组，避免创建线程池。完成后继续按 `(start_time, rec_id)` 排序。

- [ ] **Step 7: 运行全部服务与 HTTP 测试**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_local_service.py `
  tests\test_kst_local_http_server.py `
  tests\test_kst_local_api_client.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交 Task 3**

```powershell
git add modules/kst_local/service.py tests/test_kst_local_service.py
git commit -m "perf: parallelize KST conversation reads"
```

---

### Task 4: 文档同步、性能复测与完整回归

**Files:**
- Modify: `docs/kst-local-api.md`
- Review only: `AGENTS.md`
- Review only: `CLAUDE.md`
- Review only: `README.md`
- Review only: `README_同事使用说明.md`

**Interfaces:**
- Consumes: Task 1～3 的最终行为。
- Produces: 面向维护者的启动、认证和并发规则说明。

- [ ] **Step 1: 更新技术文档**

在 `docs/kst-local-api.md` 增加“性能与认证安全”小节，明确：

- 接口 URL 可从同一身份历史日志恢复；
- 公共查询参数、请求头和 Token 不跨日复用；
- 当天认证缺失时 KST 保持灰色；
- 不同会话最多 4 路并发，单条内部调用顺序不变；
- 任一会话失败仍整项目按 0。

`AGENTS.md` 已描述当天登录身份、只读 API、项目映射和失败按 0 的硬边界，无需改变规则。`CLAUDE.md`、两个 README 的用户入口和操作方式没有变化，因此只检查、不修改。

- [ ] **Step 2: 运行商务通目标测试**

Run:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_kst_local_log_source.py `
  tests\test_kst_identity_registry.py `
  tests\test_kst_api_manager.py `
  tests\test_kst_local_service.py `
  tests\test_kst_local_http_server.py `
  tests\test_kst_local_api_client.py `
  tests\test_kst_packaging.py -q
```

Expected: PASS。

- [ ] **Step 3: 运行基础测试**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_basic.py -q
```

Expected: PASS。

- [ ] **Step 4: 运行完整测试集**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: 全部通过。

- [ ] **Step 5: 运行只读性能复测**

使用新 `KstIdentityRegistry` 构建昆明牛 `2026-07-27` 的运行时，直接调用：

```python
runtime.service.build_daily_report("2026-07-27")
```

只输出注册表刷新、运行时构建、会话采集耗时和账户数量，不输出身份、接口、请求头或业务明细。验收：

- 注册表在当天认证材料存在时约 5 秒内完成；
- 13 条会话采集约 2～5 秒；
- 返回 3 个账户。

- [ ] **Step 6: 检查差异和敏感信息**

Run:

```powershell
git diff --check
rg -n "clientToken|Authorization: Bearer|KST_LOCAL_API_TOKEN=" modules gui tests docs
git status --short
```

确认没有新增真实令牌、身份目录、访客信息、日志正文或无关配置改动。

- [ ] **Step 7: 提交文档**

```powershell
git add docs/kst-local-api.md
git commit -m "docs: explain KST performance safeguards"
```

- [ ] **Step 8: 完成前验证**

按 `superpowers:verification-before-completion` 重新确认：

- 原始 78 秒启动等待的根因已由历史 URL + 当天认证组合覆盖；
- 13 条会话只读计时达到目标；
- 全量测试输出为绿色；
- 没有执行真实日报或写 Excel；
- 用户原有配置脏文件未进入提交。

---

### Task 5: 稳定 KST 管理器测试生命周期

**Files:**
- Modify: `tests/test_kst_api_manager.py`

**Interfaces:**
- Test only: 正常假服务器持续运行到 `shutdown()`；意外退出时每轮使用独立实例。
- Production: `gui/kst_api_manager.py` 不变。

- [ ] **Step 1: 修正正常假服务器语义**

让 `FakeServer.serve_forever()` 等待 `shutdown()`，不再固定 2 秒自行退出；
增加服务已启动事件，并让测试显式等待该事件。测试使用 `try/finally` 保证
`manager.stop()` 一定执行。

- [ ] **Step 2: 增加意外退出回归测试**

使用每轮独立的新假服务器实例模拟 `serve_forever()` 意外退出，验证每个实例
只关闭一次，并确认管理器能够发起下一轮启动。不得复用同一个假服务器制造不符合
真实 `ThreadingHTTPServer` 生命周期的断言。

- [ ] **Step 3: 运行定向与完整验证**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_kst_api_manager.py -q
.venv\Scripts\python.exe -m pytest -q
```

Expected: 全部通过。若完整测试仍失败，记录准确时序，不通过提高 timeout 掩盖。

- [ ] **Step 4: 提交测试稳定性修复**

```powershell
git add tests/test_kst_api_manager.py docs/superpowers/plans/2026-07-28-kst-api-startup-and-daily-performance.md
git commit -m "test: isolate KST manager server lifecycle"
```
