from modules.kst_local.api_client import KstApiClient, KstApiError
from modules.kst_local.models import KstAuthContext


def _auth(secret: str = "secret-token") -> KstAuthContext:
    return KstAuthContext(
        common_query={"compId": "733875", "clientToken": secret},
        headers={"clientToken": secret, "X-Client": "desktop"},
        endpoints={
            "visitor_info": "https://chat.example/OnlineHd/visitorInfo/load",
            "visitor_card": (
                "https://chat.example/OnlineCore/nv2012/func/ocVisitorCard/detail.do"
            ),
            "tag_dictionary": (
                "https://chat.example/OnlineCore/nv/visitorCard/custTypeQuery.do"
            ),
        },
    )


def test_client_posts_common_params_and_parses_payloads():
    calls = []

    def transport(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        if url.endswith("/visitorInfo/load"):
            return {
                "bean": {
                    "visitorId": "v-1",
                    "curEnterTime": "2026-07-27 09:00:00",
                    "visitorSendNum": 2,
                }
            }
        if url.endswith("/ocVisitorCard/detail.do"):
            return {"bean": {"cusTypeTag": '{"11":1}'}}
        return {"bean": [{"typeid": 11, "typename": "有效-三句"}]}

    client = KstApiClient(_auth(), transport=transport)

    visitor = client.load_visitor("101")
    card = client.load_card("v-1")
    tags = client.load_tag_dictionary()

    assert visitor["visitorId"] == "v-1"
    assert card["cusTypeTag"] == '{"11":1}'
    assert tags == {"11": "有效-三句"}
    assert calls[0][1]["compId"] == "733875"
    assert calls[0][1]["recId"] == "101"
    assert calls[0][1]["channelType"] == "online"
    assert calls[0][3] == 30


def test_client_error_never_contains_auth_secret():
    secret = "must-not-leak"

    def transport(url, params, headers, timeout):
        raise RuntimeError(f"network failed with {params['clientToken']}")

    client = KstApiClient(_auth(secret), transport=transport)

    try:
        client.load_visitor("101")
    except KstApiError as exc:
        assert secret not in str(exc)
        assert "visitor_info" in str(exc)
    else:
        raise AssertionError("KstApiError was not raised")
