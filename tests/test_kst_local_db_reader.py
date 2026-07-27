import json
import os
import subprocess
from pathlib import Path

import pytest

from modules.kst_local.db_reader import (
    KstDatabaseError,
    read_cache_candidates,
    read_identity_promotion_ids,
)
from modules.kst_local.models import KstInstallation


def _installation(tmp_path: Path) -> KstInstallation:
    root = tmp_path / "OnlineWebCSNew"
    electron = root / "OnlineWebCS.exe"
    sqlite_module = (
        root
        / "resources"
        / "app"
        / "node_modules"
        / "better-sqlite3-multiple-ciphers"
    )
    log_dir = tmp_path / "Local" / "OnlineWebCSNew" / "log" / "733875_1269870"
    db_a = tmp_path / "Local" / "OnlineWebCSNew" / "db" / "733875_1269870" / "VISITOR.db"
    db_b = db_a.with_name("VISITOR-backup.db")
    for path in (electron, db_a, db_b):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    sqlite_module.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    return KstInstallation(
        root=root,
        electron=electron,
        version="9.86.21",
        identity="733875_1269870",
        log_dir=log_dir,
        database_paths=(db_a, db_b),
        sqlite_module_dir=sqlite_module,
    )


def test_reader_runs_client_electron_in_node_mode_and_deduplicates(tmp_path):
    installation = _installation(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        database = Path(command[3])
        promotion_id = "72828178" if database.name == "VISITOR.db" else ""
        tag_ids = '{"1":1}' if database.name != "VISITOR.db" else ""
        payload = {
            "safeRows": [
                {
                    "recId": 101,
                    "startTime": "2026-07-27 09:00:00",
                    "visitorMessages": 2,
                    "visitorType": "WEB",
                    "channelType": 1,
                    "promotionId": promotion_id,
                    "tagIds": tag_ids,
                    "keyword": "",
                    "bidWord": "",
                },
                {
                    "recId": 999,
                    "visitorType": "APP",
                    "channelType": 2,
                    "promotionId": "ignore",
                },
            ]
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    rows = read_cache_candidates(
        installation,
        "2026-07-27",
        runner=runner,
    )

    assert len(calls) == 2
    for command, kwargs in calls:
        assert command[0] == str(installation.electron)
        assert command[2] == installation.identity
        assert command[4] == "2026-07-27"
        assert command[5] == str(installation.sqlite_module_dir)
        assert kwargs["env"]["ELECTRON_RUN_AS_NODE"] == "1"
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        if os.name == "nt":
            assert (
                kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
            ) == subprocess.CREATE_NO_WINDOW
        else:
            assert "creationflags" not in kwargs
    assert len(rows) == 1
    assert rows[0].rec_id == "101"
    assert rows[0].promotion_id in {"", "72828178"}


def test_reader_rejects_invalid_bridge_output(tmp_path):
    installation = _installation(tmp_path)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    with pytest.raises(KstDatabaseError, match="JSON"):
        read_cache_candidates(installation, "2026-07-27", runner=runner)


def test_javascript_bridge_is_readonly_and_contains_no_mutation_sql():
    bridge = (
        Path(__file__).parents[1]
        / "modules"
        / "kst_local"
        / "resources"
        / "read_visitor_db.js"
    )
    source = bridge.read_text(encoding="utf-8")
    normalized = source.upper()

    assert "READONLY: TRUE" in normalized
    assert "FILEMUSTEXIST: TRUE" in normalized
    assert "INSERT " not in normalized
    assert "UPDATE " not in normalized
    assert "DELETE " not in normalized


def test_read_identity_promotion_ids_merges_all_rotated_databases(tmp_path):
    installation = _installation(tmp_path)
    calls = []
    outputs = iter(
        [
            {"promotionIds": ["72828178", "72828179"]},
            {"promotionIds": ["72828179", "81509165"]},
        ]
    )

    def runner(command, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(next(outputs)),
            stderr="",
        )

    result = read_identity_promotion_ids(installation, runner=runner)

    assert result == {"72828178", "72828179", "81509165"}
    if os.name == "nt":
        assert all(
            (kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            == subprocess.CREATE_NO_WINDOW
            for kwargs in calls
        )
    else:
        assert all("creationflags" not in kwargs for kwargs in calls)


def test_promotion_id_bridge_is_readonly_and_returns_no_visitor_fields():
    bridge = (
        Path(__file__).parents[1]
        / "modules"
        / "kst_local"
        / "resources"
        / "read_promotion_ids.js"
    )
    source = bridge.read_text(encoding="utf-8")
    normalized = source.upper()

    assert "READONLY: TRUE" in normalized
    assert "FILEMUSTEXIST: TRUE" in normalized
    assert "INSERT " not in normalized
    assert "UPDATE " not in normalized
    assert "DELETE " not in normalized
    assert "SAFEROWS" not in normalized
