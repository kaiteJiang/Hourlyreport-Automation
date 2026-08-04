from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from modules.baidu_data_source import fetch_baidu_api_only_daily, fetch_baidu_api_only_hourly
from modules.baidu_report_api import fetch_baidu_search_word_project
from modules.config_manager import load_config
from modules.multi_project_selection import validate_multi_project_ids
from modules.preflight import check_baidu_api_profiles
from modules.project_config import build_runtime_config_from_project, load_project_config
from modules.run_pipeline import (
    run_daily_pipeline,
    run_half_auto_pipeline,
    run_word_class_pipeline,
)
from modules.task_stop_gate import read_task_stop_decision


RuntimeConfigLoader = Callable[[Path, str], dict[str, Any]]
CredentialChecker = Callable[[Path, dict[str, Any]], dict[str, Any]]


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _default_runtime_config_loader(root: Path, project_id: str) -> dict[str, Any]:
    base_config = load_config(root / "config.json", fallback_path=root / "config.example.json")
    project = load_project_config(root, project_id)
    return build_runtime_config_from_project(project, base_config)


def _resolved_excel_path(root: Path, config: dict[str, Any]) -> Path:
    value = str(config.get("excel_path") or "").strip()
    if not value:
        raise ValueError("项目配置缺少 Excel 路径")
    path = Path(value)
    return path if path.is_absolute() else root / path


def _project_artifact_config(
    config: dict[str, Any],
    project_dir: Path,
    task: str,
) -> dict[str, Any]:
    runtime = deepcopy(config)
    baidu = dict(runtime.get("baidu") or {})
    if task == "daily":
        baidu["daily_output_path"] = str(project_dir / "baidu_daily_data.json")
    elif task == "word_class":
        baidu["search_word_output_path"] = str(project_dir / "baidu_search_word_data.json")
    else:
        baidu["output_path"] = str(project_dir / "baidu_account_data.json")
    runtime["baidu"] = baidu
    return runtime


def _api_errors(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["百度 API 未返回有效报告"]
    errors = report.get("errors") or []
    if isinstance(errors, list):
        return [str(error) for error in errors]
    return [str(errors)]


def _queue_stop_requested(stop_gate: Path | None) -> bool:
    return read_task_stop_decision(stop_gate) == "cancel"


def run_multi_project_pipeline(
    *,
    root: str | Path,
    logger,
    project_ids: list[str],
    task: str,
    period: str | None = None,
    target_date: str | None = None,
    stop_gate: str | Path | None = None,
    runtime_config_loader: RuntimeConfigLoader = _default_runtime_config_loader,
    credential_checker: CredentialChecker = check_baidu_api_profiles,
    api_fetch_hourly: Callable[..., dict[str, Any]] = fetch_baidu_api_only_hourly,
    api_fetch_daily: Callable[..., dict[str, Any]] = fetch_baidu_api_only_daily,
    api_fetch_search_word: Callable[..., dict[str, Any]] = fetch_baidu_search_word_project,
    hourly_pipeline: Callable[..., dict[str, Any]] = run_half_auto_pipeline,
    daily_pipeline: Callable[..., dict[str, Any]] = run_daily_pipeline,
    word_class_pipeline: Callable[..., dict[str, Any]] = run_word_class_pipeline,
    max_workers: int = 3,
) -> dict[str, Any]:
    root_path = Path(root)
    selected = validate_multi_project_ids(project_ids, project_ids)
    if task not in {"hourly", "daily", "word_class"}:
        raise ValueError("多项目任务类型只支持 hourly、daily 或 word_class")
    if task == "hourly" and not str(period or "").strip():
        raise ValueError("多项目小时报缺少时段")
    if task in {"daily", "word_class"} and not target_date:
        target_date = (date.today() - timedelta(days=1)).isoformat()

    started_at = datetime.now().isoformat(timespec="seconds")
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    run_dir = root_path / "reports" / "multi_runs" / run_id
    gate_path = Path(stop_gate) if stop_gate else None
    if gate_path is not None and not gate_path.is_absolute():
        gate_path = root_path / gate_path

    configs: dict[str, dict[str, Any]] = {}
    initial_failures: dict[str, list[str]] = {}
    excel_owners: dict[str, str] = {}
    for project_id in selected:
        try:
            config = runtime_config_loader(root_path, project_id)
            excel_path = _resolved_excel_path(root_path, config)
        except Exception as exc:
            initial_failures[project_id] = [str(exc)]
            continue
        excel_key = os.path.normcase(os.path.abspath(excel_path))
        owner = excel_owners.get(excel_key)
        if owner is not None:
            raise ValueError(f"Excel 路径重复：{owner} 与 {project_id} 指向 {excel_path}")
        excel_owners[excel_key] = project_id
        project_dir = run_dir / project_id
        configs[project_id] = _project_artifact_config(config, project_dir, task)

    prepared: dict[str, dict[str, Any]] = {}

    def fetch_one(project_id: str) -> tuple[str, dict[str, Any]]:
        config = configs[project_id]
        project_name = str(config.get("project_name") or project_id)
        position = selected.index(project_id) + 1
        print(f"[多项目][API {position}/{len(selected)}][{project_name}] 开始读取百度数据", flush=True)
        credential_report = credential_checker(root_path, config)
        if not credential_report.get("passed"):
            errors = credential_report.get("errors") or ["百度凭据检查未通过"]
            return project_id, {"accounts": {}, "errors": [str(item) for item in errors], "data_source": "failed"}
        try:
            if task == "daily":
                report = api_fetch_daily(
                    config=config,
                    root=root_path,
                    logger=logger,
                    target_date=target_date,
                )
            elif task == "word_class":
                report = api_fetch_search_word(
                    config=config,
                    root=root_path,
                    logger=logger,
                    target_date=target_date,
                )
            else:
                report = api_fetch_hourly(
                    config=config,
                    root=root_path,
                    logger=logger,
                    period=period,
                )
        except Exception as exc:
            report = {"accounts": {}, "errors": [str(exc)], "data_source": "failed"}
        return project_id, report

    fetchable = [project_id for project_id in selected if project_id in configs]
    if fetchable:
        with ThreadPoolExecutor(max_workers=min(max(1, max_workers), len(fetchable))) as executor:
            futures = {executor.submit(fetch_one, project_id): project_id for project_id in fetchable}
            for future in as_completed(futures):
                project_id = futures[future]
                try:
                    _, prepared[project_id] = future.result()
                except Exception as exc:
                    prepared[project_id] = {"accounts": {}, "errors": [str(exc)], "data_source": "failed"}

    project_results: list[dict[str, Any]] = []
    for index, project_id in enumerate(selected, start=1):
        config = configs.get(project_id)
        project_name = str((config or {}).get("project_name") or project_id)
        base_result = {
            "project_id": project_id,
            "project_name": project_name,
            "selection_index": index,
            "excel_path": str(_resolved_excel_path(root_path, config)) if config else "",
        }
        if project_id in initial_failures:
            project_results.append({**base_result, "status": "failed", "phase": "config", "errors": initial_failures[project_id]})
            continue

        api_report = prepared.get(project_id) or {"errors": ["百度 API 准备结果缺失"], "data_source": "failed"}
        errors = _api_errors(api_report)
        if errors:
            print(f"[多项目][失败][{project_name}] {'；'.join(errors)}", flush=True)
            project_results.append({
                **base_result,
                "status": "failed",
                "phase": "api",
                "errors": errors,
                "fallback_reason": api_report.get("fallback_reason"),
            })
            continue

        if _queue_stop_requested(gate_path):
            project_results.append({**base_result, "status": "stopped", "phase": "queue", "errors": []})
            continue

        print(f"[多项目][写入 {index}/{len(selected)}][{project_name}] 开始串行处理", flush=True)

        def cached_fetch(**_kwargs):
            return api_report

        try:
            if task == "daily":
                pipeline_report = daily_pipeline(
                    config=config,
                    root=root_path,
                    logger=logger,
                    target_date=target_date,
                    kst_file=None,
                    fetch_baidu_func=cached_fetch,
                )
            elif task == "word_class":
                pipeline_report = word_class_pipeline(
                    config=config,
                    root=root_path,
                    logger=logger,
                    target_date=target_date,
                    fetch_search_word_func=cached_fetch,
                )
            else:
                pipeline_report = hourly_pipeline(
                    config=config,
                    root=root_path,
                    logger=logger,
                    period=period,
                    kst_file=None,
                    assume_yes=True,
                    confirm_before_run=False,
                    fetch_baidu_func=cached_fetch,
                )
        except Exception as exc:
            pipeline_report = {"passed": False, "errors": [str(exc)], "failed_step": "pipeline"}

        pipeline_errors = _api_errors(pipeline_report)
        status = "success" if pipeline_report.get("passed") and not pipeline_errors else "failed"
        result = {
            **base_result,
            "status": status,
            "phase": "complete" if status == "success" else str(pipeline_report.get("failed_step") or "pipeline"),
            "errors": pipeline_errors,
            "pipeline_report": pipeline_report,
        }
        project_results.append(result)
        _write_json_atomic(run_dir / project_id / "project_result.json", result)

    summary = {
        "success": sum(1 for item in project_results if item["status"] == "success"),
        "failed": sum(1 for item in project_results if item["status"] == "failed"),
        "stopped": sum(1 for item in project_results if item["status"] == "stopped"),
    }
    report = {
        "mode": "run-multi",
        "run_id": run_id,
        "task": task,
        "period": period if task == "hourly" else None,
        "date": target_date if task in {"daily", "word_class"} else None,
        "selected_project_ids": selected,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "completed": True,
        "passed": summary["failed"] == 0 and summary["stopped"] == 0,
        "projects": project_results,
        "summary": summary,
        "outputs": {"run_dir": str(run_dir)},
    }
    output_path = root_path / "reports" / "multi_project_run_report.json"
    report["outputs"]["multi_project_run_report"] = str(output_path)
    _write_json_atomic(output_path, report)
    print(
        f"[多项目][完成] 成功 {summary['success']}，失败 {summary['failed']}，停止 {summary['stopped']}",
        flush=True,
    )
    return report
