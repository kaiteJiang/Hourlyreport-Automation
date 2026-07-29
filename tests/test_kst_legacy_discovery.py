import sqlite3
import subprocess
import threading
import time

import pytest

from modules.kst_local import discovery
from modules.kst_local import legacy_discovery
from modules.kst_local.discovery import KstDiscoveryError, discover_all_installations
from modules.kst_local.legacy_discovery import (
    discover_legacy_installations,
    legacy_installation_active,
)
from modules.kst_local.machine_settings import save_kst_machine_settings


def sqlite_template(path, *, history_schema=False):
    connection = sqlite3.connect(path)
    try:
        if history_schema:
            connection.execute("CREATE TABLE OC_HDVISITORINFO (id INTEGER)")
        connection.commit()
    finally:
        connection.close()


def sqlite_history(path):
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE OC_HDVISITORINFO (
                recId TEXT,
                curEnterTime TEXT,
                diaStartTime TEXT,
                visitorSendNum INTEGER,
                visitorCustomField TEXT,
                keyword TEXT,
                bidWord TEXT,
                talkGrade TEXT,
                dialogClassification TEXT,
                classifyTag TEXT,
                cusTypeTag TEXT,
                aiTags TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def sqlite_messages(path):
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR (recId TEXT, addTime TEXT)"
        )
        connection.commit()
    finally:
        connection.close()


def make_legacy_tree(tmp_path):
    install = tmp_path / "OnlineCustomerService"
    data = tmp_path / "Documents" / "KuaiShangDataNew"
    (install / "config").mkdir(parents=True)
    (install / "OnlineCS.exe").write_bytes(b"MZ")
    sqlite_template(install / "config" / "DBCOMPANY.dll", history_schema=True)
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "260729090000.log").write_text("active", encoding="utf-8")
    sqlite_history(data / "db" / "company-a" / "company-a_HIS.cdb")
    sqlite_messages(
        data / "db" / "company-a" / "agent-a" / "07290900-onlie" / "agent-a_CS.pdb"
    )
    with sqlite3.connect(
        data / "db" / "company-a" / "agent-a" / "07290900-onlie" / "agent-a_CS.pdb"
    ) as connection:
        connection.execute(
            "INSERT INTO DIALOGRECORD_VISITOR (recId, addTime) VALUES (?, ?)",
            ("live-identity", "2026-07-29 09:00:01"),
        )
    with sqlite3.connect(
        data / "db" / "company-a" / "company-a_HIS.cdb"
    ) as connection:
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
                "live-identity",
                "2026-07-29 09:00:00",
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
    return install, data


def configure_automatic_legacy_candidates(
    monkeypatch,
    install,
    data,
):
    monkeypatch.setattr(
        legacy_discovery,
        "_legacy_root_candidates",
        lambda _paths: (install,),
    )
    monkeypatch.setattr(
        legacy_discovery,
        "_data_root_candidates",
        lambda: (data,),
    )


def test_legacy_discovery_requires_running_matching_onlinecs(tmp_path):
    install, data = make_legacy_tree(tmp_path)

    with pytest.raises(KstDiscoveryError) as captured:
        discover_legacy_installations(
            explicit_root=install,
            explicit_data_root=data,
            process_paths=[],
            now_timestamp=time.time(),
        )

    assert captured.value.category == "client_not_running"


def test_legacy_discovery_returns_each_capable_company_identity(tmp_path):
    install, data = make_legacy_tree(tmp_path)

    found = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
    )

    assert [(item.client_family, item.identity) for item in found] == [
        ("legacy_java", "company-a")
    ]
    assert found[0].history_db.name == "company-a_HIS.cdb"
    assert [path.name for path in found[0].message_database_paths] == [
        "agent-a_CS.pdb"
    ]
    assert found[0].promotion_ids == frozenset({"10001"})


@pytest.mark.parametrize("bad_kind", ["corrupt", "missing_rec_id", "missing_add_time"])
def test_legacy_discovery_rejects_entire_identity_when_any_shard_is_bad(
    tmp_path,
    bad_kind,
):
    install, data = make_legacy_tree(tmp_path)
    bad = (
        data
        / "db"
        / "company-a"
        / "agent-b"
        / "07290901-onlie"
        / "agent-b_CS.pdb"
    )
    bad.parent.mkdir(parents=True)
    if bad_kind == "corrupt":
        bad.write_bytes(b"not sqlite")
    else:
        column = "addTime TEXT" if bad_kind == "missing_rec_id" else "recId TEXT"
        with sqlite3.connect(bad) as connection:
            connection.execute(
                f"CREATE TABLE DIALOGRECORD_VISITOR ({column})"
            )

    with pytest.raises(KstDiscoveryError) as captured:
        discover_legacy_installations(
            explicit_root=install,
            explicit_data_root=data,
            process_paths=[install / "OnlineCS.exe"],
            now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
        )

    assert captured.value.category == "database_incompatible"
    assert "agent-b" not in str(captured.value)


def test_automatic_legacy_discovery_reports_bad_database_when_none_are_valid(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    bad = (
        data
        / "db"
        / "company-a"
        / "agent-b"
        / "07290901-onlie"
        / "agent-b_CS.pdb"
    )
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not sqlite")
    configure_automatic_legacy_candidates(
        monkeypatch,
        install,
        data,
    )

    with pytest.raises(KstDiscoveryError) as captured:
        discover_legacy_installations(
            process_paths=[install / "OnlineCS.exe"],
            now_timestamp=(
                data / "logs" / "260729090000.log"
            ).stat().st_mtime,
        )

    assert captured.value.category == "database_incompatible"
    assert "agent-b" not in str(captured.value)


def test_automatic_legacy_discovery_reports_client_not_running(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    configure_automatic_legacy_candidates(
        monkeypatch,
        install,
        data,
    )

    with pytest.raises(KstDiscoveryError) as captured:
        discover_legacy_installations(
            process_paths=[],
            now_timestamp=(
                data / "logs" / "260729090000.log"
            ).stat().st_mtime,
        )

    assert captured.value.category == "client_not_running"


def test_automatic_legacy_discovery_reports_inactive_log(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    configure_automatic_legacy_candidates(
        monkeypatch,
        install,
        data,
    )
    log_mtime = (
        data / "logs" / "260729090000.log"
    ).stat().st_mtime

    with pytest.raises(KstDiscoveryError) as captured:
        discover_legacy_installations(
            process_paths=[install / "OnlineCS.exe"],
            now_timestamp=log_mtime + 901,
        )

    assert captured.value.category == "inactive_log"


def test_automatic_legacy_discovery_reports_locked_database(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    configure_automatic_legacy_candidates(
        monkeypatch,
        install,
        data,
    )
    history_db = data / "db" / "company-a" / "company-a_HIS.cdb"
    locker = sqlite3.connect(history_db, timeout=0)
    try:
        locker.execute("BEGIN EXCLUSIVE")

        with pytest.raises(KstDiscoveryError) as captured:
            discover_legacy_installations(
                process_paths=[install / "OnlineCS.exe"],
                now_timestamp=(
                    data / "logs" / "260729090000.log"
                ).stat().st_mtime,
            )
    finally:
        locker.rollback()
        locker.close()

    assert captured.value.category == "database_busy_or_timeout"


def test_automatic_legacy_discovery_returns_good_identity_despite_bad_peer(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    bad_history = (
        data / "db" / "company-b" / "company-b_HIS.cdb"
    )
    sqlite_history(bad_history)
    bad_shard = (
        data
        / "db"
        / "company-b"
        / "agent-b"
        / "07290900-onlie"
        / "agent-b_CS.pdb"
    )
    bad_shard.parent.mkdir(parents=True)
    bad_shard.write_bytes(b"not sqlite")
    configure_automatic_legacy_candidates(
        monkeypatch,
        install,
        data,
    )

    found = discover_legacy_installations(
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(
            data / "logs" / "260729090000.log"
        ).stat().st_mtime,
    )

    assert [item.identity for item in found] == ["company-a"]


def test_legacy_identity_discovery_uses_one_absolute_five_second_deadline(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    deadlines = []
    monkeypatch.setattr(legacy_discovery.time, "monotonic", lambda: 100.0)

    def inspect_identity(installation, *, cancel_event, deadline):
        deadlines.append((installation.identity, cancel_event, deadline))
        return {"10001"}

    monkeypatch.setattr(
        legacy_discovery,
        "inspect_legacy_read_capability",
        inspect_identity,
    )
    cancel_event = threading.Event()

    found = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
        cancel_event=cancel_event,
    )

    assert len(found) == 1
    assert deadlines == [("company-a", cancel_event, 105.0)]


def test_legacy_liveness_rechecks_exact_process_path_and_recent_log(tmp_path):
    install, data = make_legacy_tree(tmp_path)
    now = (data / "logs" / "260729090000.log").stat().st_mtime
    item = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=now,
    )[0]

    assert legacy_installation_active(
        item,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=now,
    ) is True

    with pytest.raises(KstDiscoveryError) as stopped:
        legacy_installation_active(
            item,
            process_paths=[],
            now_timestamp=now,
        )
    assert stopped.value.category == "client_not_running"

    with pytest.raises(KstDiscoveryError) as mismatched:
        legacy_installation_active(
            item,
            process_paths=[tmp_path / "other" / "OnlineCS.exe"],
            now_timestamp=now,
        )
    assert mismatched.value.category == "client_path_mismatch"

    with pytest.raises(KstDiscoveryError) as inactive:
        legacy_installation_active(
            item,
            process_paths=[install / "OnlineCS.exe"],
            now_timestamp=now + 901,
        )
    assert inactive.value.category == "inactive_log"


def test_running_legacy_process_probe_uses_lightweight_native_path_reader(
    tmp_path,
    monkeypatch,
):
    expected = (tmp_path / "OnlineCS.exe",)
    monkeypatch.setattr(
        legacy_discovery,
        "_windows_onlinecs_process_paths",
        lambda: expected,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "liveness checks must not launch PowerShell"
        ),
    )

    assert legacy_discovery.running_kst_process_paths() == expected


def test_environment_legacy_root_is_explicit_and_never_sent_to_electron(
    tmp_path,
    monkeypatch,
):
    install, data = make_legacy_tree(tmp_path)
    monkeypatch.setenv("KST_INSTALLATION_ROOT", str(install))
    save_kst_machine_settings(
        tmp_path,
        installation_root=None,
        data_root=data,
    )
    electron_calls = []
    legacy_calls = []

    monkeypatch.setattr(
        discovery,
        "discover_installations",
        lambda **kwargs: electron_calls.append(kwargs) or [],
    )

    def fake_legacy(**kwargs):
        legacy_calls.append(kwargs)
        return [
            type(
                "LegacyInstallation",
                (),
                {
                    "client_family": "legacy_java",
                    "root": install.resolve(),
                    "identity": "legacy-id",
                },
            )()
        ]

    monkeypatch.setattr(
        legacy_discovery,
        "discover_legacy_installations",
        fake_legacy,
    )
    discover_all_installations(tmp_path, require_running_process=True)

    assert electron_calls == []
    assert legacy_calls[0]["explicit_root"] == install.resolve()


def test_machine_electron_root_overrides_legacy_environment_candidate(
    tmp_path,
    monkeypatch,
):
    electron_root = tmp_path / "OnlineWebCSNew"
    electron_root.mkdir()
    legacy_root, data = make_legacy_tree(tmp_path)
    save_kst_machine_settings(
        tmp_path,
        installation_root=electron_root,
        data_root=data,
    )
    monkeypatch.setenv("KST_INSTALLATION_ROOT", str(legacy_root))
    electron_item = type(
        "ElectronInstallation",
        (),
        {
            "root": electron_root,
            "identity": "electron-id",
        },
    )()
    monkeypatch.setattr(
        discovery,
        "discover_installations",
        lambda **_kwargs: [electron_item],
    )
    monkeypatch.setattr(
        legacy_discovery,
        "discover_legacy_installations",
        lambda **_kwargs: pytest.fail(
            "machine-selected Electron root must suppress legacy candidates"
        ),
    )

    assert discover_all_installations(
        tmp_path,
        require_running_process=True,
    ) == [electron_item]


def test_legacy_discovery_uses_injected_file_version_reader(tmp_path):
    install, data = make_legacy_tree(tmp_path)

    found = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
        version_reader=lambda _path: "7.03.17",
    )

    assert found[0].version == "7.03.17"


def test_legacy_discovery_keeps_capability_when_version_read_fails(tmp_path):
    install, data = make_legacy_tree(tmp_path)

    found = discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[install / "OnlineCS.exe"],
        now_timestamp=(data / "logs" / "260729090000.log").stat().st_mtime,
        version_reader=lambda _path: (_ for _ in ()).throw(OSError("unreadable")),
    )

    assert found[0].version == "unknown"


def test_explicit_legacy_root_fails_closed_when_capabilities_are_missing(tmp_path):
    with pytest.raises(KstDiscoveryError, match="旧版客户端"):
        discover_legacy_installations(
            explicit_root=tmp_path / "missing",
            explicit_data_root=tmp_path / "data",
            process_paths=[],
        )


def test_explicit_legacy_data_root_fails_closed_before_process_check(tmp_path):
    install, _ = make_legacy_tree(tmp_path)

    with pytest.raises(KstDiscoveryError, match="旧版客户端"):
        discover_legacy_installations(
            explicit_root=install,
            explicit_data_root=tmp_path / "missing-data",
            process_paths=[],
        )


def test_explicit_data_root_fails_closed_without_installation_candidates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(legacy_discovery, "_legacy_root_candidates", lambda _paths: ())

    with pytest.raises(KstDiscoveryError, match="旧版客户端"):
        discover_legacy_installations(
            explicit_data_root=tmp_path / "missing-data",
            process_paths=[],
            require_running_process=False,
        )


def test_discover_all_re_raises_invalid_explicit_legacy_installation(
    tmp_path,
    monkeypatch,
):
    install = tmp_path / "OnlineCustomerService"
    install.mkdir()
    (install / "OnlineCS.exe").write_bytes(b"MZ")
    save_kst_machine_settings(
        tmp_path,
        installation_root=install,
        data_root=None,
    )
    monkeypatch.setattr(discovery, "discover_installations", lambda **_kwargs: [])

    with pytest.raises(KstDiscoveryError, match="旧版客户端"):
        discover_all_installations(tmp_path, require_running_process=False)


@pytest.mark.parametrize("explicit", [True, False])
def test_legacy_scan_io_errors_fail_explicit_and_skip_automatic(
    tmp_path,
    monkeypatch,
    explicit,
):
    install, data = make_legacy_tree(tmp_path)
    original_iterdir = type(data / "db").iterdir

    def blocked_iterdir(path):
        if path == data / "db":
            raise OSError("denied")
        return original_iterdir(path)

    monkeypatch.setattr(type(data / "db"), "iterdir", blocked_iterdir)
    kwargs = {
        "process_paths": [install / "OnlineCS.exe"],
        "now_timestamp": (data / "logs" / "260729090000.log").stat().st_mtime,
    }
    if explicit:
        kwargs.update(explicit_root=install, explicit_data_root=data)
        with pytest.raises(KstDiscoveryError, match="旧版客户端"):
            discover_legacy_installations(**kwargs)
    else:
        configure_automatic_legacy_candidates(
            monkeypatch,
            install,
            data,
        )
        with pytest.raises(KstDiscoveryError) as captured:
            discover_legacy_installations(**kwargs)
        assert captured.value.category == "data_root"


@pytest.mark.parametrize("explicit", [True, False])
def test_legacy_message_scan_io_errors_fail_explicit_and_skip_automatic(
    tmp_path,
    monkeypatch,
    explicit,
):
    install, data = make_legacy_tree(tmp_path)
    company_dir = data / "db" / "company-a"
    original_rglob = type(company_dir).rglob

    def blocked_rglob(path, pattern):
        if path == company_dir:
            raise OSError("denied")
        return original_rglob(path, pattern)

    monkeypatch.setattr(type(company_dir), "rglob", blocked_rglob)
    kwargs = {
        "process_paths": [install / "OnlineCS.exe"],
        "now_timestamp": (data / "logs" / "260729090000.log").stat().st_mtime,
    }
    if explicit:
        kwargs.update(explicit_root=install, explicit_data_root=data)
        with pytest.raises(KstDiscoveryError, match="旧版客户端"):
            discover_legacy_installations(**kwargs)
    else:
        configure_automatic_legacy_candidates(
            monkeypatch,
            install,
            data,
        )
        with pytest.raises(KstDiscoveryError) as captured:
            discover_legacy_installations(**kwargs)
        assert captured.value.category == "data_root"


@pytest.mark.parametrize(
    ("electron_category", "legacy_category", "expected_category"),
    [
        (
            "inactive_log",
            "database_incompatible",
            "database_incompatible",
        ),
        (
            "database_incompatible",
            "database_busy_or_timeout",
            "database_busy_or_timeout",
        ),
    ],
)
def test_combined_automatic_discovery_propagates_most_specific_failure(
    tmp_path,
    monkeypatch,
    electron_category,
    legacy_category,
    expected_category,
):
    monkeypatch.delenv("KST_INSTALLATION_ROOT", raising=False)
    monkeypatch.setattr(
        discovery,
        "discover_installations",
        lambda **_kwargs: (_ for _ in ()).throw(
            KstDiscoveryError(
                "private stale Electron log",
                category=electron_category,
            )
        ),
    )
    monkeypatch.setattr(
        legacy_discovery,
        "discover_legacy_installations",
        lambda **_kwargs: (_ for _ in ()).throw(
            KstDiscoveryError(
                "private legacy SQL detail",
                category=legacy_category,
            )
        ),
    )

    with pytest.raises(KstDiscoveryError) as captured:
        discover_all_installations(
            tmp_path,
            require_running_process=True,
        )

    assert captured.value.category == expected_category
    assert "private" not in str(captured.value)
    assert "SQL" not in str(captured.value)
