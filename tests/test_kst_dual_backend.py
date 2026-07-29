from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import modules.kst_local.backend as backend
from modules.kst_local.backend import (
    build_installation_runtime,
    installation_ready,
    installation_runtime_state,
    read_installation_promotion_ids,
)
from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.legacy_db_reader import KstLegacyDatabaseError
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstInstallation,
    LegacyKstInstallation,
)
from modules.kst_local.runtime import LegacyKstRuntime


@pytest.fixture
def electron_installation(tmp_path):
    root = tmp_path / "electron"
    return KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity="electron-id",
        log_dir=tmp_path / "electron-log",
        database_paths=(tmp_path / "electron-db" / "VISITOR.db",),
        sqlite_module_dir=root / "sqlite",
    )


@pytest.fixture
def legacy_installation(tmp_path):
    history_db = tmp_path / "legacy-db" / "synthetic_HIS.cdb"
    first_live_db = tmp_path / "legacy-db" / "first_CS.pdb"
    second_live_db = tmp_path / "legacy-db" / "second_CS.pdb"
    history_db.parent.mkdir(parents=True)
    with sqlite3.connect(history_db) as connection:
        connection.execute(
            "CREATE TABLE OC_HDVISITORINFO (recId TEXT)"
        )
    for path in (first_live_db, second_live_db):
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE DIALOGRECORD_VISITOR "
                "(recId TEXT, addTime TEXT)"
            )
    root = tmp_path / "legacy"
    return LegacyKstInstallation(
        root=root,
        executable=root / "OnlineCS.exe",
        version="7.03.17",
        identity="legacy-id",
        log_dir=tmp_path / "legacy-log",
        data_root=tmp_path / "legacy-db",
        history_db=history_db,
        message_database_paths=(first_live_db, second_live_db),
    )


def test_backend_dispatches_legacy_without_electron_snapshot(
    legacy_installation,
):
    runtime = build_installation_runtime(
        {
            "kst": {
                "promotion_id_accounts": {
                    "10001": "账户A",
                }
            }
        },
        "2026-07-29",
        installation=legacy_installation,
    )

    assert isinstance(runtime, LegacyKstRuntime)
    assert runtime.installation is legacy_installation
    health = runtime.health()
    assert health["status"] == "ok"
    assert health["read_only_database_available"] is True

    unavailable_runtime = LegacyKstRuntime(
        installation=replace(
            legacy_installation,
            history_db=legacy_installation.history_db.with_name(
                "missing_HIS.cdb"
            ),
        ),
        service=runtime.service,
    )
    unavailable = unavailable_runtime.health()
    assert unavailable["status"] == "not_ready"
    assert unavailable["required_endpoints_available"] is False
    assert unavailable["read_only_database_available"] is False


def test_backend_preserves_electron_builder(
    electron_installation,
    monkeypatch,
):
    sentinel = object()
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(),
    )
    monkeypatch.setattr(
        backend,
        "build_live_runtime",
        lambda *args, **kwargs: sentinel,
    )

    assert build_installation_runtime(
        {},
        "2026-07-29",
        installation=electron_installation,
        snapshot=snapshot,
    ) is sentinel


@pytest.mark.parametrize(
    ("installation_fixture", "reader_name", "expected"),
    [
        ("electron_installation", "read_identity_promotion_ids", {"10001"}),
        ("legacy_installation", "read_legacy_promotion_ids", {"20001"}),
    ],
)
def test_backend_dispatches_promotion_id_reader(
    request,
    monkeypatch,
    installation_fixture,
    reader_name,
    expected,
):
    installation = request.getfixturevalue(installation_fixture)
    monkeypatch.setattr(backend, reader_name, lambda _item: expected)

    assert read_installation_promotion_ids(installation) == expected


def test_legacy_readiness_is_true_only_when_read_only_reader_succeeds(
    legacy_installation,
    monkeypatch,
):
    monkeypatch.setattr(
        backend,
        "read_legacy_promotion_ids",
        lambda _item: set(),
    )
    assert installation_ready(
        legacy_installation,
        "2026-07-29",
    ) is True

    def fail(_item):
        raise KstLegacyDatabaseError("合成读取失败")

    monkeypatch.setattr(backend, "read_legacy_promotion_ids", fail)
    assert installation_ready(
        legacy_installation,
        "2026-07-29",
    ) is False


def test_legacy_runtime_state_tracks_every_database(
    legacy_installation,
):
    before = installation_runtime_state(
        legacy_installation,
        "2026-07-29",
    )

    assert before[0] == legacy_installation.client_family
    assert before[3] == (
        str(legacy_installation.history_db),
        *(
            str(path)
            for path in legacy_installation.message_database_paths
        ),
    )

    legacy_installation.message_database_paths[-1].write_bytes(
        b"changed-state"
    )
    after = installation_runtime_state(
        legacy_installation,
        "2026-07-29",
    )
    assert after != before


@pytest.mark.parametrize(
    "operation",
    [
        lambda item: read_installation_promotion_ids(item),
        lambda item: installation_ready(item, "2026-07-29"),
        lambda item: installation_runtime_state(item, "2026-07-29"),
        lambda item: build_installation_runtime(
            {},
            "2026-07-29",
            installation=item,
        ),
    ],
)
def test_backend_rejects_unknown_installation_types(operation):
    with pytest.raises(
        KstDiscoveryError,
        match="不支持的快商通客户端结构",
    ):
        operation(Path("unknown-client"))


def test_kst_local_package_exposes_dual_backend_entrypoints():
    import modules.kst_local as kst_local

    assert (
        kst_local.build_installation_runtime
        is build_installation_runtime
    )
    assert (
        kst_local.read_installation_promotion_ids
        is read_installation_promotion_ids
    )
    assert kst_local.LegacyKstRuntime is LegacyKstRuntime
