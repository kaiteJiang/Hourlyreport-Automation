from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
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
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstInstallation,
    LegacyKstInstallation,
)
from modules.kst_local.runtime import LegacyKstRuntime


HISTORY_COLUMNS = (
    "recId TEXT",
    "curEnterTime TEXT",
    "diaStartTime TEXT",
    "visitorSendNum INTEGER",
    "visitorCustomField TEXT",
    "keyword TEXT",
    "bidWord TEXT",
    "talkGrade TEXT",
    "dialogClassification TEXT",
    "classifyTag TEXT",
    "cusTypeTag TEXT",
    "aiTags TEXT",
)


def _create_history_database(path, *, columns=HISTORY_COLUMNS) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE OC_HDVISITORINFO "
            f"({', '.join(columns)})"
        )
        connection.commit()


def _create_message_database(
    path,
    *,
    columns=("recId TEXT", "addTime TEXT"),
) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR "
            f"({', '.join(columns)})"
        )
        connection.commit()


def _insert_message(path, rec_id) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO DIALOGRECORD_VISITOR "
            "(recId, addTime) VALUES (?, ?)",
            (rec_id, "2026-07-29 09:10:01"),
        )
        connection.commit()


def _insert_history(path, rec_id) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO OC_HDVISITORINFO (
                recId, curEnterTime, diaStartTime, visitorSendNum,
                visitorCustomField, keyword, bidWord, talkGrade,
                dialogClassification, classifyTag, cusTypeTag, aiTags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                "2026-07-29 09:10:00",
                "",
                1,
                "推广 ID：10001",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ),
        )
        connection.commit()


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
    _create_history_database(history_db)
    for path in (first_live_db, second_live_db):
        _create_message_database(path)
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "OnlineCS.exe").write_bytes(b"MZ")
    log_dir = tmp_path / "legacy-log"
    log_dir.mkdir()
    (log_dir / "active.log").write_text("active", encoding="utf-8")
    return LegacyKstInstallation(
        root=root,
        executable=root / "OnlineCS.exe",
        version="7.03.17",
        identity="legacy-id",
        log_dir=log_dir,
        data_root=tmp_path / "legacy-db",
        history_db=history_db,
        message_database_paths=(first_live_db, second_live_db),
        promotion_ids=frozenset({"10001"}),
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
        process_paths=[legacy_installation.executable],
        now_timestamp=time.time(),
    )

    assert isinstance(runtime, LegacyKstRuntime)
    assert runtime.installation == legacy_installation
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
        ("legacy_installation", "inspect_legacy_read_capability", {"20001"}),
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
    monkeypatch.setattr(
        backend,
        reader_name,
        lambda _item, **_kwargs: expected,
    )
    if isinstance(installation, LegacyKstInstallation):
        installation = replace(installation, promotion_ids=None)

    assert read_installation_promotion_ids(installation) == expected


def test_legacy_readiness_rejects_complete_but_unbound_empty_databases(
    legacy_installation,
):
    empty = replace(legacy_installation, promotion_ids=None)

    with pytest.raises(Exception) as captured:
        read_installation_promotion_ids(empty)

    assert captured.value.category == "identity_mapping"


def test_legacy_runtime_builder_rejects_uninspected_empty_identity(
    legacy_installation,
):
    empty = replace(legacy_installation, promotion_ids=None)

    with pytest.raises(Exception) as captured:
        build_installation_runtime(
            {"kst": {"promotion_id_accounts": {"10001": "账户A"}}},
            "2026-07-29",
            installation=empty,
            process_paths=[empty.executable],
            now_timestamp=time.time(),
        )

    assert captured.value.category == "identity_mapping"


@pytest.mark.parametrize(
    "incomplete_case",
    [
        "missing_history_row",
        "duplicate_history_row",
    ],
)
def test_legacy_readiness_and_health_reject_incomplete_live_data(
    legacy_installation,
    incomplete_case,
):
    rec_id = "incomplete-live-rec-id"
    _insert_message(
        legacy_installation.message_database_paths[0],
        rec_id,
    )
    if incomplete_case == "duplicate_history_row":
        _insert_history(legacy_installation.history_db, rec_id)
        _insert_history(legacy_installation.history_db, rec_id)

    unchecked = replace(legacy_installation, promotion_ids=None)
    with pytest.raises(Exception):
        read_installation_promotion_ids(unchecked)
    health = LegacyKstRuntime(
        installation=unchecked,
        service=object(),
    ).health()
    assert health["status"] == "not_ready"
    assert health["read_only_database_available"] is False


@pytest.mark.parametrize(
    "invalid_case",
    [
        "missing_history",
        "corrupt_history",
        "missing_history_table",
        "missing_history_column",
        "missing_message_table",
        "missing_message_column",
    ],
)
def test_legacy_readiness_and_health_reject_invalid_declared_database(
    legacy_installation,
    tmp_path,
    invalid_case,
):
    invalid_path = tmp_path / f"{invalid_case}.db"
    invalid_installation = replace(legacy_installation, promotion_ids=None)
    if invalid_case == "missing_history":
        invalid_installation = replace(
            invalid_installation,
            history_db=invalid_path,
        )
    elif invalid_case == "corrupt_history":
        invalid_path.write_bytes(b"not-a-sqlite-database")
        invalid_installation = replace(
            invalid_installation,
            history_db=invalid_path,
        )
    elif invalid_case == "missing_history_table":
        with closing(sqlite3.connect(invalid_path)) as connection:
            connection.execute("CREATE TABLE OTHER_TABLE (id INTEGER)")
            connection.commit()
        invalid_installation = replace(
            invalid_installation,
            history_db=invalid_path,
        )
    elif invalid_case == "missing_history_column":
        _create_history_database(
            invalid_path,
            columns=HISTORY_COLUMNS[:-1],
        )
        invalid_installation = replace(
            invalid_installation,
            history_db=invalid_path,
        )
    elif invalid_case == "missing_message_table":
        with closing(sqlite3.connect(invalid_path)) as connection:
            connection.execute("CREATE TABLE OTHER_TABLE (id INTEGER)")
            connection.commit()
        invalid_installation = replace(
            invalid_installation,
            message_database_paths=(invalid_path,),
        )
    elif invalid_case == "missing_message_column":
        _create_message_database(
            invalid_path,
            columns=("recId TEXT",),
        )
        invalid_installation = replace(
            invalid_installation,
            message_database_paths=(invalid_path,),
        )

    with pytest.raises(Exception):
        read_installation_promotion_ids(invalid_installation)
    runtime = LegacyKstRuntime(
        installation=invalid_installation,
        service=object(),
    )
    health = runtime.health()
    assert health["status"] == "not_ready"
    assert health["read_only_database_available"] is False


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


def test_legacy_runtime_state_tracks_sidecars_and_newly_enumerated_shards(
    legacy_installation,
):
    before = installation_runtime_state(
        legacy_installation,
        "2026-07-29",
    )

    history_wal = legacy_installation.history_db.with_name(
        legacy_installation.history_db.name + "-wal"
    )
    history_wal.write_bytes(b"wal")
    with_wal = installation_runtime_state(
        legacy_installation,
        "2026-07-29",
    )
    assert with_wal != before

    new_shard = (
        legacy_installation.history_db.parent
        / "agent"
        / "07291200-onlie"
        / "new_CS.pdb"
    )
    new_shard.parent.mkdir(parents=True)
    _create_message_database(new_shard)
    with_new_shard = installation_runtime_state(
        legacy_installation,
        "2026-07-29",
    )

    assert with_new_shard != with_wal
    assert str(new_shard.resolve()) in with_new_shard[3]


def test_legacy_runtime_builder_reenumerates_shards_and_carries_cancel_event(
    legacy_installation,
):
    new_shard = (
        legacy_installation.history_db.parent
        / "agent"
        / "07291200-onlie"
        / "new_CS.pdb"
    )
    new_shard.parent.mkdir(parents=True)
    _create_message_database(new_shard)
    cancel_event = threading.Event()

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
        cancel_event=cancel_event,
        process_paths=[legacy_installation.executable],
        now_timestamp=time.time(),
    )

    assert new_shard.resolve() in runtime.installation.message_database_paths
    cancel_event.set()
    with pytest.raises(Exception, match="取消"):
        runtime.service.collect("2026-07-29")


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
