import sqlite3
import time

import pytest

from modules.kst_local import discovery
from modules.kst_local import legacy_discovery
from modules.kst_local.discovery import KstDiscoveryError, discover_all_installations
from modules.kst_local.legacy_discovery import discover_legacy_installations
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
        assert discover_legacy_installations(**kwargs) == []


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
        assert discover_legacy_installations(**kwargs) == []
