"""只读商务通本地数据源。"""

from modules.kst_local.discovery import KstDiscoveryError, discover_installation
from modules.kst_local.log_source import parse_log_snapshot

__all__ = [
    "KstDiscoveryError",
    "LegacyKstConversationService",
    "LegacyKstRuntime",
    "build_installation_runtime",
    "discover_installation",
    "installation_ready",
    "installation_runtime_state",
    "parse_log_snapshot",
    "read_installation_promotion_ids",
]


def __getattr__(name: str):
    if name == "LegacyKstConversationService":
        from modules.kst_local.legacy_service import (
            LegacyKstConversationService,
        )

        return LegacyKstConversationService
    if name == "LegacyKstRuntime":
        from modules.kst_local.runtime import LegacyKstRuntime

        return LegacyKstRuntime
    if name in {
        "build_installation_runtime",
        "installation_ready",
        "installation_runtime_state",
        "read_installation_promotion_ids",
    }:
        from modules.kst_local import backend

        return getattr(backend, name)
    raise AttributeError(name)
