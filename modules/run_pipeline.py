from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from modules.baidu_data_source import fetch_baidu_resilient_daily, fetch_baidu_resilient_hourly
from modules.console_ui import (
    print_final_failure,
    print_final_success,
    print_step,
    print_step_failure,
    print_step_success,
    print_write_summary,
)
from modules.data_merger import merge_daily_files, merge_data_files, normalize_period_for_report
from modules.excel_writer import write_merged_daily_data, write_merged_hourly_data
from modules.kst_daily_parser import parse_kst_daily_file, write_empty_kst_daily_result
from modules.kst_export_parser import auto_export_max_age_seconds, find_latest_kst_export, parse_kst_export_file, write_empty_kst_export_result
from modules.kst_local.source import fetch_kst_local_report
from modules.task_stop_gate import claim_excel_write, task_stop_requested


StepFunc = Callable[..., dict[str, Any]]
STALE_EXPORT_SECONDS = 30 * 60


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_path(root: Path, path: str | Path | None) -> Path | None:
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root / resolved
    return resolved


def _step_result(name: str, passed: bool, outputs: dict[str, Any] | None = None, errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "outputs": outputs or {},
        "errors": errors or [],
    }


def _errors_from_report(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return ["步骤没有返回报告"]
    errors = report.get("errors") or []
    if isinstance(errors, list):
        return [str(error) for error in errors]
    return [str(errors)]


def _data_source_label(data_source: Any) -> str:
    return {
        "api": "API",
        "browser": "浏览器",
        "browser_fallback": "浏览器降级",
        "browser_shadow": "浏览器（影子模式）",
    }.get(str(data_source or ""), "未知")


def _default_yesterday(today: date | None = None) -> str:
    base = today or date.today()
    return (base - timedelta(days=1)).isoformat()


def _finalize(root: Path, report: dict[str, Any], logger) -> dict[str, Any]:
    out = root / "reports" / "final_run_report.json"
    report["summary_text"] = _build_summary_text(report)
    report["outputs"]["final_run_report"] = str(out)
    _write_json(out, report)
    logger.info("一键流最终报告已输出：%s；结果：%s", out, "通过" if report.get("passed") else "失败")
    return report


def _finalize_daily(root: Path, report: dict[str, Any], logger) -> dict[str, Any]:
    out = root / "reports" / "daily_final_run_report.json"
    report["summary_text"] = _build_daily_summary_text(report)
    report["outputs"]["daily_final_run_report"] = str(out)
    _write_json(out, report)
    logger.info("日报一键流最终报告已输出：%s；结果：%s", out, "通过" if report.get("passed") else "失败")
    return report


def _file_info(path: Path | None, auto_discovered: bool, max_age_hours: float = 2) -> dict[str, Any]:
    info: dict[str, Any] = {
        "file_name": path.name if path else "",
        "full_path": str(path) if path else "",
        "last_modified": None,
        "age_seconds": None,
        "is_stale": False,
        "auto_discovered": auto_discovered,
    }
    if path and path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_seconds = max(0, int((datetime.now() - mtime).total_seconds()))
        info.update({
            "last_modified": mtime.isoformat(timespec="seconds"),
            "age_seconds": age_seconds,
            "is_stale": auto_discovered and age_seconds > int(max_age_hours * 60 * 60),
        })
    return info


def _max_age_hours_for_info(config: dict[str, Any]) -> float:
    return auto_export_max_age_seconds(config) / 3600


def _build_summary_text(report: dict[str, Any]) -> str:
    status = "成功" if report.get("passed") else "失败"
    if report.get("passed"):
        return (
            f"本次半自动一键流{status}：写入日期 {report.get('date') or '未知'}，"
            f"时段 {report.get('period') or '未知'}，目标 Excel {report.get('excel_path') or '未知'}，"
            f"sheet {report.get('target_sheet') or '未知'}，快商通导出文件 {report.get('kst_export_file') or '未知'}，"
            f"百度数据来源{'正常' if report.get('baidu_source_ok') else '异常'}，"
            f"写入 {report.get('write_summary', {}).get('write_count', 0)} 个单元格。"
        )
    return (
        f"本次半自动一键流{status}：失败步骤 {report.get('failed_step') or '未知'}，"
        f"原因：{'；'.join(report.get('errors') or ['未知错误'])}。"
    )


def _build_daily_summary_text(report: dict[str, Any]) -> str:
    status = "成功" if report.get("passed") else "失败"
    if report.get("passed"):
        return (
            f"本次日报一键流{status}：目标日期 {report.get('date') or '未知'}，"
            f"目标 Excel {report.get('excel_path') or '未知'}，sheet 百度，"
            f"商务通导出文件 {report.get('kst_export_file') or '未知'}，"
            f"备份文件 {report.get('backup_path') or '未知'}，"
            f"写入 {report.get('write_summary', {}).get('write_count', 0)} 个单元格，"
            f"覆盖 {report.get('write_summary', {}).get('overwrite_count', 0)} 个单元格，"
            f"复核{'通过' if report.get('write_summary', {}).get('verification_passed') else '未通过'}。"
        )
    return (
        f"本次日报一键流{status}：失败步骤 {report.get('failed_step') or '未知'}，"
        f"原因：{'；'.join(report.get('errors') or ['未知错误'])}。"
    )


def _print_preflight_checklist(report: dict[str, Any]) -> None:
    from modules.console_ui import print_confirm_panel

    kst = report.get("kst_export", {})
    print_confirm_panel({
        "task_name": f"小时报 — {report.get('period', '')}",
        "project_name": report.get("project_name", ""),
        "excel_path": report.get("excel_path", ""),
        "sheet": report.get("target_sheet", ""),
        "date": report.get("current_date", ""),
        "period": report.get("period", ""),
        "kst_file": kst.get("full_path") or kst.get("file_name", ""),
        "kst_is_stale": kst.get("is_stale", False),
    })


def run_half_auto_pipeline(
    config: dict[str, Any],
    root: Path,
    logger,
    period: str | None,
    kst_file: str | Path | None,
    assume_yes: bool = False,
    confirm_before_run: bool = False,
    input_func: Callable[[str], str] = input,
    fetch_baidu_func: StepFunc = fetch_baidu_resilient_hourly,
    parse_kst_func: Callable[..., dict[str, Any]] = parse_kst_export_file,
    fetch_kst_local_func: Callable[..., dict[str, Any]] = fetch_kst_local_report,
    merge_func: StepFunc = merge_data_files,
    write_func: StepFunc = write_merged_hourly_data,
) -> dict[str, Any]:
    kst_data_source = str(
        config.get("kst", {}).get("data_source") or "export"
    ).strip().lower()
    if kst_data_source not in {"export", "local_api"}:
        raise ValueError(f"不支持的商务通数据源：{kst_data_source}")
    auto_discovered = kst_file is None and kst_data_source == "export"
    max_age_hours = _max_age_hours_for_info(config)
    export_file = (
        _resolve_path(root, kst_file)
        if kst_data_source == "export"
        else None
    )
    if export_file is None and kst_data_source == "export":
        export_file = find_latest_kst_export(root, config)
    kst_export_info = _file_info(export_file, auto_discovered, max_age_hours)

    report: dict[str, Any] = {
        "mode": "run",
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "passed": False,
        "cancelled": False,
        "failed_step": None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "current_date": datetime.now().date().isoformat(),
        "date": None,
        "period": normalize_period_for_report(period),
        "excel_path": str(_resolve_path(root, config.get("excel_path")) or ""),
        "target_sheet": config.get("sheet_name", "时段数据"),
        "kst_export_file": str(export_file or ""),
        "kst_export": kst_export_info,
        "kst_data_source": kst_data_source,
        "baidu_source_ok": False,
        "data_source": None,
        "api_attempts": 0,
        "fallback_reason": None,
        "self_heal_actions": [],
        "preflight_confirmed": False,
        "summary_text": "",
        "steps": [],
        "write_summary": {
            "write_count": 0,
            "verification_passed": False,
        },
        "outputs": {},
        "errors": [],
    }

    def fail(step_name: str, errors: list[str]) -> dict[str, Any]:
        report["failed_step"] = step_name
        report["errors"].extend(errors)
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.error("一键流在步骤 %s 中断：%s", step_name, errors)
        return _finalize(root, report, logger)

    def stop_if_requested(boundary: str, claim_excel: bool = False) -> dict[str, Any] | None:
        should_stop = not claim_excel_write(root, resolve_from_environment=True) if claim_excel else task_stop_requested(root)
        if not should_stop:
            return None
        report["cancelled"] = True
        report["failed_step"] = "cancelled-before-excel"
        report["cancelled_at"] = boundary
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("用户停止任务：boundary=%s；未进入 Excel 写入", boundary)
        print("[停止] 已在 Excel 写入前安全停止", flush=True)
        return _finalize(root, report, logger)

    logger.info("一键流开始：period=%s，kst_file=%s", period, kst_file)

    if confirm_before_run and not assume_yes:
        _print_preflight_checklist(report)
        answer = input_func("> ").strip()
        if answer == "0":
            errors = ["用户返回主菜单"]
            return fail("preflight-confirm", errors)
    report["preflight_confirmed"] = True

    stopped = stop_if_requested("before-baidu")
    if stopped:
        return stopped

    print_step(1, 4, "读取百度搜索推广数据")
    try:
        baidu_report = fetch_baidu_func(config=config, root=root, logger=logger, period=period)
    except Exception as exc:
        report["steps"].append(_step_result("fetch-baidu-auto", False, errors=[str(exc)]))
        print_step_failure("百度数据读取异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("fetch-baidu-auto", [str(exc)])
    baidu_errors = _errors_from_report(baidu_report)
    report["data_source"] = baidu_report.get("data_source") or "browser"
    report["api_attempts"] = int(baidu_report.get("api_attempts") or 0)
    report["fallback_reason"] = baidu_report.get("fallback_reason")
    report["self_heal_actions"] = list(baidu_report.get("self_heal_actions") or [])
    logger.info("[实际来源] 百度数据：%s", _data_source_label(report["data_source"]))
    baidu_passed = not baidu_errors
    report["steps"].append(_step_result(
        "fetch-baidu-auto",
        baidu_passed,
        outputs={"baidu_account_data": str(root / config.get("baidu", {}).get("output_path", "reports/baidu_account_data.json"))},
        errors=baidu_errors,
    ))
    if not baidu_passed:
        print_step_failure("百度数据读取未通过", suggestion="；".join(baidu_errors), log_path=str(root / "logs" / "run.log"))
        return fail("fetch-baidu-auto", baidu_errors)
    report["baidu_source_ok"] = True
    report["date"] = baidu_report.get("date")
    print_step_success("百度搜索推广数据已读取")

    # 未知百度账户提醒
    from modules.baidu_unknown_accounts import print_unknown_baidu_accounts_notice
    print_unknown_baidu_accounts_notice(baidu_report)

    logger.info("一键流步骤完成：fetch-baidu-auto")

    stopped = stop_if_requested("after-baidu")
    if stopped:
        return stopped

    print_step(
        2,
        4,
        "读取商务通本地 API" if kst_data_source == "local_api" else "解析快商通导出文件",
    )
    if kst_data_source == "local_api":
        try:
            kst_result = fetch_kst_local_func(
                config,
                root,
                period,
                target_date=report.get("date"),
            )
        except Exception as exc:
            errors = [str(exc)]
            report["steps"].append(
                _step_result("fetch-kst-local", False, errors=errors)
            )
            print_step_failure(
                "商务通本地 API 读取异常",
                suggestion=str(exc),
                log_path=str(root / "logs" / "run.log"),
            )
            return fail("fetch-kst-local", errors)
        kst_parse_report = kst_result.get("parse_report", {})
        kst_errors = _errors_from_report(kst_parse_report)
        kst_passed = bool(kst_parse_report.get("passed")) and not kst_errors
        report["steps"].append(
            _step_result(
                "fetch-kst-local",
                kst_passed,
                outputs=kst_result.get("outputs", {}),
                errors=kst_errors,
            )
        )
        report["outputs"].update(kst_result.get("outputs", {}))
        if not kst_passed:
            print_step_failure(
                "商务通本地 API 校验未通过",
                suggestion="；".join(kst_errors),
                log_path=str(root / "logs" / "run.log"),
            )
            return fail("fetch-kst-local", kst_errors)
        if (
            kst_result.get("dialog_data", {}).get("source")
            == "kst_local_api_unavailable_zero"
        ):
            logger.warning("商务通 API 不可用，已按 0 继续")
            print_step_success("商务通 API 不可用，已按 0 继续")
        else:
            print_step_success("商务通自动来源数据已读取")
        logger.info("一键流步骤完成：fetch-kst-local")
    elif export_file is None:
        reason = "未找到 30 分钟内的快商通导出文件，按 0 对话处理"
        kst_result = write_empty_kst_export_result(config, root, period, reason)
        report["steps"].append(_step_result("parse-kst-export", True, outputs=kst_result.get("outputs", {})))
        report["outputs"].update(kst_result.get("outputs", {}))
        print_step_success("未找到 30 分钟内的快商通导出文件，已按 0 对话处理")
        logger.info("一键流步骤完成：parse-kst-export；%s", reason)
    else:
        if not export_file.exists():
            errors = [f"找不到快商通导出文件：{export_file}"]
            report["steps"].append(_step_result("parse-kst-export", False, errors=errors))
            print_step_failure("快商通导出文件不存在", suggestion=f"文件路径：{export_file}")
            return fail("parse-kst-export", errors)
        report["kst_export_file"] = str(export_file)
        report["kst_export"] = _file_info(export_file, auto_discovered, max_age_hours)

        try:
            kst_result = parse_kst_func(export_file, config, root, period)
        except Exception as exc:
            report["steps"].append(_step_result("parse-kst-export", False, errors=[str(exc)]))
            print_step_failure("快商通解析异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
            return fail("parse-kst-export", [str(exc)])
        kst_parse_report = kst_result.get("parse_report", {})
        kst_errors = _errors_from_report(kst_parse_report)
        kst_passed = bool(kst_parse_report.get("passed")) and not kst_errors
        report["steps"].append(_step_result(
            "parse-kst-export",
            kst_passed,
            outputs=kst_result.get("outputs", {}),
            errors=kst_errors,
        ))
        if not kst_passed:
            print_step_failure("快商通解析未通过", suggestion="；".join(kst_errors), log_path=str(root / "logs" / "run.log"))
            return fail("parse-kst-export", kst_errors)
        print_step_success("快商通导出文件已解析")
        logger.info("一键流步骤完成：parse-kst-export")

    stopped = stop_if_requested("after-kst")
    if stopped:
        return stopped

    print_step(3, 4, "合并百度与快商通数据")
    try:
        merge_result = merge_func(config=config, root=root, logger=logger, period=period)
    except Exception as exc:
        report["steps"].append(_step_result("merge-data", False, errors=[str(exc)]))
        print_step_failure("数据合并异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("merge-data", [str(exc)])
    merge_report = merge_result.get("validate_report", {})
    merge_errors = _errors_from_report(merge_report)
    merge_passed = bool(merge_report.get("passed")) and not merge_errors
    report["steps"].append(_step_result(
        "merge-data",
        merge_passed,
        outputs=merge_result.get("outputs", {}),
        errors=merge_errors,
    ))
    if not merge_passed:
        print_step_failure("数据合并不通过", suggestion="；".join(merge_errors), log_path=str(root / "logs" / "run.log"))
        return fail("merge-data", merge_errors)
    if merge_result.get("merged"):
        report["date"] = merge_result["merged"].get("date") or report["date"]
        report["period"] = merge_result["merged"].get("period") or report["period"]
    print_step_success("百度和快商通数据已合并")
    logger.info("一键流步骤完成：merge-data")

    stopped = stop_if_requested("before-excel", claim_excel=True)
    if stopped:
        return stopped

    print_step(4, 4, "写入 Excel 并复核")
    try:
        write_report = write_func(config=config, root=root, logger=logger, period=period)
    except Exception as exc:
        report["steps"].append(_step_result("write-excel", False, errors=[str(exc)]))
        print_step_failure("Excel 写入异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("write-excel", [str(exc)])
    write_errors = _errors_from_report(write_report)
    verification_passed = bool(write_report.get("self_check", {}).get("verification_passed"))
    write_passed = not write_errors and verification_passed
    report["steps"].append(_step_result(
        "write-excel",
        write_passed,
        outputs={
            "write_report": str(root / "reports" / "write_report.json"),
            "excel_path": write_report.get("excel_path"),
            "backup_path": write_report.get("backup_path"),
        },
        errors=write_errors,
    ))
    report["write_summary"] = {
        "write_count": len(write_report.get("writes", [])),
        "verification_passed": verification_passed,
    }
    report["excel_path"] = str(write_report.get("excel_path") or report["excel_path"])
    report["date"] = write_report.get("date") or report["date"]
    report["period"] = write_report.get("period") or report["period"]
    if not write_passed:
        errors = write_errors or ["Excel 写入后复核未通过"]
        print_step_failure("Excel 写入复核未通过", suggestion="请检查 write_report.json", log_path=str(root / "logs" / "run.log"))
        return fail("write-excel", errors)
    print_step_success("Excel 写入完成，复核通过")
    logger.info("一键流步骤完成：write-excel")

    report["passed"] = True
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_summary = report.get("write_summary", {})
    print_final_success(f"写入 {write_summary.get('write_count', 0)} 个单元格，复核通过")
    from modules.console_ui import verbose_print
    verbose_print("报告：reports/final_run_report.json")
    return _finalize(root, report, logger)


def run_daily_pipeline(
    config: dict[str, Any],
    root: Path,
    logger,
    target_date: str | None = None,
    kst_file: str | Path | None = None,
    today: date | None = None,
    fetch_baidu_func: StepFunc = fetch_baidu_resilient_daily,
    parse_kst_func: Callable[..., dict[str, Any]] = parse_kst_daily_file,
    merge_func: StepFunc = merge_daily_files,
    write_func: StepFunc = write_merged_daily_data,
) -> dict[str, Any]:
    daily_date = target_date or _default_yesterday(today)
    auto_discovered = kst_file is None
    max_age_hours = _max_age_hours_for_info(config)
    export_file = _resolve_path(root, kst_file)
    if export_file is None:
        export_file = find_latest_kst_export(root, config)
    kst_export_info = _file_info(export_file, auto_discovered, max_age_hours)
    excel_path = _resolve_path(root, config.get("excel_path"))

    report: dict[str, Any] = {
        "mode": "run-daily",
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "passed": False,
        "cancelled": False,
        "failed_step": None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "date": daily_date,
        "excel_path": str(excel_path or ""),
        "target_sheet": config.get("daily_sheet_name", "百度"),
        "backup_path": None,
        "kst_export_file": str(export_file or ""),
        "kst_export": kst_export_info,
        "baidu_source_ok": False,
        "data_source": None,
        "api_attempts": 0,
        "fallback_reason": None,
        "self_heal_actions": [],
        "steps": [],
        "write_summary": {
            "write_count": 0,
            "overwrite_count": 0,
            "verification_passed": False,
        },
        "summary_text": "",
        "outputs": {},
        "errors": [],
    }

    def fail(step_name: str, errors: list[str]) -> dict[str, Any]:
        report["failed_step"] = step_name
        report["errors"].extend(errors)
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.error("日报一键流在步骤 %s 中断：%s", step_name, errors)
        return _finalize_daily(root, report, logger)

    def stop_if_requested(boundary: str, claim_excel: bool = False) -> dict[str, Any] | None:
        should_stop = not claim_excel_write(root, resolve_from_environment=True) if claim_excel else task_stop_requested(root)
        if not should_stop:
            return None
        report["cancelled"] = True
        report["failed_step"] = "cancelled-before-excel"
        report["cancelled_at"] = boundary
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        logger.info("用户停止日报任务：boundary=%s；未进入 Excel 写入", boundary)
        print("[停止] 已在日报 Excel 写入前安全停止", flush=True)
        return _finalize_daily(root, report, logger)

    logger.info("日报一键流开始：date=%s，kst_file=%s", daily_date, kst_file)

    stopped = stop_if_requested("before-baidu")
    if stopped:
        return stopped

    print_step(1, 4, "读取百度日报数据")
    try:
        baidu_report = fetch_baidu_func(config=config, root=root, logger=logger, target_date=daily_date)
    except Exception as exc:
        report["steps"].append(_step_result("fetch-baidu-daily", False, errors=[str(exc)]))
        print_step_failure("百度日报读取异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("fetch-baidu-daily", [str(exc)])
    baidu_errors = _errors_from_report(baidu_report)
    report["data_source"] = baidu_report.get("data_source") or "browser"
    report["api_attempts"] = int(baidu_report.get("api_attempts") or 0)
    report["fallback_reason"] = baidu_report.get("fallback_reason")
    report["self_heal_actions"] = list(baidu_report.get("self_heal_actions") or [])
    logger.info("[实际来源] 百度数据：%s", _data_source_label(report["data_source"]))
    baidu_passed = not baidu_errors
    report["steps"].append(_step_result(
        "fetch-baidu-daily",
        baidu_passed,
        outputs={"baidu_daily_data": str(root / "reports" / "baidu_daily_data.json")},
        errors=baidu_errors,
    ))
    if not baidu_passed:
        print_step_failure("百度日报数据读取未通过", suggestion="；".join(baidu_errors), log_path=str(root / "logs" / "run.log"))
        return fail("fetch-baidu-daily", baidu_errors)
    report["baidu_source_ok"] = True
    report["date"] = baidu_report.get("date") or daily_date
    print_step_success("百度日报数据已读取")

    # 未知百度账户提醒
    from modules.baidu_unknown_accounts import print_unknown_baidu_accounts_notice
    print_unknown_baidu_accounts_notice(baidu_report)

    logger.info("日报一键流步骤完成：fetch-baidu-daily")

    stopped = stop_if_requested("after-baidu")
    if stopped:
        return stopped

    print_step(2, 4, "解析商务通日报导出文件")
    if export_file is None:
        reason = "未找到 30 分钟内的商务通日报导出文件，按 0 对话处理"
        kst_result = write_empty_kst_daily_result(config, root, daily_date, reason)
        report["steps"].append(_step_result("parse-kst-daily", True, outputs=kst_result.get("outputs", {})))
        report["outputs"].update(kst_result.get("outputs", {}))
        print_step_success("未找到 30 分钟内的商务通日报导出文件，已按 0 对话处理")
        logger.info("日报一键流步骤完成：parse-kst-daily；%s", reason)
    else:
        if not export_file.exists():
            errors = [f"找不到商务通日报导出文件：{export_file}"]
            report["steps"].append(_step_result("parse-kst-daily", False, errors=errors))
            print_step_failure("商务通日报导出文件不存在", suggestion=f"文件路径：{export_file}")
            return fail("parse-kst-daily", errors)
        report["kst_export_file"] = str(export_file)
        report["kst_export"] = _file_info(export_file, auto_discovered, max_age_hours)

        try:
            kst_result = parse_kst_func(export_file, config, root, daily_date)
        except Exception as exc:
            report["steps"].append(_step_result("parse-kst-daily", False, errors=[str(exc)]))
            print_step_failure("商务通日报解析异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
            return fail("parse-kst-daily", [str(exc)])
        kst_parse_report = kst_result.get("parse_report", {})
        kst_errors = _errors_from_report(kst_parse_report)
        kst_passed = bool(kst_parse_report.get("passed")) and not kst_errors
        report["steps"].append(_step_result(
            "parse-kst-daily",
            kst_passed,
            outputs=kst_result.get("outputs", {}),
            errors=kst_errors,
        ))
        if not kst_passed:
            print_step_failure("商务通日报解析未通过", suggestion="；".join(kst_errors), log_path=str(root / "logs" / "run.log"))
            return fail("parse-kst-daily", kst_errors)
        print_step_success("商务通日报导出文件已解析")
        logger.info("日报一键流步骤完成：parse-kst-daily")

    stopped = stop_if_requested("after-kst")
    if stopped:
        return stopped

    print_step(3, 4, "合并百度日报与商务通日报数据")
    try:
        merge_result = merge_func(config=config, root=root, logger=logger, target_date=daily_date)
    except Exception as exc:
        report["steps"].append(_step_result("merge-daily", False, errors=[str(exc)]))
        print_step_failure("日报数据合并异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("merge-daily", [str(exc)])
    merge_report = merge_result.get("validate_report", {})
    merge_errors = _errors_from_report(merge_report)
    merge_passed = bool(merge_report.get("passed")) and not merge_errors
    report["steps"].append(_step_result(
        "merge-daily",
        merge_passed,
        outputs=merge_result.get("outputs", {}),
        errors=merge_errors,
    ))
    if not merge_passed:
        print_step_failure("日报数据合并不通过", suggestion="；".join(merge_errors), log_path=str(root / "logs" / "run.log"))
        return fail("merge-daily", merge_errors)
    if merge_result.get("merged"):
        report["date"] = merge_result["merged"].get("date") or report["date"]
    print_step_success("日报数据已合并")
    logger.info("日报一键流步骤完成：merge-daily")

    stopped = stop_if_requested("before-excel", claim_excel=True)
    if stopped:
        return stopped

    print_step(4, 4, "写入日报 Excel 并复核")
    try:
        write_report = write_func(config=config, root=root, logger=logger, target_date=daily_date)
    except Exception as exc:
        report["steps"].append(_step_result("write-daily", False, errors=[str(exc)]))
        print_step_failure("日报 Excel 写入异常", suggestion=str(exc), log_path=str(root / "logs" / "run.log"))
        return fail("write-daily", [str(exc)])
    write_errors = _errors_from_report(write_report)
    verification_passed = bool(write_report.get("self_check", {}).get("verification_passed"))
    write_passed = not write_errors and verification_passed
    report["steps"].append(_step_result(
        "write-daily",
        write_passed,
        outputs={
            "daily_write_report": str(root / "reports" / "daily_write_report.json"),
            "excel_path": write_report.get("excel_path"),
            "backup_path": write_report.get("backup_path"),
        },
        errors=write_errors,
    ))
    report["excel_path"] = str(write_report.get("excel_path") or report["excel_path"])
    report["backup_path"] = str(write_report.get("backup_path") or "")
    report["date"] = write_report.get("date") or report["date"]
    report["write_summary"] = {
        "write_count": len(write_report.get("writes", [])),
        "overwrite_count": int(write_report.get("overwrite_summary", {}).get("overwrite_count", 0) or 0),
        "verification_passed": verification_passed,
    }
    if not write_passed:
        errors = write_errors or ["日报 Excel 写入后复核未通过"]
        print_step_failure("日报 Excel 写入复核未通过", suggestion="请检查 daily_write_report.json", log_path=str(root / "logs" / "run.log"))
        return fail("write-daily", errors)
    print_step_success("日报 Excel 写入完成，复核通过")
    logger.info("日报一键流步骤完成：write-daily")

    report["passed"] = True
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_summary = report.get("write_summary", {})
    print_final_success(
        f"写入 {write_summary.get('write_count', 0)} 个单元格，覆盖 {write_summary.get('overwrite_count', 0)} 个已有值，复核通过"
    )
    from modules.console_ui import verbose_print
    verbose_print("报告：reports/daily_final_run_report.json")
    return _finalize_daily(root, report, logger)
