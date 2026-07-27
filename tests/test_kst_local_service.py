from modules.kst_local.models import (
    AutomaticSourceSnapshot,
    KstAuthContext,
    KstCacheCandidate,
)
from modules.kst_local.service import KstConversationService, KstServiceError


class FakeClient:
    def __init__(self, failing_rec_id=None):
        self.failing_rec_id = failing_rec_id
        self.visitor_calls = []

    def load_tag_dictionary(self):
        return {"11": "有效-三句", "12": "转潜-有效"}

    def load_visitor(self, rec_id):
        self.visitor_calls.append(rec_id)
        if rec_id == self.failing_rec_id:
            raise RuntimeError("failed")
        return {
            "visitorId": f"visitor-{rec_id}",
            "curEnterTime": "2026-07-27 09:00:00",
            "visitorSendNum": 2,
        }

    def load_card(self, visitor_id):
        return {"cusTypeTag": '{"11":1,"12":1}'}


def _config():
    return {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "accounts": {
            "银康01": {
                "excel_name": "银康01",
                "aliases": ["银康01"],
            },
            "银康银屑02": {
                "excel_name": "银康银屑02",
                "aliases": ["银康银屑02"],
            },
            "银康03": {
                "excel_name": "银康03",
                "aliases": ["银康03"],
            },
        },
        "kst": {
            "promotion_id_accounts": {
                "72828178": "银康01",
                "72828179": "银康银屑02",
                "81509165": "银康03",
            }
        },
    }


def _snapshot():
    return AutomaticSourceSnapshot(
        sources_by_rec_id={
            "101": frozenset({"websocket_msg_type_48", "startup_auto_sync"}),
            "202": frozenset({"startup_auto_sync"}),
        },
        auth=KstAuthContext(),
    )


def _candidates():
    return [
        KstCacheCandidate(
            rec_id="101",
            start_time="2026-07-27 08:59:58",
            promotion_id="72828178",
            visitor_messages=1,
            keyword="测试词",
        ),
        KstCacheCandidate(
            rec_id="999",
            start_time="2026-07-27 09:05:00",
            promotion_id="81509165",
            visitor_messages=3,
        ),
    ]


def test_service_excludes_database_only_manual_history_and_deduplicates_sources():
    client = FakeClient()
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=client,
    )

    conversations = service.collect("2026-07-27")
    report = service.build_hourly_report("2026-07-27", "15点")

    assert [item.rec_id for item in conversations] == ["101"]
    assert conversations[0].promotion_id == "72828178"
    assert conversations[0].visitor_messages == 2
    assert conversations[0].tags == ("有效-三句", "转潜-有效")
    assert "999" not in client.visitor_calls
    assert report["source"] == "kst_local_api"
    assert report["accounts"]["银康01"] == {
        "总对话": 1,
        "有效对话": 1,
        "一般有效": 0,
        "有效转潜": 1,
        "总转潜": 1,
    }
    assert report["summary"]["automatic_rows"] == 1


def test_service_fails_instead_of_turning_query_failure_into_zero():
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=FakeClient(failing_rec_id="101"),
    )

    try:
        service.collect("2026-07-27")
    except KstServiceError as exc:
        assert "101" in str(exc)
    else:
        raise AssertionError("KstServiceError was not raised")
