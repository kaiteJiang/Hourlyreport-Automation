from __future__ import annotations

from pathlib import Path

import pytest

from modules.kst_local.identity_registry import (
    KstIdentityMappingError,
    KstIdentityRegistry,
    build_project_promotion_index,
)
from modules.kst_local.models import KstInstallation


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


def registry_for(tmp_path, projects, identities, *, runtime_builder=None):
    installations = [
        installation(tmp_path, identity)
        for identity in identities
    ]
    ids_by_identity = {
        identity: set(ids)
        for identity, ids in identities.items()
    }
    return KstIdentityRegistry(
        tmp_path,
        projects_loader=lambda _root: projects,
        installations_loader=lambda: installations,
        promotion_id_reader=lambda item: ids_by_identity[item.identity],
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
    class NotReadyRuntime(HealthyRuntime):
        def health(self):
            return {
                "status": "not_ready",
                "required_endpoints_available": False,
            }

    registry = registry_for(
        tmp_path,
        projects=[project("a", ["10001"])],
        identities={"id-a": {"10001"}},
        runtime_builder=lambda *_args, **_kwargs: NotReadyRuntime(),
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
