from __future__ import annotations

import hmac
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

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stopping = False
            self._generation += 1
            self._cancel_event = threading.Event()
            self._rescan_pending = False
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
            server = self._server if self._owns_server else None
            self._worker = None
            self._server = None
            self._server_thread = None
            self._registry = None
            self._owns_server = False
            self._external_server = False
            self._ready = False
            self._last_registry_refresh_at = None
            self._rescan_pending = False
            self._detail = "商务通 API 已停止"
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

    def _emit_status(self, ready: bool, detail: str) -> None:
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
                self._rescan_pending = True
                return
            target = (
                self._refresh_owned_registry
                if self._owns_server
                else self._ensure_service
            )
            generation = self._generation
            cancel_event = self._cancel_event
            self._worker = threading.Thread(
                target=self._run_worker,
                args=(target, generation, cancel_event),
                name="kst-api-manager",
                daemon=True,
            )
            worker = self._worker
        worker.start()

    def _run_worker(
        self,
        target: Callable[[int, threading.Event], int | None],
        generation: int,
        cancel_event: threading.Event,
    ) -> None:
        delay_ms: int | None = None
        try:
            delay_ms = target(generation, cancel_event)
        finally:
            current = threading.current_thread()
            immediate = False
            with self._lock:
                if self._worker is current:
                    self._worker = None
                if self._is_active_locked(generation, cancel_event):
                    immediate = self._rescan_pending
                    self._rescan_pending = False
            if immediate:
                self._retry_requested.emit(0, generation)
            elif delay_ms is not None:
                self._retry_requested.emit(delay_ms, generation)

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
        self._emit_status(True, detail)
        if log_message:
            self._emit_log(log_message)
        return delay_ms

    @staticmethod
    def _safe_failure(
        error: BaseException | str,
    ) -> tuple[str, str]:
        text = str(error or "").strip()
        lowered = text.casefold()
        unsafe = (
            not text
            or "traceback" in lowered
            or "\n" in text
            or "\r" in text
            or any(
                f"{letter}:\\" in text or f"{letter}:/" in text
                for letter in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            )
        )
        if (
            "根目录无效" in text
            or "程序目录不具备读取能力" in text
            or "目录不具备商务通读取能力" in text
        ):
            return (
                "installation_root",
                "快商通客户端目录无效" if unsafe else text,
            )
        if "数据目录" in text and (
            "不具备读取能力" in text
            or "无法扫描" in text
            or "未找到" in text
        ):
            return (
                "data_root",
                "快商通数据目录无效" if unsafe else text,
            )
        if "客户端未运行" in text or "未检测到正在运行" in text:
            return "client_not_running", "客户端未运行"
        if "数据库结构不兼容" in text or (
            "数据库" in text and "不兼容" in text
        ):
            return "database_incompatible", "数据库结构不兼容"
        if (
            "address already in use" in lowered
            or "winerror 10048" in lowered
            or "端口" in text and ("占用" in text or "冲突" in text)
        ):
            return "port_in_use", "商务通本地 API 端口被占用"
        if (
            "身份" in text
            or "推广 id" in lowered
            or "绑定" in text
            or "映射" in text
        ):
            return (
                "identity_mapping",
                "快商通身份映射未就绪" if unsafe else text,
            )
        if unsafe:
            return "startup_failed", "商务通本地 API 启动失败"
        return f"startup:{text}", text

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
        self._emit_status(False, detail)
        if should_log:
            self._emit_log(detail)
        return delay_ms

    def _refresh_owned_registry(
        self,
        generation: int,
        cancel_event: threading.Event,
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
        if registry is not None and last_refresh is not None:
            age = now - last_refresh
            if 0 <= age < self._registry_refresh_interval_seconds:
                try:
                    health = registry.health()
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
            registry.refresh()
            health = registry.health()
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
            "商务通登录身份尚未就绪",
        )

    def _service_for(self, project_id: str, request_date: str) -> Any:
        with self._lock:
            registry = self._registry
        if registry is None:
            raise RuntimeError("KST identity registry is not ready")
        return registry.build_runtime(project_id, request_date).service

    def _registry_health(self) -> dict[str, Any]:
        with self._lock:
            registry = self._registry
        if registry is None:
            return {
                "status": "not_ready",
                "required_endpoints_available": False,
                "project_routing": True,
            }
        return registry.health()

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
    ) -> None:
        failure: BaseException | str = "商务通本地 API 已停止"
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
                self._emit_status(
                    False,
                    "现有商务通本地 API 已断开，正在重启",
                )

            registry = self._registry_factory(self._root)
            registry.refresh()
            health = registry.health()
            if not (
                health.get("status") == "ok"
                and health.get("required_endpoints_available") is True
            ):
                return self._record_failure(
                    generation,
                    cancel_event,
                    "商务通自动数据源尚未就绪",
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
                    self._ready = True
                    self._detail = (
                        f"商务通本地 API 已启动：127.0.0.1:{port}"
                    )
                    self._retry_index = 0
                    self._last_error_key = None
                    self._last_error_log_at = None
                    self._server_thread = threading.Thread(
                        target=self._serve_owned_server,
                        args=(server, generation, cancel_event),
                        name="kst-api-server",
                        daemon=True,
                    )
                    server_thread = self._server_thread
                    server_thread.start()
            if close_server:
                self._close_server_once(server)
                return None
            detail = f"商务通本地 API 已启动：127.0.0.1:{port}"
            self._emit_status(True, detail)
            self._emit_log("商务通本地 API 已随程序启动")
            return self._retry_intervals_ms[0]
        except Exception as exc:
            return self._record_failure(
                generation,
                cancel_event,
                exc,
            )
