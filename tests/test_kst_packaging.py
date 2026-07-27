import json
from pathlib import Path

from tools.build_desktop_exe import source_fingerprint


def test_desktop_spec_packages_kst_database_bridge():
    root = Path(__file__).resolve().parents[1]
    spec_source = (
        root / "tools" / "hourlyreport_automation.spec"
    ).read_text(encoding="utf-8")

    assert "read_visitor_db.js" in spec_source
    assert "read_promotion_ids.js" in spec_source
    assert "modules/kst_local/resources" in spec_source.replace("\\", "/")


def test_desktop_fingerprint_changes_when_kst_bridge_changes(tmp_path):
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    resources = tmp_path / "modules" / "kst_local" / "resources"
    resources.mkdir(parents=True)
    bridge = resources / "read_visitor_db.js"
    bridge.write_text("first", encoding="utf-8")
    before = source_fingerprint(tmp_path)

    bridge.write_text("second", encoding="utf-8")

    assert source_fingerprint(tmp_path) != before


def test_default_project_config_contains_no_local_kst_identity():
    root = Path(__file__).resolve().parents[1]
    project = json.loads(
        (root / "configs" / "projects" / "kunming_niu.json").read_text(
            encoding="utf-8"
        )
    )

    assert "identity" not in project["kst"]
    assert "installation_root" not in project["kst"]
