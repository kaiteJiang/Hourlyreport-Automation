import json

import pytest

from modules.project_config import (
    get_project_kst_data_source,
    set_project_kst_data_source,
)


def _write_project(tmp_path, data_source="export"):
    configs = tmp_path / "configs"
    projects = configs / "projects"
    projects.mkdir(parents=True)
    (configs / "app_config.json").write_text(
        json.dumps(
            {
                "default_project_id": "kunming_niu",
                "projects_dir": "configs/projects",
                "secrets_file": "secrets/secrets.json",
            }
        ),
        encoding="utf-8",
    )
    project = {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "marker": {"keep": 1},
        "kst": {
            "data_source": data_source,
            "export_dir": "exports",
        },
    }
    (projects / "kunming_niu.json").write_text(
        json.dumps(project, ensure_ascii=False),
        encoding="utf-8",
    )
    return project


def test_set_project_kst_source_preserves_unrelated_fields(tmp_path):
    _write_project(tmp_path)

    saved = set_project_kst_data_source(tmp_path, "kunming_niu", "local_api")

    assert saved["kst"]["data_source"] == "local_api"
    assert saved["marker"] == {"keep": 1}
    assert get_project_kst_data_source(tmp_path, "kunming_niu") == "local_api"


def test_set_project_kst_source_rejects_unknown_mode(tmp_path):
    _write_project(tmp_path)

    with pytest.raises(ValueError, match="local_api.*export"):
        set_project_kst_data_source(tmp_path, "kunming_niu", "mixed")
