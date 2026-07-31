import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from gui.kst_api_manager import KstApiManager, current_project_health
from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.legacy_db_reader import KstLegacyDatabaseError


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def wait_for_attempts(attempts, count, timeout=2.0):
    assert wait_until(lambda: len(attempts) >= count, timeout=timeout)


def trigger_attempts(manager, attempts, count):
    for _ in range(count):
        target = len(attempts) + 1
        manager._retry_timer.stop()
        manager._ensure_service_async()
        wait_for_attempts(attempts, target)
        assert wait_until(
            lambda: manager._worker is None or not manager._worker.is_alive()
        )


def failing_manager(tmp_path, messages, **kwargs):
    attempts = []
    pending = list(messages)

    class FailingRegistry:
        def __init__(self, *_args):
            pass

        def refresh(self):
            attempts.append(1)
            index = min(len(attempts) - 1, len(pending) - 1)
            raise RuntimeError(pending[index])

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=FailingRegistry,
        **kwargs,
    )
    return manager, attempts


class FakeRuntime:
    service = object()


class FakeRegistry:
    def __init__(self, *_args):
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1

    def health(self):
        return {
            "status": "ok",
            "required_endpoints_available": True,
        }

    def build_runtime(self, project_id, target_date):
        return FakeRuntime()


class NotReadyRegistry(FakeRegistry):
    def health(self):
        return {
            "status": "not_ready",
            "required_endpoints_available": False,
        }


class FakeServer:
    def __init__(self):
        self.shutdown_calls = 0
        self.close_calls = 0
        self.started = threading.Event()
        self._stopped = threading.Event()

    def serve_forever(self):
        self.started.set()
        self._stopped.wait()

    def shutdown(self):
        self.shutdown_calls += 1
        self._stopped.set()

    def server_close(self):
        self.close_calls += 1


class ExplodingServer(FakeServer):
    def serve_forever(self):
        self.started.set()
        raise OSError("listener failed")


def test_fake_server_waits_for_shutdown_without_timeout():
    server = FakeServer()
    wait_timeouts = []

    class RecordingStopEvent:
        def wait(self, timeout=None):
            wait_timeouts.append(timeout)

    server._stopped = RecordingStopEvent()

    server.serve_forever()

    assert wait_timeouts == [None]


def _manager(tmp_path, **kwargs):
    return KstApiManager(
        tmp_path,
        registry_factory=FakeRegistry,
        **kwargs,
    )


def test_manager_default_retry_interval_is_five_seconds(qapp, tmp_path):
    manager = KstApiManager(
        tmp_path,
        registry_factory=FakeRegistry,
        probe=lambda *_: False,
    )

    assert manager._retry_timer.interval() == 5_000
    manager.stop()


def test_current_project_health_requires_selected_project_binding():
    health = {
        "status": "ok",
        "required_endpoints_available": True,
        "bound_project_ids": ["kunming_niu"],
    }

    assert current_project_health(
        health,
        "kunming_niu",
        "昆明牛",
    ) == (True, "昆明牛快商通 API 正常")
    assert current_project_health(
        health,
        "shenyang_bai",
        "沈阳白",
    ) == (False, "API 已启动，但沈阳白尚未映射")


def test_retry_timer_is_single_shot_with_bounded_backoff(qapp, tmp_path):
    manager, attempts = failing_manager(tmp_path, ["客户端未运行"])

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(manager._retry_timer.isActive)
        assert manager._retry_timer.isSingleShot() is True
        assert manager._retry_timer.interval() == 5_000

        expected_intervals = [15_000, 30_000, 60_000, 60_000]
        for expected in expected_intervals:
            trigger_attempts(manager, attempts, 1)
            assert wait_until(manager._retry_timer.isActive)
            assert manager._retry_timer.interval() == expected
    finally:
        manager.stop()


def test_identical_start_failure_logs_once_and_reports_each_retry_delay(
    qapp,
    tmp_path,
):
    messages = []
    activities = []
    manager, attempts = failing_manager(
        tmp_path,
        ["客户端未运行"],
    )
    manager.log_message.connect(messages.append)
    manager.activity_message.connect(activities.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1 and len(activities) == 1)
        trigger_attempts(manager, attempts, 3)
        assert wait_until(lambda: len(activities) == 4)
        assert messages == ["客户端未运行"]
        assert activities == [
            "[KST] 客户端未运行；5 秒后自动重试",
            "[KST] 客户端未运行；15 秒后自动重试",
            "[KST] 客户端未运行；30 秒后自动重试",
            "[KST] 客户端未运行；60 秒后自动重试",
        ]
    finally:
        manager.stop()


def test_error_change_logs_immediately(qapp, tmp_path):
    messages = []
    manager, attempts = failing_manager(
        tmp_path,
        ["客户端未运行", "数据库结构不兼容"],
    )
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        trigger_attempts(manager, attempts, 1)
        assert wait_until(lambda: len(messages) == 2)
        assert messages == ["客户端未运行", "数据库结构不兼容"]
    finally:
        manager.stop()


def test_legacy_schema_failure_keeps_safe_specific_problem_in_live_log(
    qapp,
    tmp_path,
):
    messages = []
    activities = []
    attempts = []

    class OldSchemaRegistry:
        def __init__(self, *_args):
            pass

        def refresh(self):
            attempts.append(1)
            raise KstLegacyDatabaseError(
                "老版快商通历史库缺少必要字段：visitorSendNum",
                category="database_incompatible",
            )

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=OldSchemaRegistry,
    )
    manager.log_message.connect(messages.append)
    manager.activity_message.connect(activities.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        assert messages == [
            "老版快商通历史库缺少必要字段：visitorSendNum"
        ]
        assert activities == [
            "[KST] 老版快商通历史库缺少必要字段：visitorSendNum；"
            "5 秒后自动重试"
        ]
    finally:
        manager.stop()


def test_typed_failure_category_changes_log_immediately_and_still_deduplicates(
    qapp,
    tmp_path,
):
    now = [0.0]
    messages = []
    attempts = []
    failures = [
        KstDiscoveryError(
            "private process path mismatch",
            category="client_not_running",
        ),
        KstLegacyDatabaseError(
            "private locked database path",
            category="database_busy_or_timeout",
        ),
    ]

    class TypedFailingRegistry:
        def __init__(self, *_args):
            pass

        def refresh(self):
            attempts.append(1)
            raise failures[min(len(attempts) - 1, 1)]

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=TypedFailingRegistry,
        monotonic=lambda: now[0],
    )
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        trigger_attempts(manager, attempts, 1)
        assert wait_until(lambda: len(messages) == 2)
        trigger_attempts(manager, attempts, 2)
        QApplication.processEvents()
        assert messages == ["客户端未运行", "数据库忙或读取超时"]

        now[0] = 301.0
        trigger_attempts(manager, attempts, 1)
        assert wait_until(lambda: len(messages) == 3)
        assert messages[-1] == "数据库忙或读取超时"
    finally:
        manager.stop()


@pytest.mark.parametrize(
    ("error_message", "expected_message"),
    [
        (
            "推广 ID 在项目 alpha 与 beta 中重复",
            "快商通身份映射未就绪",
        ),
        (
            "address already in use",
            "商务通本地 API 端口被占用",
        ),
    ],
)
def test_failure_categories_use_fixed_messages(
    qapp,
    tmp_path,
    error_message,
    expected_message,
):
    messages = []
    manager, attempts = failing_manager(tmp_path, [error_message])
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        assert messages == [expected_message]
        assert manager.status_detail() == expected_message
    finally:
        manager.stop()


@pytest.mark.parametrize(
    ("error_message", "expected_message"),
    [
        (r"\\server\share\SENTINEL_UNC", "商务通本地 API 启动失败"),
        ("/home/user/SENTINEL_POSIX", "商务通本地 API 启动失败"),
        ("file:///C:/private/SENTINEL_FILE", "商务通本地 API 启动失败"),
        ("http://example.invalid/SENTINEL_HTTP", "商务通本地 API 启动失败"),
        ("token=SENTINEL_TOKEN", "商务通本地 API 认证配置无效"),
        ("api_key=SENTINEL_API", "商务通本地 API 认证配置无效"),
        ("secret=SENTINEL_SECRET", "商务通本地 API 认证配置无效"),
        ("password=SENTINEL_PASSWORD", "商务通本地 API 认证配置无效"),
        (
            "authorization: Bearer SENTINEL_AUTH",
            "商务通本地 API 认证配置无效",
        ),
        ("Traceback...\nSENTINEL_TRACE", "商务通本地 API 启动失败"),
        ("SENTINEL_UNKNOWN", "商务通本地 API 启动失败"),
    ],
)
def test_failure_messages_never_expose_paths_credentials_or_tracebacks(
    qapp,
    tmp_path,
    error_message,
    expected_message,
):
    messages = []
    statuses = []
    manager, attempts = failing_manager(tmp_path, [error_message])
    manager.log_message.connect(messages.append)
    manager.status_changed.connect(
        lambda ready, detail: statuses.append((ready, detail))
    )

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1 and len(statuses) == 1)
        published = "\n".join(messages + [detail for _, detail in statuses])
        assert "SENTINEL" not in published
        assert messages == [expected_message]
        assert statuses == [(False, expected_message)]
        assert manager.status_detail() == expected_message
    finally:
        manager.stop()


def test_failure_message_does_not_expose_local_path_or_traceback(
    qapp,
    tmp_path,
):
    messages = []
    manager, attempts = failing_manager(
        tmp_path,
        [r"显式配置的商务通根目录无效：C:\Users\Alice\private"],
    )
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        assert messages == ["快商通客户端目录无效"]
        assert "C:\\" not in manager.status_detail()
        assert "Traceback" not in manager.status_detail()
    finally:
        manager.stop()


def test_rescan_is_immediate_only_after_manager_started(qapp, tmp_path):
    manager, attempts = failing_manager(tmp_path, ["客户端未运行"])

    manager.rescan()
    QApplication.processEvents()
    assert attempts == []

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        manager._retry_timer.stop()
        manager.rescan()
        wait_for_attempts(attempts, 2)
    finally:
        manager.stop()


def test_rescan_forces_registry_refresh_and_forwards_generation_cancel_event(
    qapp,
    tmp_path,
):
    server = FakeServer()
    calls = []
    events = []

    class ForceAwareRegistry(FakeRegistry):
        def refresh(self, *, force=False, cancel_event=None):
            calls.append(force)
            events.append(cancel_event)

        def health(self, *, cancel_event=None):
            events.append(cancel_event)
            return super().health()

    registry = ForceAwareRegistry()
    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=lambda _root: registry,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=5_000,
    )
    manager.start()
    try:
        assert wait_until(server.started.is_set)
        initial_event = next(event for event in events if event is not None)
        assert calls == [False]

        manager.rescan()
        assert wait_until(lambda: calls == [False, True])
        assert all(event is initial_event for event in events if event is not None)
    finally:
        manager.stop()

    assert initial_event.is_set() is True


def test_owned_registry_refresh_failure_invalidates_old_request_routing(
    qapp,
    tmp_path,
):
    server = FakeServer()

    class FailsOnSecondRefresh(FakeRegistry):
        def refresh(self, *, force=False, cancel_event=None):
            self.refresh_calls += 1
            if self.refresh_calls > 1:
                raise KstDiscoveryError(
                    "private stale identity database",
                    category="database_incompatible",
                )

    registry = FailsOnSecondRefresh()
    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=lambda _root: registry,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=5_000,
    )
    manager.start()
    try:
        assert wait_until(server.started.is_set)
        assert manager._registry is registry

        manager.rescan()
        assert wait_until(lambda: registry.refresh_calls == 2)
        assert wait_until(
            lambda: manager._worker is None
            or not manager._worker.is_alive()
        )

        assert manager._server is server
        assert manager._registry is None
        assert manager._registry_health()["status"] == "not_ready"
        with pytest.raises(RuntimeError, match="not ready"):
            manager._service_for("a", "2026-07-29")
    finally:
        manager.stop()


def test_stop_cancels_registry_refresh_cooperatively(qapp, tmp_path):
    entered = threading.Event()
    exited = threading.Event()
    received = []

    class CancellableRegistry:
        def __init__(self, *_args):
            pass

        def refresh(self, *, cancel_event=None):
            received.append(cancel_event)
            entered.set()
            assert cancel_event is not None
            cancel_event.wait(timeout=1)
            exited.set()

        def health(self):
            return {
                "status": "not_ready",
                "required_endpoints_available": False,
            }

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=CancellableRegistry,
    )
    manager.start()
    assert entered.wait(timeout=1)

    manager.stop()

    assert exited.wait(timeout=1)
    assert received[0].is_set() is True


def test_registry_health_failure_category_reaches_manager_status(
    qapp,
    tmp_path,
):
    messages = []

    class InactiveRegistry(FakeRegistry):
        def health(self, *, cancel_event=None):
            return {
                "status": "not_ready",
                "required_endpoints_available": False,
                "error_category": "inactive_log",
                "error_detail": "未检测到活动身份",
            }

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=InactiveRegistry,
    )
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        assert wait_until(lambda: messages == ["未检测到活动身份"])
        assert manager.status_detail() == "未检测到活动身份"
    finally:
        manager.stop()


def test_stop_does_not_wait_for_blocked_worker(qapp, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    factory_calls = []

    class BlockingRegistry(FakeRegistry):
        def refresh(self):
            entered.set()
            release.wait()

    manager = KstApiManager(
        tmp_path,
        registry_factory=BlockingRegistry,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: (
            factory_calls.append(1) or FakeServer()
        ),
    )
    manager.start()
    assert entered.wait(timeout=1)
    worker = manager._worker
    assert worker is not None

    try:
        started = time.monotonic()
        manager.stop()
        elapsed = time.monotonic() - started
        assert elapsed < 0.1
    finally:
        release.set()
    worker.join(timeout=1)
    assert worker.is_alive() is False
    assert factory_calls == []


def test_rapid_restart_never_runs_two_manager_workers(qapp, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    counts_lock = threading.Lock()
    attempts = 0
    active_workers = 0
    max_active_workers = 0

    class BlockingRegistry(FakeRegistry):
        def refresh(self):
            nonlocal attempts, active_workers, max_active_workers
            with counts_lock:
                attempts += 1
                active_workers += 1
                max_active_workers = max(max_active_workers, active_workers)
                entered.set()
            try:
                release.wait()
            finally:
                with counts_lock:
                    active_workers -= 1

    server = FakeServer()
    manager = KstApiManager(
        tmp_path,
        registry_factory=BlockingRegistry,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=5_000,
    )
    manager.start()
    assert entered.wait(timeout=1)

    try:
        started = time.monotonic()
        manager.stop()
        assert time.monotonic() - started < 0.1
        manager.start()

        time.sleep(0.05)
        with counts_lock:
            assert attempts == 1
            assert max_active_workers == 1

        release.set()
        assert wait_until(lambda: attempts >= 2)
        with counts_lock:
            assert max_active_workers == 1
    finally:
        release.set()
        manager.stop()


def test_rescan_waiting_on_retiring_worker_is_consumed_by_new_worker(
    qapp,
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    class FirstAttemptBlocks:
        def __init__(self, *_args):
            pass

        def refresh(self):
            attempts.append(1)
            if len(attempts) == 1:
                entered.set()
                release.wait()
            raise RuntimeError("客户端未运行")

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=FirstAttemptBlocks,
    )
    manager.start()
    assert entered.wait(timeout=1)

    try:
        manager.stop()
        manager.start()
        manager.rescan()
        release.set()

        assert wait_until(
            lambda: manager._retry_timer.isActive()
            and (manager._worker is None or not manager._worker.is_alive())
        )
        assert attempts == [1, 1]
        assert manager._retry_timer.interval() == 5_000
    finally:
        release.set()
        manager.stop()


def test_rescans_during_current_worker_coalesce_to_one_extra_attempt(
    qapp,
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    class FirstAttemptBlocks:
        def __init__(self, *_args):
            pass

        def refresh(self):
            attempts.append(1)
            if len(attempts) == 1:
                entered.set()
                release.wait()
            raise RuntimeError("客户端未运行")

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=FirstAttemptBlocks,
    )
    manager.start()
    assert entered.wait(timeout=1)

    try:
        manager.rescan()
        manager.rescan()
        manager.rescan()
        release.set()

        assert wait_until(
            lambda: len(attempts) == 2
            and manager._retry_timer.isActive()
            and (manager._worker is None or not manager._worker.is_alive())
        )
        QApplication.processEvents()
        time.sleep(0.05)
        assert attempts == [1, 1]
        assert manager._retry_timer.interval() == 15_000
    finally:
        release.set()
        manager.stop()


def test_delayed_old_worker_finished_does_not_bypass_current_backoff(
    qapp,
    tmp_path,
):
    manager, attempts = failing_manager(
        tmp_path,
        ["客户端未运行"],
    )

    def wait_without_qt_events(predicate, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    manager.start()
    try:
        assert wait_without_qt_events(
            lambda: len(attempts) == 1
            and (manager._worker is None or not manager._worker.is_alive())
        )

        manager.stop()
        manager.start()
        assert wait_without_qt_events(
            lambda: len(attempts) == 2
            and (manager._worker is None or not manager._worker.is_alive())
        )

        QApplication.processEvents()
        time.sleep(0.05)

        assert attempts == [1, 1]
        assert manager._retry_timer.isActive() is True
        assert manager._retry_timer.interval() == 5_000
    finally:
        manager.stop()


def test_manager_starts_owned_server_and_stops_it(qapp, tmp_path):
    server = FakeServer()
    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=20,
    )

    manager.start()
    try:
        assert wait_until(server.started.is_set)
        assert manager.is_ready() is True
        assert manager.owns_server() is True
    finally:
        manager.stop()
    assert server.shutdown_calls == 1
    assert wait_until(lambda: server.close_calls == 1)


def test_manager_reuses_external_server_without_stopping_it(qapp, tmp_path):
    factory_calls = []
    manager = _manager(
        tmp_path,
        probe=lambda *_: True,
        server_factory=lambda *_args, **_kwargs: factory_calls.append(1),
    )

    manager.start()
    try:
        assert wait_until(manager.is_ready)
    finally:
        manager.stop()
    assert manager.owns_server() is False
    assert factory_calls == []


def test_stop_discards_queued_ready_from_old_generation(qapp, tmp_path):
    statuses = []
    manager = _manager(
        tmp_path,
        probe=lambda *_: True,
        server_factory=lambda *_args, **_kwargs: pytest.fail(
            "external healthy service must not create a server"
        ),
    )
    manager.status_changed.connect(
        lambda ready, detail: statuses.append((ready, detail))
    )

    manager.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        worker = manager._worker
        if worker is None or not worker.is_alive():
            break
        time.sleep(0.01)
    else:
        pytest.fail("manager worker did not finish")

    manager.stop()
    QApplication.processEvents()

    assert statuses == [(False, "商务通 API 已停止")]
    assert manager.is_ready() is False
    assert manager.status_detail() == "商务通 API 已停止"


def test_failed_start_stays_gray_and_retries(qapp, tmp_path):
    attempts = []
    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: (
            attempts.append(1)
            or (_ for _ in ()).throw(OSError("busy"))
        ),
        retry_interval_ms=20,
    )

    manager.start()
    try:
        assert wait_until(lambda: len(attempts) >= 2)
        assert manager.is_ready() is False
    finally:
        manager.stop()


def test_not_ready_runtime_never_turns_status_green(qapp, tmp_path):
    factory_calls = []
    manager = KstApiManager(
        tmp_path,
        registry_factory=NotReadyRegistry,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: factory_calls.append(1),
        retry_interval_ms=20,
    )

    manager.start()
    try:
        assert wait_until(
            lambda: manager.status_detail() == "快商通身份映射未就绪"
        )
        assert manager.is_ready() is False
        assert factory_calls == []
    finally:
        manager.stop()


def test_external_server_loss_starts_owned_replacement(qapp, tmp_path):
    server = FakeServer()
    probe_results = iter([True, False])
    manager = _manager(
        tmp_path,
        probe=lambda *_: next(probe_results, False),
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=20,
    )

    manager.start()
    try:
        assert wait_until(manager.is_ready)
        assert manager.owns_server() is False
        assert wait_until(server.started.is_set)
        assert manager.owns_server() is True
        assert manager.is_ready() is True
    finally:
        manager.stop()


def test_healthy_owned_server_skips_full_refresh_before_interval(qapp, tmp_path):
    server = FakeServer()
    registries = []
    now = [100.0]

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
    try:
        assert wait_until(server.started.is_set)
        assert manager.is_ready() is True
        deadline = time.monotonic() + 0.08
        while time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        assert len(registries) == 1
        assert registries[0].refresh_calls == 1
        now[0] += 301
        assert wait_until(lambda: registries[0].refresh_calls == 2)
    finally:
        manager.stop()


def test_owned_server_exception_clears_ready_and_ownership(qapp, tmp_path):
    server = ExplodingServer()
    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=5_000,
    )

    manager.start()
    try:
        assert wait_until(server.started.is_set)
        assert wait_until(lambda: server.close_calls == 1)
        assert manager.is_ready() is False
        assert manager.owns_server() is False
    finally:
        manager.stop()


def test_immediate_server_exit_publishes_failure_after_startup(qapp, tmp_path):
    statuses = []

    class ShortLivedServer(ExplodingServer):
        def __init__(self):
            super().__init__()
            self.closed = threading.Event()

        def server_close(self):
            super().server_close()
            self.closed.set()

    server = ShortLivedServer()
    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=5_000,
    )
    original_queue_status = manager._queue_status

    def expose_immediate_exit_order(generation, ready, detail):
        if ready and threading.current_thread().name == "kst-api-manager":
            assert server.closed.wait(timeout=1)
        original_queue_status(generation, ready, detail)

    manager._queue_status = expose_immediate_exit_order
    manager.status_changed.connect(
        lambda ready, detail: statuses.append((ready, detail))
    )

    manager.start()
    try:
        assert server.closed.wait(timeout=1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            worker = manager._worker
            if worker is None or not worker.is_alive():
                break
            time.sleep(0.01)
        QApplication.processEvents()

        assert manager.is_ready() is False
        assert statuses
        assert statuses[-1] == (False, manager.status_detail())
    finally:
        manager.stop()


def test_unexpected_server_exit_uses_fresh_instances_and_restarts(
    qapp,
    tmp_path,
):
    servers = []

    def server_factory(*_args, **_kwargs):
        server = ExplodingServer() if len(servers) < 2 else FakeServer()
        servers.append(server)
        return server

    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=server_factory,
        retry_interval_ms=20,
    )

    manager.start()
    try:
        assert wait_until(
            lambda: len(servers) >= 3 and servers[2].started.is_set()
        )
        assert servers[0] is not servers[1]
        assert [server.close_calls for server in servers[:2]] == [1, 1]
        assert manager.owns_server() is True
        assert manager.is_ready() is True
    finally:
        manager.stop()

    assert wait_until(
        lambda: [server.close_calls for server in servers] == [1, 1, 1]
    )
    assert servers[2].shutdown_calls == 1
