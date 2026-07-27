from __future__ import annotations

import json
import logging
import threading
import time

import pytest


def test_multi_project_selection_round_trips_in_order(tmp_path):
    from modules.multi_project_selection import (
        load_multi_project_selection,
        save_multi_project_selection,
    )

    target = save_multi_project_selection(tmp_path, ["ningbo_niu", "kunming_niu"])

    assert target == tmp_path / "configs" / "multi_project_selection.json"
    assert load_multi_project_selection(
        tmp_path,
        available_ids=["kunming_niu", "ningbo_niu"],
        fallback_id="kunming_niu",
    ) == ["ningbo_niu", "kunming_niu"]


def test_multi_project_selection_filters_removed_projects_and_uses_fallback(tmp_path):
    from modules.multi_project_selection import load_multi_project_selection

    path = tmp_path / "configs" / "multi_project_selection.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"project_ids": ["removed", "ningbo_niu"]}),
        encoding="utf-8",
    )

    assert load_multi_project_selection(
        tmp_path,
        available_ids=["kunming_niu", "ningbo_niu"],
        fallback_id="kunming_niu",
    ) == ["ningbo_niu"]

    path.write_text(json.dumps({"project_ids": ["removed"]}), encoding="utf-8")
    assert load_multi_project_selection(
        tmp_path,
        available_ids=["kunming_niu"],
        fallback_id="kunming_niu",
    ) == ["kunming_niu"]


@pytest.mark.parametrize(
    ("project_ids", "message"),
    [
        ([], "至少选择 1 个项目"),
        (["kunming_niu", "kunming_niu"], "不能重复"),
        (["missing"], "不存在"),
        (["a", "b", "c", "d"], "最多选择 3 个项目"),
    ],
)
def test_validate_multi_project_ids_rejects_invalid_selection(project_ids, message):
    from modules.multi_project_selection import validate_multi_project_ids

    with pytest.raises(ValueError, match=message):
        validate_multi_project_ids(project_ids, available_ids=["kunming_niu", "a", "b", "c", "d"])


def test_validate_multi_project_ids_accepts_one_project():
    from modules.multi_project_selection import validate_multi_project_ids

    assert validate_multi_project_ids(["kunming_niu"], ["kunming_niu"]) == ["kunming_niu"]


def test_save_multi_project_selection_keeps_original_when_replace_fails(tmp_path, monkeypatch):
    import modules.multi_project_selection as selection

    path = tmp_path / "configs" / "multi_project_selection.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"project_ids": ["kunming_niu"]}), encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(selection.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        selection.save_multi_project_selection(tmp_path, ["ningbo_niu"])

    assert json.loads(path.read_text(encoding="utf-8")) == {"project_ids": ["kunming_niu"]}
    assert not list(path.parent.glob("multi_project_selection.json.*.tmp"))


def test_api_only_hourly_commits_configured_artifact_with_route_metadata(tmp_path):
    from modules.baidu_data_source import fetch_baidu_api_only_hourly

    output = tmp_path / "reports" / "multi_runs" / "run-1" / "kunming_niu" / "baidu.json"
    config = {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "accounts": {"账户1": {}},
        "baidu": {"output_path": str(output)},
    }

    def api_fetcher(**kwargs):
        assert kwargs["commit_standard_report"] is True
        assert kwargs["commit_attempt_report"] is False
        return {
            "project_id": "kunming_niu",
            "project_name": "昆明牛",
            "date": "2026-07-22",
            "period": "11点",
            "accounts": {"账户1": {"展现": 1, "点击": 1, "消费": 1.0}},
            "errors": [],
        }

    report = fetch_baidu_api_only_hourly(
        config=config,
        root=tmp_path,
        logger=logging.getLogger("test-api-only"),
        period="11点",
        api_fetcher=api_fetcher,
        sleep=lambda _seconds: None,
    )

    assert report["data_source"] == "api"
    assert report["fallback_reason"] is None
    assert json.loads(output.read_text(encoding="utf-8"))["data_source"] == "api"


def test_api_only_hourly_failure_does_not_have_browser_fallback(tmp_path):
    from modules.baidu_data_source import fetch_baidu_api_only_hourly
    from modules.baidu_report_api import BaiduReportApiError

    calls = 0

    def api_fetcher(**_kwargs):
        nonlocal calls
        calls += 1
        raise BaiduReportApiError("network down", category="network_error")

    report = fetch_baidu_api_only_hourly(
        config={"project_id": "ningbo_niu", "accounts": {}, "baidu": {}},
        root=tmp_path,
        logger=logging.getLogger("test-api-only-failure"),
        period="15点",
        api_fetcher=api_fetcher,
        sleep=lambda _seconds: None,
    )

    assert calls == 3
    assert report["data_source"] == "failed"
    assert report["fallback_reason"] == "network_error"
    assert "浏览器" not in json.dumps(report, ensure_ascii=False)


def test_daily_merge_reads_configured_baidu_artifact(tmp_path, monkeypatch):
    from modules import data_merger

    configured = tmp_path / "reports" / "multi_runs" / "run-1" / "project" / "daily.json"
    configured.parent.mkdir(parents=True)
    configured.write_text("{}", encoding="utf-8")
    shared_kst = tmp_path / "reports" / "kst_daily_data.json"
    shared_kst.write_text("{}", encoding="utf-8")
    observed = []

    def fake_read(path):
        observed.append(path)
        return {
            "date": "2026-07-21",
            "accounts": {"账户1": {}},
            "errors": [],
        }

    monkeypatch.setattr(data_merger, "_read_json", fake_read)
    monkeypatch.setattr(data_merger, "validate_merged_daily_data", lambda *_args, **_kwargs: [])

    data_merger.merge_daily_files(
        config={
            "accounts": {"账户1": {}},
            "baidu": {"daily_output_path": str(configured)},
        },
        root=tmp_path,
        logger=logging.getLogger("test-daily-merge-path"),
        target_date="2026-07-21",
    )

    assert observed[0] == configured


def _runtime_config(project_id: str, excel_name: str | None = None) -> dict:
    return {
        "project_id": project_id,
        "project_name": project_id,
        "excel_path": str(excel_name or f"{project_id}.xlsx"),
        "accounts": {f"{project_id}-账户": {}},
        "baidu": {},
        "kst": {
            "data_source": "local_api",
            "allow_zero_on_unavailable": True,
        },
    }


def test_three_project_run_keeps_each_kst_request_scoped(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    requested = []
    project_ids = ["project_a", "project_b", "project_c"]

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-kst-scope"),
        project_ids=project_ids,
        task="hourly",
        period="15点",
        runtime_config_loader=lambda _root, project_id: _runtime_config(project_id),
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=lambda config, **_kwargs: {
            "accounts": {config["project_id"]: {}},
            "errors": [],
            "data_source": "api",
        },
        hourly_pipeline=lambda config, **_kwargs: (
            requested.append(
                (
                    config["project_id"],
                    config["kst"]["data_source"],
                    config["kst"]["allow_zero_on_unavailable"],
                )
            )
            or {
                "passed": True,
                "excel_path": config["excel_path"],
                "errors": [],
            }
        ),
    )

    assert requested == [
        ("project_a", "local_api", True),
        ("project_b", "local_api", True),
        ("project_c", "local_api", True),
    ]
    assert report["summary"]["success"] == 3


def test_multi_project_runner_overlaps_api_but_serializes_pipelines_in_selection_order(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    project_ids = ["project_a", "project_b", "project_c"]
    barrier = threading.Barrier(3)
    api_active = 0
    api_max_active = 0
    pipeline_active = 0
    pipeline_max_active = 0
    pipeline_order = []
    lock = threading.Lock()

    def api_fetch(config, **_kwargs):
        nonlocal api_active, api_max_active
        with lock:
            api_active += 1
            api_max_active = max(api_max_active, api_active)
        barrier.wait(timeout=2)
        time.sleep(0.02 if config["project_id"] == "project_a" else 0)
        with lock:
            api_active -= 1
        return {"accounts": {config["project_id"]: {}}, "errors": [], "data_source": "api"}

    def pipeline(config, fetch_baidu_func, **_kwargs):
        nonlocal pipeline_active, pipeline_max_active
        cached = fetch_baidu_func()
        assert cached["data_source"] == "api"
        with lock:
            pipeline_active += 1
            pipeline_max_active = max(pipeline_max_active, pipeline_active)
            pipeline_order.append(config["project_id"])
        time.sleep(0.01)
        with lock:
            pipeline_active -= 1
        return {
            "passed": True,
            "project_id": config["project_id"],
            "excel_path": config["excel_path"],
            "errors": [],
        }

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-order"),
        project_ids=project_ids,
        task="hourly",
        period="11点",
        runtime_config_loader=lambda _root, project_id: _runtime_config(project_id),
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=api_fetch,
        hourly_pipeline=pipeline,
    )

    assert api_max_active == 3
    assert pipeline_max_active == 1
    assert pipeline_order == project_ids
    assert report["summary"] == {"success": 3, "failed": 0, "stopped": 0}


def test_multi_project_runner_isolates_api_failure_and_continues_other_projects(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    pipeline_order = []

    def api_fetch(config, **_kwargs):
        if config["project_id"] == "project_b":
            return {
                "accounts": {},
                "errors": ["authorization_error"],
                "data_source": "failed",
                "fallback_reason": "authorization_error",
            }
        return {"accounts": {config["project_id"]: {}}, "errors": [], "data_source": "api"}

    def pipeline(config, **_kwargs):
        pipeline_order.append(config["project_id"])
        return {"passed": True, "excel_path": config["excel_path"], "errors": []}

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-failure"),
        project_ids=["project_a", "project_b", "project_c"],
        task="hourly",
        period="15点",
        runtime_config_loader=lambda _root, project_id: _runtime_config(project_id),
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=api_fetch,
        hourly_pipeline=pipeline,
    )

    assert pipeline_order == ["project_a", "project_c"]
    assert [item["status"] for item in report["projects"]] == ["success", "failed", "success"]
    assert report["projects"][1]["phase"] == "api"
    assert report["summary"] == {"success": 2, "failed": 1, "stopped": 0}


def test_multi_project_runner_stop_during_current_project_skips_next_projects(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline
    from modules.task_stop_gate import request_task_stop

    stop_gate = tmp_path / "reports" / ".multi-stop.gate"
    api_order = []
    pipeline_order = []

    def api_fetch(config, **_kwargs):
        api_order.append(config["project_id"])
        return {"accounts": {config["project_id"]: {}}, "errors": [], "data_source": "api"}

    def pipeline(config, **_kwargs):
        pipeline_order.append(config["project_id"])
        request_task_stop(stop_gate)
        return {"passed": True, "excel_path": config["excel_path"], "errors": []}

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-stop"),
        project_ids=["project_a", "project_b", "project_c"],
        task="daily",
        target_date="2026-07-21",
        stop_gate=stop_gate,
        runtime_config_loader=lambda _root, project_id: _runtime_config(project_id),
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_daily=api_fetch,
        daily_pipeline=pipeline,
    )

    assert set(api_order) == {"project_a", "project_b", "project_c"}
    assert pipeline_order == ["project_a"]
    assert [item["status"] for item in report["projects"]] == ["success", "stopped", "stopped"]
    assert report["summary"] == {"success": 1, "failed": 0, "stopped": 2}


def test_multi_project_runner_rejects_duplicate_excel_paths_before_api(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    api_calls = []

    with pytest.raises(ValueError, match="Excel 路径重复"):
        run_multi_project_pipeline(
            root=tmp_path,
            logger=logging.getLogger("test-multi-duplicate-excel"),
            project_ids=["project_a", "project_b"],
            task="hourly",
            period="11点",
            runtime_config_loader=lambda _root, project_id: _runtime_config(project_id, "shared.xlsx"),
            credential_checker=lambda *_args: {"passed": True},
            api_fetch_hourly=lambda **kwargs: api_calls.append(kwargs),
        )

    assert api_calls == []


def test_multi_project_runner_isolates_invalid_project_config(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    pipeline_order = []

    def load_config(_root, project_id):
        if project_id == "project_b":
            raise ValueError("配置文件损坏")
        return _runtime_config(project_id)

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-config-isolation"),
        project_ids=["project_a", "project_b", "project_c"],
        task="hourly",
        period="18点",
        runtime_config_loader=load_config,
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=lambda config, **_kwargs: {
            "accounts": {config["project_id"]: {}},
            "errors": [],
            "data_source": "api",
        },
        hourly_pipeline=lambda config, **_kwargs: (
            pipeline_order.append(config["project_id"])
            or {"passed": True, "excel_path": config["excel_path"], "errors": []}
        ),
    )

    assert pipeline_order == ["project_a", "project_c"]
    assert report["projects"][1]["status"] == "failed"
    assert report["projects"][1]["phase"] == "config"
    assert report["projects"][1]["errors"] == ["配置文件损坏"]


def test_multi_project_runner_isolates_missing_excel_path(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    pipeline_order = []

    def load_config(_root, project_id):
        config = _runtime_config(project_id)
        if project_id == "project_b":
            config["excel_path"] = ""
        return config

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-missing-excel"),
        project_ids=["project_a", "project_b", "project_c"],
        task="hourly",
        period="18点",
        runtime_config_loader=load_config,
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=lambda config, **_kwargs: {
            "accounts": {config["project_id"]: {}},
            "errors": [],
            "data_source": "api",
        },
        hourly_pipeline=lambda config, **_kwargs: (
            pipeline_order.append(config["project_id"])
            or {"passed": True, "excel_path": config["excel_path"], "errors": []}
        ),
    )

    assert pipeline_order == ["project_a", "project_c"]
    assert report["projects"][1]["status"] == "failed"
    assert report["projects"][1]["phase"] == "config"
    assert report["projects"][1]["errors"] == ["项目配置缺少 Excel 路径"]


def test_multi_project_runner_stop_before_first_project_discards_prepared_data(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline
    from modules.task_stop_gate import request_task_stop

    stop_gate = tmp_path / "reports" / ".multi-stop.gate"
    request_task_stop(stop_gate)
    pipeline_order = []

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-stop-before-first"),
        project_ids=["project_a", "project_b"],
        task="hourly",
        period="11点",
        stop_gate=stop_gate,
        runtime_config_loader=lambda _root, project_id: _runtime_config(project_id),
        credential_checker=lambda *_args: {"passed": True},
        api_fetch_hourly=lambda config, **_kwargs: {
            "accounts": {config["project_id"]: {}},
            "errors": [],
            "data_source": "api",
        },
        hourly_pipeline=lambda config, **_kwargs: pipeline_order.append(config["project_id"]),
    )

    assert pipeline_order == []
    assert [item["status"] for item in report["projects"]] == ["stopped", "stopped"]
    assert report["summary"] == {"success": 0, "failed": 0, "stopped": 2}


def test_multi_project_runner_requires_api_authorization_not_browser_password(tmp_path):
    from modules.multi_project_runner import run_multi_project_pipeline

    secrets = tmp_path / "secrets" / "secrets.json"
    secrets.parent.mkdir(parents=True)
    secrets.write_text(
        json.dumps(
            {
                "baidu_api": {
                    "project_a_api": {
                        "access_token": "access",
                        "refresh_token": "refresh",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = _runtime_config("project_a")
    config["credentials_path"] = "secrets/secrets.json"
    config["baidu"]["api_profile"] = "project_a_api"

    report = run_multi_project_pipeline(
        root=tmp_path,
        logger=logging.getLogger("test-multi-api-credentials"),
        project_ids=["project_a"],
        task="hourly",
        period="11点",
        runtime_config_loader=lambda *_args: config,
        api_fetch_hourly=lambda **_kwargs: {"accounts": {"project_a": {}}, "errors": [], "data_source": "api"},
        hourly_pipeline=lambda **_kwargs: {"passed": True, "excel_path": config["excel_path"], "errors": []},
    )

    assert report["projects"][0]["status"] == "success"


def test_multi_project_command_builders_preserve_project_order(tmp_path):
    from gui.command_builder import build_multi_daily_command, build_multi_hourly_command

    hourly = build_multi_hourly_command(tmp_path, "11点", ["ningbo_niu", "kunming_niu"])
    daily = build_multi_daily_command(tmp_path, "2026-07-21", ["ningbo_niu"])

    assert hourly[-8:] == [
        "--mode",
        "run-multi",
        "--projects",
        "ningbo_niu,kunming_niu",
        "--task",
        "hourly",
        "--period",
        "11点",
    ]
    assert daily[-8:] == [
        "--mode",
        "run-multi",
        "--projects",
        "ningbo_niu",
        "--task",
        "daily",
        "--date",
        "2026-07-21",
    ]
