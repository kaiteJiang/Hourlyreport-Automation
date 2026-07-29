import sqlite3
import time

import pytest

from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.legacy_discovery import discover_legacy_installations


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
        connection.execute("CREATE TABLE OC_HDVISITORINFO (id INTEGER)")
        connection.commit()
    finally:
        connection.close()


def sqlite_messages(path):
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE DIALOGRECORD_VISITOR (id INTEGER)")
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
    return install, data


def test_legacy_discovery_requires_running_matching_onlinecs(tmp_path):
    install, data = make_legacy_tree(tmp_path)

    assert discover_legacy_installations(
        explicit_root=install,
        explicit_data_root=data,
        process_paths=[],
        now_timestamp=time.time(),
    ) == []


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
