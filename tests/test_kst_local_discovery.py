import json
import os
from pathlib import Path
import time

import pytest

from modules.kst_local.discovery import (
    KstDiscoveryError,
    discover_installation,
    discover_installations,
)


def _build_installation(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "custom" / "OnlineWebCSNew"
    app_dir = root / "resources" / "app"
    module_dir = app_dir / "node_modules" / "better-sqlite3-multiple-ciphers"
    module_dir.mkdir(parents=True)
    (root / "OnlineWebCS.exe").write_bytes(b"electron")
    (app_dir / "package.json").write_text(
        json.dumps({"name": "OnlineWebCSNew", "version": "9.86.21"}),
        encoding="utf-8",
    )

    local_app_data = tmp_path / "Local"
    identity = "733875_1269870"
    log_dir = local_app_data / "OnlineWebCSNew" / "log" / identity
    db_dir = local_app_data / "OnlineWebCSNew" / "db" / identity
    log_dir.mkdir(parents=True)
    db_dir.mkdir(parents=True)
    (log_dir / "app.log").write_text("ready", encoding="utf-8")
    (db_dir / "VISITOR.db").write_bytes(b"db")
    (db_dir / "VISITOR_20260726_090202.db").write_bytes(b"rotated")
    (db_dir / "VISITOR.db-wal").write_bytes(b"wal")
    return root, local_app_data


def test_explicit_root_discovers_current_identity_and_capabilities(tmp_path):
    root, local_app_data = _build_installation(tmp_path)

    found = discover_installation(
        explicit_root=root,
        local_app_data=local_app_data,
    )

    assert found.root == root.resolve()
    assert found.electron == (root / "OnlineWebCS.exe").resolve()
    assert found.version == "9.86.21"
    assert found.identity == "733875_1269870"
    assert found.log_dir == (
        local_app_data / "OnlineWebCSNew" / "log" / found.identity
    ).resolve()
    assert {path.name for path in found.database_paths} == {
        "VISITOR.db",
        "VISITOR_20260726_090202.db",
    }
    assert found.sqlite_module_dir.name == "better-sqlite3-multiple-ciphers"


def test_invalid_explicit_root_fails_instead_of_falling_back(tmp_path):
    with pytest.raises(KstDiscoveryError, match="显式配置"):
        discover_installation(
            explicit_root=tmp_path / "missing",
            local_app_data=tmp_path / "Local",
        )


def test_discover_installations_returns_every_identity(tmp_path):
    root, local_app_data = _build_installation(tmp_path)
    data_root = local_app_data / "OnlineWebCSNew"
    for identity in ("100_aaa", "200_bbb", "300_ccc"):
        log_dir = data_root / "log" / identity
        db_dir = data_root / "db" / identity
        log_dir.mkdir(parents=True)
        db_dir.mkdir(parents=True)
        (log_dir / "app.log").write_text("ready", encoding="utf-8")
        (db_dir / "VISITOR.db").write_bytes(b"db")

    found = discover_installations(
        explicit_root=root,
        local_app_data=local_app_data,
    )

    assert [item.identity for item in found] == [
        "100_aaa",
        "200_bbb",
        "300_ccc",
        "733875_1269870",
    ]


def test_discover_installations_excludes_historical_inactive_identity(tmp_path):
    root, local_app_data = _build_installation(tmp_path)
    stale_identity = "100_stale"
    log_dir = local_app_data / "OnlineWebCSNew" / "log" / stale_identity
    db_dir = local_app_data / "OnlineWebCSNew" / "db" / stale_identity
    log_dir.mkdir(parents=True)
    db_dir.mkdir(parents=True)
    log_file = log_dir / "app.log"
    log_file.write_text("old", encoding="utf-8")
    (db_dir / "VISITOR.db").write_bytes(b"db")
    stale_time = time.time() - 3600
    os.utime(log_file, (stale_time, stale_time))
    os.utime(log_dir, (stale_time, stale_time))

    found = discover_installations(
        explicit_root=root,
        local_app_data=local_app_data,
        active_within_seconds=300,
    )

    assert [item.identity for item in found] == ["733875_1269870"]


def test_active_identity_age_window_can_be_configured_by_environment(
    tmp_path,
    monkeypatch,
):
    root, local_app_data = _build_installation(tmp_path)
    log_file = next(
        (local_app_data / "OnlineWebCSNew" / "log").rglob("app.log")
    )
    old_time = time.time() - 600
    os.utime(log_file, (old_time, old_time))
    monkeypatch.setenv("KST_ACTIVE_LOG_MAX_AGE_SECONDS", "900")

    found = discover_installations(
        explicit_root=root,
        local_app_data=local_app_data,
    )

    assert len(found) == 1


def test_registry_discovery_can_require_running_client_process(tmp_path):
    root, local_app_data = _build_installation(tmp_path)

    with pytest.raises(KstDiscoveryError, match="正在运行"):
        discover_installations(
            explicit_root=root,
            local_app_data=local_app_data,
            require_running_process=True,
            process_checker=lambda _electron: False,
        )
