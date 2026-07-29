from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from modules.kst_local.db_reader import read_identity_promotion_ids
from modules.kst_local.discovery import KstDiscoveryError
from modules.kst_local.fingerprint import (
    installation_identity_fingerprint,
    legacy_identity_database_paths,
)
from modules.kst_local.legacy_db_reader import (
    KstLegacyDatabaseError,
    inspect_legacy_read_capability,
)
from modules.kst_local.legacy_discovery import legacy_installation_active
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
    return legacy_identity_database_paths(
        installation,
    )


def read_installation_promotion_ids(
    installation: KstInstallationLike,
    *,
    cancel_event: Any = None,
) -> set[str]:
    if isinstance(installation, LegacyKstInstallation):
        if installation.promotion_ids is not None:
            if installation.promotion_ids:
                return set(installation.promotion_ids)
            raise KstLegacyDatabaseError(
                "老版快商通身份缺少可用推广 ID",
                category="identity_mapping",
            )
        return inspect_legacy_read_capability(
            installation,
            cancel_event=cancel_event,
        )
    if isinstance(installation, KstInstallation):
        return read_identity_promotion_ids(installation)
    raise _unsupported_installation()


def installation_active(
    installation: KstInstallationLike,
    *,
    cancel_event: Any = None,
) -> bool:
    if cancel_event is not None and cancel_event.is_set():
        raise KstDiscoveryError(
            "快商通读取已取消",
            category="database_busy_or_timeout",
        )
    if isinstance(installation, LegacyKstInstallation):
        return legacy_installation_active(installation)
    if isinstance(installation, KstInstallation):
        return True
    raise _unsupported_installation()


def installation_ready(
    installation: KstInstallationLike,
    target_date: str,
    *,
    cancel_event: Any = None,
    process_paths: Any = None,
    now_timestamp: float | None = None,
) -> bool:
    if isinstance(installation, LegacyKstInstallation):
        if cancel_event is not None and cancel_event.is_set():
            raise KstLegacyDatabaseError(
                "老版快商通数据库读取已取消",
                category="database_busy_or_timeout",
            )
        legacy_installation_active(
            installation,
            process_paths=process_paths,
            now_timestamp=now_timestamp,
        )
        return bool(installation.promotion_ids)
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
    *,
    cancel_event: Any = None,
) -> tuple[Any, ...]:
    if isinstance(installation, LegacyKstInstallation):
        return installation_identity_fingerprint(
            installation,
            cancel_event=cancel_event,
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
    cancel_event: Any = None,
    process_paths: Any = None,
    now_timestamp: float | None = None,
) -> KstLiveRuntime | LegacyKstRuntime:
    if isinstance(installation, LegacyKstInstallation):
        ready = installation_ready(
            installation,
            target_date,
            cancel_event=cancel_event,
            process_paths=process_paths,
            now_timestamp=now_timestamp,
        )
        if not ready:
            raise KstLegacyDatabaseError(
                "老版快商通身份缺少可用推广 ID",
                category="identity_mapping",
            )
        paths = _legacy_database_paths(installation)
        installation = replace(
            installation,
            message_database_paths=tuple(paths[1:]),
        )
        service = LegacyKstConversationService(
            config,
            installation,
            cancel_event=cancel_event,
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
