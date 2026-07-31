from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from modules.kst_local.http_server import create_server
from modules.kst_local.identity_registry import KstIdentityMappingError

TEST_TOKEN = "multi-identity-token-with-more-than-32-characters"


class ProjectService:
    def __init__(self, project_id):
        self.project_id = project_id

    def build_hourly_report(self, target_date, period):
        return {
            "project_id": self.project_id,
            "date": target_date,
            "period": period,
            "accounts": {f"sentinel-{self.project_id}": {"总对话": 1}},
        }


def test_three_projects_are_routed_without_cross_read():
    calls = []
    server = create_server(
        "127.0.0.1",
        0,
        service_factory=lambda project_id, target_date: (
            calls.append((project_id, target_date))
            or ProjectService(project_id)
        ),
        token=TEST_TOKEN,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        responses = {}
        for project_id in ("project_a", "project_b", "project_c"):
            request = urllib.request.Request(
                f"{base}/v1/kst/hourly?project_id={project_id}"
                "&date=2026-07-27&period=15%E7%82%B9",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            )
            with urllib.request.urlopen(
                request,
                timeout=3,
            ) as response:
                responses[project_id] = json.loads(
                    response.read().decode("utf-8")
                )

        assert calls == [
            ("project_a", "2026-07-27"),
            ("project_b", "2026-07-27"),
            ("project_c", "2026-07-27"),
        ]
        for project_id, payload in responses.items():
            assert payload["project_id"] == project_id
            assert list(payload["accounts"]) == [f"sentinel-{project_id}"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_unbound_project_returns_safe_identity_mapping_error():
    private_detail = "项目 private_identity 无法绑定推广 ID 12345678"

    def fail_service(_project_id, _target_date):
        raise KstIdentityMappingError(private_detail)

    server = create_server(
        "127.0.0.1",
        0,
        service_factory=fail_service,
        token=TEST_TOKEN,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    request = urllib.request.Request(
        f"{base}/v1/kst/hourly?project_id=missing"
        "&date=2026-07-31&period=15%E7%82%B9",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    )
    try:
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
        else:
            raise AssertionError("未绑定项目必须返回 HTTP 错误")

        assert payload == {
            "error": "kst_data_unavailable",
            "error_category": "identity_mapping",
            "error_detail": "快商通身份映射未就绪",
        }
        assert private_detail not in json.dumps(payload, ensure_ascii=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
