from pathlib import Path

from modules.kst_local.log_source import parse_log_snapshot


def test_log_snapshot_only_whitelists_automatic_sources(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        "\n".join(
            [
                '[2026-07-27 09:00:00] websocket {"msgType":48,"msgContent":["101"]}',
                '[2026-07-27 09:00:01] manual history response {"recId":999}',
                (
                    "[2026-07-27 09:00:02] "
                    "https://chat.example/OnlineCore/onlinecs/ocHistory/v2/"
                    'getLastVisitorList.do response {\\"recId\\":\\"202\\"}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(log_dir, "2026-07-27")

    assert set(snapshot.sources_by_rec_id) == {"101", "202"}
    assert snapshot.sources_by_rec_id["101"] == frozenset({"websocket_msg_type_48"})
    assert snapshot.sources_by_rec_id["202"] == frozenset({"startup_auto_sync"})
    assert "999" not in snapshot.sources_by_rec_id


def test_log_snapshot_discovers_auth_and_endpoints_without_safe_token_leak(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    secret = "top-secret-client-token"
    lines = [
        (
            '[2026-07-27 09:01:00] [Api] GlobalCommonParams '
            f'{{"query":{{"compId":"733875","clientToken":"{secret}"}}}}'
        ),
        (
            '[2026-07-27 09:01:01] [Api] GlobalCommonHeaders '
            f'{{"clientToken":"{secret}","X-Client":"desktop"}}'
        ),
        (
            "[2026-07-27 09:01:02] request "
            "https://chat.example/OnlineHd/visitorInfo/load"
        ),
        (
            "[2026-07-27 09:01:03] request "
            "https://chat.example/OnlineCore/nv2012/func/ocVisitorCard/detail.do"
        ),
        (
            "[2026-07-27 09:01:04] request "
            "https://chat.example/OnlineCore/nv/visitorCard/custTypeQuery.do"
        ),
    ]
    (log_dir / "app.log").write_text("\n".join(lines), encoding="utf-8")

    snapshot = parse_log_snapshot(log_dir, "2026-07-27")

    assert snapshot.auth.common_query["clientToken"] == secret
    assert snapshot.auth.headers["clientToken"] == secret
    assert snapshot.auth.endpoints["visitor_info"].endswith("/OnlineHd/visitorInfo/load")
    safe = snapshot.safe_diagnostics()
    assert secret not in str(safe)
    assert safe["auth"]["common_query_available"] is True
    assert safe["auth"]["headers_available"] is True
