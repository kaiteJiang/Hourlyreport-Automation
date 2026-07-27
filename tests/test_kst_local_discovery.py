import json
from pathlib import Path

import pytest

from modules.kst_local.discovery import KstDiscoveryError, discover_installation


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
    assert found.database_paths == (
        (local_app_data / "OnlineWebCSNew" / "db" / found.identity / "VISITOR.db").resolve(),
    )
    assert found.sqlite_module_dir.name == "better-sqlite3-multiple-ciphers"


def test_invalid_explicit_root_fails_instead_of_falling_back(tmp_path):
    with pytest.raises(KstDiscoveryError, match="显式配置"):
        discover_installation(
            explicit_root=tmp_path / "missing",
            local_app_data=tmp_path / "Local",
        )
