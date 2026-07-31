import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from modules.kst_local.discovery import (
    KstDiscoveryError,
    _client_process_running,
    discover_all_installations,
    discover_installation,
    discover_installations,
)
from modules.kst_local.machine_settings import save_kst_machine_settings


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


def test_environment_root_is_authoritative_instead_of_falling_back(
    tmp_path,
    monkeypatch,
):
    root, local_app_data = _build_installation(tmp_path)
    fallback_base = tmp_path / "fallback-program-files"
    fallback_root = (
        fallback_base / "KuaishangSoftx64" / "OnlineWebCSNew"
    )
    fallback_root.parent.mkdir(parents=True)
    root.rename(fallback_root)
    monkeypatch.setenv(
        "KST_INSTALLATION_ROOT",
        str(tmp_path / "explicit-missing"),
    )
    monkeypatch.setenv("ProgramFiles", str(fallback_base))
    monkeypatch.setenv("ProgramFiles(x86)", str(fallback_base))

    with pytest.raises(KstDiscoveryError, match="KST_INSTALLATION_ROOT"):
        discover_installations(local_app_data=local_app_data)


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

    with pytest.raises(KstDiscoveryError, match="正在运行") as captured:
        discover_installations(
            explicit_root=root,
            local_app_data=local_app_data,
            require_running_process=True,
            process_checker=lambda _electron: False,
        )

    assert captured.value.category == "client_not_running"


def test_discovery_reports_inactive_log_when_every_identity_is_stale(
    tmp_path,
):
    root, local_app_data = _build_installation(tmp_path)
    log_file = next(
        (local_app_data / "OnlineWebCSNew" / "log").rglob("app.log")
    )
    stale_time = time.time() - 3600
    os.utime(log_file, (stale_time, stale_time))

    with pytest.raises(KstDiscoveryError) as captured:
        discover_installations(
            explicit_root=root,
            local_app_data=local_app_data,
            active_within_seconds=300,
        )

    assert captured.value.category == "inactive_log"


def test_client_process_check_runs_without_a_console_window():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='"OnlineWebCS.exe","123"',
            stderr="",
        )

    assert _client_process_running(
        Path("OnlineWebCS.exe"),
        runner=runner,
    )
    _, kwargs = calls[0]
    if os.name == "nt":
        assert (
            kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        ) == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in kwargs


@pytest.mark.parametrize("select_child", [False, True])
def test_discover_all_uses_configured_electron_data_root(
    tmp_path,
    monkeypatch,
    select_child,
):
    selected_root = tmp_path / "selected"
    online_data_root = selected_root / "OnlineWebCSNew"
    online_data_root.mkdir(parents=True)
    save_kst_machine_settings(
        tmp_path,
        installation_root=None,
        data_root=(online_data_root if select_child else selected_root),
    )
    installation = SimpleNamespace(
        root=tmp_path / "program",
        identity="demo",
        client_family="electron",
    )
    received = []

    def fake_discover_installations(**kwargs):
        received.append(kwargs)
        return [installation]

    monkeypatch.setattr(
        "modules.kst_local.discovery.discover_installations",
        fake_discover_installations,
    )
    monkeypatch.setattr(
        "modules.kst_local.legacy_discovery.discover_legacy_installations",
        lambda **_kwargs: [],
    )

    found = discover_all_installations(
        tmp_path,
        require_running_process=False,
    )

    assert list(found) == [installation]
    assert received[0]["local_app_data"] == selected_root.resolve()
