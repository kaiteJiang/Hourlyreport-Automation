from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.models import (
    KstInstallation,
    KstInstallationLike,
    LegacyKstInstallation,
)


_SIDECAR_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _check_cancelled(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KstDiscoveryError(
            "快商通数据库读取已取消",
            category="database_busy_or_timeout",
        )


def legacy_identity_database_paths(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
) -> tuple[Path, ...]:
    _check_cancelled(cancel_event)
    company_dir = installation.history_db.parent
    current_paths: set[Path] = {
        path.resolve()
        for path in installation.message_database_paths
        if path.is_file()
    }
    try:
        current_paths.update(
            path.resolve()
            for path in company_dir.rglob("*_CS.pdb")
            if path.is_file() and path.parent.name.endswith("-onlie")
        )
    except OSError:
        raise KstDiscoveryError(
            "旧版客户端对话数据库无法扫描",
            category="database_incompatible",
        ) from None
    _check_cancelled(cancel_event)
    return (
        installation.history_db.resolve(),
        *sorted(current_paths, key=lambda path: str(path).casefold()),
    )


def _database_file_fingerprint(
    paths: tuple[Path, ...],
    *,
    cancel_event: Any = None,
) -> tuple[tuple[str, int, int], ...]:
    state: list[tuple[str, int, int]] = []
    for path in paths:
        _check_cancelled(cancel_event)
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


def installation_identity_fingerprint(
    installation: KstInstallationLike,
    *,
    cancel_event: Any = None,
) -> tuple[Any, ...]:
    if isinstance(installation, LegacyKstInstallation):
        paths = legacy_identity_database_paths(
            installation,
            cancel_event=cancel_event,
        )
        family = installation.client_family
    elif isinstance(installation, KstInstallation):
        paths = tuple(
            sorted(
                (path.resolve() for path in installation.database_paths),
                key=lambda path: str(path).casefold(),
            )
        )
        family = "electron"
    else:
        raise KstDiscoveryError(
            "不支持的快商通客户端结构",
            category="installation_root",
        )
    return (
        family,
        str(installation.root),
        installation.identity,
        tuple(str(path) for path in paths),
        _database_file_fingerprint(
            paths,
            cancel_event=cancel_event,
        ),
    )
