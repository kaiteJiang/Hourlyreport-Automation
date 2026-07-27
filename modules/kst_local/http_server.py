from __future__ import annotations

import hmac
import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


ServiceFactory = Callable[[str, str], Any]
HealthProvider = Callable[[], dict[str, Any]]


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
            if parsed.path == "/health":
                try:
                    payload = (
                        health_provider()
                        if health_provider is not None
                        else {"status": "ok"}
                    )
                    self._send(200, payload)
                except Exception:
                    self._send(503, {"status": "not_ready"})
                return

            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return

            query = parse_qs(parsed.query)
            target_date = str(query.get("date", [date.today().isoformat()])[0])
            if parsed.path in {
                "/v1/kst/conversations",
                "/v1/kst/hourly",
            }:
                project_id = str(query.get("project_id", [""])[0]).strip()
                if not project_id:
                    self._send(400, {"error": "project_id_required"})
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
            except Exception:
                self._send(502, {"error": "kst_data_unavailable"})
                return
            self._send(404, {"error": "not_found"})

    return ThreadingHTTPServer((host, port), Handler)
