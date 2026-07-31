from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing

import pytest

from modules.kst_local import legacy_db_reader
from modules.kst_local.legacy_db_reader import (
    KstLegacyDatabaseError,
    _connect_read_only,
    normalize_legacy_tags,
    read_legacy_conversations,
    read_legacy_promotion_ids,
    validate_legacy_read_capability,
)
from modules.kst_local.models import LegacyKstInstallation


def _create_live_database(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR "
            "(recId TEXT, addTime TEXT, recType INTEGER)"
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
                info TEXT,
                keyword TEXT,
                bidWord TEXT,
                talkGrade TEXT,
                dialogClassification TEXT,
                classifyTag TEXT,
                cusTypeTag TEXT,
                aiTags TEXT,
                sourceType INTEGER
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


def insert_live_message(
    path,
    *,
    rec_id: str,
    add_time: str,
    rec_type: int = 1,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO DIALOGRECORD_VISITOR "
            "(recId, addTime, recType) VALUES (?, ?, ?)",
            (rec_id, add_time, rec_type),
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
    info: str = "",
    source_type: int | None = None,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO OC_HDVISITORINFO (
                recId, curEnterTime, diaStartTime, visitorSendNum,
                visitorCustomField, info, keyword, bidWord, talkGrade,
                dialogClassification, classifyTag, cusTypeTag, aiTags,
                sourceType
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                info,
                "",
                "",
                tags,
                "",
                "",
                "",
                "",
                source_type,
            ),
        )


def assert_read_fails_without_modifying_sources(
    installation,
    target_date: str,
    *,
    match: str | None = None,
    category: str | None = None,
    **kwargs,
) -> None:
    paths = (installation.history_db, *installation.message_database_paths)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(KstLegacyDatabaseError, match=match) as captured:
        read_legacy_conversations(
            installation,
            target_date,
            **kwargs,
        )
    if category is not None:
        assert captured.value.category == category
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


def test_system_only_live_record_does_not_require_history_details(
    legacy_installation,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="system-only",
        add_time="2026-07-29 09:10:01",
        rec_type=4,
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
        category="identity_mapping",
    )


def test_legacy_promotion_id_falls_back_to_info_field(
    legacy_installation,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="info-promotion",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="info-promotion",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="",
        tags="",
        visitor_custom_field='{"source":"legacy"}',
        info="推广 ID：10001",
    )

    rows = read_legacy_conversations(legacy_installation, "2026-07-29")

    assert [(row.rec_id, row.promotion_id) for row in rows] == [
        ("info-promotion", "10001")
    ]


def test_direct_source_without_promotion_id_is_not_a_baidu_conversation(
    legacy_installation,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="direct-source",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="direct-source",
        start="2026-07-29 09:10:00",
        messages=2,
        promotion_id="",
        tags="",
        visitor_custom_field="{}",
        source_type=7,
    )

    assert read_legacy_conversations(legacy_installation, "2026-07-29") == []


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


def test_promotion_id_read_ignores_not_yet_synced_live_conversation(
    legacy_installation,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="complete",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="complete",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="10001",
        tags="",
    )
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="still-syncing",
        add_time="2026-07-31 17:10:01",
    )

    assert read_legacy_promotion_ids(legacy_installation) == {"10001"}


def test_target_month_archive_supplies_history_when_current_file_is_empty(
    legacy_installation,
):
    archive = (
        legacy_installation.history_db.parent
        / "his"
        / "2026-07_HIS.cdb"
    )
    archive.parent.mkdir()
    _create_history_database(archive)
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="archived",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        archive,
        rec_id="archived",
        start="2026-07-29 09:10:00",
        messages=2,
        promotion_id="10001",
        tags="有效-三句话",
    )

    rows = read_legacy_conversations(legacy_installation, "2026-07-29")

    assert [(row.rec_id, row.visitor_messages) for row in rows] == [
        ("archived", 2)
    ]


def test_complete_empty_schema_is_not_a_ready_legacy_identity(
    legacy_installation,
):
    with pytest.raises(KstLegacyDatabaseError) as captured:
        validate_legacy_read_capability(legacy_installation)

    assert captured.value.category == "identity_mapping"
    assert str(captured.value) == "老版快商通身份缺少可用推广 ID"


def test_database_lock_has_safe_busy_timeout_category(
    legacy_installation,
):
    live_db = legacy_installation.message_database_paths[0]
    locker = sqlite3.connect(live_db)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(KstLegacyDatabaseError) as captured:
            validate_legacy_read_capability(legacy_installation)
    finally:
        locker.rollback()
        locker.close()

    assert captured.value.category == "database_busy_or_timeout"
    assert str(captured.value) == "老版快商通数据库忙或读取超时"


def test_readiness_capability_uses_one_absolute_deadline(
    legacy_installation,
    monkeypatch,
):
    deadlines = []
    clock_values = iter((10.0, 20.0))
    monkeypatch.setattr(
        legacy_db_reader.time,
        "monotonic",
        lambda: next(clock_values),
    )
    monkeypatch.setattr(
        legacy_db_reader,
        "_check_interrupted",
        lambda _cancel_event, _deadline: None,
    )
    monkeypatch.setattr(
        legacy_db_reader,
        "_validate_database_capability",
        lambda _path, _query, _cancel_event, deadline: deadlines.append(
            ("schema", deadline)
        ),
    )

    def read_authorized(
        _installation,
        _cancel_event,
        deadline,
    ):
        deadlines.append(("authorized", deadline))
        return {"shared-deadline-rec-id"}

    def read_history(
        _installation,
        _rec_ids,
        _cancel_event,
        deadline,
    ):
        deadlines.append(("history", deadline))
        return {
            "shared-deadline-rec-id": (
                "shared-deadline-rec-id",
                None,
                None,
                None,
                "推广 ID：10001",
            )
        }

    monkeypatch.setattr(
        legacy_db_reader,
        "_read_all_authorized_rec_ids",
        read_authorized,
    )
    monkeypatch.setattr(
        legacy_db_reader,
        "_read_history_rows",
        read_history,
    )

    validate_legacy_read_capability(legacy_installation)

    assert deadlines == [
        ("schema", 15.0),
        ("schema", 15.0),
        ("authorized", 15.0),
        ("history", 15.0),
    ]


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


def test_legacy_history_without_optional_columns_remains_readable(
    legacy_installation,
):
    history_db = legacy_installation.history_db
    with sqlite3.connect(history_db) as connection:
        connection.execute("DROP TABLE OC_HDVISITORINFO")
        connection.execute(
            """
            CREATE TABLE OC_HDVISITORINFO (
                recId TEXT,
                curEnterTime TEXT,
                visitorSendNum INTEGER,
                visitorCustomField TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO OC_HDVISITORINFO (
                recId, curEnterTime, visitorSendNum, visitorCustomField
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "old-schema",
                "2026-07-29 09:10:00",
                2,
                "推广 ID：10001",
            ),
        )
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="old-schema",
        add_time="2026-07-29 09:10:01",
    )

    rows = read_legacy_conversations(legacy_installation, "2026-07-29")

    assert len(rows) == 1
    assert rows[0].rec_id == "old-schema"
    assert rows[0].tags == ()
    assert rows[0].keyword == ""
    assert rows[0].bid_word == ""


def test_legacy_history_missing_required_column_reports_the_field(
    legacy_installation,
):
    history_db = legacy_installation.history_db
    with sqlite3.connect(history_db) as connection:
        connection.execute("DROP TABLE OC_HDVISITORINFO")
        connection.execute(
            """
            CREATE TABLE OC_HDVISITORINFO (
                recId TEXT,
                curEnterTime TEXT,
                visitorCustomField TEXT
            )
            """
        )

    with pytest.raises(KstLegacyDatabaseError) as captured:
        validate_legacy_read_capability(legacy_installation)

    assert captured.value.category == "database_incompatible"
    assert str(captured.value) == "老版快商通历史库缺少必要字段：visitorSendNum"


def test_legacy_message_database_missing_required_column_reports_the_field(
    legacy_installation,
):
    live_db = legacy_installation.message_database_paths[0]
    with sqlite3.connect(live_db) as connection:
        connection.execute("DROP TABLE DIALOGRECORD_VISITOR")
        connection.execute(
            "CREATE TABLE DIALOGRECORD_VISITOR (recId TEXT)"
        )

    with pytest.raises(KstLegacyDatabaseError) as captured:
        validate_legacy_read_capability(legacy_installation)

    assert captured.value.category == "database_incompatible"
    assert str(captured.value) == (
        "老版快商通消息库缺少必要字段：addTime、recType"
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


def test_duplicate_history_rows_for_authorized_rec_id_fail_closed(
    legacy_installation,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="duplicate",
        add_time="2026-07-29 09:10:01",
    )
    for start in ("2026-07-29 09:10:00", "2026-07-29 09:11:00"):
        insert_history(
            legacy_installation.history_db,
            rec_id="duplicate",
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
    "reader",
    [
        lambda installation, event: read_legacy_conversations(
            installation,
            "2026-07-29",
            cancel_event=event,
        ),
        lambda installation, event: read_legacy_promotion_ids(
            installation,
            cancel_event=event,
        ),
    ],
)
def test_post_processing_loop_checks_cancellation(
    legacy_installation,
    reader,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="cancel-during-conversion",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="cancel-during-conversion",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="10001",
        tags="有效-三句话",
    )

    class CancelBeforeFirstConversion:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 5

    cancel_event = CancelBeforeFirstConversion()
    paths = (
        legacy_installation.history_db,
        *legacy_installation.message_database_paths,
    )
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(KstLegacyDatabaseError):
        reader(legacy_installation, cancel_event)
    assert cancel_event.checks >= 5
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize(
    "reader",
    [
        lambda installation, event: read_legacy_conversations(
            installation,
            "2026-07-29",
            cancel_event=event,
        ),
        lambda installation, event: read_legacy_promotion_ids(
            installation,
            cancel_event=event,
        ),
    ],
)
def test_final_empty_return_checks_cancellation(
    legacy_installation,
    reader,
):
    class CancelBeforeReturn:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 3

    cancel_event = CancelBeforeReturn()
    with pytest.raises(KstLegacyDatabaseError):
        reader(legacy_installation, cancel_event)
    assert cancel_event.checks >= 3


def test_conversation_post_processing_checks_deadline(
    legacy_installation,
    monkeypatch,
):
    insert_live_message(
        legacy_installation.message_database_paths[0],
        rec_id="deadline-during-tags",
        add_time="2026-07-29 09:10:01",
    )
    insert_history(
        legacy_installation.history_db,
        rec_id="deadline-during-tags",
        start="2026-07-29 09:10:00",
        messages=1,
        promotion_id="10001",
        tags="有效-三句话",
    )

    class ControlledClock:
        expired = False

        def monotonic(self):
            return 10.0 if self.expired else 0.0

    clock = ControlledClock()
    real_normalize = normalize_legacy_tags

    def expire_during_normalization(*values):
        result = real_normalize(*values)
        clock.expired = True
        return result

    monkeypatch.setattr(legacy_db_reader.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        legacy_db_reader,
        "normalize_legacy_tags",
        expire_during_normalization,
    )
    assert_read_fails_without_modifying_sources(
        legacy_installation,
        "2026-07-29",
        deadline_seconds=5.0,
    )


def _exception_chain_text(error: BaseException) -> str:
    values: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return "\n".join(values)


def test_invalid_input_error_chain_does_not_expose_original_value(
    legacy_installation,
):
    sentinel = "PRIVATE-TARGET-DATE-SENTINEL"
    with pytest.raises(KstLegacyDatabaseError) as captured:
        read_legacy_conversations(legacy_installation, sentinel)
    assert sentinel not in _exception_chain_text(captured.value)


def test_sqlite_error_chain_does_not_expose_schema_sentinel(
    legacy_installation,
):
    sentinel = "private_schema_sentinel"
    live_db = legacy_installation.message_database_paths[0]
    with sqlite3.connect(live_db) as connection:
        connection.execute("DROP TABLE DIALOGRECORD_VISITOR")
        connection.execute(
            f"""
            CREATE VIEW DIALOGRECORD_VISITOR AS
            SELECT {sentinel}() AS recId, '2026-07-29 09:10:01' AS addTime
            """
        )
    with pytest.raises(KstLegacyDatabaseError) as captured:
        read_legacy_conversations(legacy_installation, "2026-07-29")
    assert sentinel not in _exception_chain_text(captured.value)


def test_read_only_connection_rejects_writes_and_uses_safe_uri(
    legacy_installation,
    monkeypatch,
):
    real_connect = sqlite3.connect
    calls = []

    def recording_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(legacy_db_reader.sqlite3, "connect", recording_connect)
    with closing(
        _connect_read_only(legacy_installation.message_database_paths[0])
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM DIALOGRECORD_VISITOR"
        ).fetchone() == (0,)
        for statement in (
            "CREATE TABLE forbidden (id INTEGER)",
            "INSERT INTO DIALOGRECORD_VISITOR (recId, addTime) VALUES ('x', 'y')",
            "UPDATE DIALOGRECORD_VISITOR SET recId = 'x'",
        ):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute(statement)

    database_uri, kwargs = calls[-1]
    assert "?mode=ro" in database_uri
    assert "immutable=1" not in database_uri
    assert kwargs["uri"] is True
    assert kwargs["timeout"] <= 0.5
