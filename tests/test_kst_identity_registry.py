from __future__ import annotations

from dataclasses import replace
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


class DiagnosticInstallations(list):
    def __init__(self, values, diagnostics):
        super().__init__(values)
        self.diagnostics = tuple(diagnostics)


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


def materialize_legacy_database_files(
    item: LegacyKstInstallation,
) -> None:
    item.history_db.parent.mkdir(parents=True, exist_ok=True)
    item.history_db.write_bytes(b"history")
    for path in item.message_database_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"live")


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


@pytest.mark.parametrize(
    "mutation_stage",
    [
        "before_state",
        "after_state",
        "before_builder",
        "during_builder",
        "before_cached_return",
    ],
)
def test_runtime_capture_rejects_database_added_during_request(
    tmp_path,
    mutation_stage,
):
    item = installation(tmp_path, "id-a")
    database_dir = item.database_paths[0].parent
    database_dir.mkdir(parents=True)
    item.database_paths[0].write_bytes(b"database")
    new_database = database_dir / "VISITOR_2.db"
    control = {
        "armed": False,
        "mutate_on_monotonic": False,
    }
    state_paths = []
    builder_paths = []

    def add_database():
        if not new_database.exists():
            new_database.write_bytes(b"new database")

    def monotonic():
        if (
            control["armed"]
            and control["mutate_on_monotonic"]
        ):
            control["mutate_on_monotonic"] = False
            add_database()
        return 100.0

    def check_liveness(current, **_kwargs):
        if control["armed"] and mutation_stage == "before_state":
            add_database()
        return True

    def read_state(current, *_args, **_kwargs):
        state_paths.append(
            tuple(path.name for path in current.database_paths)
        )
        if control["armed"] and mutation_stage == "after_state":
            add_database()
        if (
            control["armed"]
            and mutation_stage == "before_cached_return"
        ):
            control["mutate_on_monotonic"] = True
        return "stable"

    def build_config(loaded, _base):
        if control["armed"] and mutation_stage == "before_builder":
            add_database()
        return loaded

    def build_runtime(*_args, **kwargs):
        builder_paths.append(
            tuple(
                path.name
                for path in kwargs["installation"].database_paths
            )
        )
        if control["armed"] and mutation_stage == "during_builder":
            add_database()
        return HealthyRuntime()

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project(
            "a",
            ["10001"],
        ),
        config_builder=build_config,
        runtime_builder=build_runtime,
        runtime_state_reader=read_state,
        endpoint_checker=lambda *_args: True,
        liveness_checker=check_liveness,
        monotonic=monotonic,
    )
    registry.refresh()
    if mutation_stage == "before_cached_return":
        registry.build_runtime("a", "2026-07-29")
    calls_before_failure = len(builder_paths)
    control["armed"] = True

    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")

    expected_discarded_builds = (
        1
        if mutation_stage == "during_builder"
        else 0
    )
    assert len(builder_paths) == (
        calls_before_failure + expected_discarded_builds
    )
    assert state_paths[-1] == ("VISITOR.db",)
    if expected_discarded_builds:
        assert builder_paths[-1] == ("VISITOR.db",)
    assert registry.health()["status"] == "not_ready"

    control["armed"] = False
    registry.refresh()
    stable_runtime = registry.build_runtime("a", "2026-07-29")

    assert stable_runtime is not None
    assert state_paths[-1] == ("VISITOR.db", "VISITOR_2.db")
    assert builder_paths[-1] == ("VISITOR.db", "VISITOR_2.db")


def test_legacy_runtime_capture_rejects_new_shard_during_state(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    materialize_legacy_database_files(item)
    new_shard = (
        item.history_db.parent
        / "agent-race"
        / "07290902-onlie"
        / "race_CS.pdb"
    )
    armed = [False]
    builder_paths = []

    def read_state(current, *_args, **_kwargs):
        if armed[0]:
            new_shard.parent.mkdir(parents=True)
            new_shard.write_bytes(b"new shard")
        return tuple(
            path.name for path in current.message_database_paths
        )

    def build_runtime(*_args, **kwargs):
        builder_paths.append(
            tuple(
                path.name
                for path in kwargs[
                    "installation"
                ].message_database_paths
            )
        )
        return HealthyRuntime()

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
        runtime_builder=build_runtime,
        runtime_state_reader=read_state,
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()
    armed[0] = True

    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")

    assert builder_paths == []
    armed[0] = False
    registry.refresh()
    registry.build_runtime("a", "2026-07-29")
    assert set(builder_paths[-1]) == {
        "first_CS.pdb",
        "second_CS.pdb",
        "race_CS.pdb",
    }


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


def test_registry_requires_refresh_when_electron_database_wal_changes(
    tmp_path,
):
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

    assert registry.health()["status"] == "not_ready"
    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-27")

    registry.refresh(force=True)
    second = registry.build_runtime("a", "2026-07-27")
    assert second is not first
    assert calls == [1, 1]


def test_electron_new_database_stales_only_its_identity_until_refresh(
    tmp_path,
):
    base = installation(tmp_path, "id-a")
    database_dir = base.database_paths[0].parent
    database_dir.mkdir(parents=True)
    base.database_paths[0].write_bytes(b"database")
    unrelated_dir = tmp_path / "db" / "id-b"
    unrelated_dir.mkdir(parents=True)
    (unrelated_dir / "VISITOR.db").write_bytes(b"other identity")
    promotion_reads = []
    runtime_paths = []

    def load_installations():
        paths = tuple(
            sorted(
                path.resolve()
                for path in database_dir.glob("VISITOR*.db")
                if path.is_file()
            )
        )
        return [replace(base, database_paths=paths)]

    def read_ids(item):
        promotion_reads.append(
            tuple(path.name for path in item.database_paths)
        )
        return {"10001"}

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=load_installations,
        promotion_id_reader=read_ids,
        project_loader=lambda _root, _project_id: project("a", ["10001"]),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **kwargs: (
            runtime_paths.append(
                tuple(
                    path.name
                    for path in kwargs["installation"].database_paths
                )
            )
            or HealthyRuntime()
        ),
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()
    first = registry.build_runtime("a", "2026-07-29")

    (unrelated_dir / "VISITOR_2.db").write_bytes(b"unrelated new db")
    assert registry.health()["status"] == "ok"

    (database_dir / "VISITOR_2.db").write_bytes(b"new db")
    assert registry.health()["status"] == "not_ready"
    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")

    registry.refresh()

    assert registry.health()["status"] == "ok"
    second = registry.build_runtime("a", "2026-07-29")
    assert second is not first
    assert promotion_reads[-1] == ("VISITOR.db", "VISITOR_2.db")
    assert runtime_paths[-1] == ("VISITOR.db", "VISITOR_2.db")


def test_electron_refresh_captures_new_database_with_static_discovery_result(
    tmp_path,
):
    item = installation(tmp_path, "id-a")
    database_dir = item.database_paths[0].parent
    database_dir.mkdir(parents=True)
    item.database_paths[0].write_bytes(b"database")
    promotion_reads = []

    def read_ids(current):
        promotion_reads.append(
            tuple(path.name for path in current.database_paths)
        )
        return {"10001"}

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: [item],
        promotion_id_reader=read_ids,
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()

    (database_dir / "VISITOR_2.db").write_bytes(b"new database")
    assert registry.health()["status"] == "not_ready"

    registry.refresh()

    bound = registry.installation_for("a")
    assert promotion_reads[-1] == ("VISITOR.db", "VISITOR_2.db")
    assert tuple(
        path.name for path in bound.database_paths
    ) == ("VISITOR.db", "VISITOR_2.db")


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


@pytest.mark.parametrize(
    "ordered_categories",
    [
        ("database_busy_or_timeout", "identity_mapping"),
        ("identity_mapping", "database_busy_or_timeout"),
    ],
)
def test_registry_candidate_failure_priority_is_order_independent(
    tmp_path,
    ordered_categories,
):
    items = [
        installation(tmp_path, f"id-{index}")
        for index in range(len(ordered_categories))
    ]
    category_by_identity = {
        item.identity: category
        for item, category in zip(items, ordered_categories)
    }

    def fail_promotion(item):
        raise KstDiscoveryError(
            f"private {item.identity} database path",
            category=category_by_identity[item.identity],
        )

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: items,
        promotion_id_reader=fail_promotion,
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )

    registry.refresh()

    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "database_busy_or_timeout"
    assert "private" not in repr(health)
    assert "database path" not in repr(health)


def test_registry_combines_discovery_diagnostics_with_candidate_failures(
    tmp_path,
):
    item = installation(tmp_path, "electron-id")
    installations = DiagnosticInstallations(
        [item],
        [
            KstDiscoveryError(
                r"private D:\legacy\locked.db",
                category="database_busy_or_timeout",
            )
        ],
    )
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: installations,
        promotion_id_reader=lambda _item: (_ for _ in ()).throw(
            KstDiscoveryError(
                "private electron promotion mismatch",
                category="identity_mapping",
            )
        ),
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )

    registry.refresh()

    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "database_busy_or_timeout"
    assert "private" not in repr(health)
    assert "legacy" not in repr(health)


def test_registry_ignores_bad_discovery_diagnostic_when_binding_is_good(
    tmp_path,
):
    item = installation(tmp_path, "electron-id")
    installations = DiagnosticInstallations(
        [item],
        [
            KstDiscoveryError(
                "private legacy database",
                category="database_busy_or_timeout",
            )
        ],
    )
    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=lambda: installations,
        promotion_id_reader=lambda _item: {"10001"},
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )

    registry.refresh()

    health = registry.health()
    assert health["status"] == "ok"
    assert "error_category" not in health
    assert registry.installation_for("a") is item


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


def test_fingerprint_change_bypasses_promotion_cache_without_force(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    materialize_legacy_database_files(item)
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
        liveness_checker=lambda *_args, **_kwargs: True,
        promotion_cache_ttl_seconds=300,
    )
    registry.refresh()
    assert registry.installation_for("a") is item

    current_ids[0] = {"20002"}
    item.history_db.with_name(
        item.history_db.name + "-wal"
    ).write_bytes(b"new identity rows")
    assert registry.health()["status"] == "not_ready"

    registry.refresh()

    assert registry.installation_for("b") is item
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")
    assert reads == [{"10001"}, {"20002"}]


def test_refresh_rejects_identity_changed_during_promotion_read(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    fingerprint = ["v1"]
    promotion_ids = [{"10001"}]
    mutate_during_first_read = [True]

    def read_ids(_item):
        result = set(promotion_ids[0])
        if mutate_during_first_read[0]:
            mutate_during_first_read[0] = False
            fingerprint[0] = "v2"
            promotion_ids[0] = {"20002"}
        return result

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [
            project("a", ["10001"]),
            project("b", ["20002"]),
        ],
        installations_loader=lambda: [item],
        promotion_id_reader=read_ids,
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
        identity_fingerprint_reader=lambda _item: fingerprint[0],
    )

    registry.refresh()
    fingerprint[0] = "v1"

    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "database_busy_or_timeout"
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")

    fingerprint[0] = "v2"
    registry.refresh()

    assert registry.health()["status"] == "ok"
    assert registry.installation_for("b") is item
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")


def test_refresh_failure_atomically_invalidates_old_binding_and_runtime(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    materialize_legacy_database_files(item)
    load_calls = []
    runtime_calls = []

    def load_installations():
        load_calls.append(1)
        if len(load_calls) > 1:
            raise KstDiscoveryError(
                "private locked database",
                category="database_busy_or_timeout",
            )
        return [item]

    registry = KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: [project("a", ["10001"])],
        installations_loader=load_installations,
        promotion_id_reader=lambda _item: {"10001"},
        project_loader=lambda _root, _project_id: project("a", ["10001"]),
        config_builder=lambda loaded, _base: loaded,
        runtime_builder=lambda *_args, **_kwargs: (
            runtime_calls.append(1) or HealthyRuntime()
        ),
        endpoint_checker=lambda *_args: True,
        liveness_checker=lambda *_args, **_kwargs: True,
    )
    registry.refresh()
    registry.build_runtime("a", "2026-07-29")

    with pytest.raises(KstDiscoveryError) as captured:
        registry.refresh(force=True)

    assert captured.value.category == "database_busy_or_timeout"
    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "database_busy_or_timeout"
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")
    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")
    assert runtime_calls == [1]


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


def test_missing_legacy_main_database_marks_binding_stale_and_rejects_build(
    tmp_path,
):
    item = legacy_installation(tmp_path, "legacy-id")
    materialize_legacy_database_files(item)
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
    item.history_db.unlink()

    health = registry.health()
    assert health["status"] == "not_ready"
    assert health["error_category"] == "identity_mapping"
    with pytest.raises(KstIdentityMappingError):
        registry.installation_for("a")
    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")
    assert first is not None
    assert calls == [1]


@pytest.mark.parametrize("mutation", ["wal", "new_shard"])
def test_legacy_identity_change_recovers_only_after_successful_refresh(
    tmp_path,
    mutation,
):
    item = legacy_installation(tmp_path, "legacy-id")
    materialize_legacy_database_files(item)
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

    if mutation == "wal":
        item.history_db.with_name(
            item.history_db.name + "-wal"
        ).write_bytes(b"new rows")
    else:
        new_shard = (
            item.history_db.parent
            / "agent"
            / "07291300-onlie"
            / "new_CS.pdb"
        )
        new_shard.parent.mkdir(parents=True)
        with sqlite3.connect(new_shard) as connection:
            connection.execute(
                "CREATE TABLE DIALOGRECORD_VISITOR "
                "(recId TEXT, addTime TEXT)"
            )

    assert registry.health()["status"] == "not_ready"
    with pytest.raises(KstIdentityMappingError):
        registry.build_runtime("a", "2026-07-29")

    registry.refresh()

    assert registry.health()["status"] == "ok"
    second = registry.build_runtime("a", "2026-07-29")
    assert second is not first
    assert calls == [1, 1]
