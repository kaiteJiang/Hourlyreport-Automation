from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.kst_daily_aggregation import (
    DAILY_KST_METRICS,
    aggregate_kst_daily_rows,
    classify_daily_dialog_by_tags,
    default_daily_kst_date,
    empty_daily_kst_account_row,
    empty_daily_kst_accounts,
)
from modules.kst_export_parser import (
    SUPPORTED_SUFFIXES,
    _field_present,
    _parse_row_date,
    _resolve_path,
    read_export_rows,
)
from modules.kst_parser import (
    ACCOUNT_KEYS,
    REMARK_KEYS,
    SEARCH_WORD_KEYS,
    TAG_KEYS,
    TIME_KEYS,
    VISITOR_MESSAGE_KEYS,
    pick_value,
)
from modules.validators import get_required_accounts


def write_empty_kst_daily_result(
    config: dict[str, Any],
    root: Path,
    target_date: str | None = None,
    reason: str = "未找到 30 分钟内的商务通日报导出文件，按 0 对话处理",
) -> dict[str, Any]:
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    daily_out = reports_dir / "kst_daily_data.json"
    parse_out = reports_dir / "kst_daily_parse_report.json"
    unmatched_out = reports_dir / "kst_daily_unmatched_rows.json"
    details_out = reports_dir / "kst_daily_account_dialog_details.json"
    target = target_date or default_daily_kst_date()
    accounts = empty_daily_kst_accounts(get_required_accounts(config))
    summary = {
        "raw_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "date_filtered_rows": 0,
        "skipped_no_visitor_messages": 0,
        "no_export_file": True,
    }
    daily_data = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target,
        "source": "kst_daily_export",
        "export_file": "",
        "accounts": accounts,
        "summary": summary,
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target,
        "source": "kst_daily_export",
        "export_file": "",
        "passed": True,
        "field_info": {"headers": []},
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "summary": summary,
        "date_filtered_rows": [],
        "warnings": [reason],
        "errors": [],
    }
    daily_out.write_text(json.dumps(daily_data, ensure_ascii=False, indent=2), encoding="utf-8")
    parse_out.write_text(json.dumps(parse_report, ensure_ascii=False, indent=2), encoding="utf-8")
    unmatched_out.write_text("[]", encoding="utf-8")
    details_out.write_text("{}", encoding="utf-8")
    return {
        "daily_data": daily_data,
        "parse_report": parse_report,
        "unmatched_rows": [],
        "account_dialog_details": {},
        "outputs": {
            "daily_data": str(daily_out),
            "parse_report": str(parse_out),
            "unmatched_rows": str(unmatched_out),
            "account_dialog_details": str(details_out),
        },
    }


def _inspect_daily_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
    headers = list(rows[0].keys()) if rows else []
    return {
        "headers": headers,
        "has_dialog_time": _field_present(headers, TIME_KEYS),
        "has_tag": _field_present(headers, TAG_KEYS),
        "has_remark": _field_present(headers, REMARK_KEYS),
        "has_account": _field_present(headers, ACCOUNT_KEYS),
        "has_search_word": _field_present(headers, SEARCH_WORD_KEYS),
        "has_visitor_messages": _field_present(headers, VISITOR_MESSAGE_KEYS),
    }


def _filter_rows_by_date(rows: list[dict[str, Any]], target_date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included = []
    excluded = []
    for index, row in enumerate(rows, start=1):
        row_date = _parse_row_date(pick_value(row, TIME_KEYS))
        if row_date == target_date:
            included.append(row)
        else:
            excluded.append({"row_index": index, "reason": f"非目标日报日期或日期无法识别：{row_date}", "row": row})
    return included, excluded


def parse_kst_daily_file(file_path: str | Path, config: dict[str, Any], root: Path, target_date: str | None = None) -> dict[str, Any]:
    path = _resolve_path(root, file_path)
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    daily_out = reports_dir / "kst_daily_data.json"
    parse_out = reports_dir / "kst_daily_parse_report.json"
    unmatched_out = reports_dir / "kst_daily_unmatched_rows.json"
    details_out = reports_dir / "kst_daily_account_dialog_details.json"

    target = target_date or default_daily_kst_date()
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    field_info: dict[str, Any] = {"headers": []}
    date_filtered_rows: list[dict[str, Any]] = []

    if not path.exists():
        errors.append(f"商务通日报导出文件不存在：{path}")
    elif path.suffix.lower() not in SUPPORTED_SUFFIXES:
        errors.append(f"不支持的商务通日报导出文件类型：{path.suffix}")
    else:
        rows = read_export_rows(path)
        if not rows:
            errors.append("商务通日报导出文件为空")
        field_info = _inspect_daily_fields(rows)
        if rows and not field_info["has_dialog_time"]:
            errors.append("未识别到对话时间字段，无法按日报日期统计")
        if rows and not field_info["has_tag"]:
            errors.append("未识别到名片标签字段")
        if rows and not field_info["has_visitor_messages"]:
            errors.append("未识别到访客消息数/访客发送消息数/访客发送数字段，无法按访客发送数量大于等于 1 统计总对话")
        if rows and not (field_info["has_remark"] or field_info["has_account"]):
            errors.append("未识别到账户归属字段或备注说明推广 ID 字段")
        if rows and field_info["has_dialog_time"]:
            rows, date_filtered_rows = _filter_rows_by_date(rows, target)

    if errors:
        aggregate = {
            "accounts": empty_daily_kst_accounts(get_required_accounts(config)),
            "account_dialog_details": {},
            "summary": {
                "raw_rows": len(rows),
                "matched_rows": 0,
                "unmatched_rows": len(rows),
                "date_filtered_rows": len(date_filtered_rows),
                "skipped_no_visitor_messages": 0,
            },
            "unmatched_rows": [{"row_index": index, "reason": "解析前置校验失败", "row": row} for index, row in enumerate(rows, start=1)],
            "errors": errors,
        }
    else:
        aggregate = aggregate_kst_daily_rows(rows, config)
        aggregate["summary"]["raw_rows"] += len(date_filtered_rows)
        aggregate["summary"]["date_filtered_rows"] = len(date_filtered_rows)
        errors = aggregate.get("errors", [])

    daily_data = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target,
        "source": "kst_daily_export",
        "export_file": str(path),
        "accounts": aggregate["accounts"],
        "summary": aggregate["summary"],
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target,
        "source": "kst_daily_export",
        "export_file": str(path),
        "passed": not errors,
        "field_info": field_info,
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "summary": aggregate["summary"],
        "date_filtered_rows": date_filtered_rows,
        "errors": errors,
    }

    daily_out.write_text(json.dumps(daily_data, ensure_ascii=False, indent=2), encoding="utf-8")
    parse_out.write_text(json.dumps(parse_report, ensure_ascii=False, indent=2), encoding="utf-8")
    unmatched_out.write_text(json.dumps(aggregate.get("unmatched_rows", []), ensure_ascii=False, indent=2), encoding="utf-8")
    details_out.write_text(json.dumps(aggregate.get("account_dialog_details", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "daily_data": daily_data,
        "parse_report": parse_report,
        "unmatched_rows": aggregate.get("unmatched_rows", []),
        "account_dialog_details": aggregate.get("account_dialog_details", {}),
        "outputs": {
            "daily_data": str(daily_out),
            "parse_report": str(parse_out),
            "unmatched_rows": str(unmatched_out),
            "account_dialog_details": str(details_out),
        },
    }
