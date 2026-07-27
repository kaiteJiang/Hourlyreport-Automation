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
        self._stopped = threading.Event()

    def serve_forever(self):
        self._stopped.wait(2)

    def shutdown(self):
        self.shutdown_calls += 1
        self._stopped.set()

    def server_close(self):
        self.close_calls += 1


class ExplodingServer(FakeServer):
    def serve_forever(self):
        raise OSError("listener failed")


def _manager(tmp_path, **kwargs):
    return KstApiManager(
        tmp_path,
        registry_factory=FakeRegistry,
        **kwargs,
    )


def test_manager_starts_owned_server_and_stops_it(qapp, tmp_path):
    server = FakeServer()
    manager = _manager(
        tmp_path,
        probe=lambda *_: False,
        server_factory=lambda *_args, **_kwargs: server,
        retry_interval_ms=20,
    )

    manager.start()

    assert wait_until(manager.is_ready)
    assert manager.owns_server() is True
    manager.stop()
    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_manager_reuses_external_server_without_stopping_it(qapp, tmp_path):
    factory_calls = []
    manager = _manager(
        tmp_path,
        probe=lambda *_: True,
        server_factory=lambda *_args, **_kwargs: factory_calls.append(1),
    )

    manager.start()

    assert wait_until(manager.is_ready)
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

    assert wait_until(lambda: len(attempts) >= 2)
    assert manager.is_ready() is False
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

    assert wait_until(lambda: "尚未就绪" in manager.status_detail())
    assert manager.is_ready() is False
    assert factory_calls == []
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

    assert wait_until(manager.is_ready)
    assert manager.owns_server() is False
    assert wait_until(manager.owns_server)
    assert manager.is_ready() is True
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

    assert wait_until(manager.is_ready)
    deadline = time.monotonic() + 0.08
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert len(registries) == 1
    assert registries[0].refresh_calls == 1
    now[0] += 301
    assert wait_until(lambda: registries[0].refresh_calls == 2)
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

    assert wait_until(lambda: server.close_calls == 1)
    assert manager.is_ready() is False
    assert manager.owns_server() is False
    manager.stop()
