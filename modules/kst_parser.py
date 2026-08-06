from __future__ import annotations

import re
from typing import Any

from modules.text_normalizer import normalize_for_display, normalize_text
from modules.validators import get_required_accounts, validate_kst_report
from modules.project_config import load_default_runtime_config


KST_METRICS = ["总对话", "有效对话", "一般有效", "有效转潜", "总转潜"]
ACCOUNT_KEYS = ["账户", "推广账户", "来源账户", "项目", "来源", "账户名称"]
REMARK_KEYS = ["备注说明", "备注", "说明"]
TAG_KEYS = ["名片标签", "标签", "客户标签", "访客标签"]
TIME_KEYS = ["对话时间"]
SEARCH_WORD_KEYS = ["搜索词", "搜索关键词", "对话关键词"]
VISITOR_MESSAGE_KEYS = ["访客消息数", "访客发送消息数", "访客发送数", "访客消息", "访客消息量", "客户消息数", "访客发言数"]
PROMOTION_ID_MAP: dict[str, str] = {}


def _effective_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("accounts") or config.get("kst", {}).get("promotion_id_accounts"):
        return config
    loaded = load_default_runtime_config()
    return loaded or config


def extract_promotion_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    labeled = re.search(r"推广\s*ID\s*[:：]?\s*(\d{6,})", text, flags=re.IGNORECASE)
    if labeled:
        return labeled.group(1)
    leading = re.match(r"\s*(\d{6,})", text)
    if leading:
        return leading.group(1)
    return None


def classify_dialog_by_tags(tags: str | None) -> dict[str, int]:
    text = normalize_for_display(tags)
    is_valid_lead = "转潜-有效" in text
    return {
        "总对话": 1,
        "有效对话": 1 if is_valid_lead or "有效-三句" in text else 0,
        "一般有效": 1 if "有效-一般" in text else 0,
        "有效转潜": 1 if is_valid_lead else 0,
        "总转潜": 1 if "转潜-" in text else 0,
    }


def _parse_non_negative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = normalize_for_display(value).strip()
    if not text:
        return None
    match = re.search(r"-?\d+", text.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def has_visitor_dialog(row: dict[str, Any]) -> bool:
    count = _parse_non_negative_int(pick_value(row, VISITOR_MESSAGE_KEYS))
    return count is not None and count >= 1


def empty_kst_account_row() -> dict[str, int]:
    return {metric: 0 for metric in KST_METRICS}


def empty_kst_accounts(accounts: list[str] | None = None) -> dict[str, dict[str, int]]:
    return {account: empty_kst_account_row() for account in (accounts or [])}


def pick_value(row: dict[str, Any], aliases: list[str]) -> Any:
    normalized = {normalize_text(k): v for k, v in row.items()}
    for alias in aliases:
        key = normalize_text(alias)
        if key in normalized:
            return normalized[key]
    for key, value in normalized.items():
        if any(normalize_text(alias) in key for alias in aliases):
            return value
    return None


def map_account_from_row(row: dict[str, Any], config: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    config = _effective_config(config)
    remark = pick_value(row, REMARK_KEYS)
    if remark not in (None, ""):
        promotion_id = extract_promotion_id(remark)
        if promotion_id:
            promotion_map = config.get("kst", {}).get("promotion_id_accounts", PROMOTION_ID_MAP)
            if promotion_id in promotion_map:
                return promotion_map[promotion_id], {
                    "source_type": "promotion_id_from_remark",
                    "source_value": str(remark),
                    "promotion_id": promotion_id,
                }
            return None, {
                "source_type": "promotion_id_from_remark",
                "source_value": str(remark),
                "promotion_id": promotion_id,
                "reason": "推广 ID 不在映射表中",
            }

    raw_account = pick_value(row, ACCOUNT_KEYS)
    normalized = normalize_text(raw_account)
    account_map: dict[str, str] = {}
    for standard_name, info in config.get("accounts", {}).items():
        names = [standard_name, info.get("excel_name", ""), info.get("baidu_name", "")]
        names.extend(info.get("aliases", []))
        for name in names:
            key = normalize_text(name)
            if key:
                account_map[key] = standard_name
    if normalized in account_map:
        return account_map[normalized], {"source_type": "account_name", "source_value": str(raw_account)}
    for alias, standard_name in account_map.items():
        if alias and normalized and alias in normalized:
            return standard_name, {"source_type": "account_name", "source_value": str(raw_account)}
    return None, {"source_type": "unmapped", "source_value": str(raw_account or remark or "")}


def aggregate_kst_export_rows(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    config = _effective_config(config)
    expected_accounts = get_required_accounts(config)
    accounts = empty_kst_accounts(expected_accounts)
    account_dialog_details: dict[str, list[dict[str, Any]]] = {account: [] for account in expected_accounts}
    unmatched_rows: list[dict[str, Any]] = []
    matched_rows = 0
    skipped_no_visitor_messages = 0

    for index, row in enumerate(rows, start=1):
        account, source = map_account_from_row(row, config)
        if not account:
            unmatched_rows.append({"row_index": index, "reason": source.get("reason", "无法归属账户"), "row": row, "source": source})
            continue
        tags = pick_value(row, TAG_KEYS)
        if has_visitor_dialog(row):
            counts = classify_dialog_by_tags(None if tags is None else str(tags))
        else:
            counts = empty_kst_account_row()
            skipped_no_visitor_messages += 1
        for metric, value in counts.items():
            accounts[account][metric] += value
        account_dialog_details[account].append({
            "row_index": index,
            "dialog_time": pick_value(row, TIME_KEYS),
            "promotion_id": source.get("promotion_id"),
            "source_type": source.get("source_type"),
            "tag": tags,
            "search_word": pick_value(row, SEARCH_WORD_KEYS),
            "visitor_name": row.get("访客名称", ""),
            "visitor_messages": pick_value(row, VISITOR_MESSAGE_KEYS),
            "counts": counts,
        })
        matched_rows += 1

    report = {
        "accounts": accounts,
        "account_dialog_details": account_dialog_details,
        "summary": {
            "raw_rows": len(rows),
            "matched_rows": matched_rows,
            "unmatched_rows": len(unmatched_rows),
            "skipped_no_visitor_messages": skipped_no_visitor_messages,
        },
        "unmatched_rows": unmatched_rows,
        "errors": [],
    }
    report["errors"].extend(validate_kst_report(report, expected_accounts))
    return report
