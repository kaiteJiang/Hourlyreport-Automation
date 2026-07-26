from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from modules.baidu_multi_source import resolve_baidu_sources
from modules.browser_manager import get_browser_settings
from modules.chrome_debug import ensure_chrome_debug_ready, is_chrome_debug_port_alive
from modules.daily_excel_inspector import inspect_daily_excel_structure
from modules.excel_inspector import inspect_excel_structure
from modules.project_config import validate_project_config


def _resolve(root: Path, value: str | Path | None) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def _profiles_for_config(config: dict[str, Any]) -> list[str]:
    profiles: list[str] = []
    for source in resolve_baidu_sources(config):
        profile = str(source.get("credential_profile") or "").strip()
        if profile and profile not in profiles:
            profiles.append(profile)
    return profiles


def _api_profiles_for_config(config: dict[str, Any]) -> list[str]:
    sources = config.get("baidu_sources") or []
    if sources:
        return list(dict.fromkeys(
            str(source.get("api_profile") or "").strip()
            for source in sources
            if isinstance(source, dict) and str(source.get("api_profile") or "").strip()
        ))
    profile = str(config.get("baidu", {}).get("api_profile") or "").strip()
    return [profile] if profile else []


def _missing_source_api_mappings(config: dict[str, Any]) -> list[dict[str, str]]:
    sources = config.get("baidu_sources") or []
    if not sources:
        return []
    missing: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, dict):
            missing.append({"source_id": "", "source_name": ""})
        elif not str(source.get("api_profile") or "").strip():
            missing.append({
                "source_id": str(source.get("source_id") or "").strip(),
                "source_name": str(source.get("source_name") or "").strip(),
            })
    return missing


def check_baidu_api_profiles(root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    root_path = Path(root)
    credentials_path = _resolve(root_path, config.get("credentials_path", "secrets/secrets.json"))
    required_profiles = _api_profiles_for_config(config)
    missing_source_mappings = _missing_source_api_mappings(config)
    report: dict[str, Any] = {
        "passed": False,
        "cloud_ready": False,
        "cloud_gateway": {
            "exists": False,
            "app_id_nonempty": False,
            "client_key_nonempty": False,
            "token_url_https": False,
        },
        "required_profiles": required_profiles,
        "missing_source_mappings": missing_source_mappings,
        "profiles": [],
        "json_valid": False,
        "errors": [],
    }
    if not credentials_path or not credentials_path.exists():
        report["errors"].append("API 授权文件不可用")
        return report
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        report["errors"].append("API 授权文件无法读取")
        return report

    report["json_valid"] = True
    api_profiles = data.get("baidu_api") if isinstance(data, dict) else {}
    api_profiles = api_profiles if isinstance(api_profiles, dict) else {}
    gateway = data.get("baidu_api_gateway") if isinstance(data, dict) else {}
    gateway_exists = isinstance(gateway, dict)
    gateway = gateway if gateway_exists else {}
    gateway_app_id = str(gateway.get("app_id") or "").strip()
    token_url = str(gateway.get("token_url") or "").strip()
    gateway_report = {
        "exists": gateway_exists,
        "app_id_nonempty": bool(gateway_app_id),
        "client_key_nonempty": bool(str(gateway.get("client_key") or "").strip()),
        "token_url_https": token_url.lower().startswith("https://"),
    }
    report["cloud_gateway"] = gateway_report
    gateway_ready = all(gateway_report.values())
    if not gateway_ready:
        report["errors"].append("云端 Token 配置未就绪，请导入管理员最新授权配置")
    if missing_source_mappings:
        report["errors"].append("存在未配置 api_profile 的百度来源")
    if not required_profiles:
        report["errors"].append("未配置 api_profile")
        return report
    for profile in required_profiles:
        item = api_profiles.get(profile)
        exists = isinstance(item, dict)
        access_token_nonempty = bool(exists and str(item.get("access_token") or "").strip())
        refresh_token_nonempty = bool(exists and str(item.get("refresh_token") or "").strip())
        profile_app_id = str(item.get("app_id") or "").strip() if exists else ""
        app_id_matches_gateway = bool(profile_app_id and gateway_app_id and profile_app_id == gateway_app_id)
        report["profiles"].append({
            "api_profile": profile,
            "exists": exists,
            "access_token_nonempty": access_token_nonempty,
            "refresh_token_nonempty": refresh_token_nonempty,
            "app_id_nonempty": bool(profile_app_id),
            "app_id_matches_gateway": app_id_matches_gateway,
        })
        if not exists:
            report["errors"].append(f"缺少 api_profile：{profile}")
        elif not profile_app_id:
            report["errors"].append(f"api_profile 缺少 app_id：{profile}")
        elif gateway_app_id and not app_id_matches_gateway:
            report["errors"].append(f"api_profile 与云端应用不匹配：{profile}")
    report["passed"] = not report["errors"]
    report["cloud_ready"] = report["passed"]
    return report


def check_baidu_credentials(root: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    root_path = Path(root)
    credentials_path = _resolve(root_path, config.get("credentials_path", "secrets/secrets.json"))
    required_profiles = _profiles_for_config(config)
    report: dict[str, Any] = {
        "passed": False,
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "credentials_path": str(credentials_path or ""),
        "required_profiles": required_profiles,
        "profiles": [],
        "json_valid": False,
        "errors": [],
    }
    if not credentials_path or not credentials_path.exists():
        report["errors"].append("凭据文件不存在：secrets/secrets.json")
        return report
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        report["errors"].append(
            f"secrets/secrets.json 不是合法 JSON：line {exc.lineno} column {exc.colno}"
        )
        return report
    except Exception as exc:
        report["errors"].append(f"secrets/secrets.json 无法读取：{exc}")
        return report

    report["json_valid"] = True
    baidu = data.get("baidu") if isinstance(data, dict) else {}
    baidu = baidu if isinstance(baidu, dict) else {}
    if not required_profiles:
        report["errors"].append("当前项目未配置 credential_profile")
        return report
    for profile in required_profiles:
        item = baidu.get(profile)
        exists = isinstance(item, dict)
        username_nonempty = bool(exists and str(item.get("username") or "").strip())
        password_nonempty = bool(exists and str(item.get("password") or "").strip())
        report["profiles"].append({
            "credential_profile": profile,
            "exists": exists,
            "username_nonempty": username_nonempty,
            "password_nonempty": password_nonempty,
        })
        if not exists:
            report["errors"].append(f"缺少 credential_profile：{profile}")
            continue
        if not username_nonempty:
            report["errors"].append(f"profile {profile} 的 username 为空")
        if not password_nonempty:
            report["errors"].append(f"profile {profile} 的 password 为空")
    report["passed"] = not report["errors"]
    return report


def print_credential_report(report: dict[str, Any], output_func: Callable[[str], None] = print) -> None:
    output_func(f"当前项目：{report.get('project_name') or report.get('project_id') or '未知'}")
    required = report.get("required_profiles") or []
    output_func(f"需要的 credential_profile：{', '.join(required) if required else '无'}")
    for item in report.get("profiles") or []:
        status = "通过" if item.get("exists") and item.get("username_nonempty") and item.get("password_nonempty") else "失败"
        username = "非空" if item.get("username_nonempty") else "为空"
        password = "非空" if item.get("password_nonempty") else "为空"
        output_func(
            f"[{status}] credential_profile: {item.get('credential_profile')} "
            f"username{username} password{password}"
        )
    for error in report.get("errors") or []:
        output_func(f"[失败] {error}")


class _SilentLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def run_preflight(
    root: str | Path,
    project: dict[str, Any],
    config: dict[str, Any],
    *,
    task: str = "hourly",
    quick: bool = False,
    chrome_check_func: Callable[..., bool] = is_chrome_debug_port_alive,
    chrome_ready_func: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    checks: list[dict[str, Any]] = []

    def add(passed: bool, message: str, *, skipped: bool = False) -> None:
        item = {"passed": passed, "message": message}
        if skipped:
            item["skipped"] = True
        checks.append(item)

    add((root_path / "main.py").exists(), "项目根目录已识别" if (root_path / "main.py").exists() else "项目根目录无法识别")
    settings = get_browser_settings(config)
    host = settings.get("remote_debugging_host", "127.0.0.1")
    port = int(settings.get("remote_debugging_port", 9222))
    data_source_preference = str(config.get("baidu", {}).get("data_source_preference") or "browser").strip().lower()
    api_preferred = data_source_preference == "api"
    if api_preferred:
        add(True, "API 模式预检跳过 Chrome 就绪检查", skipped=True)
    elif chrome_ready_func is None and chrome_check_func is is_chrome_debug_port_alive:
        browser_config = config.get("browser") if isinstance(config.get("browser"), dict) else {}
        chrome_ready = ensure_chrome_debug_ready(
            root_path,
            config,
            host=host,
            port=port,
            auto_start=bool(settings.get("auto_start_debug_chrome", True)),
            wait_seconds=int(browser_config.get("debug_startup_wait_seconds", 15) or 15),
        )
        chrome_ok = bool(chrome_ready.get("ready"))
        if chrome_ok and chrome_ready.get("started_new_chrome"):
            chrome_message = f"Chrome 9222 已自动启动并连接：http://{host}:{port}"
        elif chrome_ok:
            chrome_message = f"Chrome 9222 已连接：http://{host}:{port}"
        else:
            chrome_message = chrome_ready.get("error") or f"Chrome 9222 无法连接：http://{host}:{port}"
    elif chrome_ready_func is not None:
        chrome_ready = chrome_ready_func(
            root_path,
            config,
            host=host,
            port=port,
            auto_start=bool(settings.get("auto_start_debug_chrome", True)),
        )
        chrome_ok = bool(chrome_ready.get("ready"))
        chrome_message = (
            f"Chrome 9222 已连接：http://{host}:{port}"
            if chrome_ok else chrome_ready.get("error") or f"Chrome 9222 无法连接：http://{host}:{port}"
        )
    else:
        chrome_ok = chrome_check_func(host=host, port=port)
        chrome_message = "Chrome 9222 已连接" if chrome_ok else f"Chrome 9222 无法连接：http://{host}:{port}"
    if not api_preferred:
        add(chrome_ok, chrome_message)

    project_errors = validate_project_config(project)
    add(
        not project_errors,
        f"当前项目：{project.get('project_id')} - {project.get('project_name')}" if not project_errors else "项目配置校验失败：" + "；".join(project_errors),
    )

    excel_path = _resolve(root_path, config.get("excel_path"))
    add(bool(excel_path and excel_path.exists()), "Excel 路径存在" if excel_path and excel_path.exists() else f"Excel 路径不存在：{excel_path or ''}")
    sheet_label = "日报" if task == "daily" else "小时报"
    structure_func = inspect_daily_excel_structure if task == "daily" else inspect_excel_structure
    if quick and excel_path and excel_path.exists():
        add(True, f"快速预检已跳过{sheet_label} sheet 结构扫描；写入前仍会进行结构识别和复核", skipped=True)
    elif excel_path and excel_path.exists():
        try:
            structure = structure_func(config=config, root=root_path, logger=_SilentLogger())
            errors = structure.get("errors") or []
            add(not errors, f"{sheet_label} sheet 结构可识别" if not errors else f"{sheet_label} sheet 结构识别失败：" + "；".join(errors))
        except Exception as exc:
            add(False, f"{sheet_label} sheet 结构识别失败：{exc}")
    else:
        add(False, f"{sheet_label} sheet 结构无法检查：Excel 不存在")

    kst_dir = _resolve(root_path, config.get("kst", {}).get("export_dir"))
    add(bool(kst_dir and kst_dir.exists()), "商务通目录存在" if kst_dir and kst_dir.exists() else f"商务通目录不存在：{kst_dir or ''}")

    credentials = check_baidu_credentials(root_path, config)
    json_error = next((error for error in credentials.get("errors") or [] if "JSON" in error or "无法读取" in error), "")
    add(credentials.get("json_valid", False), "secrets JSON 合法" if credentials.get("json_valid") else (json_error or "secrets/secrets.json 无法读取"))
    add(credentials.get("passed", False), "credential_profile 检查通过" if credentials.get("passed") else "凭据预检未通过，请检查 secrets/secrets.json")

    api_profiles = check_baidu_api_profiles(root_path, config)
    if api_preferred:
        api_errors = "；".join(api_profiles.get("errors") or [])
        add(
            True,
            "API 云端 Token 检查通过"
            if api_profiles.get("passed")
            else f"API 云端 Token 不可用：{api_errors or '配置未就绪'}；本次将使用浏览器降级",
        )

    return {
        "passed": all(item["passed"] for item in checks),
        "mode": "preflight",
        "task": task,
        "quick": quick,
        "project_id": config.get("project_id"),
        "project_name": config.get("project_name"),
        "checks": checks,
        "credentials": credentials,
        "api_profiles": api_profiles,
    }


def print_preflight_report(report: dict[str, Any], output_func: Callable[[str], None] = print) -> None:
    for item in report.get("checks") or []:
        status = "跳过" if item.get("skipped") else ("通过" if item.get("passed") else "失败")
        output_func(f"[{status}] {item.get('message')}")
    print_credential_report(report.get("credentials") or {}, output_func=output_func)
