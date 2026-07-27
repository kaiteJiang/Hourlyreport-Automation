import json
import threading
import urllib.error
import urllib.request

import pytest

from modules.kst_local.http_server import create_server
from modules.kst_local.models import KstConversation


class FakeService:
    def collect(self, target_date):
        return [
            KstConversation(
                rec_id="101",
                start_time=f"{target_date} 09:00:00",
                promotion_id="72828178",
                visitor_messages=2,
                tags=("有效-三句",),
                sources=frozenset({"websocket_msg_type_48"}),
            )
        ]

    def build_hourly_report(self, target_date, period):
        return {
            "date": target_date,
            "period": period,
            "source": "kst_local_api",
            "accounts": {"银康01": {"总对话": 1}},
        }


def _get(url, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_server_binds_loopback_authenticates_and_returns_safe_payload():
    server = create_server(
        "127.0.0.1",
        0,
        service_factory=lambda project_id, target_date: FakeService(),
        health_provider=lambda: {"status": "ok", "version": "9.86.21"},
        token="local-secret",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _get(f"{base}/health")
        assert status == 200
        assert health["status"] == "ok"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _get(f"{base}/v1/kst/conversations?date=2026-07-27")
        assert exc_info.value.code == 401

        status, conversations = _get(
            f"{base}/v1/kst/conversations?project_id=kunming_niu&date=2026-07-27",
            token="local-secret",
        )
        assert status == 200
        row = conversations["conversations"][0]
        assert row["rec_id"] == "101"
        assert not {
            "name",
            "phone",
            "wechat",
            "messages",
            "headers",
            "clientToken",
        }.intersection(row)

        status, hourly = _get(
            f"{base}/v1/kst/hourly?project_id=kunming_niu&date=2026-07-27&period=15%E7%82%B9",
            token="local-secret",
        )
        assert hourly["source"] == "kst_local_api"
        assert hourly["period"] == "15点"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_server_rejects_non_loopback_bind():
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(
            "0.0.0.0",
            18766,
            service_factory=lambda project_id, target_date: FakeService(),
        )


def test_hourly_endpoint_rejects_missing_project_id():
    server = create_server(
        "127.0.0.1",
        0,
        service_factory=lambda project_id, target_date: FakeService(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _get(f"{base}/v1/kst/hourly?date=2026-07-27")
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert payload == {"error": "project_id_required"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
