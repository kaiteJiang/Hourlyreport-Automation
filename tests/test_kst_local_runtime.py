from pathlib import Path

import pytest

from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstInstallation,
)
from modules.kst_local.runtime import KstLiveRuntime


@pytest.mark.parametrize(
    ("common_query", "headers", "expected"),
    [
        ({}, {"X-Client": "desktop"}, False),
        ({"compId": "1"}, {}, False),
        ({"compId": "1"}, {"X-Client": "desktop"}, True),
    ],
)
def test_runtime_health_requires_current_auth(
    tmp_path: Path,
    common_query,
    headers,
    expected,
):
    root = tmp_path / "app"
    installation = KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity="id-a",
        log_dir=tmp_path / "log" / "id-a",
        database_paths=(tmp_path / "db" / "id-a" / "VISITOR.db",),
        sqlite_module_dir=root / "sqlite",
    )
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(
            common_query=common_query,
            headers=headers,
            endpoints={
                "visitor_info": "https://example/visitor",
                "visitor_card": "https://example/card",
                "tag_dictionary": "https://example/tags",
            },
        ),
    )
    runtime = KstLiveRuntime(
        installation=installation,
        snapshot=snapshot,
        service=object(),
    )

    health = runtime.health()

    assert health["required_endpoints_available"] is expected
    assert health["status"] == ("ok" if expected else "not_ready")


def test_runtime_health_accepts_database_fallback_without_visitor_endpoint(
    tmp_path: Path,
):
    root = tmp_path / "app"
    installation = KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity="id-a",
        log_dir=tmp_path / "log" / "id-a",
        database_paths=(tmp_path / "db" / "id-a" / "VISITOR.db",),
        sqlite_module_dir=root / "sqlite",
    )
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(
            common_query={"compId": "1"},
            headers={"X-Client": "desktop"},
            endpoints={
                "visitor_card": "https://example/card",
                "tag_dictionary": "https://example/tags",
            },
        ),
    )
    runtime = KstLiveRuntime(
        installation=installation,
        snapshot=snapshot,
        service=object(),
    )

    assert runtime.health()["required_endpoints_available"] is True


def test_runtime_health_accepts_visitor_info_database_fallback(
    tmp_path: Path,
):
    root = tmp_path / "app"
    installation = KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity="id-a",
        log_dir=tmp_path / "log" / "id-a",
        database_paths=(tmp_path / "db" / "id-a" / "VISITOR.db",),
        sqlite_module_dir=root / "sqlite",
    )
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(
            common_query={"compId": "1"},
            headers={"X-Client": "desktop"},
            endpoints={
                "visitor_info": "https://example/visitor",
                "tag_dictionary": "https://example/tags",
            },
        ),
    )
    runtime = KstLiveRuntime(
        installation=installation,
        snapshot=snapshot,
        service=object(),
    )

    assert runtime.health()["required_endpoints_available"] is True
