from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from modules.kst_local.api_client import KstApiClient
from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstCacheCandidate,
    KstConversation,
)
from modules.kst_daily_aggregation import aggregate_kst_daily_rows
from modules.kst_parser import aggregate_kst_export_rows


class KstServiceError(RuntimeError):
    """自动来源会话无法被完整、安全地重建。"""


PROMOTION_PATTERN = re.compile(r"推广\s*ID\s*[:：]?\s*(\d{5,})", re.I)


def _promotion_id(value: Any) -> str:
    match = PROMOTION_PATTERN.search(str(value or ""))
    return match.group(1) if match else ""


def _tag_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        return [str(item) for item in value if str(item) != "-1"]
    if isinstance(value, list):
        return [str(item) for item in value if str(item) != "-1"]
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return [item for item in re.findall(r"\d+", str(value)) if item != "-1"]
    return _tag_ids(decoded)


class KstConversationService:
    def __init__(
        self,
        *,
        config: dict[str, Any],
        snapshot: AutomaticSourceSnapshot,
        candidates: list[KstCacheCandidate],
        client: KstApiClient,
        max_workers: int = 4,
    ) -> None:
        self._config = config
        self._snapshot = snapshot
        self._candidates = candidates
        self._client = client
        self._max_workers = max(1, min(4, int(max_workers)))
        self._cache_lock = threading.RLock()
        self._cache: dict[str, tuple[KstConversation, ...]] = {}

    def collect(self, target_date: str) -> list[KstConversation]:
        with self._cache_lock:
            return self._collect_unlocked(target_date)

    def _collect_unlocked(
        self,
        target_date: str,
    ) -> list[KstConversation]:
        if target_date in self._cache:
            return list(self._cache[target_date])
        tag_map = (
            dict(self._snapshot.tag_dictionary)
            if self._snapshot.tag_dictionary
            else self._client.load_tag_dictionary()
        )
        allowed = self._snapshot.sources_by_rec_id
        selected = [row for row in self._candidates if row.rec_id in allowed]
        promotion_map = (
            (self._config.get("kst") or {}).get("promotion_id_accounts") or {}
        )
        if not isinstance(promotion_map, dict) or not promotion_map:
            raise KstServiceError("项目缺少商务通推广 ID 映射")
        if not selected:
            self._cache[target_date] = ()
            return []
        worker_count = min(self._max_workers, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            conversations = list(
                executor.map(
                    lambda candidate: self._load_conversation(
                        candidate,
                        tag_map,
                        allowed,
                        promotion_map,
                    ),
                    selected,
                )
            )
        conversations.sort(key=lambda item: (item.start_time, item.rec_id))
        self._cache[target_date] = tuple(conversations)
        return conversations

    def _load_conversation(
        self,
        candidate: KstCacheCandidate,
        tag_map: dict[str, str],
        allowed: dict[str, frozenset[str]],
        promotion_map: dict[str, str],
    ) -> KstConversation:
        endpoint_names = set(self._snapshot.auth.endpoints)
        database_fallback = (
            not {
                "visitor_info",
                "visitor_card",
            }.issubset(endpoint_names)
            and "tag_dictionary" in endpoint_names
        )
        if database_fallback:
            return self._from_candidate(
                candidate,
                tag_map,
                allowed,
                promotion_map,
            )
        try:
            visitor = self._client.load_visitor(candidate.rec_id)
            visitor_id = str(visitor.get("visitorId") or "")
            if not visitor_id:
                raise ValueError("visitorId missing")
            card = self._client.load_card(visitor_id)
            start_time = str(
                visitor.get("curEnterTime")
                or visitor.get("dialogOpenTime")
                or ""
            )
            if not start_time:
                raise ValueError("curEnterTime missing")
            visitor_messages = int(
                visitor.get(
                    "visitorSendNum",
                    visitor.get("vsSendNum", 0),
                )
                or 0
            )
            promotion_id = (
                _promotion_id(visitor.get("visitorCustomField"))
                or candidate.promotion_id
            )
            if not promotion_id:
                raise ValueError("promotion id missing")
            if promotion_id not in promotion_map:
                raise ValueError("promotion id outside project mapping")
            tags = tuple(
                tag_map.get(tag_id, tag_id)
                for tag_id in _tag_ids(card.get("cusTypeTag"))
            )
            return KstConversation(
                rec_id=candidate.rec_id,
                start_time=start_time,
                promotion_id=promotion_id,
                visitor_messages=visitor_messages,
                tags=tags,
                sources=allowed[candidate.rec_id],
                keyword=candidate.keyword,
                bid_word=candidate.bid_word,
            )
        except Exception:
            # 云端富化失败（如访客记录尚未落到云端访客库）时，回退到本地
            # 候选数据，避免单条会话把整份小时报/日报拖垮。
            return self._from_candidate(
                candidate,
                tag_map,
                allowed,
                promotion_map,
            )

    def _from_candidate(
        self,
        candidate: KstCacheCandidate,
        tag_map: dict[str, str],
        allowed: dict[str, frozenset[str]],
        promotion_map: dict[str, str],
    ) -> KstConversation:
        start_time = candidate.start_time
        visitor_messages = int(candidate.visitor_messages or 0)
        promotion_id = candidate.promotion_id
        if not start_time:
            raise KstServiceError(
                f"自动来源会话查询失败：recId={candidate.rec_id}（缺少开始时间）"
            )
        if not promotion_id:
            raise KstServiceError(
                f"自动来源会话查询失败：recId={candidate.rec_id}（缺少推广ID）"
            )
        if promotion_id not in promotion_map:
            raise KstServiceError(
                f"自动来源会话查询失败：recId={candidate.rec_id}（推广ID不在项目映射）"
            )
        tags = tuple(
            tag_map.get(tag_id, tag_id)
            for tag_id in _tag_ids(candidate.tag_ids)
        )
        return KstConversation(
            rec_id=candidate.rec_id,
            start_time=start_time,
            promotion_id=promotion_id,
            visitor_messages=visitor_messages,
            tags=tags,
            sources=allowed[candidate.rec_id],
            keyword=candidate.keyword,
            bid_word=candidate.bid_word,
        )

    @staticmethod
    def _row(conversation: KstConversation) -> dict[str, Any]:
        return {
            "开始对话时间": conversation.start_time,
            "备注说明": f"推广ID：{conversation.promotion_id}",
            "访客消息数": conversation.visitor_messages,
            "名片标签": "、".join(conversation.tags),
            "搜索关键词": conversation.keyword,
            "竞价词": conversation.bid_word,
        }

    def build_hourly_report(
        self,
        target_date: str | None,
        period: str | None,
    ) -> dict[str, Any]:
        resolved_date = target_date or date.today().isoformat()
        conversations = self.collect(resolved_date)
        aggregate = aggregate_kst_export_rows(
            [self._row(item) for item in conversations],
            self._config,
        )
        summary = dict(aggregate["summary"])
        summary["automatic_rows"] = len(conversations)
        summary["automatic_source_counts"] = (
            self._snapshot.safe_diagnostics()["source_counts"]
        )
        return {
            "project_id": self._config.get("project_id"),
            "project_name": self._config.get("project_name"),
            "date": resolved_date,
            "period": period or "15点",
            "source": "kst_local_api",
            "accounts": aggregate["accounts"],
            "summary": summary,
            "errors": aggregate.get("errors", []),
        }

    def build_daily_report(
        self,
        target_date: str | None,
    ) -> dict[str, Any]:
        resolved_date = target_date or date.today().isoformat()
        conversations = self.collect(resolved_date)
        aggregate = aggregate_kst_daily_rows(
            [self._row(item) for item in conversations],
            self._config,
        )
        summary = dict(aggregate["summary"])
        summary["automatic_rows"] = len(conversations)
        summary["automatic_source_counts"] = (
            self._snapshot.safe_diagnostics()["source_counts"]
        )
        return {
            "project_id": self._config.get("project_id"),
            "project_name": self._config.get("project_name"),
            "date": resolved_date,
            "source": "kst_local_api",
            "accounts": aggregate["accounts"],
            "summary": summary,
            "errors": aggregate.get("errors", []),
        }
