from __future__ import annotations

import sqlite3
import threading

import pytest

from modules.kst_local.legacy_service import LegacyKstConversationService
from modules.kst_local.models import LegacyKstInstallation
from modules.kst_local.service import KstServiceError


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


@pytest.fixture
def project_config():
    return {
        "project_id": "synthetic-project",
        "project_name": "合成项目",
        "accounts": {
            "账户A": {
                "excel_name": "账户A",
                "aliases": ["账户A"],
            }
        },
        "kst": {
            "promotion_id_accounts": {
                "10001": "账户A",
            }
        },
    }


def seed_conversation(
    installation,
    *,
    rec_id,
    promotion_id,
    visitor_messages,
    tags,
):
    with sqlite3.connect(installation.message_database_paths[0]) as connection:
        connection.execute(
            "INSERT INTO DIALOGRECORD_VISITOR (recId, addTime) VALUES (?, ?)",
            (rec_id, "2026-07-29 09:10:01"),
        )
    with sqlite3.connect(installation.history_db) as connection:
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
                "2026-07-29 09:10:00",
                "",
                visitor_messages,
                f"推广 ID：{promotion_id}",
                "合成搜索词",
                "合成竞价词",
                "、".join(tags),
                "",
                "",
                "",
                "",
            ),
        )


def test_legacy_service_reuses_hourly_tag_rules(
    legacy_installation,
    project_config,
):
    seed_conversation(
        legacy_installation,
        rec_id="1",
        promotion_id="10001",
        visitor_messages=3,
        tags=("有效-三句话", "转潜-有效"),
    )

    report = LegacyKstConversationService(
        project_config,
        legacy_installation,
    ).build_hourly_report("2026-07-29", "11点")

    assert report["accounts"]["账户A"] == {
        "总对话": 1,
        "有效对话": 1,
        "一般有效": 0,
        "有效转潜": 1,
        "总转潜": 1,
    }


def test_legacy_service_reuses_daily_general_valid_rule(
    legacy_installation,
    project_config,
):
    seed_conversation(
        legacy_installation,
        rec_id="2",
        promotion_id="10001",
        visitor_messages=2,
        tags=("有效-一般",),
    )

    report = LegacyKstConversationService(
        project_config,
        legacy_installation,
    ).build_daily_report("2026-07-29")

    assert report["accounts"]["账户A"] == {
        "总对话": 1,
        "有效对话": 0,
        "无效对话": 0,
        "一般有效对话": 1,
        "有效转潜": 0,
        "总转潜": 0,
    }


def test_legacy_service_rejects_promotion_id_outside_project(
    legacy_installation,
    project_config,
):
    seed_conversation(
        legacy_installation,
        rec_id="outside-project",
        promotion_id="99999",
        visitor_messages=1,
        tags=("有效-三句话",),
    )

    with pytest.raises(KstServiceError, match="outside-project"):
        LegacyKstConversationService(
            project_config,
            legacy_installation,
        ).build_hourly_report("2026-07-29", "11点")


def test_legacy_service_preserves_safe_cancellation_category(
    legacy_installation,
    project_config,
):
    class CancelDuringReader(threading.Event):
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= 2

    cancel_event = CancelDuringReader()
    service = LegacyKstConversationService(
        project_config,
        legacy_installation,
        cancel_event=cancel_event,
    )

    with pytest.raises(KstServiceError, match="取消"):
        service.collect("2026-07-29")
