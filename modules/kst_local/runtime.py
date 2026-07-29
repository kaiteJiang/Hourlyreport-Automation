from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from modules.kst_local.api_client import KstApiClient
from modules.kst_local.db_reader import read_cache_candidates
from modules.kst_local.discovery import discover_installation
from modules.kst_local.log_source import parse_cached_log_snapshot
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstInstallation,
    LegacyKstInstallation,
)
from modules.kst_local.service import KstConversationService


@dataclass(frozen=True)
class KstLiveRuntime:
    installation: KstInstallation
    snapshot: AutomaticSourceSnapshot
    service: KstConversationService

    def health(self) -> dict[str, Any]:
        auth = self.snapshot.auth.safe_diagnostics()
        required = {"visitor_info", "visitor_card", "tag_dictionary"}
        endpoints = set(auth["endpoint_names"])
        required_endpoints_available = (
            required.issubset(endpoints)
            and bool(self.snapshot.auth.common_query)
            and bool(self.snapshot.auth.headers)
        )
        return {
            "status": "ok" if required_endpoints_available else "not_ready",
            "installation": self.installation.safe_diagnostics(),
            "automatic_sources": self.snapshot.safe_diagnostics(),
            "required_endpoints_available": required_endpoints_available,
        }


@dataclass(frozen=True)
class LegacyKstRuntime:
    installation: LegacyKstInstallation
    service: Any

    def health(self) -> dict[str, Any]:
        required_paths = (
            self.installation.history_db,
            *self.installation.message_database_paths,
        )
        ready = bool(self.installation.promotion_ids) and all(
            path.is_file() for path in required_paths
        )
        return {
            "status": "ok" if ready else "not_ready",
            "installation": self.installation.safe_diagnostics(),
            "automatic_sources": {
                "source_type": "legacy_live_database",
            },
            "required_endpoints_available": ready,
            "read_only_database_available": ready,
        }


def build_live_runtime(
    config: dict[str, Any],
    target_date: str,
    *,
    installation_root: str | Path | None = None,
    installation: KstInstallation | None = None,
    snapshot: AutomaticSourceSnapshot | None = None,
) -> KstLiveRuntime:
    kst_config = config.get("kst", {}) or {}
    if installation is None:
        installation = discover_installation(
            explicit_root=installation_root or kst_config.get("installation_root"),
            explicit_identity=kst_config.get("identity"),
        )
    if snapshot is None:
        snapshot = parse_cached_log_snapshot(
            installation.log_dir,
            target_date,
            auth_date=date.today().isoformat(),
        )
    candidates = read_cache_candidates(installation, target_date)
    client = KstApiClient(snapshot.auth)
    service = KstConversationService(
        config=config,
        snapshot=snapshot,
        candidates=candidates,
        client=client,
    )
    return KstLiveRuntime(
        installation=installation,
        snapshot=snapshot,
        service=service,
    )


def write_hourly_report(
    report: dict[str, Any],
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
