from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from modules.kst_parser import (
    SEARCH_WORD_KEYS,
    TAG_KEYS,
    TIME_KEYS,
    VISITOR_MESSAGE_KEYS,
    _effective_config,
    _parse_non_negative_int,
    has_visitor_dialog,
    map_account_from_row,
    pick_value,
)
from modules.text_normalizer import normalize_for_display
from modules.validators import get_required_accounts


DAILY_KST_METRICS = [
    "总对话",
    "有效对话",
    "无效对话",
    "一般有效对话",
    "有效转潜",
    "总转潜",
]


def default_daily_kst_date(today: date | None = None) -> str:
    base = today or date.today()
    return (base - timedelta(days=1)).isoformat()


def classify_daily_dialog_by_tags(tags: str | None) -> dict[str, int]:
    text = normalize_for_display(tags)
    is_valid = any(key in text for key in ["转潜-有效", "有效-三句"])
    is_general = "有效-一般" in text
    return {
        "总对话": 1,
        "有效对话": 1 if is_valid else 0,
        "无效对话": 0 if is_valid or is_general else 1,
        "一般有效对话": 1 if is_general else 0,
        "有效转潜": 1 if "转潜-有效" in text else 0,
        "总转潜": 1 if "转潜-" in text else 0,
    }


def empty_daily_kst_account_row() -> dict[str, int]:
    return {metric: 0 for metric in DAILY_KST_METRICS}


def empty_daily_kst_accounts(
    accounts: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    return {
        account: empty_daily_kst_account_row()
        for account in (accounts or [])
    }


def _validate_daily_accounts(
    accounts: dict[str, dict[str, int]],
    expected_accounts: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    expected_list = expected_accounts or list(accounts.keys())
    actual = set(accounts)
    expected = set(expected_list)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(
            f"日报商务通账户不足 3 个，缺少：{', '.join(missing)}"
        )
    if extra:
        errors.append(f"日报商务通账户多于 3 个：{', '.join(extra)}")
    for account in expected_list:
        row = accounts.get(account, {})
        for field in DAILY_KST_METRICS:
            value = row.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(
                    f"账户 {account} 字段 {field} 不是非负整数：{value!r}"
                )
        total = row.get("总对话", 0)
        valid = row.get("有效对话", 0)
        invalid = row.get("无效对话", 0)
        general_valid = row.get("一般有效对话", 0)
        valid_qian = row.get("有效转潜", 0)
        total_qian = row.get("总转潜", 0)
        if valid > total:
            errors.append(f"账户 {account} 有效对话大于总对话")
        if valid + general_valid + invalid < total:
            errors.append(
                f"账户 {account} 有效、一般与无效对话未覆盖总对话"
            )
        if max(valid, general_valid) + invalid > total:
            errors.append(
                f"账户 {account} 无效对话与有效或一般对话存在重复"
            )
        if valid_qian > valid:
            errors.append(f"账户 {account} 有效转潜大于有效对话")
        if total_qian > total:
            errors.append(f"账户 {account} 总转潜大于总对话")
    return errors


def aggregate_kst_daily_rows(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    config = _effective_config(config)
    expected_accounts = get_required_accounts(config)
    accounts = empty_daily_kst_accounts(expected_accounts)
    account_dialog_details: dict[str, list[dict[str, Any]]] = {
        account: [] for account in expected_accounts
    }
    unmatched_rows: list[dict[str, Any]] = []
    matched_rows = 0
    skipped_no_visitor_messages = 0

    for index, row in enumerate(rows, start=1):
        account, source = map_account_from_row(row, config)
        if not account:
            unmatched_rows.append(
                {
                    "row_index": index,
                    "reason": source.get("reason", "无法归属账户"),
                    "row": row,
                    "source": source,
                }
            )
            continue
        tags = pick_value(row, TAG_KEYS)
        if has_visitor_dialog(row):
            counts = classify_daily_dialog_by_tags(
                None if tags is None else str(tags)
            )
        else:
            counts = empty_daily_kst_account_row()
            skipped_no_visitor_messages += 1
        for metric, value in counts.items():
            accounts[account][metric] += value
        account_dialog_details[account].append(
            {
                "row_index": index,
                "dialog_time": pick_value(row, TIME_KEYS),
                "promotion_id": source.get("promotion_id"),
                "source_type": source.get("source_type"),
                "tag": tags,
                "search_word": pick_value(row, SEARCH_WORD_KEYS),
                "visitor_messages": pick_value(
                    row,
                    VISITOR_MESSAGE_KEYS,
                ),
                "counts": counts,
            }
        )
        matched_rows += 1

    errors = _validate_daily_accounts(accounts, expected_accounts)
    return {
        "accounts": accounts,
        "account_dialog_details": account_dialog_details,
        "summary": {
            "raw_rows": len(rows),
            "matched_rows": matched_rows,
            "unmatched_rows": len(unmatched_rows),
            "skipped_no_visitor_messages": skipped_no_visitor_messages,
        },
        "unmatched_rows": unmatched_rows,
        "errors": errors,
    }


def aggregate_word_class_conversations(
    conversations: list[dict[str, Any]],
    keywords: tuple[str, ...] = ("银屑病", "牛皮癣"),
) -> dict[str, Any]:
    """按"搜索关键词"筛选含关键字的对话,统计词类占比指标。

    只匹配搜索关键词(keyword),不匹配竞价词,避免竞价词命中把无关对话算进来。
    有效对话 = 关键词命中 且 标签含"有效"二字(含"有效-一般");
    有效转潜 = 关键词命中 且 标签含"转潜-有效"(有效转潜 ⊆ 有效对话)。
    """
    counts = {"总对话": 0, "有效对话": 0, "有效转潜": 0}
    keyword_counts = {keyword: 0 for keyword in keywords}
    matched_conversations = 0
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        text = str(conv.get("keyword") or "").strip()
        if not any(keyword in text for keyword in keywords):
            continue
        matched_conversations += 1
        for keyword in keywords:
            if keyword in text:
                keyword_counts[keyword] += 1
        tags_text = " | ".join(
            str(tag) for tag in (conv.get("tags") or []) if tag
        )
        counts["总对话"] += 1
        if "有效" in tags_text:
            counts["有效对话"] += 1
        if "转潜-有效" in tags_text:
            counts["有效转潜"] += 1
    return {
        "counts": counts,
        "matched_conversations": matched_conversations,
        "keyword_counts": keyword_counts,
    }


def export_rows_to_word_class_conversations(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把导出文件按日期过滤后的原始行转为词类占比聚合所需结构。

    词类占比是整项目统计,不需要账户归属;直接取每行的"搜索关键词"和
    "名片标签",与日报共用同一份导出的商务通对话表。仅保留有访客消息
    (访客消息数 >= 1)的对话,与日报"访客对话"口径一致。
    """
    conversations: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        search_word = str(pick_value(row, SEARCH_WORD_KEYS) or "").strip()
        if not search_word:
            continue
        visitor_messages = pick_value(row, VISITOR_MESSAGE_KEYS)
        visitor_count = _parse_non_negative_int(visitor_messages)
        if visitor_count is None or visitor_count < 1:
            continue
        tag = pick_value(row, TAG_KEYS)
        conversations.append(
            {
                "keyword": search_word,
                "tags": [str(tag)] if tag not in (None, "") else [],
                "visitor_messages": visitor_messages,
            }
        )
    return conversations
