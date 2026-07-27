from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from modules.kst_local.http_server import create_server
from modules.kst_local.identity_registry import KstIdentityRegistry


Probe = Callable[[str, str], bool | tuple[bool, str]]


def probe_kst_health(
    url: str,
    token: str,
    timeout: float = 1.5,
) -> tuple[bool, str]:
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
    ):
        return True, "127.0.0.1 本地 API 正常"
    return False, "商务通自动数据源尚未就绪"


class KstApiManager(QObject):
    status_changed = Signal(bool, str)
    log_message = Signal(str)

    def __init__(
        self,
        root: str | Path,
        *,
        probe: Probe = probe_kst_health,
        server_factory: Callable[..., Any] = create_server,
        registry_factory: Callable[[Path], Any] = KstIdentityRegistry,
        retry_interval_ms: int = 15_000,
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
        self._ready = False
        self._detail = "商务通 API 未启动"
        self._started = False
        self._stopping = False
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(max(10, int(retry_interval_ms)))
        self._retry_timer.timeout.connect(self._ensure_service_async)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._retry_timer.start()
        self._ensure_service_async()

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._started = False
        self._retry_timer.stop()
        with self._lock:
            server = self._server if self._owns_server else None
            worker = self._worker
            server_thread = self._server_thread
            self._external_server = False
            self._ready = False
            self._detail = "商务通 API 已停止"
        if server is not None:
            server.shutdown()
        if (
            worker is not None
            and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=5)
        if (
            server_thread is not None
            and server_thread.is_alive()
            and server_thread is not threading.current_thread()
        ):
            server_thread.join(timeout=5)
        close_fallback = False
        with self._lock:
            if self._server is server and server is not None:
                self._server = None
                self._server_thread = None
                self._registry = None
                self._owns_server = False
                close_fallback = True
            elif server is None:
                self._server = None
                self._server_thread = None
                self._registry = None
                self._owns_server = False
        if close_fallback:
            server.server_close()

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def owns_server(self) -> bool:
        with self._lock:
            return self._owns_server

    def status_detail(self) -> str:
        with self._lock:
            return self._detail

    def _publish(self, ready: bool, detail: str) -> None:
        with self._lock:
            if self._stopping:
                return
            self._ready = ready
            self._detail = detail
        try:
            self.status_changed.emit(ready, detail)
        except RuntimeError:
            pass

    def _emit_log(self, message: str) -> None:
        try:
            self.log_message.emit(message)
        except RuntimeError:
            pass

    def _ensure_service_async(self) -> None:
        with self._lock:
            if self._stopping:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            target = (
                self._refresh_owned_registry
                if self._owns_server
                else self._ensure_service
            )
            self._worker = threading.Thread(
                target=target,
                name="kst-api-manager",
                daemon=True,
            )
            worker = self._worker
        worker.start()

    def _refresh_owned_registry(self) -> None:
        with self._lock:
            if self._stopping or not self._owns_server:
                return
            registry = self._registry
        try:
            if registry is None:
                registry = self._registry_factory(self._root)
            registry.refresh()
            health = registry.health()
            ready = (
                health.get("status") == "ok"
                and health.get("required_endpoints_available") is True
            )
        except Exception:
            registry = None
            ready = False
        with self._lock:
            if self._stopping or not self._owns_server:
                return
            self._registry = registry if ready else None
        if ready:
            self._publish(True, "商务通本地 API 正常：127.0.0.1:18766")
        else:
            self._publish(
                False,
                "商务通登录身份尚未就绪，正在重试",
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

    def _serve_owned_server(self, server: Any) -> None:
        try:
            server.serve_forever()
        except Exception:
            pass
        finally:
            with self._lock:
                unexpected_stop = not self._stopping
                if self._server is server:
                    self._server = None
                    self._server_thread = None
                    self._registry = None
                    self._owns_server = False
                    self._external_server = False
                    server.server_close()
        if unexpected_stop:
            self._publish(False, "商务通本地 API 已停止，正在重试")

    @staticmethod
    def _probe_result(value: bool | tuple[bool, str]) -> tuple[bool, str]:
        if isinstance(value, tuple):
            return bool(value[0]), str(value[1])
        return bool(value), "检测到兼容的商务通本地 API"

    def _ensure_service(self) -> None:
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
            token = os.environ.get("KST_LOCAL_API_TOKEN", "")
            healthy, detail = self._probe_result(self._probe(url, token))
            if healthy:
                with self._lock:
                    already_external = (
                        self._ready and self._external_server
                    )
                    self._external_server = True
                if not already_external:
                    self._publish(True, detail)
                    self._emit_log("已复用现有商务通本地 API")
                return
            with self._lock:
                lost_external = self._ready and self._external_server
                self._external_server = False
            if lost_external:
                self._publish(False, "现有商务通本地 API 已断开，正在重启")

            registry = self._registry_factory(self._root)
            registry.refresh()
            health = registry.health()
            if not (
                health.get("status") == "ok"
                and health.get("required_endpoints_available") is True
            ):
                self._publish(
                    False,
                    "商务通自动数据源尚未就绪，正在重试",
                )
                return

            server = self._server_factory(
                "127.0.0.1",
                port,
                service_factory=self._service_for,
                health_provider=self._registry_health,
                token=token,
            )
            with self._lock:
                if self._stopping:
                    server.server_close()
                    return
                self._server = server
                self._registry = registry
                self._owns_server = True
                self._external_server = False
                self._server_thread = threading.Thread(
                    target=self._serve_owned_server,
                    args=(server,),
                    name="kst-api-server",
                    daemon=True,
                )
                server_thread = self._server_thread
            self._publish(True, f"商务通本地 API 已启动：127.0.0.1:{port}")
            self._emit_log("商务通本地 API 已随程序启动")
            server_thread.start()
        except Exception:
            self._publish(False, "商务通本地 API 启动失败，正在重试")
            self._emit_log("商务通本地 API 暂不可用，将自动重试")
