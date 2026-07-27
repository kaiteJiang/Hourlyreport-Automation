from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any
import warnings


APP_CONFIG_PATH = Path("configs/app_config.json")
REQUIRED_PROJECT_FIELDS = [
    "project_id",
    "project_name",
    "excel",
    "kst",
    "baidu",
    "accounts",
    "hourly",
    "daily",
]
REQUIRED_DAILY_WRITE_FIELDS = ["展现", "点击", "消费", "有效对话", "无效对话", "一般有效对话", "有效转潜", "总转潜"]
REQUIRED_DAILY_FORBIDDEN_FIELDS = ["总对话", "预约", "到诊", "就诊"]
REQUIRED_PERIODS = ["11点", "15点", "18点"]
DATA_SOURCE_MODES = {"browser", "api_shadow", "api_preferred"}
DATA_SOURCE_PREFERENCES = {"api", "browser"}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_json_atomically(path: Path, data: dict[str, Any]) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(json.dumps(data, ensure_ascii=False, indent=2))
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def load_app_config(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    path = root_path / APP_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"找不到应用配置文件：{path}")
    config = _read_json(path)
    if not config.get("default_project_id"):
        raise ValueError("configs/app_config.json 缺少 default_project_id")
    if not config.get("projects_dir"):
        raise ValueError("configs/app_config.json 缺少 projects_dir")
    if not config.get("secrets_file"):
        raise ValueError("configs/app_config.json 缺少 secrets_file")
    if "baidu_data_source_preference" not in config:
        config["baidu_data_source_preference"] = "api"
    else:
        preference = str(config["baidu_data_source_preference"]).strip().lower()
        if preference in DATA_SOURCE_PREFERENCES:
            config["baidu_data_source_preference"] = preference
        else:
            warnings.warn(
                "baidu_data_source_preference 无效，已按 api 处理",
                RuntimeWarning,
            )
            config["baidu_data_source_preference"] = "api"
    return config


def _project_path(root: Path, app_config: dict[str, Any], project_id: str) -> Path:
    projects_dir = _resolve(root, app_config["projects_dir"])
    return projects_dir / f"{project_id}.json"


def load_project_config(root: str | Path, project_id: str) -> dict[str, Any]:
    root_path = Path(root)
    app_config = load_app_config(root_path)
    path = _project_path(root_path, app_config, project_id)
    if not path.exists():
        raise FileNotFoundError(f"找不到项目配置文件：{path}")
    project = normalize_project_config(_read_json(path))
    project["_config_path"] = str(path)
    project["_app_config"] = app_config
    return project


def get_current_project(root: str | Path) -> dict[str, Any]:
    app_config = load_app_config(root)
    return load_project_config(root, app_config["default_project_id"])


def set_current_project(root: str | Path, project_id: str) -> dict[str, Any]:
    root_path = Path(root)
    app_config = load_app_config(root_path)
    project = load_project_config(root_path, project_id)
    app_config["default_project_id"] = project_id
    _write_json(root_path / APP_CONFIG_PATH, app_config)
    return project


def reload_current_project(root: str | Path) -> dict[str, Any]:
    return get_current_project(root)


def list_projects(root: str | Path) -> list[dict[str, str]]:
    root_path = Path(root)
    app_config = load_app_config(root_path)
    projects_dir = _resolve(root_path, app_config["projects_dir"])
    if not projects_dir.exists():
        return []
    projects: list[dict[str, str]] = []
    for path in sorted(projects_dir.glob("*.json")):
        # 排除模板文件
        if path.name == "project_template.json":
            continue
        data = normalize_project_config(_read_json(path))
        project_id = str(data.get("project_id") or "")
        # 排除模板标记
        if data.get("is_template") is True:
            continue
        if project_id == "your_project_id":
            continue
        # 文件名必须与 project_id 一致
        if f"{project_id}.json" != path.name:
            continue
        projects.append({
            "project_id": project_id,
            "project_name": str(data.get("project_name") or ""),
            "path": str(path),
        })
    return projects


def normalize_project_config(project: dict[str, Any]) -> dict[str, Any]:
    project = dict(project)
    excel = dict(project.get("excel") or {})
    sheets = dict(project.get("sheets") or {})
    if "path" not in excel and project.get("excel_path"):
        excel["path"] = project.get("excel_path")
    if "hourly_sheet" not in excel and sheets.get("hourly"):
        excel["hourly_sheet"] = sheets.get("hourly")
    if "daily_sheet" not in excel and sheets.get("daily"):
        excel["daily_sheet"] = sheets.get("daily")
    excel.setdefault("engine", project.get("excel_engine") or "openpyxl")
    project["excel"] = excel
    project["excel_path"] = excel.get("path")
    project["sheets"] = {
        "hourly": excel.get("hourly_sheet") or "时段数据",
        "daily": excel.get("daily_sheet") or "百度",
    }

    kst = dict(project.get("kst") or {})
    kst.setdefault("auto_pick_latest", True)
    kst.setdefault("max_file_age_minutes", 30)
    kst.setdefault("max_file_age_hours", 2)
    project["kst"] = kst

    daily = dict(project.get("daily") or {})
    if "do_not_write_fields" not in daily and "forbidden_fields" in daily:
        daily["do_not_write_fields"] = daily.get("forbidden_fields")
    if "forbidden_fields" not in daily and "do_not_write_fields" in daily:
        daily["forbidden_fields"] = daily.get("do_not_write_fields")
    project["daily"] = daily

    normalized_accounts = []
    for account in project.get("accounts", []) or []:
        normalized_accounts.append(_normalize_account(account))
    project["accounts"] = normalized_accounts

    if isinstance(project.get("baidu_sources"), list):
        normalized_sources = []
        for source in project.get("baidu_sources") or []:
            item = dict(source)
            item["accounts"] = [_normalize_account(account) for account in item.get("accounts", []) or []]
            item.setdefault("required", True)
            normalized_sources.append(item)
        project["baidu_sources"] = normalized_sources
        if not project["accounts"]:
            project["accounts"] = _flatten_source_accounts(normalized_sources)
    project["excel_accounts"] = _normalize_excel_accounts(project)
    return project


def _normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    item = dict(account)
    if "baidu_names" not in item:
        item["baidu_names"] = list(item.get("baidu_aliases") or [])
    if "kst_ids" not in item:
        value = item.get("kst_promotion_id")
        item["kst_ids"] = [str(value)] if value not in (None, "") else []
    if "kst_names" not in item:
        item["kst_names"] = list(item.get("kst_aliases") or [])
    item["baidu_aliases"] = list(item.get("baidu_names") or [])
    item["kst_aliases"] = list(item.get("kst_names") or [])
    if item.get("kst_ids"):
        item["kst_promotion_id"] = str(item["kst_ids"][0])
    return item


def _flatten_source_accounts(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for account in source.get("accounts", []) or []:
            standard_name = str(account.get("standard_name") or "")
            if standard_name and standard_name not in seen:
                accounts.append(dict(account))
                seen.add(standard_name)
    return accounts


def _normalize_excel_accounts(project: dict[str, Any]) -> list[dict[str, Any]]:
    raw_excel_accounts = project.get("excel_accounts")
    if not isinstance(raw_excel_accounts, list) or not raw_excel_accounts:
        return [{"standard_name": account.get("standard_name")} for account in project.get("accounts", []) if account.get("standard_name")]
    account_by_name = {str(account.get("standard_name") or ""): account for account in project.get("accounts", [])}
    normalized = []
    for item in raw_excel_accounts:
        if isinstance(item, str):
            standard_name = item
            source = account_by_name.get(standard_name, {"standard_name": standard_name})
        else:
            standard_name = str(item.get("standard_name") or "")
            source = dict(account_by_name.get(standard_name, {}), **dict(item))
        if standard_name:
            normalized.append({"standard_name": standard_name, **{k: v for k, v in source.items() if k != "standard_name"}})
    return normalized


def get_project_accounts(project: dict[str, Any]) -> list[str]:
    project = normalize_project_config(project)
    return [str(account.get("standard_name")) for account in project.get("accounts", []) if account.get("standard_name")]


def get_account_alias_maps(project: dict[str, Any]) -> dict[str, dict[str, str]]:
    project = normalize_project_config(project)
    baidu_alias_to_account: dict[str, str] = {}
    kst_id_to_account: dict[str, str] = {}
    kst_alias_to_account: dict[str, str] = {}
    excel_name_to_account: dict[str, str] = {}
    for account in project.get("accounts", []):
        standard = str(account.get("standard_name") or "")
        if not standard:
            continue
        for name in [standard, account.get("excel_name"), *account.get("baidu_names", [])]:
            if name:
                baidu_alias_to_account[str(name)] = standard
        for name in [standard, account.get("excel_name")]:
            if name:
                excel_name_to_account[str(name)] = standard
        for kst_id in account.get("kst_ids", []):
            if kst_id:
                kst_id_to_account[str(kst_id)] = standard
        for name in [standard, account.get("excel_name"), *account.get("kst_names", [])]:
            if name:
                kst_alias_to_account[str(name)] = standard
    return {
        "baidu_alias_to_account": baidu_alias_to_account,
        "kst_id_to_account": kst_id_to_account,
        "kst_alias_to_account": kst_alias_to_account,
        "excel_name_to_account": excel_name_to_account,
    }


def get_excel_path(project: dict[str, Any], root: str | Path | None = None) -> Path:
    project = normalize_project_config(project)
    value = project["excel"]["path"]
    path = Path(value)
    if path.is_absolute() or root is None:
        return path
    return Path(root) / path


def get_kst_export_dir(project: dict[str, Any], root: str | Path | None = None) -> Path:
    project = normalize_project_config(project)
    value = project["kst"]["export_dir"]
    path = Path(value)
    if path.is_absolute() or root is None:
        return path
    return Path(root) / path


def get_daily_sheet(project: dict[str, Any]) -> str:
    return str(normalize_project_config(project)["excel"].get("daily_sheet") or "百度")


def get_hourly_sheet(project: dict[str, Any]) -> str:
    return str(normalize_project_config(project)["excel"].get("hourly_sheet") or "时段数据")


def get_credential_profile(project: dict[str, Any]) -> str:
    return str(normalize_project_config(project).get("baidu", {}).get("credential_profile") or "")


def load_default_runtime_config(root: str | Path | None = None) -> dict[str, Any]:
    root_path = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        project = get_current_project(root_path)
    except Exception:
        return {}
    return build_runtime_config_from_project(project, {})


def build_runtime_config_from_project(project: dict[str, Any], base_config: dict[str, Any]) -> dict[str, Any]:
    project = normalize_project_config(project)
    config = dict(base_config)
    config["project_id"] = project.get("project_id")
    config["project_name"] = project.get("project_name")
    config["project_config_path"] = project.get("_config_path")
    config["excel_path"] = project["excel"]["path"]
    config["excel_engine"] = project["excel"].get("engine", "openpyxl")
    config["sheet_name"] = get_hourly_sheet(project)
    config["daily_sheet_name"] = get_daily_sheet(project)

    baidu = dict(config.get("baidu", {}))
    baidu["credential_project"] = get_credential_profile(project)
    baidu["credential_profile"] = get_credential_profile(project)
    project_baidu = project.get("baidu", {})
    configured_mode = str(project_baidu.get("data_source_mode") or "browser")
    preference = normalize_data_source_preference(
        project.get("_app_config", {}).get("baidu_data_source_preference")
    )
    baidu["configured_data_source_mode"] = configured_mode
    baidu["data_source_preference"] = preference
    baidu["data_source_mode"] = "api_preferred" if preference == "api" else "browser"
    for key in ("api_profile", "api_timeout_seconds"):
        if project_baidu.get(key) not in (None, ""):
            baidu[key] = project_baidu[key]
    config["baidu"] = baidu

    app_config = project.get("_app_config", {})
    if app_config.get("secrets_file"):
        config["credentials_path"] = app_config["secrets_file"]

    alias_maps = get_account_alias_maps(project)
    kst = dict(config.get("kst", {}))
    kst["export_dir"] = project["kst"]["export_dir"]
    kst["auto_pick_latest"] = project["kst"].get("auto_pick_latest", True)
    kst["max_file_age_minutes"] = project["kst"].get("max_file_age_minutes", 30)
    kst["max_file_age_hours"] = project["kst"].get("max_file_age_hours", 2)
    for key in (
        "data_source",
        "installation_root",
        "identity",
        "local_api_url",
        "local_api_token_env",
        "local_api_timeout_seconds",
    ):
        if project["kst"].get(key) not in (None, ""):
            kst[key] = project["kst"][key]
    kst.setdefault("data_source", "export")
    kst["promotion_id_accounts"] = alias_maps["kst_id_to_account"]
    config["kst"] = kst

    excel_account_names = [str(item.get("standard_name") or "") for item in project.get("excel_accounts", []) if item.get("standard_name")]
    account_by_name = {str(account.get("standard_name") or ""): account for account in project.get("accounts", [])}
    accounts: dict[str, Any] = {}
    for standard in excel_account_names or list(account_by_name):
        account = account_by_name.get(standard, {"standard_name": standard})
        standard_name = account["standard_name"]
        baidu_names = list(account.get("baidu_names", []))
        aliases = []
        aliases.extend(baidu_names)
        aliases.extend(account.get("kst_names", []))
        aliases.extend(account.get("kst_ids", []))
        aliases.append(account.get("excel_name", ""))
        aliases.append(standard_name)
        accounts[standard_name] = {
            "baidu_name": baidu_names[0] if baidu_names else standard_name,
            "baidu_names": baidu_names,
            "excel_name": account.get("excel_name", standard_name),
            "kst_ids": [str(item) for item in account.get("kst_ids", [])],
            "kst_names": list(account.get("kst_names", [])),
            "aliases": [str(alias) for alias in dict.fromkeys(aliases) if alias],
        }
    config["accounts"] = accounts
    config["excel_accounts"] = [{"standard_name": name} for name in accounts]
    if isinstance(project.get("baidu_sources"), list):
        config["baidu_sources"] = [
            {
                **{key: value for key, value in source.items() if key != "accounts"},
                "accounts": [dict(account) for account in source.get("accounts", []) or []],
            }
            for source in project.get("baidu_sources", [])
        ]
    return config


def normalize_data_source_preference(value: Any) -> str:
    normalized = str(value or "api").strip().lower()
    return normalized if normalized in DATA_SOURCE_PREFERENCES else "api"


def get_data_source_preference(root: str | Path) -> str:
    return normalize_data_source_preference(
        load_app_config(root).get("baidu_data_source_preference")
    )


def set_data_source_preference(root: str | Path, preference: str) -> str:
    root_path = Path(root)
    app_config = load_app_config(root_path)
    normalized = normalize_data_source_preference(preference)
    app_config["baidu_data_source_preference"] = normalized
    _write_json_atomically(root_path / APP_CONFIG_PATH, app_config)
    return normalized


def _require_nested(errors: list[str], project: dict[str, Any], section: str, key: str) -> None:
    value = project.get(section)
    if not isinstance(value, dict) or key not in value or value.get(key) in (None, ""):
        errors.append(f"缺少字段：{section}.{key}")


def validate_project_config(project: dict[str, Any]) -> list[str]:
    project = normalize_project_config(project)
    errors: list[str] = []
    for field in REQUIRED_PROJECT_FIELDS:
        if field not in project or project.get(field) in (None, "", []):
            errors.append(f"缺少字段：{field}")

    _require_nested(errors, project, "excel", "path")
    _require_nested(errors, project, "excel", "hourly_sheet")
    _require_nested(errors, project, "excel", "daily_sheet")
    _require_nested(errors, project, "excel", "engine")
    _require_nested(errors, project, "kst", "export_dir")
    _require_nested(errors, project, "kst", "auto_pick_latest")
    _require_nested(errors, project, "kst", "max_file_age_minutes")
    has_sources = isinstance(project.get("baidu_sources"), list) and bool(project.get("baidu_sources"))
    if not has_sources:
        _require_nested(errors, project, "baidu", "credential_profile")
    excel = project.get("excel")
    if isinstance(excel, dict):
        if excel.get("engine") not in (None, "", "openpyxl", "excel_com"):
            errors.append("excel.engine 只支持 openpyxl 或 excel_com")
        if excel.get("path") in (None, ""):
            errors.append("缺少字段：excel.path")
        if excel.get("hourly_sheet") in (None, ""):
            errors.append("缺少字段：excel.hourly_sheet")
        if excel.get("daily_sheet") in (None, ""):
            errors.append("缺少字段：excel.daily_sheet")

    data_path = project.get("baidu", {}).get("data_path") if isinstance(project.get("baidu"), dict) else None
    if data_path != ["首页", "数据报告", "数据概览", "搜索推广"]:
        errors.append("baidu.data_path 必须为：首页-数据报告-数据概览-搜索推广")

    data_source_mode = str(project.get("baidu", {}).get("data_source_mode") or "browser")
    if data_source_mode not in DATA_SOURCE_MODES:
        errors.append("baidu.data_source_mode 只支持 browser、api_shadow 或 api_preferred")

    periods = project.get("hourly", {}).get("periods") if isinstance(project.get("hourly"), dict) else None
    if periods != REQUIRED_PERIODS:
        errors.append("hourly.periods 必须为：11点、15点、18点")

    daily = project.get("daily") if isinstance(project.get("daily"), dict) else {}
    if daily.get("write_fields") != REQUIRED_DAILY_WRITE_FIELDS:
        errors.append("daily.write_fields 不完整或顺序不正确")
    if daily.get("do_not_write_fields") != REQUIRED_DAILY_FORBIDDEN_FIELDS:
        errors.append("daily.do_not_write_fields 必须为：总对话、预约、到诊、就诊")

    if has_sources:
        errors.extend(_validate_baidu_sources(project))

    accounts = project.get("accounts")
    if not isinstance(accounts, list) or len(accounts) < 1:
        errors.append("项目至少需要 1 个账户")
        return errors

    promotion_ids = set()
    standard_names = set()
    for index, account in enumerate(accounts, start=1):
        prefix = f"accounts[{index}]"
        for field in ["standard_name", "baidu_names", "excel_name", "kst_ids", "kst_names"]:
            if field not in account or account.get(field) in (None, "", []):
                errors.append(f"缺少字段：{prefix}.{field}")
        standard_name = account.get("standard_name")
        promotion_id = str((account.get("kst_ids") or [""])[0] or "")
        if standard_name in standard_names:
            errors.append(f"账户标准名重复：{standard_name}")
        standard_names.add(standard_name)
        if promotion_id in promotion_ids:
            errors.append(f"商务通推广 ID 重复：{promotion_id}")
        promotion_ids.add(promotion_id)
        if not isinstance(account.get("baidu_names"), list):
            errors.append(f"{prefix}.baidu_names 必须是列表")
        if not isinstance(account.get("kst_ids"), list):
            errors.append(f"{prefix}.kst_ids 必须是列表")
        if not isinstance(account.get("kst_names"), list):
            errors.append(f"{prefix}.kst_names 必须是列表")
    return errors


def _validate_baidu_sources(project: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sources = project.get("baidu_sources")
    if not isinstance(sources, list) or not sources:
        return errors
    seen_source_ids: set[str] = set()
    for source_index, source in enumerate(sources, start=1):
        source_id = str(source.get("source_id") or f"baidu_sources[{source_index}]")
        if source_id in seen_source_ids:
            errors.append(f"百度来源 ID 重复：{source_id}")
        seen_source_ids.add(source_id)
        for field in ["source_id", "source_name", "credential_profile", "api_profile", "accounts"]:
            if field not in source or source.get(field) in (None, "", []):
                errors.append(f"缺少字段：baidu_sources[{source_index}].{field}")
        accounts = source.get("accounts")
        if not isinstance(accounts, list) or not accounts:
            errors.append(f"{source_id} accounts 未配置")
            continue
        baidu_name_owner: dict[str, str] = {}
        for account_index, account in enumerate(accounts, start=1):
            prefix = f"baidu_sources[{source_index}].accounts[{account_index}]"
            standard_name = str(account.get("standard_name") or "")
            for field in ["standard_name", "baidu_names", "excel_name", "kst_ids", "kst_names"]:
                if field not in account or account.get(field) in (None, "", []):
                    errors.append(f"缺少字段：{prefix}.{field}")
            for baidu_name in account.get("baidu_names") or []:
                name = str(baidu_name)
                if name in baidu_name_owner:
                    errors.append(f"{source_id} 百度账户名重复：{name} 同时属于 {baidu_name_owner[name]} 和 {standard_name}")
                else:
                    baidu_name_owner[name] = standard_name
    return errors
