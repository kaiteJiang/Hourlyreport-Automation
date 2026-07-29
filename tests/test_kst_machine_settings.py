from pathlib import Path

import pytest

from modules.kst_local.machine_settings import (
    KstMachineSettingsError,
    load_kst_machine_settings,
    save_kst_machine_settings,
)


def test_machine_settings_round_trip_uses_runtime_only(tmp_path):
    saved = save_kst_machine_settings(
        tmp_path,
        installation_root=r"D:\KST\OnlineCustomerService",
        data_root=r"D:\Documents\KuaiShangDataNew",
    )

    assert saved.installation_root == Path(r"D:\KST\OnlineCustomerService")
    assert load_kst_machine_settings(tmp_path) == saved
    assert not (tmp_path / "configs" / "app_config.json").exists()


def test_invalid_machine_settings_json_fails_closed(tmp_path):
    path = tmp_path / "runtime" / "kst_machine_settings.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(KstMachineSettingsError, match="本机路径设置"):
        load_kst_machine_settings(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        '{"unexpected": "value"}',
        '{"installation_root": 42}',
    ],
)
def test_machine_settings_rejects_unknown_or_non_string_values(tmp_path, payload):
    path = tmp_path / "runtime" / "kst_machine_settings.json"
    path.parent.mkdir()
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(KstMachineSettingsError, match="本机路径设置"):
        load_kst_machine_settings(tmp_path)


def test_machine_settings_normalizes_empty_strings_to_none(tmp_path):
    saved = save_kst_machine_settings(
        tmp_path,
        installation_root="",
        data_root="",
    )

    assert saved.installation_root is None
    assert saved.data_root is None
