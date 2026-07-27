from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modules.kst_local.models import AutomaticSourceSnapshot, KstAuthContext


PUSH_PATTERN = re.compile(
    r'"msgType"\s*:\s*48.*?"msgContent"\s*:\s*\[?\s*"?(\d+)'
)
SYNC_REC_ID_PATTERN = re.compile(r'\\?"recId\\?"\s*:\s*\\?"?(\d+)')
ENDPOINT_SUFFIXES = {
    "visitor_info": "OnlineHd/visitorInfo/load",
    "dialog_records": "OnlineHd/dialogRecord/recordsByRecIdNew",
    "visitor_card": "OnlineCore/nv2012/func/ocVisitorCard/detail.do",
    "tag_dictionary": "OnlineCore/nv/visitorCard/custTypeQuery.do",
    "startup_sync": "OnlineCore/onlinecs/ocHistory/v2/getLastVisitorList.do",
}


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


def parse_log_snapshot(log_dir: str | Path, target_date: str) -> AutomaticSourceSnapshot:
    paths = _log_files(Path(log_dir))
    sources: dict[str, set[str]] = {}
    common_query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    endpoints: dict[str, str] = {}

    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"[{target_date} ") and '"msgType":48' in line:
                match = PUSH_PATTERN.search(line)
                if match:
                    sources.setdefault(match.group(1), set()).add(
                        "websocket_msg_type_48"
                    )

            if ENDPOINT_SUFFIXES["startup_sync"] in line:
                for match in SYNC_REC_ID_PATTERN.finditer(line):
                    sources.setdefault(match.group(1), set()).add(
                        "startup_auto_sync"
                    )

            parsed_common = _decode_json_after(line, "[Api] GlobalCommonParams")
            if parsed_common is not None:
                candidate = parsed_common.get("query")
                common_query = dict(candidate) if isinstance(candidate, dict) else {}

            parsed_headers = _decode_json_after(line, "[Api] GlobalCommonHeaders")
            if parsed_headers is not None:
                headers = {
                    str(key): str(value)
                    for key, value in parsed_headers.items()
                    if value is not None
                }

            for name, suffix in ENDPOINT_SUFFIXES.items():
                endpoint = _find_endpoint(line, suffix)
                if endpoint:
                    endpoints[name] = endpoint

    return AutomaticSourceSnapshot(
        sources_by_rec_id={
            rec_id: frozenset(source_names)
            for rec_id, source_names in sources.items()
        },
        auth=KstAuthContext(
            common_query=common_query,
            headers=headers,
            endpoints=endpoints,
        ),
        log_files=paths,
    )
