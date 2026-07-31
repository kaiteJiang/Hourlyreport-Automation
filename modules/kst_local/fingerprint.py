from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Any

from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.models import (
    KstInstallation,
    KstInstallationLike,
    LegacyKstInstallation,
)
from modules.kst_local.legacy_db_reader import (
    legacy_history_database_paths,
)


_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _check_cancelled(
    cancel_event: Any,
    deadline: float | None = None,
) -> None:
    if (
        cancel_event is not None
        and cancel_event.is_set()
    ) or (
        deadline is not None
        and time.monotonic() >= deadline
    ):
        raise KstDiscoveryError(
            "快商通数据库读取已取消",
            category="database_busy_or_timeout",
        )


def legacy_identity_database_paths(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[Path, ...]:
    _check_cancelled(cancel_event, deadline)
    company_dir = installation.history_db.parent
    history_paths = legacy_history_database_paths(installation)
    current_paths: set[Path] = {
        path.resolve()
        for path in installation.message_database_paths
        if path.is_file()
    }
    try:
        for path in company_dir.rglob("*CS.pdb"):
            _check_cancelled(cancel_event, deadline)
            if path.is_file() and path.parent.name.endswith("-onlie"):
                current_paths.add(path.resolve())
    except OSError:
        raise KstDiscoveryError(
            "旧版客户端对话数据库无法扫描",
            category="database_incompatible",
        ) from None
    _check_cancelled(cancel_event, deadline)
    return (
        *history_paths,
        *sorted(current_paths, key=lambda path: str(path).casefold()),
    )


def electron_identity_database_paths(
    installation: KstInstallation,
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[Path, ...]:
    _check_cancelled(cancel_event, deadline)
    identity_roots: set[Path] = set()
    for path in installation.database_paths:
        resolved = path.resolve()
        identity_root = next(
            (
                parent
                for parent in (resolved.parent, *resolved.parents)
                if parent.name == installation.identity
            ),
            resolved.parent,
        )
        identity_roots.add(identity_root)
    current_paths: set[Path] = {
        path.resolve()
        for path in installation.database_paths
        if path.is_file()
    }
    try:
        for identity_root in identity_roots:
            for path in identity_root.rglob("VISITOR*.db"):
                _check_cancelled(cancel_event, deadline)
                if (
                    path.is_file()
                    and not path.name.endswith(
                        ("-wal", "-shm", "-journal")
                    )
                ):
                    current_paths.add(path.resolve())
    except OSError:
        raise KstDiscoveryError(
            "快商通身份数据库目录无法扫描",
            category="database_incompatible",
        ) from None
    _check_cancelled(cancel_event, deadline)
    return tuple(
        sorted(current_paths, key=lambda path: str(path).casefold())
    )


def _database_file_fingerprint(
    paths: tuple[Path, ...],
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for path in paths:
        _check_cancelled(cancel_event, deadline)
        for suffix in _SIDECAR_SUFFIXES:
            related_path = (
                path
                if not suffix
                else path.with_name(path.name + suffix)
            )
            try:
                stat = related_path.stat()
            except FileNotFoundError:
                state.append((str(related_path), -1, -1))
            except OSError:
                raise KstDiscoveryError(
                    "快商通数据库状态无法读取",
                    category="database_incompatible",
                ) from None
            else:
                state.append(
                    (
                        str(related_path),
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                )
    return tuple(state)


def capture_installation_identity(
    installation: KstInstallationLike,
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[KstInstallationLike, tuple[Any, ...]]:
    if isinstance(installation, LegacyKstInstallation):
        history_paths = legacy_history_database_paths(installation)
        paths = legacy_identity_database_paths(
            installation,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        family = installation.client_family
        message_paths = tuple(paths[len(history_paths) :])
        captured_installation: KstInstallationLike = (
            replace(
                installation,
                message_database_paths=message_paths,
            )
            if (
                message_paths
                and message_paths
                != installation.message_database_paths
            )
            else installation
        )
    elif isinstance(installation, KstInstallation):
        paths = electron_identity_database_paths(
            installation,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        family = "electron"
        captured_installation = (
            replace(
                installation,
                database_paths=paths,
            )
            if paths and paths != installation.database_paths
            else installation
        )
    else:
        raise KstDiscoveryError(
            "不支持的快商通客户端结构",
            category="installation_root",
        )
    return (
        captured_installation,
        (
            family,
            str(installation.root),
            installation.identity,
            tuple(str(path) for path in paths),
            _database_file_fingerprint(
                paths,
                cancel_event=cancel_event,
                deadline=deadline,
            ),
        ),
    )


def installation_identity_fingerprint(
    installation: KstInstallationLike,
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[Any, ...]:
    _, fingerprint = capture_installation_identity(
        installation,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    return fingerprint
