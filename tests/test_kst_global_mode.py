from __future__ import annotations

import json

import pytest

from modules.project_config import (
    build_runtime_config_from_project,
    get_kst_data_source,
    load_project_config,
    set_kst_data_source,
)


def _write_app_and_project(tmp_path, *, app_extra=None):
    configs = tmp_path / "configs"
    projects = configs / "projects"
    projects.mkdir(parents=True)
    app_config = {
        "default_project_id": "project_a",
        "projects_dir": "configs/projects",
        "secrets_file": "secrets/secrets.json",
        **(app_extra or {}),
    }
    (configs / "app_config.json").write_text(
        json.dumps(app_config, ensure_ascii=False),
        encoding="utf-8",
    )
    project_path = projects / "project_a.json"
    project_path.write_text(
        json.dumps(
            {
                "project_id": "project_a",
                "project_name": "项目 A",
                "excel": {"path": "report.xlsx"},
                "kst": {
                    "export_dir": "exports",
                    "data_source": "export",
                    "allow_zero_on_unavailable": False,
                },
                "baidu": {},
                "accounts": [],
                "hourly": {},
                "daily": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return project_path


def test_global_kst_mode_defaults_to_local_api(tmp_path):
    _write_app_and_project(tmp_path)

    assert get_kst_data_source(tmp_path) == "local_api"


def test_global_kst_mode_persists_without_changing_projects(tmp_path):
    project_path = _write_app_and_project(tmp_path)
    before = project_path.read_text(encoding="utf-8")

    assert set_kst_data_source(tmp_path, "export") == "export"

    assert get_kst_data_source(tmp_path) == "export"
    assert project_path.read_text(encoding="utf-8") == before


def test_global_kst_mode_rejects_unknown_value(tmp_path):
    _write_app_and_project(tmp_path)

    with pytest.raises(ValueError, match="local_api.*export"):
        set_kst_data_source(tmp_path, "mixed")


def test_runtime_uses_global_mode_for_every_project(tmp_path):
    _write_app_and_project(tmp_path)
    project = load_project_config(tmp_path, "project_a")

    runtime = build_runtime_config_from_project(project, {})

    assert runtime["kst"]["data_source"] == "local_api"
    assert runtime["kst"]["allow_zero_on_unavailable"] is True
