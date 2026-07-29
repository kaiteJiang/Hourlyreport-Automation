import json
import subprocess
import sys
from pathlib import Path

from tools.build_desktop_exe import source_fingerprint


def test_desktop_build_uses_versioned_staging_directory(tmp_path):
    from tools.build_desktop_exe import desktop_staging_dir

    assert desktop_staging_dir(
        tmp_path,
        "2026.7.27.114",
    ) == tmp_path / "build" / "release_2026.7.27.114_staging"


def test_publish_release_replaces_dist_with_only_two_assets(
    tmp_path,
    monkeypatch,
):
    import tools.build_publish_release as publisher

    version = "2026.7.27.114"
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "old.zip").write_bytes(b"old")
    (dist / "hourlyreport_automation.exe").write_bytes(b"old-exe")

    def fake_build_desktop(root, *, output_dir=None):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "hourlyreport_automation.exe").write_bytes(b"new-exe")
        (output / "hourlyreport_automation.build.json").write_text(
            "{}",
            encoding="utf-8",
        )
        return 0

    def fake_build_release(
        root,
        *,
        version,
        online_update,
        artifact_dir,
        output_dir,
    ):
        assert online_update is True
        assert Path(artifact_dir).parent == tmp_path / "build"
        output = Path(output_dir) / (
            f"Hourlyreport_automation_v{version}.zip"
        )
        output.write_bytes(b"update")
        return output

    def fake_build_installer(
        root,
        version,
        *,
        compiler,
        artifact_dir,
        output_dir,
    ):
        output = Path(output_dir) / (
            f"Hourlyreport_automation_setup_v{version}.exe"
        )
        output.write_bytes(b"installer")
        return output

    monkeypatch.setattr(
        publisher,
        "build_desktop_exe",
        fake_build_desktop,
    )
    monkeypatch.setattr(
        publisher,
        "build_release",
        fake_build_release,
    )
    monkeypatch.setattr(
        publisher,
        "build_windows_installer",
        fake_build_installer,
    )

    update, installer = publisher.build_publish_release(
        tmp_path,
        version,
        compiler="fake-iscc.exe",
    )

    assert update.name == f"Hourlyreport_automation_v{version}.zip"
    assert installer.name == (
        f"Hourlyreport_automation_setup_v{version}.exe"
    )
    assert {path.name for path in dist.iterdir()} == {
        update.name,
        installer.name,
    }


def test_gui_kst_api_import_graph_does_not_load_tabular_stack():
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-c",
        (
            "import sys; import gui.kst_api_manager; "
            "blocked = {'pandas', 'numpy'} & set(sys.modules); "
            "raise SystemExit(','.join(sorted(blocked)) if blocked else 0)"
        ),
    ]

    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr


def test_desktop_spec_packages_kst_database_bridge():
    from gui.version import CURRENT_VERSION

    root = Path(__file__).resolve().parents[1]
    spec_source = (
        root / "tools" / "hourlyreport_automation.spec"
    ).read_text(encoding="utf-8")

    assert "read_visitor_db.js" in spec_source
    assert "read_promotion_ids.js" in spec_source
    assert "modules/kst_local" in spec_source.replace("\\", "/")
    assert "modules/kst_local/resources" in spec_source.replace("\\", "/")
    assert CURRENT_VERSION == "2026.7.29.117"


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
