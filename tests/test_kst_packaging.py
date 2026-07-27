from pathlib import Path


def test_desktop_spec_packages_kst_database_bridge():
    root = Path(__file__).resolve().parents[1]
    spec_source = (
        root / "tools" / "hourlyreport_automation.spec"
    ).read_text(encoding="utf-8")

    assert "read_visitor_db.js" in spec_source
    assert "modules/kst_local/resources" in spec_source.replace("\\", "/")
