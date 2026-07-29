from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from modules.kst_local.models import KstConversation, LegacyKstInstallation


class KstLegacyDatabaseError(RuntimeError):
    """老版快商通数据库无法被完整、安全地读取。"""


_PROMOTION_PATTERN = re.compile(
    r"推广\s*ID\s*[:：]?\s*(\d{5,})(?!\w)",
    re.I,
)
_TAG_SEPARATOR_PATTERN = re.compile(r"[、,，;；|\r\n]+")
_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]")
_INTEGER_PATTERN = re.compile(r"-?\d+")
_HISTORY_CHUNK_SIZE = 500
_HISTORY_CAPABILITY_QUERY = """
    SELECT recId, curEnterTime, diaStartTime, visitorSendNum,
           visitorCustomField, keyword, bidWord, talkGrade,
           dialogClassification, classifyTag, cusTypeTag, aiTags
    FROM OC_HDVISITORINFO
    LIMIT 0
"""
_MESSAGE_CAPABILITY_QUERY = """
    SELECT recId, addTime
    FROM DIALOGRECORD_VISITOR
    LIMIT 0
"""


def normalize_legacy_tags(*values: Any) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()

    def append(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for nested in value.values():
                append(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                append(nested)
            return
        text = str(value).strip()
        if not text:
            return
        if text[:1] in '[{"':
            try:
                decoded = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                pass
            else:
                append(decoded)
                return
        for label in _TAG_SEPARATOR_PATTERN.split(text):
            normalized = label.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                tags.append(normalized)

    for value in values:
        append(value)
    return tuple(tags)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=0.5,
    )
    connection.execute("PRAGMA busy_timeout=500")
    return connection


def _check_interrupted(cancel_event: Any, deadline: float) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KstLegacyDatabaseError("老版快商通数据库读取已取消")
    if time.monotonic() >= deadline:
        raise KstLegacyDatabaseError("老版快商通数据库读取超时")


def _validate_target_date(value: str) -> str:
    parsed: date | None = None
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        pass
    if parsed is not None:
        normalized = parsed.isoformat()
        if normalized == value:
            return normalized
    raise KstLegacyDatabaseError("老版快商通目标日期无效") from None


def _validate_start_time(cur_enter_time: Any, dialog_start_time: Any) -> str:
    value = str(cur_enter_time or dialog_start_time or "").strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        pass
    if parsed is not None and _DATETIME_PATTERN.match(value) is not None:
        return value
    raise KstLegacyDatabaseError("老版快商通会话时间无效") from None


def _validate_visitor_messages(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise KstLegacyDatabaseError("老版快商通访客消息数无效")
    text = str(value).strip()
    if _INTEGER_PATTERN.fullmatch(text) is None:
        raise KstLegacyDatabaseError("老版快商通访客消息数无效")
    messages: int | None = None
    try:
        messages = int(text)
    except ValueError:
        pass
    if messages is not None and messages >= 0:
        return messages
    raise KstLegacyDatabaseError("老版快商通访客消息数无效") from None


def _install_progress_handler(
    connection: sqlite3.Connection,
    cancel_event: Any,
    deadline: float,
) -> None:
    def interrupted() -> int:
        return int(
            (cancel_event is not None and cancel_event.is_set())
            or time.monotonic() >= deadline
        )

    connection.set_progress_handler(interrupted, 1000)


def _validate_database_capability(
    path: Path,
    query: str,
    cancel_event: Any,
    deadline: float,
) -> None:
    _check_interrupted(cancel_event, deadline)
    database_error = False
    try:
        with closing(_connect_read_only(path)) as connection:
            _install_progress_handler(connection, cancel_event, deadline)
            connection.execute(query).fetchall()
            _check_interrupted(cancel_event, deadline)
    except KstLegacyDatabaseError:
        raise
    except (OSError, sqlite3.Error, ValueError):
        database_error = True
    if database_error:
        _check_interrupted(cancel_event, deadline)
        raise KstLegacyDatabaseError(
            "老版快商通数据库读取能力不可用"
        ) from None


def validate_legacy_read_capability(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline_seconds: float = 5.0,
) -> None:
    deadline = time.monotonic() + deadline_seconds
    if not installation.message_database_paths:
        raise KstLegacyDatabaseError(
            "老版快商通数据库读取能力不可用"
        )
    _validate_database_capability(
        installation.history_db,
        _HISTORY_CAPABILITY_QUERY,
        cancel_event,
        deadline,
    )
    for database_path in installation.message_database_paths:
        _validate_database_capability(
            database_path,
            _MESSAGE_CAPABILITY_QUERY,
            cancel_event,
            deadline,
        )
    _check_interrupted(cancel_event, deadline)


def _read_authorized_rec_ids(
    installation: LegacyKstInstallation,
    target_date: str,
    cancel_event: Any,
    deadline: float,
) -> set[str]:
    rec_ids: set[str] = set()
    for database_path in installation.message_database_paths:
        _check_interrupted(cancel_event, deadline)
        database_error = False
        try:
            with closing(_connect_read_only(database_path)) as connection:
                _install_progress_handler(connection, cancel_event, deadline)
                rows = connection.execute(
                    """
                    SELECT DISTINCT recId
                    FROM DIALOGRECORD_VISITOR
                    WHERE date(addTime) = ?
                    """,
                    (target_date,),
                )
                rec_ids.update(
                    str(row[0]).strip()
                    for row in rows
                    if row[0] is not None and str(row[0]).strip()
                )
                _check_interrupted(cancel_event, deadline)
        except KstLegacyDatabaseError:
            raise
        except (OSError, sqlite3.Error, ValueError):
            database_error = True
        if database_error:
            _check_interrupted(cancel_event, deadline)
            raise KstLegacyDatabaseError(
                "老版快商通实时会话数据库读取失败"
            ) from None
    return rec_ids


def _read_all_authorized_rec_ids(
    installation: LegacyKstInstallation,
    cancel_event: Any,
    deadline: float,
) -> set[str]:
    rec_ids: set[str] = set()
    for database_path in installation.message_database_paths:
        _check_interrupted(cancel_event, deadline)
        database_error = False
        try:
            with closing(_connect_read_only(database_path)) as connection:
                _install_progress_handler(connection, cancel_event, deadline)
                rows = connection.execute(
                    """
                    SELECT DISTINCT recId
                    FROM DIALOGRECORD_VISITOR
                    """
                )
                rec_ids.update(
                    str(row[0]).strip()
                    for row in rows
                    if row[0] is not None and str(row[0]).strip()
                )
                _check_interrupted(cancel_event, deadline)
        except KstLegacyDatabaseError:
            raise
        except (OSError, sqlite3.Error, ValueError):
            database_error = True
        if database_error:
            _check_interrupted(cancel_event, deadline)
            raise KstLegacyDatabaseError(
                "老版快商通实时会话数据库读取失败"
            ) from None
    return rec_ids


def _read_history_rows(
    installation: LegacyKstInstallation,
    rec_ids: set[str],
    cancel_event: Any,
    deadline: float,
) -> dict[str, tuple[Any, ...]]:
    rows_by_rec_id: dict[str, tuple[Any, ...]] = {}
    ordered_rec_ids = sorted(rec_ids)
    database_error = False
    try:
        with closing(_connect_read_only(installation.history_db)) as connection:
            _install_progress_handler(connection, cancel_event, deadline)
            for offset in range(0, len(ordered_rec_ids), _HISTORY_CHUNK_SIZE):
                _check_interrupted(cancel_event, deadline)
                chunk = ordered_rec_ids[offset : offset + _HISTORY_CHUNK_SIZE]
                markers = ", ".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT recId, curEnterTime, diaStartTime, visitorSendNum,
                           visitorCustomField, keyword, bidWord,
                           talkGrade, dialogClassification, classifyTag,
                           cusTypeTag, aiTags
                    FROM OC_HDVISITORINFO
                    WHERE recId IN ({markers})
                    """,
                    chunk,
                )
                for row in rows:
                    rec_id = str(row[0] or "").strip()
                    if rec_id:
                        if rec_id in rows_by_rec_id:
                            raise KstLegacyDatabaseError(
                                "老版快商通历史会话记录重复"
                            )
                        rows_by_rec_id[rec_id] = tuple(row)
                _check_interrupted(cancel_event, deadline)
    except KstLegacyDatabaseError:
        raise
    except (OSError, sqlite3.Error, ValueError):
        database_error = True
    if database_error:
        _check_interrupted(cancel_event, deadline)
        raise KstLegacyDatabaseError(
            "老版快商通历史会话数据库读取失败"
        ) from None
    return rows_by_rec_id


def read_legacy_promotion_ids(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline_seconds: float = 5.0,
) -> set[str]:
    deadline = time.monotonic() + deadline_seconds
    authorized = _read_all_authorized_rec_ids(
        installation,
        cancel_event,
        deadline,
    )
    if not authorized:
        _check_interrupted(cancel_event, deadline)
        return set()
    history_rows = _read_history_rows(
        installation,
        authorized,
        cancel_event,
        deadline,
    )
    if set(history_rows) != authorized:
        raise KstLegacyDatabaseError("老版快商通会话尚未同步完整")
    promotion_ids: set[str] = set()
    _check_interrupted(cancel_event, deadline)
    for row in history_rows.values():
        _check_interrupted(cancel_event, deadline)
        match = _PROMOTION_PATTERN.search(str(row[4] or ""))
        if match is not None:
            promotion_ids.add(match.group(1))
        _check_interrupted(cancel_event, deadline)
    _check_interrupted(cancel_event, deadline)
    return promotion_ids


def read_legacy_conversations(
    installation: LegacyKstInstallation,
    target_date: str,
    *,
    cancel_event: Any = None,
    deadline_seconds: float = 5.0,
) -> list[KstConversation]:
    target_date = _validate_target_date(target_date)
    deadline = time.monotonic() + deadline_seconds
    authorized = _read_authorized_rec_ids(
        installation,
        target_date,
        cancel_event,
        deadline,
    )
    if not authorized:
        _check_interrupted(cancel_event, deadline)
        return []
    history_rows = _read_history_rows(
        installation,
        authorized,
        cancel_event,
        deadline,
    )
    if set(history_rows) != authorized:
        raise KstLegacyDatabaseError("老版快商通会话尚未同步完整")

    conversations: list[KstConversation] = []
    _check_interrupted(cancel_event, deadline)
    for rec_id in sorted(authorized):
        _check_interrupted(cancel_event, deadline)
        row = history_rows[rec_id]
        visitor_messages = _validate_visitor_messages(row[3])
        if visitor_messages == 0:
            _check_interrupted(cancel_event, deadline)
            continue
        start_time = _validate_start_time(row[1], row[2])
        promotion_match = _PROMOTION_PATTERN.search(str(row[4] or ""))
        if promotion_match is None:
            raise KstLegacyDatabaseError("老版快商通推广 ID 无效")
        promotion_id = promotion_match.group(1)
        tags = normalize_legacy_tags(*row[7:12])
        conversations.append(
            KstConversation(
                rec_id=rec_id,
                start_time=start_time,
                promotion_id=promotion_id,
                visitor_messages=visitor_messages,
                tags=tags,
                sources=frozenset({"server_push"}),
                keyword=str(row[5] or ""),
                bid_word=str(row[6] or ""),
            )
        )
        _check_interrupted(cancel_event, deadline)
    conversations.sort(key=lambda item: (item.start_time, item.rec_id))
    _check_interrupted(cancel_event, deadline)
    return conversations
