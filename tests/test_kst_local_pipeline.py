from pathlib import Path

from modules.project_config import build_runtime_config_from_project
from modules.run_pipeline import run_daily_pipeline, run_half_auto_pipeline


class Logger:
    def __init__(self):
        self.warnings = []

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def warning(self, message, *args, **kwargs):
        self.warnings.append(message % args if args else message)


def _config():
    return {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "excel_path": "never-written.xlsx",
        "sheet_name": "时段数据",
        "accounts": {"银康01": {"excel_name": "银康01"}},
        "kst": {
            "data_source": "local_api",
            "export_dir": "missing-export-dir",
        },
    }


def test_hourly_pipeline_uses_local_api_without_looking_for_export(tmp_path):
    calls = {"local": 0, "export": 0}

    def fetch_baidu(**kwargs):
        return {
            "date": "2026-07-27",
            "period": "15点",
            "data_source": "api",
            "errors": [],
        }

    def fetch_local(config, root, period, target_date=None):
        calls["local"] += 1
        assert target_date == "2026-07-27"
        return {
            "parse_report": {"passed": True, "errors": []},
            "outputs": {
                "dialog_data": str(root / "reports" / "kst_dialog_data.json")
            },
        }

    def parse_export(*args, **kwargs):
        calls["export"] += 1
        raise AssertionError("export parser must not run")

    def merge(**kwargs):
        return {
            "validate_report": {"passed": True, "errors": []},
            "outputs": {},
            "merged": {"date": "2026-07-27", "period": "15点"},
        }

    def write(**kwargs):
        return {
            "errors": [],
            "self_check": {"verification_passed": True},
            "writes": [],
            "excel_path": str(tmp_path / "fake.xlsx"),
            "date": "2026-07-27",
            "period": "15点",
        }

    report = run_half_auto_pipeline(
        config=_config(),
        root=tmp_path,
        logger=Logger(),
        period="15点",
        kst_file=None,
        assume_yes=True,
        fetch_baidu_func=fetch_baidu,
        parse_kst_func=parse_export,
        fetch_kst_local_func=fetch_local,
        merge_func=merge,
        write_func=write,
    )

    assert report["passed"] is True
    assert calls == {"local": 1, "export": 0}
    assert report["kst_data_source"] == "local_api"


def test_hourly_pipeline_accepts_api_zero_fallback_and_warns(tmp_path):
    logger = Logger()

    def fetch_baidu(**kwargs):
        return {
            "date": "2026-07-27",
            "period": "15点",
            "data_source": "api",
            "errors": [],
        }

    def fetch_local(config, root, period, target_date=None):
        return {
            "dialog_data": {"source": "kst_local_api_unavailable_zero"},
            "parse_report": {"passed": True, "errors": []},
            "outputs": {},
        }

    report = run_half_auto_pipeline(
        config=_config(),
        root=tmp_path,
        logger=logger,
        period="15点",
        kst_file=None,
        assume_yes=True,
        fetch_baidu_func=fetch_baidu,
        fetch_kst_local_func=fetch_local,
        merge_func=lambda **kwargs: {
            "validate_report": {"passed": True, "errors": []},
            "outputs": {},
            "merged": {"date": "2026-07-27", "period": "15点"},
        },
        write_func=lambda **kwargs: {
            "errors": [],
            "self_check": {"verification_passed": True},
            "writes": [],
            "excel_path": str(tmp_path / "fake.xlsx"),
            "date": "2026-07-27",
            "period": "15点",
        },
    )

    assert report["passed"] is True
    assert any("API 不可用" in warning and "按 0 继续" in warning for warning in logger.warnings)


def test_daily_pipeline_uses_local_api_without_parsing_export(tmp_path):
    calls = {"local": 0, "export": 0}

    def fetch_local(config, root, target_date=None):
        calls["local"] += 1
        assert config["project_id"] == "kunming_niu"
        assert target_date == "2026-07-26"
        return {
            "daily_data": {
                "project_id": "kunming_niu",
                "date": "2026-07-26",
                "source": "kst_local_api",
            },
            "parse_report": {"passed": True, "errors": []},
            "outputs": {
                "daily_data": str(root / "reports" / "kst_daily_data.json")
            },
        }

    def parse_export(*args, **kwargs):
        calls["export"] += 1
        raise AssertionError("API 模式不得解析人工导出")

    report = run_daily_pipeline(
        config=_config(),
        root=tmp_path,
        logger=Logger(),
        target_date="2026-07-26",
        kst_file=None,
        fetch_baidu_func=lambda **kwargs: {
            "date": "2026-07-26",
            "data_source": "api",
            "errors": [],
        },
        parse_kst_func=parse_export,
        fetch_kst_local_func=fetch_local,
        merge_func=lambda **kwargs: {
            "merged": {"date": "2026-07-26"},
            "validate_report": {"passed": True, "errors": []},
            "outputs": {},
        },
        write_func=lambda **kwargs: {
            "date": "2026-07-26",
            "excel_path": str(tmp_path / "fake.xlsx"),
            "writes": [],
            "overwrite_summary": {"overwrite_count": 0},
            "self_check": {"verification_passed": True},
            "errors": [],
        },
    )

    assert report["passed"] is True
    assert report["kst_data_source"] == "local_api"
    assert calls == {"local": 1, "export": 0}
    assert report["steps"][1]["name"] == "fetch-kst-local-daily"


def test_runtime_config_preserves_kst_local_api_fields():
    project = {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "excel": {
            "path": "x.xlsx",
            "hourly_sheet": "时段数据",
            "daily_sheet": "百度",
        },
        "kst": {
            "export_dir": "exports",
            "auto_pick_latest": True,
            "max_file_age_minutes": 30,
            "data_source": "local_api",
            "installation_root": r"D:\KST",
            "identity": "733875_1269870",
            "local_api_url": "http://127.0.0.1:18766",
            "local_api_token_env": "KST_LOCAL_API_TOKEN",
            "allow_zero_on_unavailable": True,
        },
        "baidu": {"credential_profile": "demo"},
        "accounts": [
            {
                "standard_name": "银康01",
                "baidu_names": ["银康01"],
                "excel_name": "银康01",
                "kst_ids": ["72828178"],
                "kst_names": ["银康01"],
            }
        ],
    }

    runtime = build_runtime_config_from_project(project, {})

    assert runtime["kst"]["data_source"] == "local_api"
    assert runtime["kst"]["installation_root"] == r"D:\KST"
    assert runtime["kst"]["identity"] == "733875_1269870"
    assert runtime["kst"]["allow_zero_on_unavailable"] is True
