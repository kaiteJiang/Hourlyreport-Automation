from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

from modules.kst_daily_aggregation import (
    DAILY_KST_METRICS,
    default_daily_kst_date,
    empty_daily_kst_accounts,
)
from modules.kst_local.auth import load_or_create_local_token
from modules.kst_parser import empty_kst_accounts
from modules.kst_local.runtime import write_hourly_report
from modules.validators import (
    get_required_accounts,
    validate_daily_kst_counts,
    validate_kst_report,
)


class KstLocalSourceError(RuntimeError):
    """小时报无法从回环商务通 API 取得可信结果。"""

    def __init__(self, message: str, *, category: str = "api_unavailable") -> None:
        super().__init__(message)
        self.category = (
            category
            if category in _SAFE_FAILURE_DETAILS
            else "api_unavailable"
        )


Transport = Callable[[str, dict[str, str], int], Any]

_SAFE_FAILURE_DETAILS = {
    "api_unavailable": "商务通本地 API 不可用",
    "client_not_running": "客户端未运行",
    "database_incompatible": "数据库结构不兼容",
    "database_busy_or_timeout": "数据库忙或读取超时",
    "identity_mapping": "快商通身份映射未就绪",
    "installation_root": "快商通客户端目录无效",
    "data_root": "快商通数据目录无效",
}


def _safe_failure_detail(category: str) -> str:
    return _SAFE_FAILURE_DETAILS.get(category, _SAFE_FAILURE_DETAILS["api_unavailable"])


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout: int,
) -> Any:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        category = str(payload.get("error_category") or "api_unavailable")
        raise KstLocalSourceError(
            _safe_failure_detail(category),
            category=category,
        ) from None
    except (urllib.error.URLError, TimeoutError):
        raise KstLocalSourceError("商务通本地 API 请求失败") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise KstLocalSourceError("商务通本地 API 未返回 JSON") from None


def _validate_loopback_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise KstLocalSourceError("商务通本地 API 必须使用 127.0.0.1 回环地址")
    if parsed.port != 18766:
        raise KstLocalSourceError("商务通本地 API 必须使用固定端口 18766")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise KstLocalSourceError("商务通本地 API 基础地址格式无效")
    return "http://127.0.0.1:18766"


def _local_api_timeout(kst_config: dict[str, Any]) -> int:
    value = kst_config.get("local_api_timeout_seconds")
    if value in (None, ""):
        return 15
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return 15
    return max(1, min(15, timeout))


def write_unavailable_zero_result(
    config: dict[str, Any],
    root: Path,
    period: str | None,
    target_date: str | None,
    reason: str,
    *,
    error_category: str = "api_unavailable",
) -> dict[str, Any]:
    summary = {
        "raw_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "api_unavailable": True,
    }
    payload = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": target_date or date.today().isoformat(),
        "period": period or "15点",
        "source": "kst_local_api_unavailable_zero",
        "accounts": empty_kst_accounts(get_required_accounts(config)),
        "summary": summary,
        "errors": [],
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": payload["date"],
        "period": payload["period"],
        "source": payload["source"],
        "passed": True,
        "summary": summary,
        "diagnostics": {"error_category": error_category},
        "warnings": [reason],
        "errors": [],
    }
    reports_dir = root / "reports"
    dialog_out = write_hourly_report(
        payload,
        reports_dir / "kst_dialog_data.json",
    )
    parse_out = write_hourly_report(
        parse_report,
        reports_dir / "kst_parse_report.json",
    )
    return {
        "dialog_data": payload,
        "parse_report": parse_report,
        "outputs": {
            "dialog_data": str(dialog_out),
            "parse_report": str(parse_out),
        },
    }


def fetch_kst_local_report(
    config: dict[str, Any],
    root: Path,
    period: str | None,
    *,
    target_date: str | None = None,
    transport: Transport = _default_transport,
) -> dict[str, Any]:
    kst_config = config.get("kst", {}) or {}
    base_url = _validate_loopback_url(
        str(kst_config.get("local_api_url") or "http://127.0.0.1:18766")
    )
    query = urllib.parse.urlencode(
        {
            "project_id": config.get("project_id") or "",
            "date": target_date or "",
            "period": period or "15点",
        }
    )
    token_env = str(
        kst_config.get("local_api_token_env") or "KST_LOCAL_API_TOKEN"
    )
    if token_env != "KST_LOCAL_API_TOKEN":
        raise KstLocalSourceError(
            "商务通本地 API 令牌变量必须为 KST_LOCAL_API_TOKEN"
        )
    token = load_or_create_local_token(root)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = _local_api_timeout(kst_config)
    try:
        try:
            payload = transport(
                f"{base_url}/v1/kst/hourly?{query}",
                headers,
                timeout,
            )
        except KstLocalSourceError:
            raise
        except Exception:
            raise KstLocalSourceError("商务通本地 API 请求失败") from None
        if not isinstance(payload, dict):
            raise KstLocalSourceError("商务通本地 API 响应结构不兼容")
        if payload.get("source") != "kst_local_api":
            raise KstLocalSourceError("商务通本地 API 响应来源不可信")
        expected_project_id = str(config.get("project_id") or "")
        if str(payload.get("project_id") or "") != expected_project_id:
            raise KstLocalSourceError("商务通本地 API 响应项目不匹配")
        expected_date = target_date or date.today().isoformat()
        if str(payload.get("date") or "") != expected_date:
            raise KstLocalSourceError("商务通本地 API 响应日期不匹配")
        expected_period = period or "15点"
        if str(payload.get("period") or "") != expected_period:
            raise KstLocalSourceError("商务通本地 API 响应时段不匹配")
        errors = payload.get("errors") or []
        if errors:
            raise KstLocalSourceError("商务通本地 API 返回校验错误")
        if not isinstance(payload.get("accounts"), dict):
            raise KstLocalSourceError("商务通本地 API 响应缺少账户统计")
        if validate_kst_report(payload, get_required_accounts(config)):
            raise KstLocalSourceError("商务通本地 API 响应账户统计不完整")
    except KstLocalSourceError as exc:
        if not bool(kst_config.get("allow_zero_on_unavailable")):
            raise
        safe_detail = _safe_failure_detail(exc.category)
        return write_unavailable_zero_result(
            config,
            root,
            period,
            target_date,
            f"{safe_detail}，商务通指标已按 0 继续",
            error_category=exc.category,
        )

    reports_dir = root / "reports"
    dialog_out = write_hourly_report(
        payload,
        reports_dir / "kst_dialog_data.json",
    )
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": payload.get("date"),
        "period": payload.get("period"),
        "source": "kst_local_api",
        "passed": True,
        "summary": payload.get("summary") or {},
        "warnings": [],
        "errors": [],
    }
    parse_out = write_hourly_report(
        parse_report,
        reports_dir / "kst_parse_report.json",
    )
    return {
        "dialog_data": payload,
        "parse_report": parse_report,
        "outputs": {
            "dialog_data": str(dialog_out),
            "parse_report": str(parse_out),
        },
    }


def _daily_payload_errors(
    payload: dict[str, Any],
    expected_accounts: list[str],
) -> list[str]:
    accounts = payload.get("accounts")
    if not isinstance(accounts, dict):
        return ["商务通本地 API 日报响应缺少账户统计"]
    errors: list[str] = []
    if set(accounts) != set(expected_accounts):
        errors.append("商务通本地 API 日报响应账户不完整")
        return errors
    for account in expected_accounts:
        row = accounts.get(account)
        if not isinstance(row, dict):
            errors.append(f"账户 {account} 日报统计结构无效")
            continue
        for metric in DAILY_KST_METRICS:
            value = row.get(metric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"账户 {account} 字段 {metric} 不是非负整数")
        if not any(
            not isinstance(row.get(metric), int)
            or isinstance(row.get(metric), bool)
            for metric in DAILY_KST_METRICS
        ):
            errors.extend(
                f"账户 {account} {error}"
                for error in validate_daily_kst_counts(row)
            )
    return errors


def _write_daily_result(
    root: Path,
    daily_data: dict[str, Any],
    parse_report: dict[str, Any],
) -> dict[str, Any]:
    reports_dir = root / "reports"
    daily_out = write_hourly_report(
        daily_data,
        reports_dir / "kst_daily_data.json",
    )
    parse_out = write_hourly_report(
        parse_report,
        reports_dir / "kst_daily_parse_report.json",
    )
    unmatched_out = write_hourly_report(
        [],
        reports_dir / "kst_daily_unmatched_rows.json",
    )
    details_out = write_hourly_report(
        {},
        reports_dir / "kst_daily_account_dialog_details.json",
    )
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


def write_unavailable_daily_zero_result(
    config: dict[str, Any],
    root: Path,
    target_date: str | None,
    reason: str,
    *,
    error_category: str = "api_unavailable",
) -> dict[str, Any]:
    resolved_date = target_date or default_daily_kst_date()
    summary = {
        "raw_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "date_filtered_rows": 0,
        "skipped_no_visitor_messages": 0,
        "api_unavailable": True,
    }
    daily_data = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": resolved_date,
        "source": "kst_local_api_unavailable_zero",
        "accounts": empty_daily_kst_accounts(get_required_accounts(config)),
        "summary": summary,
        "errors": [],
    }
    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": resolved_date,
        "source": "kst_local_api_unavailable_zero",
        "passed": True,
        "summary": summary,
        "diagnostics": {"error_category": error_category},
        "warnings": [reason],
        "errors": [],
    }
    return _write_daily_result(root, daily_data, parse_report)


def fetch_kst_local_daily_report(
    config: dict[str, Any],
    root: Path,
    *,
    target_date: str | None = None,
    transport: Transport = _default_transport,
) -> dict[str, Any]:
    kst_config = config.get("kst", {}) or {}
    base_url = _validate_loopback_url(
        str(kst_config.get("local_api_url") or "http://127.0.0.1:18766")
    )
    resolved_date = target_date or default_daily_kst_date()
    query = urllib.parse.urlencode(
        {
            "project_id": config.get("project_id") or "",
            "date": resolved_date,
        }
    )
    token_env = str(
        kst_config.get("local_api_token_env") or "KST_LOCAL_API_TOKEN"
    )
    if token_env != "KST_LOCAL_API_TOKEN":
        raise KstLocalSourceError(
            "商务通本地 API 令牌变量必须为 KST_LOCAL_API_TOKEN"
        )
    token = load_or_create_local_token(root)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = _local_api_timeout(kst_config)
    try:
        try:
            payload = transport(
                f"{base_url}/v1/kst/daily?{query}",
                headers,
                timeout,
            )
        except KstLocalSourceError:
            raise
        except Exception:
            raise KstLocalSourceError("商务通本地 API 请求失败") from None
        if not isinstance(payload, dict):
            raise KstLocalSourceError("商务通本地 API 日报响应结构不兼容")
        if payload.get("source") != "kst_local_api":
            raise KstLocalSourceError("商务通本地 API 日报响应来源不可信")
        expected_project_id = str(config.get("project_id") or "")
        if str(payload.get("project_id") or "") != expected_project_id:
            raise KstLocalSourceError("商务通本地 API 日报响应项目不匹配")
        if str(payload.get("date") or "") != resolved_date:
            raise KstLocalSourceError("商务通本地 API 日报响应日期不匹配")
        if payload.get("errors"):
            raise KstLocalSourceError("商务通本地 API 日报返回校验错误")
        if _daily_payload_errors(payload, get_required_accounts(config)):
            raise KstLocalSourceError("商务通本地 API 日报账户统计不完整")
    except KstLocalSourceError as exc:
        if not bool(kst_config.get("allow_zero_on_unavailable")):
            raise
        safe_detail = _safe_failure_detail(exc.category)
        return write_unavailable_daily_zero_result(
            config,
            root,
            resolved_date,
            f"{safe_detail}，商务通日报指标已按 0 继续",
            error_category=exc.category,
        )

    parse_report = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": payload.get("date"),
        "source": "kst_local_api",
        "passed": True,
        "summary": payload.get("summary") or {},
        "warnings": [],
        "errors": [],
    }
    return _write_daily_result(root, payload, parse_report)


def _write_conversations_result(
    root: Path,
    conversations: list[dict[str, Any]],
    resolved_date: str,
    *,
    unavailable: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    reports_dir = root / "reports"
    payload = {
        "project_id": None,
        "date": resolved_date,
        "source": "kst_local_api_unavailable_zero" if unavailable else "kst_local_api",
        "conversations": conversations,
        "summary": {
            "count": len(conversations),
            "unavailable": unavailable,
            "reason": reason if unavailable else "",
        },
    }
    dialog_out = write_hourly_report(
        payload,
        reports_dir / "kst_conversations_data.json",
    )
    parse_report = {
        "project_id": None,
        "date": resolved_date,
        "source": payload["source"],
        "passed": True,
        "summary": payload["summary"],
        "warnings": [reason] if unavailable else [],
        "errors": [],
    }
    parse_out = write_hourly_report(
        parse_report,
        reports_dir / "kst_conversations_parse_report.json",
    )
    return {
        "conversations": conversations,
        "summary": payload["summary"],
        "outputs": {
            "dialog_data": str(dialog_out),
            "parse_report": str(parse_out),
        },
    }


def fetch_kst_conversations(
    config: dict[str, Any],
    root: Path,
    *,
    target_date: str | None = None,
    transport: Transport = _default_transport,
) -> dict[str, Any]:
    kst_config = config.get("kst", {}) or {}
    base_url = _validate_loopback_url(
        str(kst_config.get("local_api_url") or "http://127.0.0.1:18766")
    )
    resolved_date = target_date or default_daily_kst_date()
    query = urllib.parse.urlencode(
        {
            "project_id": config.get("project_id") or "",
            "date": resolved_date,
        }
    )
    token_env = str(
        kst_config.get("local_api_token_env") or "KST_LOCAL_API_TOKEN"
    )
    if token_env != "KST_LOCAL_API_TOKEN":
        raise KstLocalSourceError(
            "商务通本地 API 令牌变量必须为 KST_LOCAL_API_TOKEN"
        )
    token = load_or_create_local_token(root)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = _local_api_timeout(kst_config)
    try:
        try:
            payload = transport(
                f"{base_url}/v1/kst/conversations?{query}",
                headers,
                timeout,
            )
        except KstLocalSourceError:
            raise
        except Exception:
            raise KstLocalSourceError("商务通本地 API 请求失败") from None
        if not isinstance(payload, dict):
            raise KstLocalSourceError("商务通本地 API 对话响应结构不兼容")
        if payload.get("source") != "kst_local_api":
            raise KstLocalSourceError("商务通本地 API 对话响应来源不可信")
        expected_project_id = str(config.get("project_id") or "")
        if str(payload.get("project_id") or "") != expected_project_id:
            raise KstLocalSourceError("商务通本地 API 对话响应项目不匹配")
        if str(payload.get("date") or "") != resolved_date:
            raise KstLocalSourceError("商务通本地 API 对话响应日期不匹配")
        if payload.get("errors"):
            raise KstLocalSourceError("商务通本地 API 对话返回校验错误")
        conversations = payload.get("conversations")
        if not isinstance(conversations, list):
            raise KstLocalSourceError("商务通本地 API 对话响应缺少 conversations")
    except KstLocalSourceError as exc:
        if not bool(kst_config.get("allow_zero_on_unavailable")):
            raise
        safe_detail = _safe_failure_detail(exc.category)
        return _write_conversations_result(
            root,
            [],
            resolved_date,
            unavailable=True,
            reason=f"{safe_detail}，商务通对话指标已按 0 继续",
        )
    return _write_conversations_result(root, conversations, resolved_date)
