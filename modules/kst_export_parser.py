from __future__ import annotations

import csv
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from dateutil import parser as date_parser

from modules.kst_parser import (
    ACCOUNT_KEYS,
    REMARK_KEYS,
    SEARCH_WORD_KEYS,
    TAG_KEYS,
    TIME_KEYS,
    VISITOR_MESSAGE_KEYS,
    aggregate_kst_export_rows,
    empty_kst_accounts,
    pick_value,
)
from modules.text_normalizer import normalize_text
from modules.validators import get_required_accounts


SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}
DEFAULT_AUTO_EXPORT_MAX_AGE_SECONDS = 30 * 60


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def auto_export_max_age_seconds(config: dict[str, Any]) -> int:
    kst_config = config.get("kst", {}) if isinstance(config.get("kst"), dict) else {}
    minutes = kst_config.get("max_file_age_minutes", 30)
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        value = 30
    if value <= 0:
        return DEFAULT_AUTO_EXPORT_MAX_AGE_SECONDS
    return int(value * 60)


def _is_recent_export(path: Path, max_age_seconds: int, now: float | None = None) -> bool:
    try:
        age_seconds = (now if now is not None else time.time()) - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= max_age_seconds


def find_latest_kst_export(root: Path, config: dict[str, Any]) -> Path | None:
    export_dir = _resolve_path(root, config.get("kst", {}).get("export_dir", "kst_exports"))
    max_age_seconds = auto_export_max_age_seconds(config)
    if not export_dir.exists():
        return None
    if export_dir.is_file():
        return export_dir if export_dir.suffix.lower() in SUPPORTED_SUFFIXES and _is_recent_export(export_dir, max_age_seconds) else None
    files = [
        path
        for path in export_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and not path.name.startswith("~$")
        and not path.name.startswith(".")
    ]
    files = [path for path in files if _is_recent_export(path, max_age_seconds)]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def write_empty_kst_export_result(
    config: dict[str, Any],
    root: Path,
    period: str | None,
    reason: str,
) -> dict[str, Any]:
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    dialog_out = reports_dir / "kst_dialog_data.json"
    parse_out = reports_dir / "kst_parse_report.json"
    unmatched_out = reports_dir / "kst_unmatched_rows.json"
    details_out = reports_dir / "kst_account_dialog_details.json"
    target_date = date.today().isoformat()
    accounts = empty_kst_accounts(get_required_accounts(config))
    summary = {
        "raw_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "date_filtered_rows": 0,
        "skipped_no_visitor_messages": 0,
        "no_export_file": True,
    }
    dialog_data = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target_date,
        "period": _normalize_period(period),
        "source": "kst_export",
        "export_file": "",
        "accounts": accounts,
        "summary": summary,
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target_date,
        "period": dialog_data["period"],
        "source": "kst_export",
        "export_file": "",
        "passed": True,
        "field_info": {"headers": []},
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "summary": summary,
        "date_filtered_rows": [],
        "warnings": [reason],
        "errors": [],
    }
    dialog_out.write_text(json.dumps(dialog_data, ensure_ascii=False, indent=2), encoding="utf-8")
    parse_out.write_text(json.dumps(parse_report, ensure_ascii=False, indent=2), encoding="utf-8")
    unmatched_out.write_text("[]", encoding="utf-8")
    details_out.write_text("{}", encoding="utf-8")
    return {
        "dialog_data": dialog_data,
        "parse_report": parse_report,
        "unmatched_rows": [],
        "account_dialog_details": {},
        "outputs": {
            "dialog_data": str(dialog_out),
            "parse_report": str(parse_out),
            "unmatched_rows": str(unmatched_out),
            "account_dialog_details": str(details_out),
        },
    }


def _read_csv(path: Path) -> list[dict[str, Any]]:
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb18030"]
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"CSV 编码识别失败：{last_error}")


def _read_excel(path: Path) -> list[dict[str, Any]]:
    frame = pd.read_excel(path, dtype=str)
    frame = frame.where(pd.notna(frame), "")
    return frame.to_dict(orient="records")


def read_export_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return _read_excel(path)
    raise ValueError(f"不支持的快商通导出文件类型：{path.suffix}")


def _field_present(headers: list[str], aliases: list[str]) -> bool:
    normalized_headers = [normalize_text(header) for header in headers]
    for alias in aliases:
        key = normalize_text(alias)
        if any(key == header or key in header for header in normalized_headers):
            return True
    return False


def _inspect_fields(rows: list[dict[str, Any]]) -> dict[str, Any]:
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


def _normalize_period(period: str | None) -> str:
    if period is None:
        return "15点"
    text = str(period).strip()
    if text in {"11", "11点"}:
        return "11点"
    if text in {"15", "15点"}:
        return "15点"
    if text in {"18", "18点"}:
        return "18点"
    return text


def _parse_row_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = date_parser.parse(str(value), fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed.date().isoformat()


def _filter_current_date_rows(rows: list[dict[str, Any]], target_date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included = []
    excluded = []
    for index, row in enumerate(rows, start=1):
        row_date = _parse_row_date(pick_value(row, TIME_KEYS))
        if row_date == target_date:
            included.append(row)
        else:
            excluded.append({"row_index": index, "reason": f"非当前日期或日期无法识别：{row_date}", "row": row})
    return included, excluded


def parse_kst_export_file(file_path: str | Path, config: dict[str, Any], root: Path, period: str | None) -> dict[str, Any]:
    path = _resolve_path(root, file_path)
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    dialog_out = reports_dir / "kst_dialog_data.json"
    parse_out = reports_dir / "kst_parse_report.json"
    unmatched_out = reports_dir / "kst_unmatched_rows.json"
    details_out = reports_dir / "kst_account_dialog_details.json"

    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    field_info: dict[str, Any] = {"headers": []}

    target_date = date.today().isoformat()
    date_filtered_rows: list[dict[str, Any]] = []

    if not path.exists():
        errors.append(f"快商通导出文件不存在：{path}")
    elif path.suffix.lower() not in SUPPORTED_SUFFIXES:
        errors.append(f"不支持的快商通导出文件类型：{path.suffix}")
    else:
        rows = read_export_rows(path)
        if not rows:
            errors.append("快商通导出文件为空")
        field_info = _inspect_fields(rows)
        if rows and not field_info["has_dialog_time"]:
            errors.append("未识别到对话时间字段，无法按当前日期统计")
        if rows and not field_info["has_tag"]:
            errors.append("未识别到名片标签字段")
        if rows and not field_info["has_visitor_messages"]:
            errors.append("未识别到访客消息数/访客发送消息数/访客发送数字段，无法按访客发送数量大于等于 1 统计总对话")
        if rows and not (field_info["has_remark"] or field_info["has_account"]):
            errors.append("未识别到账户归属字段或备注说明推广 ID 字段")
        if rows and field_info["has_dialog_time"]:
            rows, date_filtered_rows = _filter_current_date_rows(rows, target_date)

    if errors:
        aggregate = {
            "accounts": empty_kst_accounts(get_required_accounts(config)),
            "account_dialog_details": {},
            "summary": {
                "raw_rows": len(rows),
                "matched_rows": 0,
                "unmatched_rows": len(rows),
                "date_filtered_rows": 0,
                "skipped_no_visitor_messages": 0,
            },
            "unmatched_rows": [{"row_index": index, "reason": "解析前置校验失败", "row": row} for index, row in enumerate(rows, start=1)],
            "errors": errors,
        }
    else:
        aggregate = aggregate_kst_export_rows(rows, config)
        aggregate["summary"]["raw_rows"] += len(date_filtered_rows)
        aggregate["summary"]["date_filtered_rows"] = len(date_filtered_rows)
        errors = aggregate.get("errors", [])

    dialog_data = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target_date,
        "period": _normalize_period(period),
        "source": "kst_export",
        "export_file": str(path),
        "accounts": aggregate["accounts"],
        "summary": aggregate["summary"],
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": dialog_data["date"],
        "period": dialog_data["period"],
        "source": "kst_export",
        "export_file": str(path),
        "passed": not errors,
        "field_info": field_info,
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "summary": aggregate["summary"],
        "date_filtered_rows": date_filtered_rows,
        "errors": errors,
    }

    dialog_out.write_text(json.dumps(dialog_data, ensure_ascii=False, indent=2), encoding="utf-8")
    parse_out.write_text(json.dumps(parse_report, ensure_ascii=False, indent=2), encoding="utf-8")
    unmatched_out.write_text(json.dumps(aggregate.get("unmatched_rows", []), ensure_ascii=False, indent=2), encoding="utf-8")
    details_out.write_text(json.dumps(aggregate.get("account_dialog_details", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "dialog_data": dialog_data,
        "parse_report": parse_report,
        "unmatched_rows": aggregate.get("unmatched_rows", []),
        "account_dialog_details": aggregate.get("account_dialog_details", {}),
        "outputs": {
            "dialog_data": str(dialog_out),
            "parse_report": str(parse_out),
            "unmatched_rows": str(unmatched_out),
            "account_dialog_details": str(details_out),
        },
    }
