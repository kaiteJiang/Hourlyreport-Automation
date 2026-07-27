from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from modules.kst_local.models import KstCacheCandidate, KstInstallation


class KstDatabaseError(RuntimeError):
    """只读数据库桥无法返回兼容数据。"""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _score(row: KstCacheCandidate) -> int:
    return sum(
        bool(value)
        for value in (
            row.promotion_id,
            row.tag_ids,
            row.keyword,
            row.bid_word,
            row.start_time,
        )
    )


def _merge(old: KstCacheCandidate, new: KstCacheCandidate) -> KstCacheCandidate:
    primary, secondary = (new, old) if _score(new) > _score(old) else (old, new)
    return KstCacheCandidate(
        rec_id=primary.rec_id,
        start_time=primary.start_time or secondary.start_time,
        promotion_id=primary.promotion_id or secondary.promotion_id,
        visitor_messages=max(primary.visitor_messages, secondary.visitor_messages),
        tag_ids=primary.tag_ids or secondary.tag_ids,
        keyword=primary.keyword or secondary.keyword,
        bid_word=primary.bid_word or secondary.bid_word,
    )


def _as_candidate(value: dict[str, Any]) -> KstCacheCandidate | None:
    if str(value.get("visitorType") or "").upper() != "WEB":
        return None
    try:
        if int(value.get("channelType") or 0) != 1:
            return None
        visitor_messages = int(value.get("visitorMessages") or 0)
    except (TypeError, ValueError):
        return None
    rec_id = str(value.get("recId") or "").strip()
    if not rec_id:
        return None
    return KstCacheCandidate(
        rec_id=rec_id,
        start_time=str(value.get("startTime") or ""),
        promotion_id=str(value.get("promotionId") or ""),
        visitor_messages=visitor_messages,
        tag_ids=str(value.get("tagIds") or ""),
        keyword=str(value.get("keyword") or ""),
        bid_word=str(value.get("bidWord") or ""),
    )


def read_cache_candidates(
    installation: KstInstallation,
    target_date: str,
    *,
    runner: Runner = subprocess.run,
    bridge_script: str | Path | None = None,
) -> list[KstCacheCandidate]:
    script = (
        Path(bridge_script)
        if bridge_script is not None
        else Path(__file__).parent / "resources" / "read_visitor_db.js"
    ).resolve()
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    merged: dict[str, KstCacheCandidate] = {}

    for database_path in installation.database_paths:
        command = [
            str(installation.electron),
            str(script),
            installation.identity,
            str(database_path),
            target_date,
            str(installation.sqlite_module_dir),
        ]
        try:
            completed = runner(
                command,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KstDatabaseError(
                f"商务通只读数据库桥执行失败：{database_path.name}"
            ) from exc
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise KstDatabaseError(
                f"商务通只读数据库桥未返回有效 JSON：{database_path.name}"
            ) from exc
        rows = payload.get("safeRows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise KstDatabaseError(
                f"商务通只读数据库桥响应缺少 safeRows：{database_path.name}"
            )
        for raw in rows:
            candidate = _as_candidate(raw) if isinstance(raw, dict) else None
            if candidate is None:
                continue
            old = merged.get(candidate.rec_id)
            merged[candidate.rec_id] = (
                _merge(old, candidate) if old is not None else candidate
            )

    return sorted(merged.values(), key=lambda row: (row.start_time, row.rec_id))
