from __future__ import annotations

import hmac
import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from modules.kst_local.auth import local_health_proof


ServiceFactory = Callable[[str, str], Any]
HealthProvider = Callable[[], dict[str, Any]]

_SAFE_FAILURE_DETAILS = {
    "client_not_running": "客户端未运行",
    "client_path_mismatch": "客户端程序与运行进程不匹配",
    "inactive_log": "未检测到活动身份",
    "database_incompatible": "数据库结构不兼容",
    "database_busy_or_timeout": "数据库忙或读取超时",
    "identity_mapping": "快商通身份映射未就绪",
    "installation_root": "快商通客户端目录无效",
    "data_root": "快商通数据目录无效",
    "discovery_failed": "快商通客户端发现失败",
}


def create_server(
    host: str,
    port: int,
    *,
    service_factory: ServiceFactory,
    health_provider: HealthProvider | None = None,
    token: str = "",
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("商务通本地 API 只允许绑定 127.0.0.1")

    health_proof = local_health_proof(token)

    class Handler(BaseHTTPRequestHandler):
        server_version = "KstLocalApi/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            expected = f"Bearer {token}"
            actual = self.headers.get("Authorization", "")
            return hmac.compare_digest(actual, expected)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return

            if parsed.path == "/health":
                try:
                    payload = (
                        health_provider()
                        if health_provider is not None
                        else {"status": "ok"}
                    )
                    payload = dict(payload)
                    payload["auth_proof"] = health_proof
                    self._send(200, payload)
                except Exception:
                    self._send(503, {"status": "not_ready"})
                return

            query = parse_qs(parsed.query)
            target_date = str(query.get("date", [date.today().isoformat()])[0])
            if parsed.path in {
                "/v1/kst/conversations",
                "/v1/kst/hourly",
                "/v1/kst/daily",
            }:
                project_id = str(query.get("project_id", [""])[0]).strip()
                if not project_id:
                    self._send(400, {"error": "project_id_required"})
                    return
                try:
                    parsed_date = date.fromisoformat(target_date)
                except ValueError:
                    self._send(400, {"error": "invalid_date"})
                    return
                if parsed_date.isoformat() != target_date:
                    self._send(400, {"error": "invalid_date"})
                    return
            else:
                project_id = ""
            try:
                service = service_factory(project_id, target_date)
                if parsed.path == "/v1/kst/conversations":
                    conversations = [
                        item.safe_dict() for item in service.collect(target_date)
                    ]
                    self._send(
                        200,
                        {
                            "project_id": project_id,
                            "date": target_date,
                            "source": "kst_local_api",
                            "conversations": conversations,
                        },
                    )
                    return
                if parsed.path == "/v1/kst/hourly":
                    period = str(query.get("period", ["15点"])[0])
                    self._send(
                        200,
                        service.build_hourly_report(target_date, period),
                    )
                    return
                if parsed.path == "/v1/kst/daily":
                    self._send(
                        200,
                        service.build_daily_report(target_date),
                    )
                    return
            except Exception as exc:
                category = str(getattr(exc, "category", "")).strip()
                detail = _SAFE_FAILURE_DETAILS.get(category)
                payload = {"error": "kst_data_unavailable"}
                if detail:
                    payload.update(
                        {
                            "error_category": category,
                            "error_detail": detail,
                        }
                    )
                self._send(502, payload)
                return
            self._send(404, {"error": "not_found"})

    return ThreadingHTTPServer((host, port), Handler)
