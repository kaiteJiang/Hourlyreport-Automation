from __future__ import annotations

import hmac
import inspect
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from modules.kst_local.auth import (
    load_or_create_local_token,
    local_health_proof,
)
from modules.kst_local.http_server import create_server
from modules.kst_local.identity_registry import KstIdentityRegistry


Probe = Callable[[str, str], bool | tuple[bool, str]]


_TYPED_FAILURE_MESSAGES = {
    "client_not_running": "客户端未运行",
    "client_path_mismatch": "客户端程序与运行进程不匹配",
    "inactive_log": "未检测到活动身份",
    "database_incompatible": "数据库结构不兼容",
    "database_busy_or_timeout": "数据库忙或读取超时",
    "identity_mapping": "快商通身份映射未就绪",
    "installation_root": "快商通客户端目录无效",
    "data_root": "快商通数据目录无效",
    "port_in_use": "商务通本地 API 端口被占用",
    "authentication": "商务通本地 API 认证配置无效",
}


class _RegistryHealthFailure(RuntimeError):
    def __init__(self, category: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category


def _call_with_supported_keywords(
    function: Callable[..., Any],
    *args: Any,
    **keywords: Any,
) -> Any:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*args)
    accepts_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    supported = {
        key: value
        for key, value in keywords.items()
        if accepts_keywords or key in signature.parameters
    }
    return function(*args, **supported)


def _health_failure(
    health: dict[str, Any],
    fallback: str,
) -> BaseException:
    category = str(health.get("error_category") or "").strip()
    if category in _TYPED_FAILURE_MESSAGES:
        return _RegistryHealthFailure(
            category,
            _TYPED_FAILURE_MESSAGES[category],
        )
    return RuntimeError(fallback)


def probe_kst_health(
    url: str,
    token: str,
    timeout: float = 1.5,
) -> tuple[bool, str]:
    try:
        expected_proof = local_health_proof(token)
    except Exception:
        return False, "本地 API 令牌不可用"
    request = urllib.request.Request(
        f"{url.rstrip('/')}/health",
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ):
        return False, "本地 API 未响应"
    if not isinstance(payload, dict):
        return False, "本地 API 响应不兼容"
    if (
        payload.get("status") == "ok"
        and payload.get("required_endpoints_available") is True
        and payload.get("project_routing") is True
        and hmac.compare_digest(
            str(payload.get("auth_proof") or ""),
            expected_proof,
        )
    ):
        return True, "127.0.0.1 本地 API 正常"
    return False, "商务通自动数据源尚未就绪"


class KstApiManager(QObject):
    status_changed = Signal(bool, str)
    log_message = Signal(str)
    _retry_requested = Signal(int, int)
    _worker_finished = Signal(int, object)
    _status_requested = Signal(int, bool, str)

    def __init__(
        self,
        root: str | Path,
        *,
        probe: Probe = probe_kst_health,
        server_factory: Callable[..., Any] = create_server,
        registry_factory: Callable[[Path], Any] = KstIdentityRegistry,
        retry_interval_ms: int = 5_000,
        registry_refresh_interval_ms: int = 300_000,
        monotonic: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._probe = probe
        self._server_factory = server_factory
        self._registry_factory = registry_factory
        self._registry: Any | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._worker_generation: int | None = None
        self._retiring_worker_restart: (
            tuple[threading.Thread, int, int] | None
        ) = None
        self._server_thread: threading.Thread | None = None
        self._server: Any | None = None
        self._owns_server = False
        self._external_server = False
        self._registry_refresh_interval_seconds = max(
            0.0,
            float(registry_refresh_interval_ms) / 1000.0,
        )
        self._monotonic = monotonic
        self._last_registry_refresh_at: float | None = None
        self._ready = False
        self._detail = "商务通 API 未启动"
        self._started = False
        self._stopping = False
        self._generation = 0
        self._cancel_event = threading.Event()
        self._rescan_pending = False
        self._force_refresh_requested = False
        self._closed_server_ids: set[int] = set()
        base_retry_ms = max(10, int(retry_interval_ms))
        self._retry_intervals_ms = (
            base_retry_ms,
            base_retry_ms * 3,
            base_retry_ms * 6,
            base_retry_ms * 12,
        )
        self._retry_index = 0
        self._last_error_key: str | None = None
        self._last_error_log_at: float | None = None
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(self._retry_intervals_ms[0])
        self._retry_timer.timeout.connect(self._ensure_service_async)
        self._retry_requested.connect(
            self._start_retry_timer,
            Qt.ConnectionType.QueuedConnection,
        )
        self._worker_finished.connect(
            self._on_worker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._status_requested.connect(
            self._publish_status,
            Qt.ConnectionType.QueuedConnection,
        )

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopping = False
            self._generation += 1
            self._cancel_event = threading.Event()
            self._rescan_pending = False
            self._force_refresh_requested = False
            self._retiring_worker_restart = None
            self._retry_index = 0
            self._last_error_key = None
            self._last_error_log_at = None
        self._retry_timer.stop()
        self._ensure_service_async()

    def stop(self) -> None:
        self._retry_timer.stop()
        with self._lock:
            if not self._started and self._stopping:
                return
            self._started = False
            self._stopping = True
            self._generation += 1
            self._cancel_event.set()
            self._retiring_worker_restart = None
            server = self._server if self._owns_server else None
            self._server = None
            self._server_thread = None
            self._registry = None
            self._owns_server = False
            self._external_server = False
            self._ready = False
            self._last_registry_refresh_at = None
            self._rescan_pending = False
            self._force_refresh_requested = False
            detail = "商务通 API 已停止"
            self._detail = detail
            generation = self._generation
        self._queue_status(generation, False, detail)
        if server is not None:
            threading.Thread(
                target=self._request_server_shutdown,
                args=(server,),
                name="kst-api-shutdown",
                daemon=True,
            ).start()

    def rescan(self) -> None:
        with self._lock:
            if not self._started or self._stopping:
                return
            self._last_registry_refresh_at = None
            self._retry_index = 0
            self._last_error_key = None
            self._last_error_log_at = None
            self._force_refresh_requested = True
            worker_running = (
                self._worker is not None and self._worker.is_alive()
            )
            self._rescan_pending = worker_running
        self._retry_timer.stop()
        if not worker_running:
            self._ensure_service_async()

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def owns_server(self) -> bool:
        with self._lock:
            return self._owns_server

    def status_detail(self) -> str:
        with self._lock:
            return self._detail

    def _queue_status(
        self,
        generation: int,
        ready: bool,
        detail: str,
    ) -> None:
        try:
            self._status_requested.emit(generation, ready, detail)
        except RuntimeError:
            pass

    def _publish_status(
        self,
        generation: int,
        ready: bool,
        detail: str,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
        try:
            self.status_changed.emit(ready, detail)
        except RuntimeError:
            pass

    def _emit_log(self, message: str) -> None:
        try:
            self.log_message.emit(message)
        except RuntimeError:
            pass

    def _is_active_locked(
        self,
        generation: int,
        cancel_event: threading.Event,
    ) -> bool:
        return (
            self._started
            and not self._stopping
            and self._generation == generation
            and self._cancel_event is cancel_event
            and not cancel_event.is_set()
        )

    def _is_active(
        self,
        generation: int,
        cancel_event: threading.Event,
    ) -> bool:
        with self._lock:
            return self._is_active_locked(generation, cancel_event)

    def _ensure_service_async(self) -> None:
        with self._lock:
            if not self._started or self._stopping:
                return
            if self._worker is not None and self._worker.is_alive():
                worker_generation = self._worker_generation
                if (
                    worker_generation is not None
                    and worker_generation != self._generation
                ):
                    self._retiring_worker_restart = (
                        self._worker,
                        worker_generation,
                        self._generation,
                    )
                return
            target = (
                self._refresh_owned_registry
                if self._owns_server
                else self._ensure_service
            )
            generation = self._generation
            cancel_event = self._cancel_event
            force_refresh = self._force_refresh_requested
            self._force_refresh_requested = False
            self._worker = threading.Thread(
                target=self._run_worker,
                args=(
                    target,
                    generation,
                    cancel_event,
                    force_refresh,
                ),
                name="kst-api-manager",
                daemon=True,
            )
            self._worker_generation = generation
            self._retiring_worker_restart = None
            self._rescan_pending = False
            worker = self._worker
        worker.start()

    def _run_worker(
        self,
        target: Callable[
            [int, threading.Event, bool],
            int | None,
        ],
        generation: int,
        cancel_event: threading.Event,
        force_refresh: bool,
    ) -> None:
        delay_ms: int | None = None
        try:
            delay_ms = target(
                generation,
                cancel_event,
                force_refresh,
            )
        finally:
            current = threading.current_thread()
            immediate = False
            with self._lock:
                if self._worker is current:
                    self._worker = None
                    self._worker_generation = None
                if self._is_active_locked(generation, cancel_event):
                    immediate = self._rescan_pending
                    self._rescan_pending = False
            if immediate:
                self._retry_requested.emit(0, generation)
            elif delay_ms is not None:
                self._retry_requested.emit(delay_ms, generation)
            self._worker_finished.emit(generation, current)

    def _on_worker_finished(
        self,
        generation: int,
        worker: threading.Thread,
    ) -> None:
        with self._lock:
            token = self._retiring_worker_restart
            should_start_current_generation = (
                self._started
                and not self._stopping
                and token is not None
                and token[0] is worker
                and token[1] == generation
                and token[2] == self._generation
                and (
                    self._worker is None
                    or not self._worker.is_alive()
                )
            )
            if should_start_current_generation:
                self._retiring_worker_restart = None
        if should_start_current_generation:
            self._ensure_service_async()

    def _start_retry_timer(self, delay_ms: int, generation: int) -> None:
        with self._lock:
            if (
                not self._started
                or self._stopping
                or generation != self._generation
            ):
                return
        if delay_ms <= 0:
            self._ensure_service_async()
            return
        self._retry_timer.start(int(delay_ms))

    def _next_failure_delay_locked(self) -> int:
        index = min(self._retry_index, len(self._retry_intervals_ms) - 1)
        delay_ms = self._retry_intervals_ms[index]
        self._retry_index = min(
            self._retry_index + 1,
            len(self._retry_intervals_ms) - 1,
        )
        return delay_ms

    def _record_success(
        self,
        generation: int,
        cancel_event: threading.Event,
        detail: str,
        *,
        log_message: str | None = None,
    ) -> int | None:
        with self._lock:
            if not self._is_active_locked(generation, cancel_event):
                return None
            self._ready = True
            self._detail = detail
            self._retry_index = 0
            self._last_error_key = None
            self._last_error_log_at = None
            delay_ms = self._retry_intervals_ms[0]
        self._queue_status(generation, True, detail)
        if log_message:
            self._emit_log(log_message)
        return delay_ms

    @staticmethod
    def _safe_failure(
        error: BaseException | str,
    ) -> tuple[str, str]:
        typed_category = str(
            getattr(error, "category", "")
        ).strip()
        if typed_category in _TYPED_FAILURE_MESSAGES:
            return (
                typed_category,
                _TYPED_FAILURE_MESSAGES[typed_category],
            )
        text = str(error or "").strip()
        lowered = text.casefold()
        if any(
            credential_key in lowered
            for credential_key in (
                "token",
                "api_key",
                "secret",
                "password",
                "authorization",
            )
        ):
            return "authentication", "商务通本地 API 认证配置无效"
        if (
            "根目录无效" in text
            or "程序目录不具备读取能力" in text
            or "目录不具备商务通读取能力" in text
        ):
            return "installation_root", "快商通客户端目录无效"
        if "数据目录" in text and (
            "不具备读取能力" in text
            or "无法扫描" in text
            or "未找到" in text
        ):
            return "data_root", "快商通数据目录无效"
        if "客户端未运行" in text or "未检测到正在运行" in text:
            return "client_not_running", "客户端未运行"
        if "数据库结构不兼容" in text or (
            "数据库" in text and "不兼容" in text
        ):
            return "database_incompatible", "数据库结构不兼容"
        if (
            "数据库忙" in text
            or "读取超时" in text
            or "database is locked" in lowered
        ):
            return (
                "database_busy_or_timeout",
                "数据库忙或读取超时",
            )
        if "活动身份" in text:
            return "inactive_log", "未检测到活动身份"
        if (
            "address already in use" in lowered
            or "winerror 10048" in lowered
            or "端口" in text and ("占用" in text or "冲突" in text)
        ):
            return "port_in_use", "商务通本地 API 端口被占用"
        if (
            "身份" in text
            or "自动数据源" in text
            or "推广 id" in lowered
            or "绑定" in text
            or "映射" in text
        ):
            return "identity_mapping", "快商通身份映射未就绪"
        return "startup_failed", "商务通本地 API 启动失败"

    def _record_failure(
        self,
        generation: int,
        cancel_event: threading.Event,
        error: BaseException | str,
    ) -> int | None:
        key, detail = self._safe_failure(error)
        now = self._monotonic()
        with self._lock:
            if not self._is_active_locked(generation, cancel_event):
                return None
            should_log = (
                key != self._last_error_key
                or self._last_error_log_at is None
                or now - self._last_error_log_at >= 300.0
            )
            self._last_error_key = key
            if should_log:
                self._last_error_log_at = now
            self._ready = False
            self._detail = detail
            delay_ms = self._next_failure_delay_locked()
        self._queue_status(generation, False, detail)
        if should_log:
            self._emit_log(detail)
        return delay_ms

    def _refresh_owned_registry(
        self,
        generation: int,
        cancel_event: threading.Event,
        force_refresh: bool = False,
    ) -> int | None:
        with self._lock:
            if (
                not self._is_active_locked(generation, cancel_event)
                or not self._owns_server
            ):
                return None
            registry = self._registry
            last_refresh = self._last_registry_refresh_at
        now = self._monotonic()
        if (
            not force_refresh
            and registry is not None
            and last_refresh is not None
        ):
            age = now - last_refresh
            if 0 <= age < self._registry_refresh_interval_seconds:
                try:
                    health = _call_with_supported_keywords(
                        registry.health,
                        cancel_event=cancel_event,
                    )
                    ready = (
                        health.get("status") == "ok"
                        and health.get("required_endpoints_available") is True
                    )
                except Exception:
                    ready = False
                if ready:
                    return self._record_success(
                        generation,
                        cancel_event,
                        "商务通本地 API 正常：127.0.0.1:18766",
                    )
        try:
            if registry is None:
                registry = self._registry_factory(self._root)
            _call_with_supported_keywords(
                registry.refresh,
                force=force_refresh,
                cancel_event=cancel_event,
            )
            health = _call_with_supported_keywords(
                registry.health,
                cancel_event=cancel_event,
            )
            ready = (
                health.get("status") == "ok"
                and health.get("required_endpoints_available") is True
            )
        except Exception as exc:
            return self._record_failure(generation, cancel_event, exc)
        with self._lock:
            if (
                not self._is_active_locked(generation, cancel_event)
                or not self._owns_server
            ):
                return None
            self._registry = registry if ready else None
            self._last_registry_refresh_at = now if ready else None
        if ready:
            return self._record_success(
                generation,
                cancel_event,
                "商务通本地 API 正常：127.0.0.1:18766",
            )
        return self._record_failure(
            generation,
            cancel_event,
            _health_failure(
                health,
                "商务通登录身份尚未就绪",
            ),
        )

    def _service_for(self, project_id: str, request_date: str) -> Any:
        with self._lock:
            registry = self._registry
            cancel_event = self._cancel_event
        if registry is None:
            raise RuntimeError("KST identity registry is not ready")
        return _call_with_supported_keywords(
            registry.build_runtime,
            project_id,
            request_date,
            cancel_event=cancel_event,
        ).service

    def _registry_health(self) -> dict[str, Any]:
        with self._lock:
            registry = self._registry
            cancel_event = self._cancel_event
        if registry is None:
            return {
                "status": "not_ready",
                "required_endpoints_available": False,
                "project_routing": True,
            }
        return _call_with_supported_keywords(
            registry.health,
            cancel_event=cancel_event,
        )

    def _close_server_once(self, server: Any) -> None:
        server_id = id(server)
        with self._lock:
            if server_id in self._closed_server_ids:
                return
            self._closed_server_ids.add(server_id)
        try:
            server.server_close()
        except Exception:
            pass

    @staticmethod
    def _request_server_shutdown(server: Any) -> None:
        try:
            server.shutdown()
        except Exception:
            pass

    def _serve_owned_server(
        self,
        server: Any,
        generation: int,
        cancel_event: threading.Event,
        port: int,
    ) -> None:
        failure: BaseException | str = "商务通本地 API 已停止"
        delay_ms = self._record_success(
            generation,
            cancel_event,
            f"商务通本地 API 已启动：127.0.0.1:{port}",
            log_message="商务通本地 API 已随程序启动",
        )
        if delay_ms is not None:
            self._retry_requested.emit(delay_ms, generation)
        try:
            server.serve_forever()
        except Exception as exc:
            failure = exc
        finally:
            with self._lock:
                unexpected_stop = self._is_active_locked(
                    generation,
                    cancel_event,
                )
                if self._server is server:
                    self._server = None
                    self._server_thread = None
                    self._registry = None
                    self._last_registry_refresh_at = None
                    self._owns_server = False
                    self._external_server = False
            self._close_server_once(server)
        if unexpected_stop:
            delay_ms = self._record_failure(
                generation,
                cancel_event,
                failure,
            )
            if delay_ms is not None:
                self._retry_requested.emit(delay_ms, generation)

    @staticmethod
    def _probe_result(value: bool | tuple[bool, str]) -> tuple[bool, str]:
        if isinstance(value, tuple):
            return bool(value[0]), str(value[1])
        return bool(value), "检测到兼容的商务通本地 API"

    def _ensure_service(
        self,
        generation: int,
        cancel_event: threading.Event,
        force_refresh: bool = False,
    ) -> int | None:
        try:
            url = "http://127.0.0.1:18766"
            parsed = urllib.parse.urlparse(url)
            if (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or parsed.port not in (None, 18766)
            ):
                raise ValueError(
                    "商务通本地 API 必须使用 127.0.0.1:18766"
                )
            port = parsed.port or 18766
            token = load_or_create_local_token(self._root)
            healthy, detail = self._probe_result(self._probe(url, token))
            if healthy:
                with self._lock:
                    if not self._is_active_locked(
                        generation,
                        cancel_event,
                    ):
                        return None
                    already_external = (
                        self._ready and self._external_server
                    )
                    self._external_server = True
                return self._record_success(
                    generation,
                    cancel_event,
                    detail,
                    log_message=(
                        None
                        if already_external
                        else "已复用现有商务通本地 API"
                    ),
                )
            with self._lock:
                if not self._is_active_locked(generation, cancel_event):
                    return None
                lost_external = self._ready and self._external_server
                self._external_server = False
            if lost_external:
                self._queue_status(
                    generation,
                    False,
                    "现有商务通本地 API 已断开，正在重启",
                )

            registry = self._registry_factory(self._root)
            _call_with_supported_keywords(
                registry.refresh,
                force=force_refresh,
                cancel_event=cancel_event,
            )
            health = _call_with_supported_keywords(
                registry.health,
                cancel_event=cancel_event,
            )
            if not (
                health.get("status") == "ok"
                and health.get("required_endpoints_available") is True
            ):
                return self._record_failure(
                    generation,
                    cancel_event,
                    _health_failure(
                        health,
                        "商务通自动数据源尚未就绪",
                    ),
                )
            if not self._is_active(generation, cancel_event):
                return None

            server = self._server_factory(
                "127.0.0.1",
                port,
                service_factory=self._service_for,
                health_provider=self._registry_health,
                token=token,
            )
            with self._lock:
                if not self._is_active_locked(generation, cancel_event):
                    close_server = True
                    server_thread = None
                else:
                    close_server = False
                    self._server = server
                    self._registry = registry
                    self._last_registry_refresh_at = self._monotonic()
                    self._owns_server = True
                    self._external_server = False
                    self._server_thread = threading.Thread(
                        target=self._serve_owned_server,
                        args=(server, generation, cancel_event, port),
                        name="kst-api-server",
                        daemon=True,
                    )
                    server_thread = self._server_thread
                    server_thread.start()
            if close_server:
                self._close_server_once(server)
                return None
            return None
        except Exception as exc:
            return self._record_failure(
                generation,
                cancel_event,
                exc,
            )
