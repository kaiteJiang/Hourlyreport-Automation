"""只读商务通本地数据源。"""

from modules.kst_local.discovery import KstDiscoveryError, discover_installation
from modules.kst_local.log_source import parse_log_snapshot

__all__ = ["KstDiscoveryError", "discover_installation", "parse_log_snapshot"]
