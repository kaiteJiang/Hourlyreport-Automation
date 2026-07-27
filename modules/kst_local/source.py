from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

from modules.kst_parser import empty_kst_accounts
from modules.kst_local.runtime import write_hourly_report
from modules.validators import get_required_accounts, validate_kst_report


class KstLocalSourceError(RuntimeError):
    """小时报无法从回环商务通 API 取得可信结果。"""


Transport = Callable[[str, dict[str, str], int], Any]


def _default_transport(
    url: str,
    headers: dict[str, str],
    timeout: int,
) -> Any:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
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


def write_unavailable_zero_result(
    config: dict[str, Any],
    root: Path,
    period: str | None,
    target_date: str | None,
    reason: str,
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
    token = os.environ.get("KST_LOCAL_API_TOKEN", "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = int(kst_config.get("local_api_timeout_seconds") or 60)
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
        errors = payload.get("errors") or []
        if errors:
            raise KstLocalSourceError("商务通本地 API 返回校验错误")
        if not isinstance(payload.get("accounts"), dict):
            raise KstLocalSourceError("商务通本地 API 响应缺少账户统计")
        if validate_kst_report(payload, get_required_accounts(config)):
            raise KstLocalSourceError("商务通本地 API 响应账户统计不完整")
    except KstLocalSourceError:
        if not bool(kst_config.get("allow_zero_on_unavailable")):
            raise
        return write_unavailable_zero_result(
            config,
            root,
            period,
            target_date,
            "商务通本地 API 不可用，商务通指标已按 0 继续",
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
