from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.kst_local.db_reader import read_identity_promotion_ids
from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.legacy_db_reader import (
    read_legacy_promotion_ids,
    validate_legacy_read_capability,
)
from modules.kst_local.legacy_service import LegacyKstConversationService
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstInstallation,
    KstInstallationLike,
    LegacyKstInstallation,
)
from modules.kst_local.runtime import (
    KstLiveRuntime,
    LegacyKstRuntime,
    build_live_runtime,
)


def _unsupported_installation() -> KstDiscoveryError:
    return KstDiscoveryError("不支持的快商通客户端结构")


def _required_endpoints_available(
    installation: KstInstallation,
    target_date: str,
) -> bool:
    from modules.kst_local.identity_registry import (
        _required_endpoints_available as electron_checker,
    )

    return electron_checker(installation, target_date)


def _runtime_input_state(
    installation: KstInstallation,
    target_date: str,
    snapshot: AutomaticSourceSnapshot,
) -> tuple[Any, ...]:
    from modules.kst_local.identity_registry import (
        _runtime_input_state as electron_state_reader,
    )

    return electron_state_reader(
        installation,
        target_date,
        snapshot,
    )


def _legacy_database_paths(
    installation: LegacyKstInstallation,
) -> tuple[Path, ...]:
    return (
        installation.history_db,
        *installation.message_database_paths,
    )


def read_installation_promotion_ids(
    installation: KstInstallationLike,
) -> set[str]:
    if isinstance(installation, LegacyKstInstallation):
        return read_legacy_promotion_ids(installation)
    if isinstance(installation, KstInstallation):
        return read_identity_promotion_ids(installation)
    raise _unsupported_installation()


def installation_ready(
    installation: KstInstallationLike,
    target_date: str,
) -> bool:
    if isinstance(installation, LegacyKstInstallation):
        try:
            validate_legacy_read_capability(installation)
        except Exception:
            return False
        return True
    if isinstance(installation, KstInstallation):
        try:
            return _required_endpoints_available(
                installation,
                target_date,
            )
        except Exception:
            return False
    raise _unsupported_installation()


def installation_runtime_state(
    installation: KstInstallationLike,
    target_date: str,
    snapshot: AutomaticSourceSnapshot | None = None,
) -> tuple[Any, ...]:
    if isinstance(installation, LegacyKstInstallation):
        paths = _legacy_database_paths(installation)
        database_state: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
                database_state.append(
                    (str(path), stat.st_size, stat.st_mtime_ns)
                )
            except OSError:
                database_state.append((str(path), -1, -1))
        return (
            installation.client_family,
            str(installation.root),
            installation.identity,
            tuple(str(path) for path in paths),
            tuple(database_state),
        )
    if isinstance(installation, KstInstallation):
        if snapshot is None:
            raise KstDiscoveryError("快商通 Electron 日志快照缺失")
        return (
            "electron",
            *_runtime_input_state(
                installation,
                target_date,
                snapshot,
            ),
        )
    raise _unsupported_installation()


def build_installation_runtime(
    config: dict[str, Any],
    target_date: str,
    *,
    installation: KstInstallationLike,
    snapshot: AutomaticSourceSnapshot | None = None,
) -> KstLiveRuntime | LegacyKstRuntime:
    if isinstance(installation, LegacyKstInstallation):
        service = LegacyKstConversationService(
            config,
            installation,
        )
        return LegacyKstRuntime(
            installation=installation,
            service=service,
        )
    if isinstance(installation, KstInstallation):
        return build_live_runtime(
            config,
            target_date,
            installation=installation,
            snapshot=snapshot,
        )
    raise _unsupported_installation()
