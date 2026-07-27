from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from modules.kst_local.models import KstInstallation


class KstDiscoveryError(RuntimeError):
    """商务通安装或当前身份无法安全定位。"""


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
                candidates.append(
                    (
                        _latest_log_mtime(log_dir),
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
    if explicit_root is not None:
        try:
            root, electron, sqlite_module, version = _validate_root(Path(explicit_root))
        except KstDiscoveryError as exc:
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
) -> list[KstInstallation]:
    root, electron, sqlite_module, version = _resolve_installation_root(
        explicit_root
    )
    local_root = Path(
        local_app_data
        if local_app_data is not None
        else os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ).expanduser().resolve()
    candidates = _identity_candidates(local_root)
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
