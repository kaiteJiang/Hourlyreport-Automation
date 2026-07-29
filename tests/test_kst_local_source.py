import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from modules.kst_local.source import (
    KstLocalSourceError,
    fetch_kst_local_daily_report,
    fetch_kst_local_report,
)


def _config(url="http://127.0.0.1:18766"):
    return {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "accounts": {
            "银康01": {"excel_name": "银康01"},
            "银康银屑02": {"excel_name": "银康银屑02"},
            "银康03": {"excel_name": "银康03"},
        },
        "kst": {
            "local_api_url": url,
            "local_api_token_env": "KST_LOCAL_API_TOKEN",
        },
    }


def test_source_fetches_loopback_report_and_writes_existing_shape(
    tmp_path,
    monkeypatch,
):
    token = "source-test-token-with-more-than-32-characters"
    monkeypatch.setenv("KST_LOCAL_API_TOKEN", token)
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, headers, timeout))
        return {
            "project_id": "kunming_niu",
            "project_name": "昆明牛",
            "date": "2026-07-27",
            "period": "15点",
            "source": "kst_local_api",
            "accounts": {
                account: {
                    "总对话": 1 if account == "银康01" else 0,
                    "有效对话": 0,
                    "一般有效": 0,
                    "有效转潜": 0,
                    "总转潜": 0,
                }
                for account in ("银康01", "银康银屑02", "银康03")
            },
            "summary": {"automatic_rows": 1},
            "errors": [],
        }

    result = fetch_kst_local_report(
        _config(),
        tmp_path,
        "15点",
        target_date="2026-07-27",
        transport=transport,
    )

    assert calls[0][0].startswith(
        "http://127.0.0.1:18766/v1/kst/hourly?"
    )
    assert parse_qs(urlparse(calls[0][0]).query)["project_id"] == [
        "kunming_niu"
    ]
    assert calls[0][1]["Authorization"] == f"Bearer {token}"
    assert calls[0][2] == 15
    assert result["parse_report"]["passed"] is True
    output = tmp_path / "reports" / "kst_dialog_data.json"
    assert json.loads(output.read_text(encoding="utf-8"))["source"] == "kst_local_api"


def test_source_rejects_non_loopback_url(tmp_path):
    with pytest.raises(KstLocalSourceError, match="回环"):
        fetch_kst_local_report(
            _config("http://192.168.1.10:18766"),
            tmp_path,
            "15点",
            transport=lambda *args: {},
        )


def test_source_rejects_alternate_loopback_port(tmp_path):
    with pytest.raises(KstLocalSourceError, match="18766"):
        fetch_kst_local_report(
            _config("http://127.0.0.1:19999"),
            tmp_path,
            "15点",
            transport=lambda *args: {},
        )


def test_api_unavailable_can_emit_zero_kst_report(tmp_path):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True

    result = fetch_kst_local_report(
        config,
        tmp_path,
        "15点",
        target_date="2026-07-27",
        transport=lambda *_: (_ for _ in ()).throw(OSError("offline")),
    )

    assert result["parse_report"]["passed"] is True
    assert result["dialog_data"]["source"] == "kst_local_api_unavailable_zero"
    assert result["dialog_data"]["summary"]["api_unavailable"] is True
    assert all(
        value == 0
        for account in result["dialog_data"]["accounts"].values()
        for value in account.values()
    )


def test_api_unavailable_still_raises_without_opt_in(tmp_path):
    with pytest.raises(KstLocalSourceError, match="请求失败"):
        fetch_kst_local_report(
            _config(),
            tmp_path,
            "15点",
            transport=lambda *_: (_ for _ in ()).throw(OSError("offline")),
        )


def test_incomplete_api_accounts_use_zero_fallback_when_opted_in(tmp_path):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True

    result = fetch_kst_local_report(
        config,
        tmp_path,
        "15点",
        transport=lambda *_: {
            "source": "kst_local_api",
            "accounts": {},
            "errors": [],
        },
    )

    assert result["dialog_data"]["source"] == "kst_local_api_unavailable_zero"
    assert result["dialog_data"]["summary"]["api_unavailable"] is True


def test_response_for_another_project_is_rejected_and_zeroed(tmp_path):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True

    result = fetch_kst_local_report(
        config,
        tmp_path,
        "15点",
        transport=lambda *_: {
            "project_id": "another_project",
            "source": "kst_local_api",
            "accounts": {
                account: {
                    "总对话": 99,
                    "有效对话": 99,
                    "一般有效": 99,
                    "有效转潜": 99,
                    "总转潜": 99,
                }
                for account in config["accounts"]
            },
            "errors": [],
        },
    )

    assert result["dialog_data"]["source"] == "kst_local_api_unavailable_zero"
    assert all(
        value == 0
        for account in result["dialog_data"]["accounts"].values()
        for value in account.values()
    )


@pytest.mark.parametrize(
    ("response_date", "response_period"),
    [
        ("2026-07-26", "15点"),
        ("2026-07-27", "18点"),
    ],
)
def test_hourly_response_date_and_period_must_match_request(
    tmp_path,
    response_date,
    response_period,
):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True

    result = fetch_kst_local_report(
        config,
        tmp_path,
        "15点",
        target_date="2026-07-27",
        transport=lambda *_: {
            "project_id": "kunming_niu",
            "date": response_date,
            "period": response_period,
            "source": "kst_local_api",
            "accounts": {
                account: {
                    "总对话": 99,
                    "有效对话": 0,
                    "一般有效": 0,
                    "有效转潜": 0,
                    "总转潜": 0,
                }
                for account in config["accounts"]
            },
            "errors": [],
        },
    )

    assert result["dialog_data"]["source"] == "kst_local_api_unavailable_zero"


def _daily_accounts(total=0):
    return {
        account: {
            "总对话": total if account == "银康01" else 0,
            "有效对话": total if account == "银康01" else 0,
            "无效对话": 0,
            "一般有效对话": 0,
            "有效转潜": 0,
            "总转潜": 0,
        }
        for account in ("银康01", "银康银屑02", "银康03")
    }


def test_daily_source_fetches_project_report_and_writes_daily_contract(
    tmp_path,
):
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, headers, timeout))
        return {
            "project_id": "kunming_niu",
            "project_name": "昆明牛",
            "date": "2026-07-26",
            "source": "kst_local_api",
            "accounts": _daily_accounts(total=1),
            "summary": {"automatic_rows": 1},
            "errors": [],
        }

    result = fetch_kst_local_daily_report(
        _config(),
        tmp_path,
        target_date="2026-07-26",
        transport=transport,
    )

    assert urlparse(calls[0][0]).path == "/v1/kst/daily"
    assert parse_qs(urlparse(calls[0][0]).query) == {
        "project_id": ["kunming_niu"],
        "date": ["2026-07-26"],
    }
    assert result["parse_report"]["passed"] is True
    assert result["daily_data"]["accounts"]["银康01"]["总对话"] == 1
    output = tmp_path / "reports" / "kst_daily_data.json"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["source"] == "kst_local_api"
    assert saved["project_id"] == "kunming_niu"
    assert calls[0][2] == 15


@pytest.mark.parametrize(
    ("configured_timeout", "expected_timeout"),
    [(99, 15), (0, 1), (-10, 1)],
)
def test_hourly_source_clamps_configured_timeout(
    tmp_path,
    configured_timeout,
    expected_timeout,
):
    config = _config()
    config["kst"]["local_api_timeout_seconds"] = configured_timeout
    recorded_timeouts = []

    fetch_kst_local_report(
        config,
        tmp_path,
        "15点",
        target_date="2026-07-27",
        transport=lambda _url, _headers, timeout: (
            recorded_timeouts.append(timeout)
            or {
                "project_id": "kunming_niu",
                "date": "2026-07-27",
                "period": "15点",
                "source": "kst_local_api",
                "accounts": {
                    account: {
                        "总对话": 0,
                        "有效对话": 0,
                        "一般有效": 0,
                        "有效转潜": 0,
                        "总转潜": 0,
                    }
                    for account in config["accounts"]
                },
                "errors": [],
            }
        ),
    )

    assert recorded_timeouts == [expected_timeout]


@pytest.mark.parametrize(
    ("configured_timeout", "expected_timeout"),
    [(99, 15), (0, 1), (-10, 1)],
)
def test_daily_source_clamps_configured_timeout(
    tmp_path,
    configured_timeout,
    expected_timeout,
):
    config = _config()
    config["kst"]["local_api_timeout_seconds"] = configured_timeout
    recorded_timeouts = []

    fetch_kst_local_daily_report(
        config,
        tmp_path,
        target_date="2026-07-26",
        transport=lambda _url, _headers, timeout: (
            recorded_timeouts.append(timeout)
            or {
                "project_id": "kunming_niu",
                "date": "2026-07-26",
                "source": "kst_local_api",
                "accounts": _daily_accounts(),
                "errors": [],
            }
        ),
    )

    assert recorded_timeouts == [expected_timeout]


def test_daily_response_for_another_project_is_zeroed_when_opted_in(
    tmp_path,
):
    config = _config()
    config["kst"]["allow_zero_on_unavailable"] = True

    result = fetch_kst_local_daily_report(
        config,
        tmp_path,
        target_date="2026-07-26",
        transport=lambda *_: {
            "project_id": "another_project",
            "date": "2026-07-26",
            "source": "kst_local_api",
            "accounts": _daily_accounts(total=99),
            "errors": [],
        },
    )

    assert result["daily_data"]["source"] == "kst_local_api_unavailable_zero"
    assert result["daily_data"]["summary"]["api_unavailable"] is True
    assert all(
        value == 0
        for account in result["daily_data"]["accounts"].values()
        for value in account.values()
    )
