import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_service_builds_daily_report_from_automatic_conversations():
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=FakeClient(),
    )

    report = service.build_daily_report("2026-07-27")

    assert report["project_id"] == "kunming_niu"
    assert report["date"] == "2026-07-27"
    assert report["source"] == "kst_local_api"
    assert report["accounts"]["银康01"] == {
        "总对话": 1,
        "有效对话": 1,
        "无效对话": 0,
        "一般有效对话": 0,
        "有效转潜": 1,
        "总转潜": 1,
    }
    assert report["summary"]["automatic_rows"] == 1


def test_service_falls_back_to_local_candidate_when_visitor_query_fails():
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=FakeClient(failing_rec_id="101"),
    )

    conversations = service.collect("2026-07-27")

    assert [item.rec_id for item in conversations] == ["101"]
    # 云端富化失败时回退到本地候选数据：开始时间、推广ID、访客消息数均来自候选
    assert conversations[0].start_time == "2026-07-27 08:59:58"
    assert conversations[0].promotion_id == "72828178"
    assert conversations[0].visitor_messages == 1
    assert conversations[0].keyword == "测试词"


def test_service_still_raises_when_visitor_query_fails_and_local_data_invalid():
    candidates = [
        KstCacheCandidate(
            rec_id="101",
            start_time="2026-07-27 08:59:58",
            promotion_id="99999999",
            visitor_messages=1,
        )
    ]

    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=candidates,
        client=FakeClient(failing_rec_id="101"),
    )

    with pytest.raises(KstServiceError, match="101"):
        service.collect("2026-07-27")


def test_service_rejects_automatic_conversation_outside_project_mapping():
    candidates = [
        KstCacheCandidate(
            rec_id="101",
            start_time="2026-07-27 08:59:58",
            promotion_id="99999999",
            visitor_messages=1,
        )
    ]

    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=candidates,
        client=FakeClient(),
    )

    with pytest.raises(KstServiceError, match="101"):
        service.collect("2026-07-27")


def test_service_loads_different_conversations_with_bounded_parallelism():
    active = 0
    peak = 0
    lock = threading.Lock()
    release = threading.Event()

    class ParallelClient(FakeClient):
        def load_visitor(self, rec_id):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if peak >= 4:
                    release.set()
            release.wait(timeout=1)
            with lock:
                active -= 1
            return {
                "visitorId": f"visitor-{rec_id}",
                "curEnterTime": "2026-07-27 09:00:00",
                "visitorSendNum": 2,
            }

    rec_ids = [str(100 + index) for index in range(8)]
    snapshot_with_eight_allowed_ids = AutomaticSourceSnapshot(
        sources_by_rec_id={
            rec_id: frozenset({"websocket_msg_type_48"})
            for rec_id in rec_ids
        },
        auth=KstAuthContext(),
    )
    eight_candidates = [
        KstCacheCandidate(
            rec_id=rec_id,
            start_time="2026-07-27 09:00:00",
            promotion_id="72828178",
            visitor_messages=2,
        )
        for rec_id in rec_ids
    ]
    service = KstConversationService(
        config=_config(),
        snapshot=snapshot_with_eight_allowed_ids,
        candidates=eight_candidates,
        client=ParallelClient(),
    )

    conversations = service.collect("2026-07-27")

    assert len(conversations) == 8
    assert peak == 4


def test_service_preserves_visitor_then_card_order_per_conversation():
    calls = []
    lock = threading.Lock()

    class OrderedClient(FakeClient):
        def load_visitor(self, rec_id):
            with lock:
                calls.append(("visitor", rec_id))
            return {
                "visitorId": f"visitor-{rec_id}",
                "curEnterTime": "2026-07-27 09:00:00",
                "visitorSendNum": 2,
            }

        def load_card(self, visitor_id):
            rec_id = visitor_id.removeprefix("visitor-")
            with lock:
                calls.append(("card", rec_id))
            return {"cusTypeTag": '{"11":1}'}

    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=OrderedClient(),
    )

    service.collect("2026-07-27")

    assert calls.index(("visitor", "101")) < calls.index(("card", "101"))


def test_parallel_failure_does_not_cache_partial_results():
    successful_conversation_loaded = threading.Event()

    class CoordinatedService(KstConversationService):
        def _load_conversation(
            self,
            candidate,
            tag_map,
            allowed,
            promotion_map,
        ):
            if candidate.rec_id == "202":
                if not successful_conversation_loaded.wait(timeout=1):
                    raise AssertionError(
                        "failing conversation ran before successful conversation"
                    )
            conversation = super()._load_conversation(
                candidate,
                tag_map,
                allowed,
                promotion_map,
            )
            if candidate.rec_id == "101":
                successful_conversation_loaded.set()
            return conversation

    candidates = [
        KstCacheCandidate(
            rec_id="101",
            start_time="2026-07-27 08:59:58",
            promotion_id="72828178",
            visitor_messages=1,
        ),
        KstCacheCandidate(
            rec_id="202",
            start_time="2026-07-27 09:00:00",
            promotion_id="99999999",
            visitor_messages=1,
        ),
    ]
    client = FakeClient(failing_rec_id="202")
    service = CoordinatedService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=candidates,
        client=client,
    )

    with pytest.raises(KstServiceError):
        service.collect("2026-07-27")

    assert successful_conversation_loaded.is_set()
    assert "2026-07-27" not in service._cache
    assert client.visitor_calls.count("101") == 1
    assert client.visitor_calls.count("202") == 1

    successful_conversation_loaded.clear()
    with pytest.raises(KstServiceError):
        service.collect("2026-07-27")

    assert successful_conversation_loaded.is_set()
    assert client.visitor_calls.count("101") == 2
    assert client.visitor_calls.count("202") == 2


def test_service_collect_is_single_flight_for_shared_runtime():
    started = threading.Event()
    release = threading.Event()

    class BlockingClient(FakeClient):
        def load_visitor(self, rec_id):
            self.visitor_calls.append(rec_id)
            if len(self.visitor_calls) == 1:
                started.set()
                release.wait(timeout=2)
            return {
                "visitorId": f"visitor-{rec_id}",
                "curEnterTime": "2026-07-27 09:00:00",
                "visitorSendNum": 2,
            }

    client = BlockingClient()
    service = KstConversationService(
        config=_config(),
        snapshot=_snapshot(),
        candidates=_candidates(),
        client=client,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.collect, "2026-07-27")
        assert started.wait(timeout=1)
        second = executor.submit(service.collect, "2026-07-27")
        time.sleep(0.05)
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2)

    assert client.visitor_calls == ["101"]


def test_service_uses_cached_log_tags_when_live_tag_endpoint_is_unavailable():
    class CachedTagClient(FakeClient):
        def load_tag_dictionary(self):
            raise AssertionError("live tag endpoint must not be called")

    base = _snapshot()
    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id=base.sources_by_rec_id,
        auth=base.auth,
        tag_dictionary={"11": "有效-三句", "12": "转潜-有效"},
    )
    service = KstConversationService(
        config=_config(),
        snapshot=snapshot,
        candidates=_candidates(),
        client=CachedTagClient(),
    )

    conversations = service.collect("2026-07-27")

    assert conversations[0].tags == ("有效-三句", "转潜-有效")


@pytest.mark.parametrize(
    "endpoints",
    [
        {
            "visitor_card": "https://example/card",
            "tag_dictionary": "https://example/tags",
        },
        {
            "visitor_info": "https://example/visitor",
            "tag_dictionary": "https://example/tags",
        },
    ],
)
def test_service_uses_readonly_database_fields_when_visitor_endpoint_is_absent(
    endpoints,
):
    class DatabaseOnlyClient(FakeClient):
        def load_visitor(self, rec_id):
            raise AssertionError("visitor endpoint must not be called")

        def load_card(self, visitor_id):
            raise AssertionError("visitor card endpoint must not be called")

    snapshot = AutomaticSourceSnapshot(
        sources_by_rec_id={
            "101": frozenset({"startup_auto_sync"}),
        },
        auth=KstAuthContext(
            common_query={"compId": "1"},
            headers={"X-Client": "desktop"},
            endpoints=endpoints,
        ),
        tag_dictionary={
            "11": "有效-三句",
            "12": "转潜-有效",
        },
    )
    candidate = KstCacheCandidate(
        rec_id="101",
        start_time="2026-07-27 08:59:58",
        promotion_id="72828178",
        visitor_messages=1,
        tag_ids='{"11":1,"12":1}',
        keyword="测试词",
    )
    service = KstConversationService(
        config=_config(),
        snapshot=snapshot,
        candidates=[candidate],
        client=DatabaseOnlyClient(),
    )

    conversations = service.collect("2026-07-27")

    assert len(conversations) == 1
    assert conversations[0].start_time == "2026-07-27 08:59:58"
    assert conversations[0].promotion_id == "72828178"
    assert conversations[0].visitor_messages == 1
    assert conversations[0].tags == ("有效-三句", "转潜-有效")
