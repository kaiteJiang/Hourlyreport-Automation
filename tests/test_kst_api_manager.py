import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from gui.kst_api_manager import KstApiManager


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


def test_identical_start_failure_logs_once_until_reminder(qapp, tmp_path):
    now = [0.0]
    messages = []
    manager, attempts = failing_manager(
        tmp_path,
        ["客户端未运行"],
        monotonic=lambda: now[0],
    )
    manager.log_message.connect(messages.append)

    manager.start()
    try:
        wait_for_attempts(attempts, 1)
        assert wait_until(lambda: len(messages) == 1)
        trigger_attempts(manager, attempts, 3)
        QApplication.processEvents()
        assert messages == ["客户端未运行"]

        now[0] = 301.0
        trigger_attempts(manager, attempts, 1)
        assert wait_until(lambda: len(messages) == 2)
        assert messages == ["客户端未运行", "客户端未运行"]
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


@pytest.mark.parametrize(
    ("error_message", "expected_message"),
    [
        (
            "推广 ID 在项目 alpha 与 beta 中重复",
            "推广 ID 在项目 alpha 与 beta 中重复",
        ),
        (
            "address already in use",
            "商务通本地 API 端口被占用",
        ),
    ],
)
def test_failure_categories_preserve_only_safe_messages(
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
        assert wait_until(lambda: "尚未就绪" in manager.status_detail())
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
