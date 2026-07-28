from pathlib import Path

from modules.kst_local.log_source import (
    IncrementalLogSnapshotCache,
    parse_log_snapshot,
)


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


def test_log_snapshot_whitelists_every_rec_id_in_one_push_batch(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        (
            '[2026-07-27 13:32:19] websocket '
            '{"msgType":48,"msgContent":[101,202,303]}'
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(log_dir, "2026-07-27")

    assert set(snapshot.sources_by_rec_id) == {"101", "202", "303"}
    assert all(
        source_names == frozenset({"websocket_msg_type_48"})
        for source_names in snapshot.sources_by_rec_id.values()
    )


def test_incremental_cache_reads_only_appended_log_bytes(tmp_path: Path):
    log = tmp_path / "app.log"
    first = (
        '[2026-07-27 09:00:00] websocket '
        '{"msgType":48,"msgContent":[101]}\n'
    )
    second = (
        '[2026-07-27 09:01:00] websocket '
        '{"msgType":48,"msgContent":[202]}\n'
    )
    log.write_text(first, encoding="utf-8")
    cache = IncrementalLogSnapshotCache()

    initial = cache.parse(tmp_path, "2026-07-27")
    before = cache.diagnostics()["bytes_read"]
    size_before = log.stat().st_size
    with log.open("a", encoding="utf-8") as stream:
        stream.write(second)
    appended_bytes = log.stat().st_size - size_before
    updated = cache.parse(tmp_path, "2026-07-27")

    assert set(initial.sources_by_rec_id) == {"101"}
    assert set(updated.sources_by_rec_id) == {"101", "202"}
    assert (
        cache.diagnostics()["bytes_read"] - before
        == appended_bytes
    )


def test_incremental_cache_rebuilds_after_log_truncation(tmp_path: Path):
    log = tmp_path / "app.log"
    log.write_text(
        (
            '[2026-07-27 09:00:00] websocket '
            '{"msgType":48,"msgContent":[101,102]}\n'
        ),
        encoding="utf-8",
    )
    cache = IncrementalLogSnapshotCache()
    cache.parse(tmp_path, "2026-07-27")

    log.write_text(
        (
            '[2026-07-27 09:01:00] websocket '
            '{"msgType":48,"msgContent":[202]}\n'
        ),
        encoding="utf-8",
    )
    rebuilt = cache.parse(tmp_path, "2026-07-27")

    assert set(rebuilt.sources_by_rec_id) == {"202"}
    assert cache.diagnostics()["full_rebuilds"] == 2


def test_incremental_cache_rebuilds_after_same_path_log_replacement(
    tmp_path: Path,
):
    log = tmp_path / "app.log"
    log.write_text(
        (
            '[2026-07-27 09:00:00] websocket '
            '{"msgType":48,"msgContent":[101]}\n'
        ),
        encoding="utf-8",
    )
    cache = IncrementalLogSnapshotCache()
    cache.parse(tmp_path, "2026-07-27")

    replacement = tmp_path / "replacement.log"
    replacement.write_text(
        (
            '[2026-07-27 09:01:00] websocket '
            '{"msgType":48,"msgContent":[202,203]}\n'
        ),
        encoding="utf-8",
    )
    replacement.replace(log)
    rebuilt = cache.parse(tmp_path, "2026-07-27")

    assert set(rebuilt.sources_by_rec_id) == {"202", "203"}
    assert cache.diagnostics()["full_rebuilds"] == 2


def test_log_snapshot_accepts_whitespace_around_push_message_type(
    tmp_path: Path,
):
    log = tmp_path / "app.log"
    log.write_text(
        (
            '[2026-07-27 09:00:00] websocket '
            '{"msgType": 48, "msgContent": [301]}\n'
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(tmp_path, "2026-07-27")

    assert set(snapshot.sources_by_rec_id) == {"301"}


def test_incremental_cache_evicts_old_date_entries(tmp_path: Path):
    (tmp_path / "app.log").write_text("", encoding="utf-8")
    cache = IncrementalLogSnapshotCache(max_entries=2)

    cache.parse(tmp_path, "2026-07-25")
    cache.parse(tmp_path, "2026-07-26")
    cache.parse(tmp_path, "2026-07-27")

    assert cache.diagnostics()["entry_count"] == 2


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


def test_log_snapshot_recovers_cached_tag_dictionary(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(
        (
            "[2026-07-27 08:00:00] "
            "https://chat.example/OnlineCore/nv/visitorCard/custTypeQuery.do "
            r'response {\"bean\":[{\"typeid\":11,\"typename\":\"有效-三句\"},'
            r'{\"typeid\":12,\"typename\":\"转潜-有效\"}]}'
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(log_dir, "2026-07-27")

    assert snapshot.tag_dictionary == {
        "11": "有效-三句",
        "12": "转潜-有效",
    }


def test_snapshot_reuses_historical_endpoint_urls_but_not_historical_auth(
    tmp_path: Path,
):
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(
            [
                '[2026-07-27 09:00:00] [Api] GlobalCommonParams {"query":{"old":"1"}}',
                '[2026-07-27 09:00:00] [Api] GlobalCommonHeaders {"clientToken":"old"}',
                "[2026-07-27 09:00:00] post https://old.example/OnlineHd/visitorInfo/load",
                "[2026-07-27 09:00:01] post https://old.example/OnlineCore/nv2012/func/ocVisitorCard/detail.do",
                "[2026-07-27 09:00:02] post https://old.example/OnlineCore/nv/visitorCard/custTypeQuery.do",
                '[2026-07-28 09:00:00] [Api] GlobalCommonParams {"query":{"today":"1"}}',
                '[2026-07-28 09:00:00] [Api] GlobalCommonHeaders {"clientToken":"today"}',
            ]
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(
        tmp_path,
        "2026-07-28",
        auth_date="2026-07-28",
    )

    assert snapshot.auth.common_query == {"today": "1"}
    assert snapshot.auth.headers == {"clientToken": "today"}
    assert set(snapshot.auth.endpoints) >= {
        "visitor_info",
        "visitor_card",
        "tag_dictionary",
    }


def test_current_endpoint_url_overrides_historical_url(tmp_path: Path):
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(
            [
                "[2026-07-28 09:00:00] post https://new.example/OnlineHd/visitorInfo/load",
                "[2026-07-27 09:00:00] post https://old.example/OnlineHd/visitorInfo/load",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = parse_log_snapshot(
        tmp_path,
        "2026-07-28",
        auth_date="2026-07-28",
    )

    assert snapshot.auth.endpoints["visitor_info"].startswith(
        "https://new.example/"
    )
