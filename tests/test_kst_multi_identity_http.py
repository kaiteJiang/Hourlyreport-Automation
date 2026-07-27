from __future__ import annotations

import json
import threading
import urllib.request

from modules.kst_local.http_server import create_server


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
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        responses = {}
        for project_id in ("project_a", "project_b", "project_c"):
            with urllib.request.urlopen(
                f"{base}/v1/kst/hourly?project_id={project_id}"
                "&date=2026-07-27&period=15%E7%82%B9",
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
