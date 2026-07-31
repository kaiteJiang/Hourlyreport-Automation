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

    def __init__(
        self,
        message: str,
        *,
        category: str = "database_incompatible",
    ) -> None:
        super().__init__(message)
        self.category = category


_PROMOTION_PATTERN = re.compile(
    r"推广\s*ID\s*[:：]?\s*(\d{5,})(?!\w)",
    re.I,
)
_TAG_SEPARATOR_PATTERN = re.compile(r"[、,，;；|\r\n]+")
_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]")
_INTEGER_PATTERN = re.compile(r"-?\d+")
_MONTHLY_HISTORY_PATTERN = re.compile(r"^\d{4}-\d{2}_HIS\.cdb$", re.I)
_DIRECT_SOURCE_TYPE = "7"
_HISTORY_CHUNK_SIZE = 500
_HISTORY_FIELDS = (
    "recId",
    "curEnterTime",
    "diaStartTime",
    "visitorSendNum",
    "visitorCustomField",
    "keyword",
    "bidWord",
    "talkGrade",
    "dialogClassification",
    "classifyTag",
    "cusTypeTag",
    "aiTags",
    "info",
    "sourceType",
)
_HISTORY_REQUIRED_FIELDS = (
    "recId",
    "visitorSendNum",
    "visitorCustomField",
)
_HISTORY_CAPABILITY_QUERY = """
    SELECT recId, curEnterTime, diaStartTime, visitorSendNum,
           visitorCustomField, keyword, bidWord, talkGrade,
           dialogClassification, classifyTag, cusTypeTag, aiTags
    FROM OC_HDVISITORINFO
    LIMIT 0
"""
_MESSAGE_CAPABILITY_QUERY = """
    SELECT recId, addTime, recType
    FROM DIALOGRECORD_VISITOR
    LIMIT 0
"""


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    rows = connection.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()
    return {
        str(row[1]).casefold()
        for row in rows
        if len(row) > 1 and str(row[1] or "").strip()
    }


def legacy_history_database_paths(
    installation: LegacyKstInstallation,
    target_date: str | None = None,
) -> tuple[Path, ...]:
    primary = installation.history_db.resolve()
    archive_dir = primary.parent / "his"
    archives: list[Path] = []
    try:
        if archive_dir.is_dir():
            archives = [
                path.resolve()
                for path in archive_dir.iterdir()
                if (
                    path.is_file()
                    and _MONTHLY_HISTORY_PATTERN.fullmatch(path.name)
                    and (
                        target_date is None
                        or path.name.casefold()
                        == f"{target_date[:7]}_HIS.cdb".casefold()
                    )
                )
            ]
    except OSError:
        raise KstLegacyDatabaseError(
            "老版快商通历史库目录无法扫描"
        ) from None
    return tuple(
        dict.fromkeys(
            (
                *sorted(archives, key=lambda path: str(path).casefold()),
                primary,
            )
        )
    )


def _history_select_list(connection: sqlite3.Connection) -> str:
    columns = _table_columns(connection, "OC_HDVISITORINFO")
    if not columns:
        raise KstLegacyDatabaseError(
            "老版快商通历史库缺少必要数据表：OC_HDVISITORINFO"
        )
    missing = [
        field
        for field in _HISTORY_REQUIRED_FIELDS
        if field.casefold() not in columns
    ]
    if not {
        "curentertime",
        "diastarttime",
    }.intersection(columns):
        missing.append("curEnterTime/diaStartTime")
    if missing:
        raise KstLegacyDatabaseError(
            "老版快商通历史库缺少必要字段：" + "、".join(missing)
        )
    return ", ".join(
        (
            field
            if field.casefold() in columns
            else f'NULL AS "{field}"'
        )
        for field in _HISTORY_FIELDS
    )


def _validate_message_schema(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "DIALOGRECORD_VISITOR")
    if not columns:
        raise KstLegacyDatabaseError(
            "老版快商通消息库缺少必要数据表：DIALOGRECORD_VISITOR"
        )
    missing = [
        field
        for field in ("recId", "addTime", "recType")
        if field.casefold() not in columns
    ]
    if missing:
        raise KstLegacyDatabaseError(
            "老版快商通消息库缺少必要字段：" + "、".join(missing)
        )


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


def _promotion_match(row: tuple[Any, ...]) -> re.Match[str] | None:
    for index in (4, 12):
        if index >= len(row):
            continue
        match = _PROMOTION_PATTERN.search(str(row[index] or ""))
        if match is not None:
            return match
    return None


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=0.5,
    )
    connection.execute("PRAGMA busy_timeout=500")
    return connection


def _database_failure(
    error: BaseException,
    *,
    incompatible_message: str,
) -> KstLegacyDatabaseError:
    error_code = getattr(error, "sqlite_errorcode", None)
    lowered = str(error).casefold()
    if (
        error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        or "locked" in lowered
        or "busy" in lowered
    ):
        return KstLegacyDatabaseError(
            "老版快商通数据库忙或读取超时",
            category="database_busy_or_timeout",
        )
    return KstLegacyDatabaseError(
        incompatible_message,
        category="database_incompatible",
    )


def _check_interrupted(cancel_event: Any, deadline: float) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KstLegacyDatabaseError(
            "老版快商通数据库读取已取消",
            category="database_busy_or_timeout",
        )
    if time.monotonic() >= deadline:
        raise KstLegacyDatabaseError(
            "老版快商通数据库忙或读取超时",
            category="database_busy_or_timeout",
        )


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
    failure: KstLegacyDatabaseError | None = None
    try:
        with closing(_connect_read_only(path)) as connection:
            _install_progress_handler(connection, cancel_event, deadline)
            effective_query = query
            if query == _HISTORY_CAPABILITY_QUERY:
                effective_query = (
                    f"SELECT {_history_select_list(connection)} "
                    "FROM OC_HDVISITORINFO LIMIT 0"
                )
            elif query == _MESSAGE_CAPABILITY_QUERY:
                _validate_message_schema(connection)
            connection.execute(effective_query).fetchall()
            _check_interrupted(cancel_event, deadline)
    except KstLegacyDatabaseError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        failure = _database_failure(
            exc,
            incompatible_message="老版快商通数据库结构不兼容",
        )
    if failure is not None:
        _check_interrupted(cancel_event, deadline)
        raise failure from None


def inspect_legacy_read_capability(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline: float | None = None,
    deadline_seconds: float = 5.0,
) -> set[str]:
    absolute_deadline = (
        time.monotonic() + deadline_seconds
        if deadline is None
        else float(deadline)
    )
    if not installation.message_database_paths:
        raise KstLegacyDatabaseError(
            "老版快商通数据库结构不兼容",
            category="database_incompatible",
        )
    for history_path in legacy_history_database_paths(installation):
        _validate_database_capability(
            history_path,
            _HISTORY_CAPABILITY_QUERY,
            cancel_event,
            absolute_deadline,
        )
    for database_path in installation.message_database_paths:
        _validate_database_capability(
            database_path,
            _MESSAGE_CAPABILITY_QUERY,
            cancel_event,
            absolute_deadline,
        )
    promotion_ids = _read_legacy_promotion_ids_with_deadline(
        installation,
        cancel_event,
        absolute_deadline,
    )
    _check_interrupted(cancel_event, absolute_deadline)
    if not promotion_ids:
        raise KstLegacyDatabaseError(
            "老版快商通身份缺少可用推广 ID",
            category="identity_mapping",
        )
    return promotion_ids


def validate_legacy_read_capability(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline_seconds: float = 5.0,
) -> None:
    inspect_legacy_read_capability(
        installation,
        cancel_event=cancel_event,
        deadline_seconds=deadline_seconds,
    )


def _read_authorized_rec_ids(
    installation: LegacyKstInstallation,
    target_date: str,
    cancel_event: Any,
    deadline: float,
) -> set[str]:
    rec_ids: set[str] = set()
    for database_path in installation.message_database_paths:
        _check_interrupted(cancel_event, deadline)
        failure: KstLegacyDatabaseError | None = None
        try:
            with closing(_connect_read_only(database_path)) as connection:
                _install_progress_handler(connection, cancel_event, deadline)
                _validate_message_schema(connection)
                rows = connection.execute(
                    """
                    SELECT DISTINCT recId
                    FROM DIALOGRECORD_VISITOR
                    WHERE recType = 1 AND date(addTime) = ?
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
        except (OSError, sqlite3.Error, ValueError) as exc:
            failure = _database_failure(
                exc,
                incompatible_message="老版快商通数据库结构不兼容",
            )
        if failure is not None:
            _check_interrupted(cancel_event, deadline)
            raise failure from None
    return rec_ids


def _read_all_authorized_rec_ids(
    installation: LegacyKstInstallation,
    cancel_event: Any,
    deadline: float,
) -> set[str]:
    rec_ids: set[str] = set()
    for database_path in installation.message_database_paths:
        _check_interrupted(cancel_event, deadline)
        failure: KstLegacyDatabaseError | None = None
        try:
            with closing(_connect_read_only(database_path)) as connection:
                _install_progress_handler(connection, cancel_event, deadline)
                _validate_message_schema(connection)
                rows = connection.execute(
                    """
                    SELECT DISTINCT recId
                    FROM DIALOGRECORD_VISITOR
                    WHERE recType = 1
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
        except (OSError, sqlite3.Error, ValueError) as exc:
            failure = _database_failure(
                exc,
                incompatible_message="老版快商通数据库结构不兼容",
            )
        if failure is not None:
            _check_interrupted(cancel_event, deadline)
            raise failure from None
    return rec_ids


def _read_history_rows(
    installation: LegacyKstInstallation,
    rec_ids: set[str],
    cancel_event: Any,
    deadline: float,
    target_date: str | None = None,
) -> dict[str, tuple[Any, ...]]:
    rows_by_rec_id: dict[str, tuple[Any, ...]] = {}
    ordered_rec_ids = sorted(rec_ids)
    failure: KstLegacyDatabaseError | None = None
    try:
        for history_path in legacy_history_database_paths(
            installation,
            target_date,
        ):
            with closing(_connect_read_only(history_path)) as connection:
                _install_progress_handler(connection, cancel_event, deadline)
                select_list = _history_select_list(connection)
                for offset in range(
                    0,
                    len(ordered_rec_ids),
                    _HISTORY_CHUNK_SIZE,
                ):
                    _check_interrupted(cancel_event, deadline)
                    chunk = ordered_rec_ids[
                        offset : offset + _HISTORY_CHUNK_SIZE
                    ]
                    markers = ", ".join("?" for _ in chunk)
                    rows = connection.execute(
                        f"""
                        SELECT {select_list}
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
    except (OSError, sqlite3.Error, ValueError) as exc:
        failure = _database_failure(
            exc,
            incompatible_message="老版快商通数据库结构不兼容",
        )
    if failure is not None:
        _check_interrupted(cancel_event, deadline)
        raise failure from None
    return rows_by_rec_id


def _read_legacy_promotion_ids_with_deadline(
    installation: LegacyKstInstallation,
    cancel_event: Any,
    deadline: float,
) -> set[str]:
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
    promotion_ids: set[str] = set()
    _check_interrupted(cancel_event, deadline)
    for row in history_rows.values():
        _check_interrupted(cancel_event, deadline)
        match = _promotion_match(row)
        if match is not None:
            promotion_ids.add(match.group(1))
        _check_interrupted(cancel_event, deadline)
    _check_interrupted(cancel_event, deadline)
    return promotion_ids


def read_legacy_promotion_ids(
    installation: LegacyKstInstallation,
    *,
    cancel_event: Any = None,
    deadline_seconds: float = 5.0,
) -> set[str]:
    deadline = time.monotonic() + deadline_seconds
    promotion_ids = _read_legacy_promotion_ids_with_deadline(
        installation,
        cancel_event,
        deadline,
    )
    _check_interrupted(cancel_event, deadline)
    if not promotion_ids:
        raise KstLegacyDatabaseError(
            "老版快商通身份缺少可用推广 ID",
            category="identity_mapping",
        )
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
        target_date,
    )
    if set(history_rows) != authorized:
        raise KstLegacyDatabaseError(
            "老版快商通会话尚未同步完整",
            category="identity_mapping",
        )

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
        promotion_match = _promotion_match(row)
        if promotion_match is None:
            if (
                len(row) > 13
                and str(row[13] or "").strip() == _DIRECT_SOURCE_TYPE
            ):
                _check_interrupted(cancel_event, deadline)
                continue
            raise KstLegacyDatabaseError(
                "老版快商通推广 ID 无效",
                category="identity_mapping",
            )
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
