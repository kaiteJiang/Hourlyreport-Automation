from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from modules.validators import get_required_accounts, validate_baidu_report
from modules.baidu_token_manager import BaiduTokenError, ensure_valid_access_token_cloud_first


REPORT_API_URL = "https://api.baidu.com/json/sms/service/OpenApiReportService/getReportData"
ACCOUNT_REPORT_TYPE = 2208157
REPORT_COLUMNS = ["date", "userName", "userId", "impression", "click", "cost"]
# 搜索词报告（搜索推广，词维度）。经真实 API 探针校准：reportType=2307838，
# 行键为 date/userName/userId/queryWord/impression/click/cost。
SEARCH_WORD_REPORT_TYPE = 2307838
SEARCH_WORD_COLUMNS = [
    "date",
    "userName",
    "userId",
    "queryWord",
    "impression",
    "click",
    "cost",
]
WORD_CLASS_KEYWORDS = ("银屑病", "牛皮癣")
SEARCH_WORD_PAGE_SIZE = 1000
SEARCH_WORD_MAX_PAGES = 50
ATTEMPT_ERROR_SUMMARIES = {
    "api_error": "百度 API 读取失败，请稍后重试",
    "authorization_error": "百度 API 授权校验失败，请检查授权状态",
    "configuration_error": "百度 API 配置无效，请检查项目配置",
    "integrity_error": "百度 API 返回数据未通过完整性校验",
    "network_error": "百度 API 网络请求失败，请稍后重试",
    "reauthorization_required": "百度 API 授权已失效，需要重新授权",
}


def _safe_attempt_detail(category: str, message: str) -> str:
    if category != "integrity_error":
        return ATTEMPT_ERROR_SUMMARIES[category]
    checks = (
        ("日期不一致", "百度 API 返回日期不一致"),
        ("缺少日期", "百度 API 返回账户日期缺失"),
        ("未知推广 ID", "百度 API 返回未知推广账户"),
        ("重复账户", "百度 API 返回重复账户"),
        ("有限数字", "百度 API 返回字段数值异常"),
        ("负数", "百度 API 返回字段数值异常"),
        ("汇总校验失败", "百度 API 汇总校验失败"),
        ("未返回完整汇总", "百度 API 汇总校验失败"),
    )
    return next(
        (detail for marker, detail in checks if marker in message),
        ATTEMPT_ERROR_SUMMARIES[category],
    )


class BaiduReportApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        category: str = "api_error",
        reauthorization_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.reauthorization_required = reauthorization_required


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if integer:
        return int(parsed) if parsed.is_integer() else None
    return round(parsed, 2)


def _account_user_ids(config: dict[str, Any]) -> tuple[list[int], dict[int, str]]:
    user_ids: list[int] = []
    account_by_id: dict[int, str] = {}
    for standard_name, account in (config.get("accounts") or {}).items():
        candidates = account.get("baidu_user_ids") or account.get("kst_ids") or []
        if not candidates:
            raise BaiduReportApiError(f"账户 {standard_name} 未配置百度推广 ID")
        raw_id = str(candidates[0]).strip()
        if not raw_id.isdigit():
            raise BaiduReportApiError(f"账户 {standard_name} 的百度推广 ID 不是纯数字")
        user_id = int(raw_id)
        if user_id in account_by_id:
            raise BaiduReportApiError(f"百度推广 ID 重复：{user_id}")
        user_ids.append(user_id)
        account_by_id[user_id] = str(standard_name)
    if not user_ids:
        raise BaiduReportApiError("当前项目没有可用于 API 报表的账户 ID")
    return user_ids, account_by_id


def _load_api_identity(root: Path, config: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    secrets_path = _resolve_path(root, config.get("credentials_path", "secrets/secrets.json"))
    if not secrets_path.exists():
        raise BaiduReportApiError(f"找不到凭据文件：{secrets_path}")
    secrets = _read_json(secrets_path)

    credential_profile = str(
        config.get("baidu", {}).get("credential_profile")
        or config.get("baidu", {}).get("credential_project")
        or ""
    )
    api_profile = str(config.get("baidu", {}).get("api_profile") or "")
    if not credential_profile:
        raise BaiduReportApiError("当前项目未配置百度登录 profile")
    if not api_profile:
        raise BaiduReportApiError("当前项目未配置 baidu.api_profile，API 通道尚未启用")

    browser_username = str(
        (secrets.get("baidu", {}).get(credential_profile) or {}).get("username") or ""
    ).strip()
    api_record = secrets.get("baidu_api", {}).get(api_profile) or {}
    username = str(api_record.get("master_name") or browser_username).strip()
    if not username:
        raise BaiduReportApiError(
            f"百度 API profile 缺少 master_name，且百度凭据 profile 缺少 username：{api_profile}"
        )
    return username, api_profile, secrets


def _load_api_auth(root: Path, config: dict[str, Any]) -> tuple[str, str, str]:
    username, api_profile, secrets = _load_api_identity(root, config)
    token = str((secrets.get("baidu_api", {}).get(api_profile) or {}).get("access_token") or "").strip()
    if not token:
        raise BaiduReportApiError(f"百度 API profile 缺少 access_token：{api_profile}")
    if token.count(".") != 2:
        raise BaiduReportApiError(f"百度 API profile 的 access_token 格式无效：{api_profile}")
    return username, token, api_profile


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json;charset=UTF-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if 500 <= int(exc.code) <= 599:
            category = "network_error"
        elif int(exc.code) in {401, 403}:
            category = "authorization_error"
        else:
            category = "api_error"
        raise BaiduReportApiError("百度 API HTTP 请求失败", category=category) from exc
    except urllib.error.URLError as exc:
        raise BaiduReportApiError("百度 API 网络连接失败", category="network_error") from exc
    except TimeoutError as exc:
        raise BaiduReportApiError("百度 API 请求超时", category="network_error") from exc
    except json.JSONDecodeError as exc:
        raise BaiduReportApiError("百度 API 返回内容不是合法 JSON", category="api_error") from exc


def _build_payload(username: str, token: str, user_ids: list[int], target_date: str) -> dict[str, Any]:
    return {
        "header": {"userName": username, "accessToken": token},
        "body": {
            "reportType": ACCOUNT_REPORT_TYPE,
            "userIds": user_ids,
            "startDate": target_date,
            "endDate": target_date,
            "timeUnit": "DAY",
            "columns": REPORT_COLUMNS,
            "sorts": [],
            "filters": [],
            "startRow": 0,
            "rowCount": max(20, len(user_ids)),
            "needSum": True,
        },
    }


def _build_search_word_payload(
    username: str,
    token: str,
    user_ids: list[int],
    target_date: str,
    start_row: int = 0,
    row_count: int = SEARCH_WORD_PAGE_SIZE,
) -> dict[str, Any]:
    return {
        "header": {"userName": username, "accessToken": token},
        "body": {
            "reportType": SEARCH_WORD_REPORT_TYPE,
            "userIds": user_ids,
            "startDate": target_date,
            "endDate": target_date,
            "timeUnit": "DAY",
            "columns": SEARCH_WORD_COLUMNS,
            "sorts": [],
            "filters": [],
            "startRow": start_row,
            "rowCount": row_count,
            "needSum": False,
        },
    }


def aggregate_search_word_rows(
    rows: list[dict[str, Any]],
    keywords: tuple[str, ...] = WORD_CLASS_KEYWORDS,
) -> dict[str, Any]:
    """按搜索词列 queryWord 过滤含关键字(银屑病/牛皮癣)的行,加总点击/消费/展现。"""
    click = 0
    cost = 0.0
    impression = 0
    matched_rows = 0
    keyword_counts = {keyword: 0 for keyword in keywords}
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        search_word = str(row.get("queryWord") or "").strip()
        if not search_word:
            continue
        if not any(keyword in search_word for keyword in keywords):
            continue
        matched_rows += 1
        for keyword in keywords:
            if keyword in search_word:
                keyword_counts[keyword] += 1
        row_click = _number(row.get("click"), integer=True)
        row_cost = _number(row.get("cost"))
        row_impression = _number(row.get("impression"), integer=True)
        if row_click is None or row_cost is None or row_impression is None:
            errors.append(f"搜索词 {search_word} 存在非有限数字")
            continue
        if row_click < 0 or row_cost < 0 or row_impression < 0:
            errors.append(f"搜索词 {search_word} 存在负数值")
            continue
        click += row_click
        cost = round(cost + row_cost, 2)
        impression += row_impression
    return {
        "click": click,
        "cost": cost,
        "impression": impression,
        "matched_rows": matched_rows,
        "keyword_counts": keyword_counts,
        "errors": errors,
    }


def _parse_api_response(
    response: dict[str, Any],
    *,
    config: dict[str, Any],
    account_by_id: dict[int, str],
    expected_date: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    errors: list[str] = []
    header = response.get("header") or {}
    failures = header.get("failures") or []
    if header.get("status") != 0 or str(header.get("desc") or "").lower() != "success" or failures:
        codes = ", ".join(str(item.get("code") or "unknown") for item in failures)
        detail = f"，错误码：{codes}" if codes else ""
        errors.append(f"百度 API 返回失败：{header.get('desc') or 'unknown'}{detail}")

    data = (response.get("body") or {}).get("data") or []
    wrapper = data[0] if data and isinstance(data[0], dict) else {}
    rows = wrapper.get("rows") or []
    summary = wrapper.get("summary") or {}
    accounts: dict[str, Any] = {}
    unknown_rows: list[dict[str, Any]] = []
    returned_totals = {"impression": 0, "click": 0, "cost": 0.0}

    for row in rows:
        row_date = str(row.get("date") or "").strip()
        if expected_date:
            if not row_date:
                errors.append("百度 API 返回账户行缺少日期")
            elif row_date != expected_date:
                errors.append(f"百度 API 返回日期不一致：{row_date} != {expected_date}")
        raw_id = row.get("userId")
        try:
            user_id = int(raw_id)
        except (TypeError, ValueError):
            errors.append("百度 API 返回了无效的 userId")
            continue
        standard_name = account_by_id.get(user_id)
        metrics = {
            "展现": _number(row.get("impression"), integer=True),
            "点击": _number(row.get("click"), integer=True),
            "消费": _number(row.get("cost")),
        }
        for field, value in metrics.items():
            if value is None:
                errors.append(f"百度 API 账户 {standard_name or user_id} 字段 {field} 不是有限数字")
            elif value < 0:
                errors.append(f"百度 API 账户 {standard_name or user_id} 字段 {field} 不能为负数")
        if all(value is not None and value >= 0 for value in metrics.values()):
            returned_totals["impression"] += int(metrics["展现"])
            returned_totals["click"] += int(metrics["点击"])
            returned_totals["cost"] = round(returned_totals["cost"] + float(metrics["消费"]), 2)
        if not standard_name:
            unknown_rows.append({"userId": user_id, "userName": row.get("userName"), **metrics})
            errors.append(f"百度 API 返回未知推广 ID：{user_id}")
            continue
        if standard_name in accounts:
            errors.append(f"百度 API 返回重复账户：{standard_name}")
            continue
        accounts[standard_name] = {
            "source_account": row.get("userName") or standard_name,
            "source_user_id": user_id,
            **metrics,
        }

    empty_success = (
        header.get("status") == 0
        and str(header.get("desc") or "").lower() == "success"
        and not failures
        and wrapper.get("totalRowCount") in (0, "0")
        and not rows
        and not summary
    )
    summary_metrics = (
        {"impression": 0, "click": 0, "cost": 0.0}
        if empty_success
        else {
            "impression": _number(
                summary.get("impression"),
                integer=True,
            ),
            "click": _number(summary.get("click"), integer=True),
            "cost": _number(summary.get("cost")),
        }
    )
    summary_complete = all(value is not None for value in summary_metrics.values())
    summary_matches_rows = False
    if summary_complete:
        summary_matches_rows = True
        for field, tolerance in (("impression", 0), ("click", 0), ("cost", 0.01)):
            diff = round(float(summary_metrics[field]) - float(returned_totals[field]), 2)
            if abs(diff) > tolerance:
                summary_matches_rows = False
                errors.append(f"百度 API 汇总校验失败：{field} 差额 {diff}")
    else:
        errors.append("百度 API 未返回完整汇总指标")

    zero_filled_accounts: list[str] = []
    if not errors and summary_complete and summary_matches_rows:
        for user_id, standard_name in account_by_id.items():
            if standard_name in accounts:
                continue
            accounts[standard_name] = {
                "source_account": standard_name,
                "source_user_id": user_id,
                "展现": 0,
                "点击": 0,
                "消费": 0.0,
                "synthetic_zero": True,
            }
            zero_filled_accounts.append(standard_name)

    account_report = {"accounts": accounts}
    errors.extend(error for error in validate_baidu_report(account_report, get_required_accounts(config)) if error not in errors)

    diagnostics = {
        "api_status": header.get("status"),
        "api_desc": header.get("desc"),
        "failure_codes": [item.get("code") for item in failures],
        "row_count": len(rows),
        "total_row_count": wrapper.get("totalRowCount"),
        "requested_account_count": len(account_by_id),
        "summary": summary_metrics,
        "account_totals": returned_totals,
        "unknown_rows": unknown_rows,
        "zero_filled_accounts": zero_filled_accounts,
        "zero_filled_count": len(zero_filled_accounts),
    }
    return accounts, diagnostics, errors


def _failure_codes(response: dict[str, Any]) -> set[str]:
    header = response.get("header") if isinstance(response, dict) else None
    failures = header.get("failures") if isinstance(header, dict) else None
    return {
        str(item.get("code"))
        for item in (failures or [])
        if isinstance(item, dict) and item.get("code") is not None
    }


def _response_succeeded(response: dict[str, Any]) -> bool:
    header = response.get("header") if isinstance(response, dict) else None
    return bool(
        isinstance(header, dict)
        and str(header.get("status")) == "0"
        and str(header.get("desc") or "").lower() == "success"
        and not (header.get("failures") or [])
    )


def _raise_api_failure(response: dict[str, Any]) -> None:
    codes = _failure_codes(response)
    if codes & {"894062", "894063", "894064"}:
        raise BaiduReportApiError(
            "百度 API 授权已失效，需要重新授权",
            category="reauthorization_required",
            reauthorization_required=True,
        )
    if codes & {"89405", "89406", "89407", "894061"}:
        raise BaiduReportApiError("百度 API 授权校验失败", category="authorization_error")
    raise BaiduReportApiError("百度 API 服务返回失败", category="api_error")


def _safe_token_metadata(metadata: Any, api_profile: str) -> dict[str, Any]:
    source = metadata if isinstance(metadata, dict) else {}
    return {
        "api_profile": str(source.get("api_profile") or api_profile),
        "token_refresh": str(source.get("token_refresh") or "unknown"),
        "expires_time": source.get("expires_time"),
    }


def _write_attempt_report(
    root: Path,
    *,
    config: dict[str, Any],
    selected_date: str,
    period: str | None,
    category: str,
    message: str,
) -> None:
    safe_category = str(category or "api_error")
    if safe_category not in ATTEMPT_ERROR_SUMMARIES:
        safe_category = "api_error"
    source = config.get("baidu_source") or {}
    baidu = config.get("baidu") or {}
    source_id = str(source.get("source_id") or "default")
    source_name = str(
        source.get("source_name")
        or config.get("project_name")
        or source_id
    )
    _write_json_atomic(
        root / "reports" / "baidu_api_attempt_report.json",
        {
            "passed": False,
            "project_id": config.get("project_id"),
            "project_name": config.get("project_name"),
            "date": selected_date,
            "period": period,
            "source": "baidu_open_api",
            "source_id": source_id,
            "source_name": source_name,
            "api_profile": str(baidu.get("api_profile") or ""),
            "error_category": safe_category,
            "errors": [ATTEMPT_ERROR_SUMMARIES[safe_category]],
            "safe_detail": _safe_attempt_detail(safe_category, message),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def _remaining_timeout(
    configured_timeout: float,
    deadline: float | None,
    clock: Callable[[], float],
) -> float:
    timeout = float(configured_timeout)
    if deadline is not None:
        remaining = float(deadline) - float(clock())
        if remaining <= 0:
            raise BaiduReportApiError("百度 API 自修复预算已用尽", category="network_error")
        timeout = min(timeout, remaining)
    if timeout <= 0:
        raise BaiduReportApiError("百度 API 请求超时配置无效", category="configuration_error")
    return timeout


def _fetch_baidu_api_production(
    config: dict[str, Any],
    root: Path,
    logger,
    *,
    selected_date: str,
    period: str | None,
    output_path: Path,
    token_provider: Callable[..., tuple[str, dict[str, Any]]],
    commit_standard_report: bool,
    commit_attempt_report: bool,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]],
    daily: bool,
    task_context: dict[str, Any] | None,
    deadline: float | None,
    clock: Callable[[], float],
) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    context = task_context if isinstance(task_context, dict) else {}
    context.setdefault("refresh_attempted", False)
    context.setdefault("report_request_count", 0)
    actions = context.setdefault("self_heal_actions", [])
    if not isinstance(actions, list):
        actions = []
        context["self_heal_actions"] = actions

    def add_action(action: str) -> None:
        if action not in actions:
            actions.append(action)

    def provide_token(api_profile: str, *, force_refresh: bool) -> tuple[str, dict[str, Any]]:
        if force_refresh:
            if context.get("refresh_attempted"):
                raise BaiduReportApiError(
                    "本次任务已刷新过百度授权令牌，授权仍不可用",
                    category="authorization_error",
                )
            context["refresh_attempted"] = True
            add_action("token_refresh")
        timeout = _remaining_timeout(
            float(config.get("baidu", {}).get("api_timeout_seconds", 30)),
            deadline,
            clock,
        )
        try:
            token_value, metadata = token_provider(
                config,
                root,
                api_profile,
                force_refresh=force_refresh,
                timeout_seconds=timeout,
                clock=clock,
            )
        except BaiduTokenError as exc:
            raise BaiduReportApiError(
                str(exc),
                category=exc.category,
                reauthorization_required=exc.reauthorization_required,
            ) from exc
        if str((metadata or {}).get("token_refresh") or "") == "refreshed":
            context["refresh_attempted"] = True
            add_action("token_refresh")
        return token_value, metadata

    def request_report(payload: dict[str, Any]) -> dict[str, Any]:
        timeout = _remaining_timeout(
            float(config.get("baidu", {}).get("api_timeout_seconds", 30)),
            deadline,
            clock,
        )
        context["report_request_count"] = int(context.get("report_request_count") or 0) + 1
        return transport(REPORT_API_URL, payload, timeout)

    try:
        date.fromisoformat(selected_date)
        username, api_profile, _secrets = _load_api_identity(root, config)
        user_ids, account_by_id = _account_user_ids(config)
        token, token_metadata = provide_token(api_profile, force_refresh=False)

        payload = _build_payload(username, token, user_ids, selected_date)
        response = request_report(payload)
        if "894061" in _failure_codes(response):
            token, token_metadata = provide_token(api_profile, force_refresh=True)
            payload = _build_payload(username, token, user_ids, selected_date)
            response = request_report(payload)
        if not _response_succeeded(response):
            _raise_api_failure(response)

        accounts, diagnostics, errors = _parse_api_response(
            response,
            config=config,
            account_by_id=account_by_id,
            expected_date=selected_date,
        )
        if errors:
            raise BaiduReportApiError("；".join(errors), category="integrity_error")
        report: dict[str, Any] = {
            "project_id": config.get("project_id"),
            "project_name": config.get("project_name"),
            "date": selected_date,
            "source": "baidu_open_api",
            "accounts": accounts,
            "unknown_accounts": [],
            "exceptions": [],
            "errors": [],
            "diagnostics": {
                **diagnostics,
                "token": _safe_token_metadata(token_metadata, api_profile),
                "api_request_count": int(context.get("report_request_count") or 0),
                "self_heal_actions": list(actions),
            },
            "self_check": {
                "passed": True,
                "wrote_excel": False,
                "production_output_replaced": bool(commit_standard_report),
            },
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        if daily:
            report["target_date"] = selected_date
        else:
            report["period"] = period or "15点"
        if commit_standard_report:
            _write_json_atomic(output_path, report)
        logger.info("百度 API 数据读取完成：%s；账户数：%s", selected_date, len(accounts))
        return report
    except BaiduReportApiError as exc:
        if commit_attempt_report:
            _write_attempt_report(
                root,
                config=config,
                selected_date=selected_date,
                period=period,
                category=exc.category,
                message=str(exc),
            )
        raise
    except Exception as exc:
        error = BaiduReportApiError("百度 API 数据读取异常", category="api_error")
        if commit_attempt_report:
            _write_attempt_report(
                root,
                config=config,
                selected_date=selected_date,
                period=period,
                category=error.category,
                message=str(error),
            )
        raise error from exc


def fetch_baidu_api_hourly(
    config: dict[str, Any],
    root: Path,
    logger,
    period: str | None = None,
    token_provider: Callable[..., tuple[str, dict[str, Any]]] = ensure_valid_access_token_cloud_first,
    commit_standard_report: bool = True,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]] = _post_json,
    task_context: dict[str, Any] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    target_date: str | None = None,
    commit_attempt_report: bool = True,
) -> dict[str, Any]:
    output_path = _resolve_path(root, config.get("baidu", {}).get("output_path", "reports/baidu_account_data.json"))
    return _fetch_baidu_api_production(
        config,
        root,
        logger,
        selected_date=target_date or date.today().isoformat(),
        period=period,
        output_path=output_path,
        token_provider=token_provider,
        commit_standard_report=commit_standard_report,
        commit_attempt_report=commit_attempt_report,
        transport=transport,
        daily=False,
        task_context=task_context,
        deadline=deadline,
        clock=clock,
    )


def fetch_baidu_api_daily(
    config: dict[str, Any],
    root: Path,
    logger,
    target_date: str | None = None,
    token_provider: Callable[..., tuple[str, dict[str, Any]]] = ensure_valid_access_token_cloud_first,
    commit_standard_report: bool = True,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]] = _post_json,
    task_context: dict[str, Any] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    commit_attempt_report: bool = True,
) -> dict[str, Any]:
    selected_date = target_date or (date.today().fromordinal(date.today().toordinal() - 1).isoformat())
    output_path = _resolve_path(root, config.get("baidu", {}).get("daily_output_path", "reports/baidu_daily_data.json"))
    return _fetch_baidu_api_production(
        config,
        root,
        logger,
        selected_date=selected_date,
        period=None,
        output_path=output_path,
        token_provider=token_provider,
        commit_standard_report=commit_standard_report,
        commit_attempt_report=commit_attempt_report,
        transport=transport,
        daily=True,
        task_context=task_context,
        deadline=deadline,
        clock=clock,
    )


def fetch_baidu_api_probe(
    config: dict[str, Any],
    root: Path,
    logger,
    target_date: str | None = None,
    period: str | None = None,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]] = _post_json,
) -> dict[str, Any]:
    report_path = root / "reports" / "baidu_api_probe_report.json"
    selected_date = target_date or date.today().isoformat()
    started_at = datetime.now().isoformat(timespec="seconds")
    report: dict[str, Any] = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": selected_date,
        "period": period,
        "source": "baidu_open_api_probe",
        "accounts": {},
        "diagnostics": {},
        "errors": [],
        "self_check": {"wrote_excel": False, "production_output_replaced": False},
        "started_at": started_at,
        "finished_at": None,
        "outputs": {"probe_report": str(report_path)},
    }

    try:
        username, token, api_profile = _load_api_auth(root, config)
        user_ids, account_by_id = _account_user_ids(config)
        payload = _build_payload(username, token, user_ids, selected_date)
        response = transport(
            REPORT_API_URL,
            payload,
            int(config.get("baidu", {}).get("api_timeout_seconds", 30)),
        )
        accounts, diagnostics, errors = _parse_api_response(
            response,
            config=config,
            account_by_id=account_by_id,
        )
        report["accounts"] = accounts
        report["diagnostics"] = diagnostics
        report["diagnostics"]["api_profile"] = api_profile
        report["errors"].extend(errors)
    except BaiduReportApiError as exc:
        report["errors"].append(str(exc))
    except Exception as exc:
        report["errors"].append(f"百度 API 探测异常：{type(exc).__name__}")

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["self_check"]["passed"] = not report["errors"]
    _write_json(report_path, report)
    logger.info("百度 API 只读探测完成：%s；结果：%s", report_path, "通过" if not report["errors"] else "失败")
    return report


def _fetch_baidu_search_word_production(
    config: dict[str, Any],
    root: Path,
    logger,
    *,
    selected_date: str,
    output_path: Path,
    token_provider: Callable[..., tuple[str, dict[str, Any]]],
    commit_standard_report: bool,
    commit_attempt_report: bool,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]],
    task_context: dict[str, Any] | None,
    deadline: float | None,
    clock: Callable[[], float],
) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    context = task_context if isinstance(task_context, dict) else {}
    context.setdefault("refresh_attempted", False)
    context.setdefault("report_request_count", 0)
    actions = context.setdefault("self_heal_actions", [])
    if not isinstance(actions, list):
        actions = []
        context["self_heal_actions"] = actions

    def add_action(action: str) -> None:
        if action not in actions:
            actions.append(action)

    def provide_token(api_profile: str, *, force_refresh: bool) -> tuple[str, dict[str, Any]]:
        if force_refresh:
            if context.get("refresh_attempted"):
                raise BaiduReportApiError(
                    "本次任务已刷新过百度授权令牌，授权仍不可用",
                    category="authorization_error",
                )
            context["refresh_attempted"] = True
            add_action("token_refresh")
        timeout = _remaining_timeout(
            float(config.get("baidu", {}).get("api_timeout_seconds", 30)),
            deadline,
            clock,
        )
        try:
            token_value, metadata = token_provider(
                config,
                root,
                api_profile,
                force_refresh=force_refresh,
                timeout_seconds=timeout,
                clock=clock,
            )
        except BaiduTokenError as exc:
            raise BaiduReportApiError(
                str(exc),
                category=exc.category,
                reauthorization_required=exc.reauthorization_required,
            ) from exc
        if str((metadata or {}).get("token_refresh") or "") == "refreshed":
            context["refresh_attempted"] = True
            add_action("token_refresh")
        return token_value, metadata

    def request_report(payload: dict[str, Any]) -> dict[str, Any]:
        timeout = _remaining_timeout(
            float(config.get("baidu", {}).get("api_timeout_seconds", 30)),
            deadline,
            clock,
        )
        context["report_request_count"] = int(context.get("report_request_count") or 0) + 1
        return transport(REPORT_API_URL, payload, timeout)

    try:
        date.fromisoformat(selected_date)
        username, api_profile, _secrets = _load_api_identity(root, config)
        user_ids, account_by_id = _account_user_ids(config)
        token, token_metadata = provide_token(api_profile, force_refresh=False)

        all_rows: list[dict[str, Any]] = []
        total_row_count: Any = None
        start_row = 0
        page = 0
        while True:
            payload = _build_search_word_payload(
                username,
                token,
                user_ids,
                selected_date,
                start_row=start_row,
                row_count=SEARCH_WORD_PAGE_SIZE,
            )
            response = request_report(payload)
            if page == 0 and _failure_codes(response) & {"894061", "89406"}:
                token, token_metadata = provide_token(api_profile, force_refresh=True)
                payload = _build_search_word_payload(
                    username,
                    token,
                    user_ids,
                    selected_date,
                    start_row=start_row,
                    row_count=SEARCH_WORD_PAGE_SIZE,
                )
                response = request_report(payload)
            if not _response_succeeded(response):
                _raise_api_failure(response)
            wrapper = (response.get("body") or {}).get("data") or [{}]
            wrapper = wrapper[0] if wrapper and isinstance(wrapper[0], dict) else {}
            rows = wrapper.get("rows") or []
            total_row_count = wrapper.get("totalRowCount")
            all_rows.extend(rows)
            page += 1
            if (
                not rows
                or total_row_count is None
                or len(all_rows) >= int(total_row_count)
                or page >= SEARCH_WORD_MAX_PAGES
            ):
                break
            start_row += len(rows)

        aggregate = aggregate_search_word_rows(all_rows)
        report: dict[str, Any] = {
            "project_id": config.get("project_id"),
            "project_name": config.get("project_name"),
            "date": selected_date,
            "source": "baidu_open_api_search_word",
            "report_type": SEARCH_WORD_REPORT_TYPE,
            "columns": SEARCH_WORD_COLUMNS,
            "raw_rows": len(all_rows),
            "matched_rows": aggregate["matched_rows"],
            "search_word_rows": all_rows,
            "totals": {
                "click": aggregate["click"],
                "cost": aggregate["cost"],
                "impression": aggregate["impression"],
            },
            "keyword_counts": aggregate["keyword_counts"],
            "row_pages": page,
            "total_row_count": total_row_count,
            "errors": aggregate["errors"],
            "diagnostics": {
                "api_request_count": int(context.get("report_request_count") or 0),
                "self_heal_actions": list(actions),
                "token": _safe_token_metadata(token_metadata, api_profile),
            },
            "self_check": {
                "passed": not aggregate["errors"],
                "wrote_excel": False,
                "production_output_replaced": bool(commit_standard_report),
            },
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        if commit_standard_report:
            _write_json_atomic(output_path, report)
        logger.info(
            "百度搜索词数据读取完成：%s；原始行 %s，命中 %s",
            selected_date,
            len(all_rows),
            aggregate["matched_rows"],
        )
        return report
    except BaiduReportApiError as exc:
        if commit_attempt_report:
            _write_attempt_report(
                root,
                config=config,
                selected_date=selected_date,
                period=None,
                category=exc.category,
                message=str(exc),
            )
        raise
    except Exception as exc:
        error = BaiduReportApiError("百度搜索词数据读取异常", category="api_error")
        if commit_attempt_report:
            _write_attempt_report(
                root,
                config=config,
                selected_date=selected_date,
                period=None,
                category=error.category,
                message=str(error),
            )
        raise error from exc


def fetch_baidu_search_word_report(
    config: dict[str, Any],
    root: Path,
    logger,
    target_date: str | None = None,
    token_provider: Callable[..., tuple[str, dict[str, Any]]] = ensure_valid_access_token_cloud_first,
    commit_standard_report: bool = True,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]] = _post_json,
    task_context: dict[str, Any] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    commit_attempt_report: bool = True,
) -> dict[str, Any]:
    selected_date = target_date or (
        date.today().fromordinal(date.today().toordinal() - 1).isoformat()
    )
    output_path = _resolve_path(
        root,
        config.get("baidu", {}).get(
            "search_word_output_path",
            "reports/baidu_search_word_data.json",
        ),
    )
    return _fetch_baidu_search_word_production(
        config,
        root,
        logger,
        selected_date=selected_date,
        output_path=output_path,
        token_provider=token_provider,
        commit_standard_report=commit_standard_report,
        commit_attempt_report=commit_attempt_report,
        transport=transport,
        task_context=task_context,
        deadline=deadline,
        clock=clock,
    )


def fetch_baidu_search_word_project(
    config: dict[str, Any],
    root: Path,
    logger,
    target_date: str | None = None,
    token_provider: Callable[..., tuple[str, dict[str, Any]]] = ensure_valid_access_token_cloud_first,
    transport: Callable[[str, dict[str, Any], int], dict[str, Any]] = _post_json,
    task_context: dict[str, Any] | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """按项目(含多来源)拉取搜索词报告并聚合。

    单来源项目用顶层 baidu.api_profile;多来源项目逐 source 用各自的
    api_profile+账户拉取后合并原始行一次聚合。任一 source 缺授权即报错。
    """
    from modules.baidu_multi_source import (
        build_source_runtime_config,
        resolve_baidu_sources,
    )

    resolved_date = target_date or (
        date.today().fromordinal(date.today().toordinal() - 1).isoformat()
    )
    date.fromisoformat(resolved_date)
    raw_sources = config.get("baidu_sources")
    is_multi = bool(
        isinstance(raw_sources, list) and raw_sources
    )
    if not is_multi:
        # 单来源项目:顶层 baidu.api_profile + accounts(dict),直接复用单配置抓取
        return fetch_baidu_search_word_report(
            config,
            root,
            logger,
            target_date=resolved_date,
            token_provider=token_provider,
            commit_standard_report=True,
            transport=transport,
            task_context=task_context,
            deadline=deadline,
            clock=clock,
        )

    from modules.baidu_multi_source import (
        build_source_runtime_config,
        resolve_baidu_sources,
    )

    sources = resolve_baidu_sources(config)
    all_rows: list[dict[str, Any]] = []
    source_errors: list[str] = []
    api_request_count = 0
    actions: list[str] = []
    top_level_api_profile = str((config.get("baidu") or {}).get("api_profile") or "")

    for source in sources:
        source_id = str(source.get("source_id") or "default")
        source_name = str(source.get("source_name") or source_id)
        source_config = build_source_runtime_config(config, source, task="daily")
        baidu = dict(source_config.get("baidu") or {})
        api_profile = str(
            source.get("api_profile") or top_level_api_profile or ""
        ).strip()
        baidu["api_profile"] = api_profile
        source_config["baidu"] = baidu
        if not api_profile:
            source_errors.append(
                f"{source_name} 未配置百度 API 授权，词类占比百度侧不可用"
            )
            continue
        try:
            report = fetch_baidu_search_word_report(
                source_config,
                root,
                logger,
                target_date=resolved_date,
                token_provider=token_provider,
                commit_standard_report=False,
                transport=transport,
                task_context=task_context,
                deadline=deadline,
                clock=clock,
            )
        except BaiduReportApiError as exc:
            source_errors.append(f"{source_name}：{exc}")
            continue
        all_rows.extend(report.get("search_word_rows") or [])
        diagnostics = report.get("diagnostics") or {}
        api_request_count += int(diagnostics.get("api_request_count") or 0)
        for action in diagnostics.get("self_heal_actions") or []:
            if action not in actions:
                actions.append(action)

    aggregate = aggregate_search_word_rows(all_rows)
    report: dict[str, Any] = {
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "date": resolved_date,
        "source": "baidu_open_api_search_word",
        "report_type": SEARCH_WORD_REPORT_TYPE,
        "columns": SEARCH_WORD_COLUMNS,
        "raw_rows": len(all_rows),
        "matched_rows": aggregate["matched_rows"],
        "totals": {
            "click": aggregate["click"],
            "cost": aggregate["cost"],
            "impression": aggregate["impression"],
        },
        "keyword_counts": aggregate["keyword_counts"],
        "source_errors": source_errors,
        "errors": list(aggregate["errors"]) + source_errors,
        "diagnostics": {
            "api_request_count": api_request_count,
            "self_heal_actions": list(actions),
        },
        "self_check": {
            "passed": not (aggregate["errors"] or source_errors),
            "wrote_excel": False,
            "production_output_replaced": False,
        },
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json_atomic(
        _resolve_path(
            root,
            config.get("baidu", {}).get(
                "search_word_output_path",
                "reports/baidu_search_word_data.json",
            ),
        ),
        report,
    )
    logger.info(
        "百度搜索词数据读取完成：%s；原始行 %s，命中 %s",
        resolved_date,
        len(all_rows),
        aggregate["matched_rows"],
    )
    return report
