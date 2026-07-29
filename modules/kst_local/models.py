from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KstInstallation:
    root: Path
    electron: Path
    version: str
    identity: str
    log_dir: Path
    database_paths: tuple[Path, ...]
    sqlite_module_dir: Path

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "electron": str(self.electron),
            "version": self.version,
            "identity": self.identity,
            "log_dir": str(self.log_dir),
            "database_count": len(self.database_paths),
            "sqlcipher_module_available": self.sqlite_module_dir.is_dir(),
        }


@dataclass(frozen=True)
class LegacyKstInstallation:
    root: Path
    executable: Path
    version: str
    identity: str
    log_dir: Path
    data_root: Path
    history_db: Path
    message_database_paths: tuple[Path, ...]
    promotion_ids: frozenset[str] | None = None
    client_family: str = "legacy_java"

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "executable": str(self.executable),
            "version": self.version,
            "identity": self.identity,
            "log_dir": str(self.log_dir),
            "data_root": str(self.data_root),
            "message_database_count": len(self.message_database_paths),
            "client_family": self.client_family,
        }


KstInstallationLike = KstInstallation | LegacyKstInstallation


@dataclass(frozen=True)
class KstAuthContext:
    common_query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    endpoints: dict[str, str] = field(default_factory=dict)

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "common_query_available": bool(self.common_query),
            "headers_available": bool(self.headers),
            "endpoint_names": sorted(self.endpoints),
        }


@dataclass(frozen=True)
class AutomaticSourceSnapshot:
    sources_by_rec_id: dict[str, frozenset[str]]
    auth: KstAuthContext
    tag_dictionary: dict[str, str] = field(default_factory=dict)
    log_files: tuple[Path, ...] = ()

    def safe_diagnostics(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for sources in self.sources_by_rec_id.values():
            for source in sources:
                counts[source] = counts.get(source, 0) + 1
        return {
            "automatic_conversation_count": len(self.sources_by_rec_id),
            "source_counts": counts,
            "tag_dictionary_size": len(self.tag_dictionary),
            "log_file_count": len(self.log_files),
            "auth": self.auth.safe_diagnostics(),
        }


@dataclass(frozen=True)
class KstCacheCandidate:
    rec_id: str
    start_time: str
    promotion_id: str
    visitor_messages: int
    tag_ids: str = ""
    keyword: str = ""
    bid_word: str = ""


@dataclass(frozen=True)
class KstConversation:
    rec_id: str
    start_time: str
    promotion_id: str
    visitor_messages: int
    tags: tuple[str, ...]
    sources: frozenset[str]
    keyword: str = ""
    bid_word: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return {
            "rec_id": self.rec_id,
            "start_time": self.start_time,
            "promotion_id": self.promotion_id,
            "visitor_messages": self.visitor_messages,
            "tags": list(self.tags),
            "sources": sorted(self.sources),
            "keyword": self.keyword,
            "bid_word": self.bid_word,
        }
