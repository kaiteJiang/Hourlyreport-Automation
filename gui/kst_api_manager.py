from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from modules.kst_local.http_server import create_server
from modules.kst_local.runtime import build_live_runtime
from modules.project_config import (
    build_runtime_config_from_project,
    load_project_config,
)


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
        project_id: str = "kunming_niu",
        probe: Probe = probe_kst_health,
        server_factory: Callable[..., Any] = create_server,
        project_loader: Callable[..., dict[str, Any]] = load_project_config,
        config_builder: Callable[..., dict[str, Any]] = build_runtime_config_from_project,
        runtime_builder: Callable[..., Any] = build_live_runtime,
        retry_interval_ms: int = 15_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._project_id = project_id
        self._probe = probe
        self._server_factory = server_factory
        self._project_loader = project_loader
        self._config_builder = config_builder
        self._runtime_builder = runtime_builder
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._server: Any | None = None
        self._owns_server = False
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
            self._server = None
            self._owns_server = False
            self._ready = False
            self._detail = "商务通 API 已停止"
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        if (
            worker is not None
            and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=5)

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
        self.status_changed.emit(ready, detail)

    def _ensure_service_async(self) -> None:
        with self._lock:
            if self._stopping or self._ready:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._ensure_service,
                name="kst-api-manager",
                daemon=True,
            )
            worker = self._worker
        worker.start()

    @staticmethod
    def _probe_result(value: bool | tuple[bool, str]) -> tuple[bool, str]:
        if isinstance(value, tuple):
            return bool(value[0]), str(value[1])
        return bool(value), "检测到兼容的商务通本地 API"

    def _ensure_service(self) -> None:
        try:
            project = self._project_loader(self._root, self._project_id)
            config = self._config_builder(project, {})
            kst_config = config.get("kst", {}) or {}
            url = str(
                kst_config.get("local_api_url")
                or "http://127.0.0.1:18766"
            ).rstrip("/")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
                raise ValueError("商务通本地 API 必须使用 127.0.0.1")
            port = parsed.port or 18766
            token_env = str(
                kst_config.get("local_api_token_env")
                or "KST_LOCAL_API_TOKEN"
            )
            token = os.environ.get(token_env, "")
            healthy, detail = self._probe_result(self._probe(url, token))
            if healthy:
                self._publish(True, detail)
                self.log_message.emit("已复用现有商务通本地 API")
                return

            today = date.today().isoformat()
            runtime = self._runtime_builder(
                config,
                today,
                installation_root=kst_config.get("installation_root"),
            )

            def service_factory(request_date: str):
                return self._runtime_builder(
                    config,
                    request_date,
                    installation_root=kst_config.get("installation_root"),
                ).service

            server = self._server_factory(
                "127.0.0.1",
                port,
                service_factory=service_factory,
                health_provider=runtime.health,
                token=token,
            )
            with self._lock:
                if self._stopping:
                    server.server_close()
                    return
                self._server = server
                self._owns_server = True
            self._publish(True, f"商务通本地 API 已启动：127.0.0.1:{port}")
            self.log_message.emit("商务通本地 API 已随程序启动")
            server.serve_forever()
            with self._lock:
                unexpected_stop = not self._stopping
                if self._server is server:
                    self._server = None
                    self._owns_server = False
            if unexpected_stop:
                self._publish(False, "商务通本地 API 已停止，正在重试")
        except Exception:
            self._publish(False, "商务通本地 API 启动失败，正在重试")
            self.log_message.emit("商务通本地 API 暂不可用，将自动重试")
