from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Callable, Iterable

from modules.kst_local.models import KstInstallation, KstInstallationLike
from modules.kst_local.subprocess_utils import hidden_subprocess_kwargs


class KstDiscoveryError(RuntimeError):
    """商务通安装或当前身份无法安全定位。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "discovery_failed",
    ) -> None:
        super().__init__(message)
        self.category = category


_DISCOVERY_ERROR_PRIORITY = {
    "database_busy_or_timeout": 900,
    "database_incompatible": 800,
    "identity_mapping": 700,
    "inactive_log": 600,
    "client_path_mismatch": 500,
    "client_not_running": 450,
    "data_root": 300,
    "installation_root": 200,
    "discovery_failed": 100,
}

_DISCOVERY_SAFE_DETAILS = {
    "database_busy_or_timeout": "数据库忙或读取超时",
    "database_incompatible": "数据库结构不兼容",
    "identity_mapping": "快商通身份映射未就绪",
    "inactive_log": "未检测到活动身份",
    "client_path_mismatch": "客户端程序与运行进程不匹配",
    "client_not_running": "客户端未运行",
    "data_root": "快商通数据目录无效",
    "installation_root": "快商通客户端目录无效",
    "discovery_failed": "快商通客户端发现失败",
}


def safe_discovery_error(
    error: BaseException,
    *,
    fallback_category: str = "discovery_failed",
) -> KstDiscoveryError:
    category = str(
        getattr(error, "category", fallback_category)
    ).strip()
    if category not in _DISCOVERY_ERROR_PRIORITY:
        category = fallback_category
    if category not in _DISCOVERY_SAFE_DETAILS:
        category = "discovery_failed"
    return KstDiscoveryError(
        _DISCOVERY_SAFE_DETAILS[category],
        category=category,
    )


def discovery_error_priority(error: BaseException) -> int:
    safe_error = safe_discovery_error(error)
    return _DISCOVERY_ERROR_PRIORITY[safe_error.category]


def most_specific_discovery_error(
    errors: Iterable[BaseException],
    *,
    fallback_category: str = "installation_root",
) -> KstDiscoveryError:
    safe_errors = [
        safe_discovery_error(error)
        for error in errors
    ]
    if not safe_errors:
        return safe_discovery_error(
            KstDiscoveryError(
                "",
                category=fallback_category,
            ),
            fallback_category="installation_root",
        )
    _, selected = max(
        enumerate(safe_errors),
        key=lambda pair: (
            discovery_error_priority(pair[1]),
            -pair[0],
        ),
    )
    return selected


class KstInstallationDiscoveryResult(list[KstInstallationLike]):
    def __init__(
        self,
        installations: Iterable[KstInstallationLike] = (),
        *,
        diagnostics: Iterable[BaseException] = (),
    ) -> None:
        super().__init__(installations)
        self.diagnostics = tuple(
            safe_discovery_error(error)
            for error in diagnostics
        )


def _active_log_max_age_seconds() -> float:
    raw = os.environ.get("KST_ACTIVE_LOG_MAX_AGE_SECONDS", "300")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 300
    return min(max(value, 30), 86_400)


def _client_process_running(
    electron: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    if os.name != "nt":
        return True
    try:
        completed = runner(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {electron.name}",
                "/FO",
                "CSV",
                "/NH",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return f'"{electron.name}"'.casefold() in completed.stdout.casefold()


def _candidate_roots() -> Iterable[Path]:
    configured = os.environ.get("KST_INSTALLATION_ROOT")
    if configured:
        yield Path(configured)
    for base_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(base_name)
        if not base:
            continue
        base_path = Path(base)
        yield base_path / "KuaishangSoftx64" / "OnlineWebCSNew"
        yield base_path / "KuaishangSoft" / "OnlineWebCSNew"
    yield Path("D:/Program Files (x86)/KuaishangSoftx64/OnlineWebCSNew")
    yield Path("D:/Program Files/KuaishangSoftx64/OnlineWebCSNew")


def _validate_root(root: Path) -> tuple[Path, Path, Path, str]:
    resolved = root.expanduser().resolve()
    package_path = resolved / "resources" / "app" / "package.json"
    sqlite_module = (
        resolved
        / "resources"
        / "app"
        / "node_modules"
        / "better-sqlite3-multiple-ciphers"
    )
    executables = [
        resolved / "OnlineWebCS.exe",
        resolved / "OnlineWebCSNew.exe",
    ]
    electron = next((path for path in executables if path.is_file()), None)
    if not package_path.is_file() or electron is None or not sqlite_module.is_dir():
        raise KstDiscoveryError(
            f"目录不具备商务通读取能力：{resolved}",
            category="installation_root",
        )
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KstDiscoveryError(
            f"无法读取商务通版本信息：{package_path}",
            category="installation_root",
        ) from exc
    version = str(package.get("version") or "").strip()
    if not version:
        raise KstDiscoveryError(
            f"商务通版本字段为空：{package_path}",
            category="installation_root",
        )
    return resolved, electron.resolve(), sqlite_module.resolve(), version


def _latest_log_mtime(log_dir: Path) -> float:
    files = list(log_dir.glob("app*.log"))
    return max((path.stat().st_mtime for path in files), default=log_dir.stat().st_mtime)


def _identity_candidates(
    local_app_data: Path,
    active_within_seconds: float | None = None,
) -> list[tuple[float, str, Path, tuple[Path, ...]]]:
    data_root = local_app_data / "OnlineWebCSNew"
    log_root = data_root / "log"
    db_root = data_root / "db"
    candidates: list[tuple[float, str, Path, tuple[Path, ...]]] = []
    if log_root.is_dir() and db_root.is_dir():
        for log_dir in log_root.iterdir():
            if not log_dir.is_dir() or "_" not in log_dir.name:
                continue
            db_dir = db_root / log_dir.name
            database_paths = tuple(
                sorted(
                    (
                        path.resolve()
                        for path in db_dir.rglob("VISITOR*.db")
                        if path.is_file()
                        and not path.name.endswith(("-wal", "-shm"))
                    ),
                    key=str,
                )
            )
            if database_paths:
                latest_log_mtime = _latest_log_mtime(log_dir)
                if (
                    active_within_seconds is not None
                    and time.time() - latest_log_mtime
                    > active_within_seconds
                ):
                    continue
                candidates.append(
                    (
                        latest_log_mtime,
                        log_dir.name,
                        log_dir.resolve(),
                        database_paths,
                    )
                )
    return candidates


def _discover_identity(
    local_app_data: Path,
    explicit_identity: str | None = None,
) -> tuple[str, Path, tuple[Path, ...]]:
    data_root = local_app_data / "OnlineWebCSNew"
    candidates = _identity_candidates(local_app_data)
    if not candidates:
        raise KstDiscoveryError(
            f"未找到同时具有日志和 VISITOR.db 的商务通身份目录：{data_root}"
        )
    if explicit_identity:
        selected = next(
            (item for item in candidates if item[1] == explicit_identity),
            None,
        )
        if selected is None:
            raise KstDiscoveryError(
                f"显式配置的商务通身份不存在或缺少数据文件：{explicit_identity}"
            )
        _, identity, log_dir, database_paths = selected
        return identity, log_dir, database_paths
    _, identity, log_dir, database_paths = max(candidates, key=lambda item: item[0])
    return identity, log_dir, database_paths


def _resolve_installation_root(
    explicit_root: str | Path | None = None,
) -> tuple[Path, Path, Path, str]:
    environment_root = os.environ.get("KST_INSTALLATION_ROOT")
    root_source = "explicit"
    if explicit_root is None and environment_root:
        explicit_root = environment_root
        root_source = "environment"
    if explicit_root is not None:
        try:
            root, electron, sqlite_module, version = _validate_root(Path(explicit_root))
        except KstDiscoveryError as exc:
            if root_source == "environment":
                raise KstDiscoveryError(
                    f"KST_INSTALLATION_ROOT 配置无效：{exc}",
                    category="installation_root",
                ) from exc
            raise KstDiscoveryError(
                f"显式配置的商务通根目录无效：{exc}",
                category="installation_root",
            ) from exc
    else:
        attempts: list[str] = []
        resolved_values = None
        for candidate in _candidate_roots():
            try:
                resolved_values = _validate_root(candidate)
                break
            except KstDiscoveryError:
                attempts.append(str(candidate))
        if resolved_values is None:
            raise KstDiscoveryError(
                "未自动发现商务通安装目录；已检查："
                + "；".join(attempts),
                category="installation_root",
            )
        root, electron, sqlite_module, version = resolved_values
    return root, electron, sqlite_module, version


def discover_installations(
    explicit_root: str | Path | None = None,
    local_app_data: str | Path | None = None,
    active_within_seconds: float | None = None,
    require_running_process: bool = False,
    process_checker: Callable[[Path], bool] = _client_process_running,
) -> list[KstInstallation]:
    root, electron, sqlite_module, version = _resolve_installation_root(
        explicit_root
    )
    if require_running_process and not process_checker(electron):
        raise KstDiscoveryError(
            "未检测到正在运行的商务通客户端进程",
            category="client_not_running",
        )
    local_root = Path(
        local_app_data
        if local_app_data is not None
        else os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ).expanduser().resolve()
    all_candidates = _identity_candidates(local_root)
    active_age = (
        _active_log_max_age_seconds()
        if active_within_seconds is None
        else active_within_seconds
    )
    candidates = [
        item
        for item in all_candidates
        if time.time() - item[0] <= active_age
    ]
    if not candidates:
        data_root = local_root / "OnlineWebCSNew"
        raise KstDiscoveryError(
            f"未找到同时具有日志和 VISITOR.db 的商务通身份目录：{data_root}",
            category=(
                "inactive_log"
                if all_candidates
                else "data_root"
            ),
        )
    return [
        KstInstallation(
            root=root,
            electron=electron,
            version=version,
            identity=identity,
            log_dir=log_dir,
            database_paths=database_paths,
            sqlite_module_dir=sqlite_module,
        )
        for _, identity, log_dir, database_paths in sorted(
            candidates,
            key=lambda item: item[1],
        )
    ]


def discover_installation(
    explicit_root: str | Path | None = None,
    local_app_data: str | Path | None = None,
    explicit_identity: str | None = None,
) -> KstInstallation:
    root, electron, sqlite_module, version = _resolve_installation_root(
        explicit_root
    )

    local_root = Path(
        local_app_data
        if local_app_data is not None
        else os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ).expanduser().resolve()
    identity, log_dir, database_paths = _discover_identity(
        local_root,
        explicit_identity=explicit_identity,
    )
    return KstInstallation(
        root=root,
        electron=electron,
        version=version,
        identity=identity,
        log_dir=log_dir,
        database_paths=database_paths,
        sqlite_module_dir=sqlite_module,
    )


def discover_all_installations(
    root: str | Path,
    *,
    require_running_process: bool = True,
    cancel_event: object | None = None,
) -> KstInstallationDiscoveryResult:
    from modules.kst_local.legacy_discovery import discover_legacy_installations
    from modules.kst_local.machine_settings import load_kst_machine_settings

    settings = load_kst_machine_settings(root)
    environment_root = os.environ.get("KST_INSTALLATION_ROOT")
    configured_root = settings.installation_root or (
        Path(environment_root).expanduser().resolve()
        if environment_root
        else None
    )
    is_explicit_legacy_root = bool(
        configured_root and (configured_root / "OnlineCS.exe").is_file()
    )
    electron_explicit_root = (
        None if is_explicit_legacy_root else configured_root
    )
    electron_data_root = settings.data_root
    if is_explicit_legacy_root:
        electron_data_root = None
    if (
        electron_data_root is not None
        and electron_data_root.name.casefold() == "onlinewebcsnew"
    ):
        electron_data_root = electron_data_root.parent
    legacy_explicit_root = configured_root if is_explicit_legacy_root else None
    installations: list[KstInstallationLike] = []
    discovery_errors: list[KstDiscoveryError] = []
    try:
        installations.extend(
            discover_installations(
                explicit_root=electron_explicit_root,
                local_app_data=electron_data_root,
                require_running_process=require_running_process,
            )
        )
    except KstDiscoveryError as exc:
        if electron_explicit_root is not None:
            raise
        discovery_errors.append(exc)
    if configured_root is None or is_explicit_legacy_root:
        try:
            legacy_result = discover_legacy_installations(
                explicit_root=legacy_explicit_root,
                explicit_data_root=settings.data_root,
                require_running_process=require_running_process,
                cancel_event=cancel_event,
            )
            installations.extend(legacy_result)
            discovery_errors.extend(
                getattr(legacy_result, "diagnostics", ()) or ()
            )
        except KstDiscoveryError as exc:
            if (
                not installations
                and (
                    legacy_explicit_root is not None
                    or settings.data_root is not None
                )
            ):
                raise
            discovery_errors.append(exc)
    unique: dict[tuple[str, Path, str], KstInstallationLike] = {}
    for installation in installations:
        client_family = getattr(installation, "client_family", "electron")
        unique[(client_family, installation.root, installation.identity)] = installation
    if not unique:
        raise most_specific_discovery_error(discovery_errors)
    return KstInstallationDiscoveryResult(
        unique.values(),
        diagnostics=discovery_errors,
    )
