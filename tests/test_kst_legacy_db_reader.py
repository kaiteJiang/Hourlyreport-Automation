from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from modules.kst_local.legacy_db_reader import (
    KstLegacyDatabaseError,
    normalize_legacy_tags,
    read_legacy_conversations,
    read_legacy_promotion_ids,
)
from modules.kst_local.models import LegacyKstInstallation


def _create_live_database(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR (recId TEXT, addTime TEXT)"
        )


def _create_history_database(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE OC_HDVISITORINFO (
                recId TEXT,
                curEnterTime TEXT,
                diaStartTime TEXT,
                visitorSendNum INTEGER,
                visitorCustomField TEXT,
                keyword TEXT,
                bidWord TEXT,
                talkGrade TEXT,
                dialogClassification TEXT,
                classifyTag TEXT,
                cusTypeTag TEXT,
                aiTags TEXT
            )
            """
        )


@pytest.fixture
def legacy_installation(tmp_path):
    history_db = tmp_path / "synthetic_HIS.cdb"
    live_db = tmp_path / "synthetic_CS.pdb"
    _create_history_database(history_db)
    _create_live_database(live_db)
    return LegacyKstInstallation(
        root=tmp_path / "client",
        executable=tmp_path / "client" / "OnlineCS.exe",
        version="7.03.17",
        identity="synthetic",
        log_dir=tmp_path / "logs",
        data_root=tmp_path,
        history_db=history_db,
        message_database_paths=(live_db,),
    )


def insert_live_message(path, *, rec_id: str, add_time: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO DIALOGRECORD_VISITOR (recId, addTime) VALUES (?, ?)",
            (rec_id, add_time),
        )


def insert_history(
    path,
    *,
    rec_id: str,
    start: str,
    messages: int,
    promotion_id: str,
    tags: str,
    dia_start: str = "",
    visitor_custom_field: str | None = None,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO OC_HDVISITORINFO (
                recId, curEnterTime, diaStartTime, visitorSendNum,
                visitorCustomField, keyword, bidWord, talkGrade,
                dialogClassification, classifyTag, cusTypeTag, aiTags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                start,
                dia_start,
                messages,
                (
                    visitor_custom_field
                    if visitor_custom_field is not None
                    else f"推广 ID：{promotion_id}"
                ),
                "",
                "",
                tags,
                "",
                "",
                "",
                "",
            ),
        )


def assert_read_fails_without_modifying_sources(
    installation,
    target_date: str,
    *,
    match: str | None = None,
    **kwargs,
) -> None:
    paths = (installation.history_db, *installation.message_database_paths)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(KstLegacyDatabaseError, match=match):
        read_legacy_conversations(
            installation,
            target_date,
            **kwargs,
        )
    assert {path: path.read_bytes() for path in paths} == before


def test_history_only_record_is_not_counted(legacy_installation):
    insert_history(
        legacy_installation.history_db,
        rec_id="history-only",
        start="2026-07-29 09:10:00",
        messages=3,
        promotion_id="10001",
        tags="有效-三句话",
    )
    assert read_legacy_conversations(legacy_installation, "2026-07-29") == []


def test_live_shard_authorizes_matching_history_record(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="live-1",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="live-1",
        start="2026-07-29 09:10:00",
        messages=3,
        promotion_id="10001",
        tags="有效-三句话",
    )
    rows = read_legacy_conversations(legacy_installation, "2026-07-29")
    assert [(row.rec_id, row.promotion_id, row.visitor_messages) for row in rows] == [
        ("live-1", "10001", 3)
    ]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            ('["有效-三句话", "转潜-有效"]',),
            ("有效-三句话", "转潜-有效"),
        ),
        (({"1": "有效-一般"},), ("有效-一般",)),
        (
            ("有效-三句话、有效-一般|转潜-有效",),
            ("有效-三句话", "有效-一般", "转潜-有效"),
        ),
        (
            ({"first": ["有效-三句话", {"nested": "转潜-有效"}]}, "有效-三句话"),
            ("有效-三句话", "转潜-有效"),
        ),
        (('"有效-三句话"',), ("有效-三句话",)),
    ],
)
def test_normalize_legacy_tags(values, expected):
    assert normalize_legacy_tags(*values) == expected


def test_live_record_without_history_details_fails_closed(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="not-synced",
        add_time="2026-07-29 09:10:01",
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        match="老版快商通会话尚未同步完整",
    )


def test_missing_promotion_id_fails_closed(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="missing-promotion",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="missing-promotion",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="",
        tags="",
        visitor_custom_field="备注中没有推广编号",
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
    )


@pytest.mark.parametrize(
    "visitor_custom_field",
    [
        "订单 123456，推广 ID：1234",
        "推广 ID：12345abc",
    ],
)
def test_promotion_id_requires_explicit_label_and_five_digits(
    legacy_installation,
    visitor_custom_field,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="bad-promotion",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="bad-promotion",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="",
        tags="",
        visitor_custom_field=visitor_custom_field,
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
    )


@pytest.mark.parametrize("messages", [None, -1, 1.5, "not-a-count"])
def test_invalid_visitor_message_count_fails_closed(
    legacy_installation,
    messages,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="negative-count",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="negative-count",
        start="2026-07-29 09:10:00",
        messages=messages,
        promotion_id="10001",
        tags="",
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
    )


def test_zero_visitor_message_count_is_dropped(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="zero-count",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="zero-count",
        start="2026-07-29 09:10:00",
        messages=0,
        promotion_id="10001",
        tags="",
    )
    assert read_legacy_conversations(legacy_installation, "2026-07-29") == []


@pytest.mark.parametrize("target_date", ["2026-02-30", "29-07-2026", ""])
def test_invalid_target_date_fails_closed(legacy_installation, target_date):
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        target_date,
    )


@pytest.mark.parametrize("start", ["not-a-time", "2026-07-29"])
def test_invalid_conversation_start_time_fails_closed(
    legacy_installation,
    start,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="bad-start",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="bad-start",
        start=start,
        messages=1,
        promotion_id="10001",
        tags="",
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
    )


@pytest.mark.parametrize(
    "cancel_event,deadline_seconds",
    [(threading.Event(), 0.0)],
)
def test_expired_deadline_fails_without_modifying_sources(
    legacy_installation,
    cancel_event,
    deadline_seconds,
):
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        cancel_event=cancel_event,
        deadline_seconds=deadline_seconds,
    )


def test_pre_set_cancellation_fails_without_modifying_sources(legacy_installation):
    cancel_event = threading.Event()
    cancel_event.set()
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        cancel_event=cancel_event,
    )


def test_promotion_ids_use_only_live_authorized_records(legacy_installation):
    insert_history(
        legacy_installation.history_db,
        rec_id="history-only",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="99999",
        tags="",
    )
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="live",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="live",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="10001",
        tags="",
    )
    paths = (
        legacy_installation.history_db,
        *legacy_installation.message_database_paths,
    )
    before = {path: path.read_bytes() for path in paths}
    assert read_legacy_promotion_ids(legacy_installation) == {"10001"}
    assert {path: path.read_bytes() for path in paths} == before


def test_live_records_outside_target_date_are_not_counted(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="yesterday",
        add_time="2026-07-28 23:59:59",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="yesterday",
        start="2026-07-28 23:59:59",
        messages=1,
        promotion_id="10001",
        tags="有效-三句话",
    )
    assert read_legacy_conversations(legacy_installation, "2026-07-29") == []


def test_history_fallback_fields_and_tags_are_normalized(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="fallback",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="fallback",
        start="",
        dia_start="2026-07-29 09:10:00",
        messages=2,
        promotion_id="10001",
        tags='["有效-三句话", "转潜-有效"]',
    )
    row = read_legacy_conversations(legacy_installation, "2026-07-29")[0]
    assert (row.start_time, row.tags) == (
        "2026-07-29 09:10:00",
        ("有效-三句话", "转潜-有效"),
    )


def test_database_lock_wait_is_bounded_and_sources_are_unchanged(
    legacy_installation,
):
    live_db = legacy_installation.message_database_paths[0]
    paths = (legacy_installation.history_db, live_db)
    before = {path: path.read_bytes() for path in paths}
    locker = sqlite3.connect(live_db)
    locker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    try:
        with pytest.raises(KstLegacyDatabaseError):
            read_legacy_conversations(legacy_installation, "2026-07-29")
    finally:
        elapsed = time.monotonic() - started
        locker.rollback()
        locker.close()
    assert elapsed < 1.25
    assert {path: path.read_bytes() for path in paths} == before


def test_progress_handler_honors_cancellation_without_modifying_sources(
    legacy_installation,
):
    live_db = legacy_installation.message_database_paths[0]
    with sqlite3.connect(live_db) as connection:
        connection.executemany(
            "INSERT INTO DIALOGRECORD_VISITOR (recId, addTime) VALUES (?, ?)",
            (
                (f"bulk-{index}", "2026-07-29 09:10:01")
                for index in range(10_000)
            ),
        )

    class CancelDuringQuery:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 3

    cancel_event = CancelDuringQuery()
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        cancel_event=cancel_event,
    )
    assert cancel_event.checks >= 3


def test_cancellation_is_checked_after_each_query_stage(legacy_installation):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="cancel-after-history",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="cancel-after-history",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="10001",
        tags="",
    )

    class CancelAfterHistoryQuery:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 4

    cancel_event = CancelAfterHistoryQuery()
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        cancel_event=cancel_event,
    )
    assert cancel_event.checks >= 4
