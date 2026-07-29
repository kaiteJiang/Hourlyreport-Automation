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
        raise KstDiscoveryError(f"目录不具备商务通读取能力：{resolved}")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KstDiscoveryError(f"无法读取商务通版本信息：{package_path}") from exc
    version = str(package.get("version") or "").strip()
    if not version:
        raise KstDiscoveryError(f"商务通版本字段为空：{package_path}")
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
                    f"KST_INSTALLATION_ROOT 配置无效：{exc}"
                ) from exc
            raise KstDiscoveryError(f"显式配置的商务通根目录无效：{exc}") from exc
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
                "未自动发现商务通安装目录；已检查：" + "；".join(attempts)
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
        raise KstDiscoveryError("未检测到正在运行的商务通客户端进程")
    local_root = Path(
        local_app_data
        if local_app_data is not None
        else os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ).expanduser().resolve()
    candidates = _identity_candidates(
        local_root,
        active_within_seconds=(
            _active_log_max_age_seconds()
            if active_within_seconds is None
            else active_within_seconds
        ),
    )
    if not candidates:
        data_root = local_root / "OnlineWebCSNew"
        raise KstDiscoveryError(
            f"未找到同时具有日志和 VISITOR.db 的商务通身份目录：{data_root}"
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
) -> list[KstInstallationLike]:
    from modules.kst_local.legacy_discovery import discover_legacy_installations
    from modules.kst_local.machine_settings import load_kst_machine_settings

    settings = load_kst_machine_settings(root)
    configured_root = settings.installation_root
    is_explicit_legacy_root = bool(
        configured_root and (configured_root / "OnlineCS.exe").is_file()
    )
    installations: list[KstInstallationLike] = []
    try:
        installations.extend(
            discover_installations(
                explicit_root=(None if is_explicit_legacy_root else configured_root),
                require_running_process=require_running_process,
            )
        )
    except KstDiscoveryError:
        pass
    try:
        installations.extend(
            discover_legacy_installations(
                explicit_root=(configured_root if is_explicit_legacy_root else None),
                explicit_data_root=settings.data_root,
                require_running_process=require_running_process,
            )
        )
    except KstDiscoveryError:
        pass
    unique: dict[tuple[str, Path, str], KstInstallationLike] = {}
    for installation in installations:
        client_family = getattr(installation, "client_family", "electron")
        unique[(client_family, installation.root, installation.identity)] = installation
    return list(unique.values())
