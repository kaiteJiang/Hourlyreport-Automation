from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Iterable

from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.models import LegacyKstInstallation
from modules.kst_local.subprocess_utils import hidden_subprocess_kwargs


_LEGACY_LOG_MAX_AGE_SECONDS = 900


def redirected_documents_candidates() -> tuple[Path, ...]:
    values: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                values.append(
                    Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
                )
        except OSError:
            pass
    values.append(Path.home() / "Documents")
    return tuple(dict.fromkeys(path.expanduser().resolve() for path in values))


def running_kst_process_paths() -> tuple[Path, ...]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='OnlineCS.exe'\" | "
                "Select-Object -ExpandProperty ExecutablePath",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(
        Path(line.strip()).resolve()
        for line in completed.stdout.splitlines()
        if line.strip()
    )


def _legacy_root_candidates(process_paths: Iterable[Path]) -> tuple[Path, ...]:
    values = [path.resolve().parent for path in process_paths]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            values.append(Path(base) / "KuaishangSoft" / "OnlineCustomerService")
            values.append(Path(base) / "KuaishangSoftx64" / "OnlineCustomerService")
    return tuple(dict.fromkeys(path.expanduser().resolve() for path in values))


def _data_root_candidates() -> tuple[Path, ...]:
    return tuple(path / "KuaiShangDataNew" for path in redirected_documents_candidates())


def _has_sqlite_header(path: Path) -> bool:
    try:
        with path.open("rb") as database:
            return database.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _has_table(path: Path, table: str) -> bool:
    if not _has_sqlite_header(path):
        return False
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return False
    return row is not None


def _validate_legacy_root(root: Path) -> tuple[Path, Path]:
    resolved = root.expanduser().resolve()
    executable = resolved / "OnlineCS.exe"
    template = resolved / "config" / "DBCOMPANY.dll"
    if not executable.is_file() or not _has_sqlite_header(template):
        raise KstDiscoveryError("旧版客户端程序目录不具备读取能力")
    return resolved, executable.resolve()


def _validate_data_root(data_root: Path) -> tuple[Path, Path, Path]:
    resolved = data_root.expanduser().resolve()
    db_root = resolved / "db"
    log_dir = resolved / "logs"
    if not db_root.is_dir() or not log_dir.is_dir():
        raise KstDiscoveryError("旧版客户端数据目录不具备读取能力")
    return resolved, db_root, log_dir


def _has_recent_log(log_dir: Path, now_timestamp: float) -> bool:
    try:
        return any(
            now_timestamp - path.stat().st_mtime <= _LEGACY_LOG_MAX_AGE_SECONDS
            for path in log_dir.rglob("*.log")
            if path.is_file()
        )
    except OSError:
        return False


def _matching_process(executable: Path, process_paths: Iterable[Path]) -> bool:
    executable_text = str(executable.resolve()).casefold()
    return any(str(Path(path).resolve()).casefold() == executable_text for path in process_paths)


def _discover_company_identities(
    *, root: Path, executable: Path, data_root: Path, db_root: Path, log_dir: Path
) -> list[LegacyKstInstallation]:
    found: list[LegacyKstInstallation] = []
    for company_dir in sorted((path for path in db_root.iterdir() if path.is_dir()), key=lambda path: path.name):
        identity = company_dir.name
        history_db = company_dir / f"{identity}_HIS.cdb"
        if not _has_table(history_db, "OC_HDVISITORINFO"):
            continue
        message_paths = tuple(
            sorted(
                (
                    path.resolve()
                    for path in company_dir.rglob("*_CS.pdb")
                    if path.is_file()
                    and path.parent.name.endswith("-onlie")
                    and _has_table(path, "DIALOGRECORD_VISITOR")
                ),
                key=str,
            )
        )
        if not message_paths:
            continue
        found.append(
            LegacyKstInstallation(
                root=root,
                executable=executable,
                version="legacy_java",
                identity=identity,
                log_dir=log_dir,
                data_root=data_root,
                history_db=history_db.resolve(),
                message_database_paths=message_paths,
            )
        )
    return found


def discover_legacy_installations(
    *,
    explicit_root: str | Path | None = None,
    explicit_data_root: str | Path | None = None,
    process_paths: Iterable[Path] | None = None,
    now_timestamp: float | None = None,
    require_running_process: bool = True,
) -> list[LegacyKstInstallation]:
    detected_process_paths = tuple(
        Path(path).expanduser().resolve()
        for path in (running_kst_process_paths() if process_paths is None else process_paths)
    )
    root_candidates = (
        (Path(explicit_root),)
        if explicit_root is not None
        else _legacy_root_candidates(detected_process_paths)
    )
    data_candidates = (
        (Path(explicit_data_root),)
        if explicit_data_root is not None
        else _data_root_candidates()
    )
    timestamp = time.time() if now_timestamp is None else now_timestamp
    found: list[LegacyKstInstallation] = []
    root_errors: list[KstDiscoveryError] = []
    data_errors: list[KstDiscoveryError] = []
    for root_candidate in root_candidates:
        try:
            root, executable = _validate_legacy_root(root_candidate)
        except KstDiscoveryError as exc:
            root_errors.append(exc)
            continue
        valid_data_roots: list[tuple[Path, Path, Path]] = []
        for data_candidate in data_candidates:
            try:
                valid_data_roots.append(_validate_data_root(data_candidate))
            except KstDiscoveryError as exc:
                data_errors.append(exc)
                continue
        if require_running_process and not _matching_process(executable, detected_process_paths):
            continue
        for data_root, db_root, log_dir in valid_data_roots:
            if not _has_recent_log(log_dir, timestamp):
                continue
            found.extend(
                _discover_company_identities(
                    root=root,
                    executable=executable,
                    data_root=data_root,
                    db_root=db_root,
                    log_dir=log_dir,
                )
            )
    if explicit_root is not None and root_errors:
        raise root_errors[0]
    if explicit_data_root is not None and data_errors:
        raise data_errors[0]
    return found
