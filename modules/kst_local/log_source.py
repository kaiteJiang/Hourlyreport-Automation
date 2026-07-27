from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.kst_local.models import AutomaticSourceSnapshot, KstAuthContext


PUSH_BATCH_PATTERN = re.compile(
    r'"msgType"\s*:\s*48.*?"msgContent"\s*:\s*\[([^\]]*)\]'
)
PUSH_REC_ID_PATTERN = re.compile(r'(?<!\d)\d+(?!\d)')
SYNC_REC_ID_PATTERN = re.compile(r'\\?"recId\\?"\s*:\s*\\?"?(\d+)')
TAG_PAIR_PATTERN = re.compile(
    r'"typeid"\s*:\s*"?(\d+)"?.*?"typename"\s*:\s*"([^"]+)"'
)
ENDPOINT_SUFFIXES = {
    "visitor_info": "OnlineHd/visitorInfo/load",
    "dialog_records": "OnlineHd/dialogRecord/recordsByRecIdNew",
    "visitor_card": "OnlineCore/nv2012/func/ocVisitorCard/detail.do",
    "tag_dictionary": "OnlineCore/nv/visitorCard/custTypeQuery.do",
    "startup_sync": "OnlineCore/onlinecs/ocHistory/v2/getLastVisitorList.do",
}


class _SnapshotAccumulator:
    def __init__(self, target_date: str, auth_date: str | None) -> None:
        self.target_date = target_date
        self.auth_date = auth_date
        self.sources: dict[str, set[str]] = {}
        self.common_query: dict[str, Any] = {}
        self.headers: dict[str, str] = {}
        self.endpoints: dict[str, str] = {}
        self.tag_dictionary: dict[str, str] = {}

    def consume(self, line: str) -> None:
        source_line = line.startswith(f"[{self.target_date} ")
        auth_line = (
            self.auth_date is None
            or line.startswith(f"[{self.auth_date} ")
        )
        if source_line and '"msgType":48' in line:
            match = PUSH_BATCH_PATTERN.search(line)
            if match:
                for rec_id in PUSH_REC_ID_PATTERN.findall(match.group(1)):
                    self.sources.setdefault(rec_id, set()).add(
                        "websocket_msg_type_48"
                    )

        if ENDPOINT_SUFFIXES["startup_sync"] in line:
            for match in SYNC_REC_ID_PATTERN.finditer(line):
                self.sources.setdefault(match.group(1), set()).add(
                    "startup_auto_sync"
                )

        parsed_common = (
            _decode_json_after(line, "[Api] GlobalCommonParams")
            if auth_line
            else None
        )
        if parsed_common is not None:
            candidate = parsed_common.get("query")
            self.common_query = (
                dict(candidate) if isinstance(candidate, dict) else {}
            )

        parsed_headers = (
            _decode_json_after(line, "[Api] GlobalCommonHeaders")
            if auth_line
            else None
        )
        if parsed_headers is not None:
            self.headers = {
                str(key): str(value)
                for key, value in parsed_headers.items()
                if value is not None
            }

        if auth_line:
            for name, suffix in ENDPOINT_SUFFIXES.items():
                endpoint = _find_endpoint(line, suffix)
                if endpoint:
                    self.endpoints[name] = endpoint

        if auth_line and ENDPOINT_SUFFIXES["tag_dictionary"] in line:
            unescaped = line.replace(r"\"", '"')
            for match in TAG_PAIR_PATTERN.finditer(unescaped):
                self.tag_dictionary[match.group(1)] = match.group(2)

    def snapshot(self, paths: tuple[Path, ...]) -> AutomaticSourceSnapshot:
        return AutomaticSourceSnapshot(
            sources_by_rec_id={
                rec_id: frozenset(source_names)
                for rec_id, source_names in self.sources.items()
            },
            auth=KstAuthContext(
                common_query=dict(self.common_query),
                headers=dict(self.headers),
                endpoints=dict(self.endpoints),
            ),
            tag_dictionary=dict(self.tag_dictionary),
            log_files=paths,
        )


@dataclass
class _FileCursor:
    offset: int = 0
    pending: bytes = b""


@dataclass
class _CacheEntry:
    accumulator: _SnapshotAccumulator
    cursors: dict[Path, _FileCursor] = field(default_factory=dict)


class IncrementalLogSnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[
            tuple[Path, str, str | None],
            _CacheEntry,
        ] = {}
        self._bytes_read = 0
        self._full_rebuilds = 0

    @staticmethod
    def _cache_root(log_dir: Path) -> Path:
        return log_dir.resolve()

    @staticmethod
    def _requires_rebuild(
        entry: _CacheEntry,
        paths: tuple[Path, ...],
    ) -> bool:
        if set(entry.cursors) != set(paths):
            return True
        for path in paths:
            try:
                if path.stat().st_size < entry.cursors[path].offset:
                    return True
            except OSError:
                return True
        return False

    def _consume_file(
        self,
        entry: _CacheEntry,
        path: Path,
    ) -> None:
        cursor = entry.cursors.setdefault(path, _FileCursor())
        with path.open("rb") as stream:
            stream.seek(cursor.offset)
            appended = stream.read()
        if not appended:
            return
        self._bytes_read += len(appended)
        cursor.offset += len(appended)
        combined = cursor.pending + appended
        cursor.pending = b""
        for raw_line in combined.splitlines(keepends=True):
            if raw_line.endswith((b"\n", b"\r")):
                entry.accumulator.consume(
                    raw_line.rstrip(b"\r\n").decode(
                        "utf-8",
                        errors="replace",
                    )
                )
            else:
                cursor.pending = raw_line

    def _new_entry(
        self,
        paths: tuple[Path, ...],
        target_date: str,
        auth_date: str | None,
    ) -> _CacheEntry:
        entry = _CacheEntry(
            accumulator=_SnapshotAccumulator(target_date, auth_date),
            cursors={path: _FileCursor() for path in paths},
        )
        for path in paths:
            self._consume_file(entry, path)
        self._full_rebuilds += 1
        return entry

    def parse(
        self,
        log_dir: str | Path,
        target_date: str,
        *,
        auth_date: str | None = None,
    ) -> AutomaticSourceSnapshot:
        directory = Path(log_dir)
        key = (self._cache_root(directory), target_date, auth_date)
        with self._lock:
            paths = _log_files(directory)
            entry = self._entries.get(key)
            if entry is None or self._requires_rebuild(entry, paths):
                entry = self._new_entry(paths, target_date, auth_date)
                self._entries[key] = entry
            else:
                for path in paths:
                    self._consume_file(entry, path)
            return entry.accumulator.snapshot(paths)

    def diagnostics(self) -> dict[str, int]:
        with self._lock:
            return {
                "bytes_read": self._bytes_read,
                "full_rebuilds": self._full_rebuilds,
                "entry_count": len(self._entries),
            }


_DEFAULT_LOG_SNAPSHOT_CACHE = IncrementalLogSnapshotCache()


def _log_files(log_dir: Path) -> tuple[Path, ...]:
    if log_dir.is_file():
        return (log_dir.resolve(),)
    return tuple(
        sorted(
            (path.resolve() for path in log_dir.glob("app*.log") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
    )


def _decode_json_after(line: str, marker: str) -> dict[str, Any] | None:
    if marker not in line:
        return None
    raw = line.split(marker, 1)[1].strip()
    try:
        value, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _find_endpoint(line: str, suffix: str) -> str | None:
    index = line.find(suffix)
    if index < 0:
        return None
    start = max(line.rfind("https://", 0, index), line.rfind("http://", 0, index))
    if start < 0:
        return None
    end = index + len(suffix)
    return line[start:end]


def parse_log_snapshot(
    log_dir: str | Path,
    target_date: str,
    *,
    auth_date: str | None = None,
) -> AutomaticSourceSnapshot:
    paths = _log_files(Path(log_dir))
    accumulator = _SnapshotAccumulator(target_date, auth_date)

    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            accumulator.consume(line)
    return accumulator.snapshot(paths)


def parse_cached_log_snapshot(
    log_dir: str | Path,
    target_date: str,
    *,
    auth_date: str | None = None,
) -> AutomaticSourceSnapshot:
    return _DEFAULT_LOG_SNAPSHOT_CACHE.parse(
        log_dir,
        target_date,
        auth_date=auth_date,
    )
