from __future__ import annotations

import threading
from datetime import date
from typing import Any

from modules.kst_daily_aggregation import aggregate_kst_daily_rows
from modules.kst_local.legacy_db_reader import (
    KstLegacyDatabaseError,
    read_legacy_conversations,
)
from modules.kst_local.models import KstConversation, LegacyKstInstallation
from modules.kst_local.service import KstServiceError
from modules.kst_parser import aggregate_kst_export_rows


class LegacyKstConversationService:
    def __init__(
        self,
        config: dict[str, Any],
        installation: LegacyKstInstallation,
        cancel_event: Any = None,
    ) -> None:
        self._config = config
        self._installation = installation
        self._cancel_event = cancel_event
        self._cache_lock = threading.RLock()
        self._cache: dict[str, tuple[KstConversation, ...]] = {}

    def _check_cancelled(self) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise KstServiceError("老版快商通会话读取已取消")

    def collect(self, target_date: str) -> list[KstConversation]:
        with self._cache_lock:
            self._check_cancelled()
            cached = self._cache.get(target_date)
            if cached is not None:
                return list(cached)
            promotion_map = (
                (self._config.get("kst") or {}).get(
                    "promotion_id_accounts"
                )
                or {}
            )
            if not isinstance(promotion_map, dict) or not promotion_map:
                raise KstServiceError("项目缺少商务通推广 ID 映射")
            try:
                conversations = read_legacy_conversations(
                    self._installation,
                    target_date,
                    cancel_event=self._cancel_event,
                )
            except KstLegacyDatabaseError as exc:
                raise KstServiceError(str(exc)) from None
            for conversation in conversations:
                self._check_cancelled()
                if conversation.promotion_id not in promotion_map:
                    raise KstServiceError(
                        "自动来源会话查询失败："
                        f"recId={conversation.rec_id}"
                    )
            self._cache[target_date] = tuple(conversations)
            return list(conversations)

    def _row(self, conversation: KstConversation) -> dict[str, Any]:
        promotion_map = self._config["kst"]["promotion_id_accounts"]
        return {
            "账户": promotion_map[conversation.promotion_id],
            "开始对话时间": conversation.start_time,
            "备注说明": f"推广ID：{conversation.promotion_id}",
            "访客消息数": conversation.visitor_messages,
            "名片标签": "、".join(conversation.tags),
            "搜索关键词": conversation.keyword,
            "竞价词": conversation.bid_word,
        }

    @staticmethod
    def _source_counts(
        conversations: list[KstConversation],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for conversation in conversations:
            for source in conversation.sources:
                counts[source] = counts.get(source, 0) + 1
        return counts

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
        summary["automatic_source_counts"] = self._source_counts(
            conversations
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
        summary["automatic_source_counts"] = self._source_counts(
            conversations
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
