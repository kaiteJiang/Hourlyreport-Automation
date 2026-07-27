import json
from pathlib import Path

import pytest

from modules.kst_local.source import KstLocalSourceError, fetch_kst_local_report


def _config(url="http://127.0.0.1:18766"):
    return {
        "project_id": "kunming_niu",
        "project_name": "昆明牛",
        "kst": {
            "local_api_url": url,
            "local_api_token_env": "TEST_KST_TOKEN",
        },
    }


def test_source_fetches_loopback_report_and_writes_existing_shape(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TEST_KST_TOKEN", "secret")
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, headers, timeout))
        return {
            "project_id": "kunming_niu",
            "project_name": "昆明牛",
            "date": "2026-07-27",
            "period": "15点",
            "source": "kst_local_api",
            "accounts": {"银康01": {"总对话": 1}},
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
    assert calls[0][1]["Authorization"] == "Bearer secret"
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
