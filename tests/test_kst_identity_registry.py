from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

import pytest

import modules.kst_local.identity_registry as registry_module
from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.identity_registry import (
    KstIdentityMappingError,
    KstIdentityRegistry,
    build_project_promotion_index,
)
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstInstallation,
    LegacyKstInstallation,
)


def project(project_id, promotion_ids):
    return {
        "project_id": project_id,
        "project_name": project_id,
        "accounts": [
            {
                "standard_name": f"{project_id}-account",
                "kst_ids": list(promotion_ids),
            }
        ],
        "kst": {},
    }


class HealthyRuntime:
    service = object()

    def health(self):
        return {
            "status": "ok",
            "required_endpoints_available": True,
        }


def installation(tmp_path: Path, identity: str) -> KstInstallation:
    root = tmp_path / "app"
    return KstInstallation(
        root=root,
        electron=root / "OnlineWebCS.exe",
        version="9.86.21",
        identity=identity,
        log_dir=tmp_path / "log" / identity,
        database_paths=(tmp_path / "db" / identity / "VISITOR.db",),
        sqlite_module_dir=root / "resources" / "app" / "node_modules" / "sqlite",
    )


def legacy_installation(
    tmp_path: Path,
    identity: str,
) -> LegacyKstInstallation:
    root = tmp_path / "legacy-app"
    return LegacyKstInstallation(
        root=root,
        executable=root / "OnlineCS.exe",
        version="7.03.17",
        identity=identity,
        log_dir=tmp_path / "legacy-log" / identity,
        data_root=tmp_path / "legacy-db",
        history_db=tmp_path / "legacy-db" / f"{identity}_HIS.cdb",
        message_database_paths=(
            tmp_path / "legacy-db" / identity / "first_CS.pdb",
            tmp_path / "legacy-db" / identity / "second_CS.pdb",
        ),
    )


@pytest.mark.parametrize(
    ("common_query", "headers", "expected"),
    [
        ({}, {"X-Client": "desktop"}, False),
        ({"compId": "1"}, {}, False),
        ({"compId": "1"}, {"X-Client": "desktop"}, True),
    ],
)
def test_required_endpoints_also_require_current_auth(
    monkeypatch,
    tmp_path,
    common_query,
    headers,
    expected,
):
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
    monkeypatch.setattr(
        registry_module,
        "parse_cached_log_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    item = installation(tmp_path, "id-a")

    assert registry_module._required_endpoints_available(
        item,
        "2026-07-28",
    ) is expected


def registry_for(
    tmp_path,
    projects,
    identities,
    *,
    runtime_builder=None,
    endpoint_checker=lambda *_args: True,
    promotion_id_reader=None,
    promotion_cache_ttl_seconds=300,
    runtime_state_reader=None,
    runtime_cache_ttl_seconds=60,
    runtime_cache_max_entries=64,
    monotonic=None,
):
    installations = [
        installation(tmp_path, identity)
        for identity in identities
    ]
    ids_by_identity = {
        identity: set(ids)
        for identity, ids in identities.items()
    }
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if runtime_state_reader is not None:
        kwargs["runtime_state_reader"] = runtime_state_reader
    return KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: projects,
        installations_loader=lambda: installations,
        promotion_id_reader=promotion_id_reader or (
            lambda item: ids_by_identity[item.identity]
        ),
        project_loader=lambda _root, project_id: next(
            item for item in projects if item["project_id"] == project_id
        ),
        config_builder=lambda loaded, _base: {
            **loaded,
            "kst": {
                **loaded.get("kst", {}),
                "promotion_id_accounts": {},
            },
        },
        runtime_builder=runtime_builder or (
            lambda *_args, **_kwargs: HealthyRuntime()
        ),
        endpoint_checker=endpoint_checker,
        promotion_cache_ttl_seconds=promotion_cache_ttl_seconds,
        runtime_cache_ttl_seconds=runtime_cache_ttl_seconds,
        runtime_cache_max_entries=runtime_cache_max_entries,
        **kwargs,
    )


def test_duplicate_promotion_id_across_projects_is_rejected():
    with pytest.raises(KstIdentityMappingError, match="重复"):
        build_project_promotion_index(
            [
                project("a", ["1001"]),
                project("b", ["1001"]),
            ]
        )


def test_index_includes_accounts_nested_under_baidu_sources():
    nested = project("nested", [])
    nested["baidu_sources"] = [
        {
            "accounts": [
                {"standard_name": "source-account", "kst_ids": ["60001"]}
            ]
        }
    ]

    assert build_project_promotion_index([nested]) == {"60001": "nested"}


def test_three_identities_map_to_three_projects_by_promotion_id(tmp_path):
    registry = registry_for(
        tmp_path,
        projects=[
            project("a", ["10001"]),
            project("b", ["20001"]),
            project("c", ["30001"]),
        ],
        identities={
            "id-a": {"10001"},
            "id-b": {"20001"},
            "id-c": {"30001"},
        },
    )

    registry.refresh()

    assert registry.installation_for("a").identity == "id-a"
    assert registry.installation_for("b").identity == "id-b"
    assert registry.installation_for("c").identity == "id-c"
    assert registry.health()["bound_project_ids"] == ["a", "b", "c"]


@pytest.mark.parametrize(
    "identities",
    [
        {"id-a": {"10001", "20001"}},
        {"id-a": {"10001"}, "id-b": {"10001"}},
    ],
)
def test_ambiguous_identity_mapping_is_never_guessed(tmp_path, identities):
    registry = registry_for(
        tmp_path,
        projects=[
            project("a", ["10001"]),
            project("b", ["20001"]),
        ],
        identities=identities,
    )

    registry.refresh()

    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")


def test_registry_build_runtime_passes_only_bound_installation(tmp_path):
    calls = []
    sentinel = HealthyRuntime()
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda config, target_date, **kwargs: (
            calls.append((config["project_id"], target_date, kwargs["installation"].identity))
            or sentinel
        ),
    )
    registry.refresh()

    result = registry.build_runtime("a", "2026-07-27")

    assert result is sentinel
    assert calls[-1] == ("a", "2026-07-27", "id-a")


def test_registry_reuses_runtime_when_semantic_inputs_are_unchanged(tmp_path):
    now = [100.0]
    calls = []
    state = ["v1"]
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
        runtime_state_reader=lambda *_args: state[0],
        runtime_cache_ttl_seconds=60,
        monotonic=lambda: now[0],
    )
    registry.refresh()

    first = registry.build_runtime("a", "2026-07-27")
    second = registry.build_runtime("a", "2026-07-27")

    assert first is second
    assert calls == [1]


def test_registry_rebuilds_runtime_on_state_change_or_ttl_expiry(tmp_path):
    now = [100.0]
    calls = []
    state = ["v1"]
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
        runtime_state_reader=lambda *_args: state[0],
        runtime_cache_ttl_seconds=60,
        monotonic=lambda: now[0],
    )
    registry.refresh()

    first = registry.build_runtime("a", "2026-07-27")
    state[0] = "v2"
    second = registry.build_runtime("a", "2026-07-27")
    now[0] += 61
    third = registry.build_runtime("a", "2026-07-27")

    assert first is not second
    assert second is not third
    assert calls == [1, 1, 1]


def test_registry_evicts_old_runtime_dates(tmp_path):
    calls = []
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
        runtime_state_reader=lambda *_args: "stable",
        runtime_cache_max_entries=2,
    )
    registry.refresh()

    first = registry.build_runtime("a", "2026-07-25")
    registry.build_runtime("a", "2026-07-26")
    registry.build_runtime("a", "2026-07-27")
    rebuilt = registry.build_runtime("a", "2026-07-25")

    assert rebuilt is not first
    assert calls == [1, 1, 1, 1]


def test_registry_rebuilds_runtime_when_database_wal_changes(tmp_path):
    database = tmp_path / "db" / "id-a" / "VISITOR.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"database")
    calls = []
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
    )
    registry.refresh()

    first = registry.build_runtime("a", "2026-07-27")
    database.with_name(database.name + "-wal").write_bytes(b"new rows")
    second = registry.build_runtime("a", "2026-07-27")

    assert second is not first
    assert calls == [1, 1]


def test_registry_with_no_binding_is_not_healthy(tmp_path):
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"unknown": {"99999"}},
    )

    registry.refresh()

    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["required_endpoints_available"] is False


def test_binding_with_missing_required_endpoints_is_rejected(tmp_path):
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        endpoint_checker=lambda *_args: False,
    )

    registry.refresh()

    with pytest.raises(KstIdentityMappingError, match="接口"):
        registry.installation_for("a")


def test_health_diagnostics_expose_no_identity_or_promotion_ids(tmp_path):
    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"]), project("b", ["20001"])],
        identities={"private_identity": {"10001"}},
    )
    registry.refresh()

    health = registry.health()

    assert health == {
        "status": "ok",
        "required_endpoints_available": True,
        "project_routing": True,
        "identity_count": 1,
        "bound_project_ids": ["a"],
        "unbound_project_ids": ["b"],
        "mapping_error_count": 1,
    }
    assert "private_identity" not in repr(health)
    assert "10001" not in repr(health)


def test_registry_caches_promotion_ids_until_ttl_expires(tmp_path):
    now = [100.0]
    calls = []

    def promotion_id_reader(item):
        calls.append(item.identity)
        return {"10001"}

    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        promotion_id_reader=promotion_id_reader,
        promotion_cache_ttl_seconds=300,
        monotonic=lambda: now[0],
    )

    registry.refresh()
    registry.refresh()

    assert calls == ["id-a"]
    now[0] += 301
    registry.refresh()
    assert calls == ["id-a", "id-a"]


def test_registry_default_loader_uses_combined_discovery(
    tmp_path,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        registry_module,
        "discover_all_installations",
        lambda root, **kwargs: (
            calls.append((Path(root), kwargs))
            or []
        ),
    )
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [],
    )

    registry.refresh()

    assert calls == [
        (
            tmp_path,
            {
                "require_running_process": True,
                "cancel_event": None,
            },
        )
    ]


def test_registry_builds_legacy_without_electron_snapshot(tmp_path):
    item = legacy_installation(tmp_path, "legacy-id")
    snapshots = []
    runtime = object()
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project(
            "a",
            ["10001"],
        ),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **kwargs: (
            snapshots.append(("runtime", kwargs["snapshot"]))
            or runtime
        ),
        runtime_state_reader=lambda *_args: (
            snapshots.append(("state", _args[2]))
            or "legacy-state"
        ),
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()

    assert registry.build_runtime("a", "2026-07-29") is runtime
    assert snapshots == [
        ("state", None),
        ("runtime", None),
    ]


def test_registry_still_parses_electron_snapshot_before_build(
    tmp_path,
    monkeypatch,
):
    item = installation(tmp_path, "electron-id")
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={},
        auth=KstAuthContext(),
    )
    snapshots = []
    monkeypatch.setattr(
        registry_module,
        "parse_cached_log_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project(
            "a",
            ["10001"],
        ),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **kwargs: (
            snapshots.append(("runtime", kwargs["snapshot"]))
            or object()
        ),
        runtime_state_reader=lambda *_args: (
            snapshots.append(("state", _args[2]))
            or "electron-state"
        ),
        endpoint_checker=lambda *_args: True,
    )
    registry.refresh()

    registry.build_runtime("a", "2026-07-29")

    assert snapshots == [
        ("state", snapshot),
        ("runtime", snapshot),
    ]


def test_installation_cache_key_includes_family_and_all_databases(
    tmp_path,
):
    electron = installation(tmp_path, "shared-id")
    legacy = legacy_installation(tmp_path, "shared-id")

    electron_key = KstIdentityRegistry._installation_cache_key(
        electron
    )
    legacy_key = KstIdentityRegistry._installation_cache_key(legacy)

    assert electron_key[0] == "electron"
    assert legacy_key[0] == "legacy_java"
    assert legacy_key[-1] == (
        str(legacy.history_db),
        *(str(path) for path in legacy.message_database_paths),
    )


def test_legacy_health_and_business_build_recheck_liveness_each_time(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    active = [True]
    liveness_calls = []
    runtime_calls = []

    def check_liveness(installation, *, cancel_event=None):
        liveness_calls.append((installation.identity, cancel_event))
        if not active[0]:
            raise KstDiscoveryError(
                "客户端未运行",
                category="client_not_running",
            )
        return True

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project("a", ["10001"]),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **_kwargs: (
            runtime_calls.append(1) or HealthyRuntime()
        ),
        runtime_state_reader=lambda *_args, **_kwargs: "stable",
        endpoint_checker=lambda *_args: True,
        liveness_checker=check_liveness,
    )
    registry.refresh()
    liveness_calls.clear()

    assert registry.health()["status"] == "ok"
    assert registry.health()["status"] == "ok"
    assert len(liveness_calls) == 2

    active[0] = False
    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "client_not_running"
    with pytest.raises(KstDiscoveryError) as captured:
        registry.build_runtime("a", "2026-07-29")
    assert captured.value.category == "client_not_running"
    assert runtime_calls == []


def test_force_refresh_clears_promotion_and_runtime_caches_immediately(
    tmp_path,
):
    item = installation(tmp_path, "id-a")
    current_ids = [{"10001"}]
    reads = []
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [
            project("a", ["10001"]),
            project("b", ["20002"]),
        ],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: (
            reads.append(set(current_ids[0])) or set(current_ids[0])
        ),
        endpoint_checker=lambda *_args: True,
        promotion_cache_ttl_seconds=300,
    )

    registry.refresh()
    assert registry.installation_for("a") is item
    current_ids[0] = {"20002"}
    registry.refresh()
    assert registry.installation_for("a") is item
    assert reads == [{"10001"}]

    registry.refresh(force=True)

    assert registry.installation_for("b") is item
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")
    assert reads == [{"10001"}, {"20002"}]


def test_registry_refresh_and_runtime_forward_generation_cancellation(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    cancel_event = threading.Event()
    calls = []

    def load_installations(*, cancel_event=None):
        calls.append(("discover", cancel_event))
        return [item]

    def read_ids(installation, *, cancel_event=None):
        calls.append(("promotion", cancel_event))
        return {"10001"}

    def check_endpoint(installation, target_date, *, cancel_event=None):
        calls.append(("ready", cancel_event))
        return True

    def check_liveness(installation, *, cancel_event=None):
        calls.append(("liveness", cancel_event))
        return True

    def read_state(
        installation,
        target_date,
        snapshot,
        *,
        cancel_event=None,
    ):
        calls.append(("state", cancel_event))
        return "state"

    def build_runtime(
        config,
        target_date,
        *,
        installation,
        snapshot,
        cancel_event=None,
    ):
        calls.append(("runtime", cancel_event))
        return HealthyRuntime()

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=load_installations,
        promotion_id_reader=read_ids,
        project_loader=lambda _root, _project_id: project("a", ["10001"]),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=build_runtime,
        runtime_state_reader=read_state,
        endpoint_checker=check_endpoint,
        liveness_checker=check_liveness,
    )

    registry.refresh(cancel_event=cancel_event)
    registry.build_runtime(
        "a",
        "2026-07-29",
        cancel_event=cancel_event,
    )

    assert calls == [
        ("discover", cancel_event),
        ("promotion", cancel_event),
        ("ready", cancel_event),
        ("liveness", cancel_event),
        ("state", cancel_event),
        ("runtime", cancel_event),
    ]


def test_new_legacy_shard_invalidates_cached_runtime(tmp_path):
    item = legacy_installation(tmp_path, "legacy-id")
    item.history_db.parent.mkdir(parents=True)
    item.history_db.write_bytes(b"history")
    for path in item.message_database_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"live")
    calls = []
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project("a", ["10001"]),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **_kwargs: (
            calls.append(1) or HealthyRuntime()
        ),
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()

    first = registry.build_runtime("a", "2026-07-29")
    second = registry.build_runtime("a", "2026-07-29")
    assert second is first

    new_shard = (
        item.history_db.parent
        / "agent"
        / "07291300-onlie"
        / "new_CS.pdb"
    )
    new_shard.parent.mkdir(parents=True)
    with sqlite3.connect(new_shard) as connection:
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR (recId TEXT, addTime TEXT)"
        )

    third = registry.build_runtime("a", "2026-07-29")

    assert third is not first
    assert calls == [1, 1]
