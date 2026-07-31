from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
import time
from typing import Callable, Iterable

from modules.kst_local.discovery import (
    KstDiscoveryError,
    KstInstallationDiscoveryResult,
    most_specific_discovery_error,
)
from modules.kst_local.fingerprint import capture_installation_identity
from modules.kst_local.legacy_db_reader import (
    KstLegacyDatabaseError,
    inspect_legacy_read_capability,
)
from modules.kst_local.models import LegacyKstInstallation


_LEGACY_LOG_MAX_AGE_SECONDS = 900


def read_windows_file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(
            str(path), 0, size, buffer
        ):
            return None
        translation = ctypes.c_void_p()
        translation_length = wintypes.UINT()
        if (
            ctypes.windll.version.VerQueryValueW(
                buffer,
                r"\VarFileInfo\Translation",
                ctypes.byref(translation),
                ctypes.byref(translation_length),
            )
            and translation_length.value >= 4
        ):
            language, code_page = ctypes.cast(
                translation, ctypes.POINTER(ctypes.c_ushort)
            )[:2]
            text = ctypes.c_void_p()
            text_length = wintypes.UINT()
            if ctypes.windll.version.VerQueryValueW(
                buffer,
                f"\\StringFileInfo\\{language:04x}{code_page:04x}\\FileVersion",
                ctypes.byref(text),
                ctypes.byref(text_length),
            ):
                version_text = ctypes.wstring_at(
                    text, text_length.value
                ).rstrip("\0").strip()
                if version_text:
                    return version_text
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            buffer, "\\", ctypes.byref(value), ctypes.byref(length)
        ):
            return None

        class VsFixedFileInfo(ctypes.Structure):
            _fields_ = [
                ("signature", wintypes.DWORD),
                ("struct_version", wintypes.DWORD),
                ("file_version_ms", wintypes.DWORD),
                ("file_version_ls", wintypes.DWORD),
                ("product_version_ms", wintypes.DWORD),
                ("product_version_ls", wintypes.DWORD),
                ("file_flags_mask", wintypes.DWORD),
                ("file_flags", wintypes.DWORD),
                ("file_os", wintypes.DWORD),
                ("file_type", wintypes.DWORD),
                ("file_subtype", wintypes.DWORD),
                ("file_date_ms", wintypes.DWORD),
                ("file_date_ls", wintypes.DWORD),
            ]

        fixed = ctypes.cast(
            value, ctypes.POINTER(VsFixedFileInfo)
        ).contents
        if fixed.signature != 0xFEEF04BD:
            return None
        version = (
            fixed.file_version_ms >> 16,
            fixed.file_version_ms & 0xFFFF,
            fixed.file_version_ls >> 16,
            fixed.file_version_ls & 0xFFFF,
        )
        return ".".join(str(part) for part in version) if any(version) else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


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


def _windows_onlinecs_process_paths() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32W),
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessEntry32W),
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or snapshot == invalid_handle:
            return ()
        paths: list[Path] = []
        try:
            entry = ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(ProcessEntry32W)
            has_entry = bool(
                kernel32.Process32FirstW(
                    snapshot,
                    ctypes.byref(entry),
                )
            )
            while has_entry:
                if entry.szExeFile.casefold() == "onlinecs.exe":
                    process = kernel32.OpenProcess(
                        0x1000,
                        False,
                        entry.th32ProcessID,
                    )
                    if process:
                        try:
                            size = wintypes.DWORD(32_768)
                            buffer = ctypes.create_unicode_buffer(
                                size.value
                            )
                            if kernel32.QueryFullProcessImageNameW(
                                process,
                                0,
                                buffer,
                                ctypes.byref(size),
                            ):
                                paths.append(
                                    Path(buffer.value).resolve()
                                )
                        finally:
                            kernel32.CloseHandle(process)
                has_entry = bool(
                    kernel32.Process32NextW(
                        snapshot,
                        ctypes.byref(entry),
                    )
                )
        finally:
            kernel32.CloseHandle(snapshot)
        return tuple(dict.fromkeys(paths))
    except (AttributeError, OSError, TypeError, ValueError):
        return ()


def running_kst_process_paths() -> tuple[Path, ...]:
    return _windows_onlinecs_process_paths()


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


def _validate_legacy_root(root: Path) -> tuple[Path, Path]:
    resolved = root.expanduser().resolve()
    executable = resolved / "OnlineCS.exe"
    template = resolved / "config" / "DBCOMPANY.dll"
    if not executable.is_file() or not _has_sqlite_header(template):
        raise KstDiscoveryError(
            "旧版客户端程序目录不具备读取能力",
            category="installation_root",
        )
    return resolved, executable.resolve()


def _validate_data_root(data_root: Path) -> tuple[Path, Path, Path]:
    resolved = data_root.expanduser().resolve()
    db_root = resolved / "db"
    log_dir = resolved / "logs"
    if not db_root.is_dir() or not log_dir.is_dir():
        raise KstDiscoveryError(
            "旧版客户端数据目录不具备读取能力",
            category="data_root",
        )
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


def legacy_installation_active(
    installation: LegacyKstInstallation,
    *,
    process_paths: Iterable[Path] | None = None,
    now_timestamp: float | None = None,
) -> bool:
    detected_process_paths = tuple(
        Path(path).expanduser().resolve()
        for path in (
            running_kst_process_paths()
            if process_paths is None
            else process_paths
        )
    )
    if not _matching_process(
        installation.executable,
        detected_process_paths,
    ):
        mismatch = bool(detected_process_paths)
        message = (
            "客户端程序与运行进程不匹配"
            if mismatch
            else "客户端未运行"
        )
        raise KstDiscoveryError(
            message,
            category=(
                "client_path_mismatch"
                if mismatch
                else "client_not_running"
            ),
        )
    timestamp = (
        time.time()
        if now_timestamp is None
        else float(now_timestamp)
    )
    if not _has_recent_log(installation.log_dir, timestamp):
        raise KstDiscoveryError(
            "未检测到活动身份",
            category="inactive_log",
        )
    return True


def _discover_company_identities(
    *,
    root: Path,
    executable: Path,
    version: str,
    data_root: Path,
    db_root: Path,
    log_dir: Path,
    cancel_event: object | None,
    fail_closed: bool,
    diagnostics: list[KstDiscoveryError],
) -> list[LegacyKstInstallation]:
    found: list[LegacyKstInstallation] = []
    diagnostic_start = len(diagnostics)
    try:
        company_dirs = sorted(
            (path for path in db_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise KstDiscoveryError(
            "旧版客户端数据库目录无法扫描",
            category="data_root",
        ) from exc
    raise_identity_error = fail_closed and len(company_dirs) == 1
    for company_dir in company_dirs:
        if cancel_event is not None and cancel_event.is_set():
            raise KstDiscoveryError(
                "老版快商通数据库读取已取消",
                category="database_busy_or_timeout",
            )
        deadline = time.monotonic() + 5.0
        identity = company_dir.name
        history_db = company_dir / f"{identity}_HIS.cdb"
        if not history_db.is_file():
            error = KstDiscoveryError(
                "旧版快商通数据库结构不兼容",
                category="database_incompatible",
            )
            if raise_identity_error:
                raise error
            diagnostics.append(error)
            continue
        try:
            message_paths = tuple(
                sorted(
                    (
                        path.resolve()
                        for path in company_dir.rglob("*CS.pdb")
                        if path.is_file()
                        and path.parent.name.endswith("-onlie")
                    ),
                    key=str,
                )
            )
        except OSError as exc:
            error = KstDiscoveryError(
                "旧版客户端对话数据库无法扫描",
                category="data_root",
            )
            if raise_identity_error:
                raise error from exc
            diagnostics.append(error)
            continue
        if not message_paths:
            error = KstDiscoveryError(
                "旧版快商通数据库结构不兼容",
                category="database_incompatible",
            )
            if raise_identity_error:
                raise error
            diagnostics.append(error)
            continue
        installation = LegacyKstInstallation(
            root=root,
            executable=executable,
            version=version,
            identity=identity,
            log_dir=log_dir,
            data_root=data_root,
            history_db=history_db.resolve(),
            message_database_paths=message_paths,
        )
        try:
            installation, fingerprint_before = (
                capture_installation_identity(
                    installation,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
            )
            promotion_ids = inspect_legacy_read_capability(
                installation,
                cancel_event=cancel_event,
                deadline=deadline,
            )
            installation_after, fingerprint_after = (
                capture_installation_identity(
                    installation,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
            )
            if fingerprint_before != fingerprint_after:
                raise KstLegacyDatabaseError(
                    "旧版快商通数据库读取期间发生变化",
                    category="database_busy_or_timeout",
                )
            installation = installation_after
            fingerprint = fingerprint_after
        except KstLegacyDatabaseError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise KstDiscoveryError(
                    str(exc),
                    category=exc.category,
                ) from None
            if raise_identity_error:
                raise KstDiscoveryError(
                    str(exc),
                    category=exc.category,
                ) from None
            diagnostics.append(
                KstDiscoveryError(
                    str(exc),
                    category=exc.category,
                )
            )
            continue
        except KstDiscoveryError as exc:
            if raise_identity_error:
                raise
            diagnostics.append(exc)
            continue
        found.append(
            replace(
                installation,
                promotion_ids=frozenset(promotion_ids),
                identity_fingerprint=fingerprint,
            )
        )
    if fail_closed and not found and len(diagnostics) > diagnostic_start:
        raise most_specific_discovery_error(
            diagnostics[diagnostic_start:]
        )
    return found


def discover_legacy_installations(
    *,
    explicit_root: str | Path | None = None,
    explicit_data_root: str | Path | None = None,
    process_paths: Iterable[Path] | None = None,
    now_timestamp: float | None = None,
    require_running_process: bool = True,
    version_reader: Callable[[Path], str | None] = read_windows_file_version,
    cancel_event: object | None = None,
) -> KstInstallationDiscoveryResult:
    environment_root = os.environ.get("KST_INSTALLATION_ROOT")
    root_is_explicit = explicit_root is not None
    if explicit_root is None and environment_root:
        explicit_root = Path(environment_root)
        root_is_explicit = True
    detected_process_paths = tuple(
        Path(path).expanduser().resolve()
        for path in (running_kst_process_paths() if process_paths is None else process_paths)
    )
    root_candidates = (
        (Path(explicit_root),)
        if explicit_root is not None
        else _legacy_root_candidates(detected_process_paths)
    )
    explicit_data = (
        _validate_data_root(Path(explicit_data_root))
        if explicit_data_root is not None
        else None
    )
    data_candidates = (
        _data_root_candidates() if explicit_data is None else ()
    )
    timestamp = time.time() if now_timestamp is None else now_timestamp
    found: list[LegacyKstInstallation] = []
    root_errors: list[KstDiscoveryError] = []
    data_errors: list[KstDiscoveryError] = []
    automatic_errors: list[KstDiscoveryError] = []
    for root_candidate in root_candidates:
        try:
            root, executable = _validate_legacy_root(root_candidate)
        except KstDiscoveryError as exc:
            root_errors.append(exc)
            continue
        valid_data_roots = [explicit_data] if explicit_data is not None else []
        for data_candidate in data_candidates:
            try:
                valid_data_roots.append(_validate_data_root(data_candidate))
            except KstDiscoveryError as exc:
                data_errors.append(exc)
                continue
        if not valid_data_roots:
            automatic_errors.append(
                KstDiscoveryError(
                    "旧版客户端数据目录无效",
                    category="data_root",
                )
            )
        if require_running_process and not _matching_process(executable, detected_process_paths):
            if root_is_explicit:
                mismatch = bool(detected_process_paths)
                raise KstDiscoveryError(
                    (
                        "客户端程序与运行进程不匹配"
                        if mismatch
                        else "客户端未运行"
                    ),
                    category=(
                        "client_path_mismatch"
                        if mismatch
                        else "client_not_running"
                    ),
                )
            automatic_errors.append(
                KstDiscoveryError(
                    (
                        "客户端程序与运行进程不匹配"
                        if detected_process_paths
                        else "客户端未运行"
                    ),
                    category=(
                        "client_path_mismatch"
                        if detected_process_paths
                        else "client_not_running"
                    ),
                )
            )
            continue
        for data_root, db_root, log_dir in valid_data_roots:
            if not _has_recent_log(log_dir, timestamp):
                if root_is_explicit or explicit_data_root is not None:
                    raise KstDiscoveryError(
                        "未检测到活动身份",
                        category="inactive_log",
                    )
                automatic_errors.append(
                    KstDiscoveryError(
                        "未检测到活动身份",
                        category="inactive_log",
                    )
                )
                continue
            try:
                raw_version = version_reader(executable)
                version = str(raw_version).strip() if raw_version else "unknown"
            except (AttributeError, OSError, TypeError, ValueError):
                version = "unknown"
            try:
                found.extend(
                    _discover_company_identities(
                        root=root,
                        executable=executable,
                        version=version,
                        data_root=data_root,
                        db_root=db_root,
                        log_dir=log_dir,
                        cancel_event=cancel_event,
                        fail_closed=(
                            root_is_explicit
                            or explicit_data_root is not None
                        ),
                        diagnostics=automatic_errors,
                    )
                )
            except (KstDiscoveryError, OSError) as exc:
                if cancel_event is not None and cancel_event.is_set():
                    if isinstance(exc, KstDiscoveryError):
                        raise
                    raise KstDiscoveryError(
                        "老版快商通数据库读取已取消",
                        category="database_busy_or_timeout",
                    ) from None
                if root_is_explicit or explicit_data_root is not None:
                    if isinstance(exc, KstDiscoveryError):
                        raise
                    raise KstDiscoveryError("旧版客户端数据目录无法扫描") from exc
                automatic_errors.append(
                    exc
                    if isinstance(exc, KstDiscoveryError)
                    else KstDiscoveryError(
                        "旧版客户端数据目录无法扫描",
                        category="data_root",
                    )
                )
                continue
    if root_is_explicit and root_errors:
        raise root_errors[0]
    if explicit_data_root is not None and data_errors:
        raise data_errors[0]
    if found:
        return KstInstallationDiscoveryResult(
            found,
            diagnostics=automatic_errors,
        )
    raise most_specific_discovery_error(
        [
            *root_errors,
            *data_errors,
            *automatic_errors,
        ]
    )
