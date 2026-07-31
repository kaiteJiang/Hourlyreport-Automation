from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from gui.main_window import InlineConfigMenu, MainWindow
from modules.kst_local.machine_settings import (
    load_kst_machine_settings,
    save_kst_machine_settings,
)


class FakeKstApiManager(QObject):
    status_changed = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self):
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0
        self.rescan_calls = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    def rescan(self):
        self.rescan_calls += 1


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _write_app_config(root: Path, mode: str) -> None:
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "app_config.json").write_text(
        json.dumps(
            {
                "default_project_id": "demo",
                "projects_dir": "configs/projects",
                "secrets_file": "secrets/secrets.json",
                "kst_data_source": mode,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_system_menu_contains_global_kst_submenu(qapp):
    root = Path(__file__).resolve().parents[1]
    window = MainWindow(
        root,
        kst_api_manager_factory=lambda *_: FakeKstApiManager(),
    )

    assert window.kst_mode_menu.title() == "快商通模式"
    assert window.kst_api_action.text() == "API 自动获取"
    assert window.kst_export_action.text() == "人工导出对话"
    assert window.kst_api_action.actionGroup().isExclusive()
    assert window.kst_mode_menu.styleSheet() == window.excel_auto_open_menu.styleSheet()
    assert window.inline_config_menu.kst_mode_toggle.text() == "快商通模式"
    assert window.inline_config_menu.kst_api_choice.text() == "API 自动获取"
    assert window.inline_config_menu.kst_export_choice.text() == "人工导出对话"
    assert (
        window.inline_config_menu.kst_installation_root_choice.text()
        == "选择快商通程序目录"
    )
    assert (
        window.inline_config_menu.kst_data_root_choice.text()
        == "选择快商通数据目录"
    )
    assert window.inline_config_menu.kst_rescan_choice.text() == "重新扫描快商通"
    assert [
        "---" if action.isSeparator() else action.text()
        for action in window.kst_mode_menu.actions()
    ] == [
        "API 自动获取",
        "人工导出对话",
        "---",
        "选择快商通程序目录",
        "选择快商通数据目录",
        "重新扫描快商通",
    ]

    window.stop_kst_api()
    window.close()


def test_inline_kst_path_controls_emit_requests_and_keep_rescan_available(
    qapp,
):
    menu = InlineConfigMenu()
    events = []
    menu.kst_installation_root_requested.connect(
        lambda: events.append("installation")
    )
    menu.kst_data_root_requested.connect(
        lambda: events.append("data")
    )
    menu.kst_rescan_requested.connect(
        lambda: events.append("rescan")
    )

    menu.sync("hidden", 1.0, False, "local_api")
    menu.kst_installation_root_choice.click()
    menu.kst_data_root_choice.click()
    menu.kst_rescan_choice.click()

    assert events == ["installation", "data", "rescan"]
    assert menu.kst_rescan_choice.isEnabled() is True

    menu.sync("hidden", 1.0, False, "export")

    assert menu.kst_rescan_choice.isEnabled() is True
    menu.close()


def test_path_failure_does_not_open_directory_dialog(
    qapp,
    tmp_path,
):
    _write_app_config(tmp_path, "local_api")
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: FakeKstApiManager(),
    )
    prompts = []
    window.choose_kst_installation_root = lambda: prompts.append(
        "installation"
    )
    window.choose_kst_data_root = lambda: prompts.append("data")

    window.on_kst_api_status_changed(
        False,
        "快商通客户端目录无效",
    )
    window.on_kst_api_status_changed(
        False,
        "快商通客户端目录无效",
    )
    window.on_kst_api_status_changed(
        False,
        "快商通数据目录无效",
    )

    assert prompts == []
    window.stop_kst_api()
    window.close()


def test_database_timeout_does_not_open_path_dialog(
    qapp,
    tmp_path,
):
    _write_app_config(tmp_path, "local_api")
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: FakeKstApiManager(),
    )
    prompts = []
    window.choose_kst_installation_root = lambda: prompts.append(
        "installation"
    )
    window.choose_kst_data_root = lambda: prompts.append("data")

    window.on_kst_api_status_changed(
        False,
        "数据库忙或读取超时",
    )

    assert prompts == []
    window.stop_kst_api()
    window.close()


def test_kst_path_dialog_cancel_keeps_machine_settings(
    qapp,
    tmp_path,
    monkeypatch,
):
    _write_app_config(tmp_path, "local_api")
    original_installation = tmp_path / "original-program"
    original_data = tmp_path / "original-data"
    save_kst_machine_settings(
        tmp_path,
        installation_root=original_installation,
        data_root=original_data,
    )
    settings_path = tmp_path / "runtime" / "kst_machine_settings.json"
    original_payload = settings_path.read_text(encoding="utf-8")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: "",
    )

    window.choose_kst_installation_root()

    assert settings_path.read_text(encoding="utf-8") == original_payload
    assert fake.rescan_calls == 0
    window.stop_kst_api()
    window.close()


def test_kst_path_selections_preserve_the_other_setting_and_rescan(
    qapp,
    tmp_path,
    monkeypatch,
):
    _write_app_config(tmp_path, "local_api")
    original_installation = tmp_path / "original-program"
    original_data = tmp_path / "original-data"
    new_installation = tmp_path / "new-program"
    new_data = tmp_path / "new-data"
    save_kst_machine_settings(
        tmp_path,
        installation_root=original_installation,
        data_root=original_data,
    )
    selected = iter((str(new_installation), str(new_data)))
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: next(selected),
    )
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )

    window.choose_kst_installation_root()
    after_installation = load_kst_machine_settings(tmp_path)
    window.choose_kst_data_root()
    after_data = load_kst_machine_settings(tmp_path)

    assert after_installation.installation_root == new_installation
    assert after_installation.data_root == original_data
    assert after_data.installation_root == new_installation
    assert after_data.data_root == new_data
    assert fake.rescan_calls == 2
    window.stop_kst_api()
    window.close()


def test_kst_machine_settings_save_failure_keeps_file_and_skips_rescan(
    qapp,
    tmp_path,
    monkeypatch,
):
    _write_app_config(tmp_path, "local_api")
    original_installation = tmp_path / "original-program"
    original_data = tmp_path / "original-data"
    save_kst_machine_settings(
        tmp_path,
        installation_root=original_installation,
        data_root=original_data,
    )
    settings_path = tmp_path / "runtime" / "kst_machine_settings.json"
    original_payload = settings_path.read_text(encoding="utf-8")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(tmp_path / "new-program"),
    )
    monkeypatch.setattr(
        "gui.main_window.save_kst_machine_settings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("save failed")
        ),
    )

    window.choose_kst_installation_root()

    assert settings_path.read_text(encoding="utf-8") == original_payload
    assert fake.rescan_calls == 0
    window.stop_kst_api()
    window.close()


def test_export_mode_rescan_switches_to_api_and_scans_once(
    qapp,
    tmp_path,
):
    _write_app_config(tmp_path, "export")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    QApplication.processEvents()

    assert window.kst_rescan_action.isEnabled() is True
    window.rescan_kst_api()
    assert window.kst_data_source == "local_api"
    assert (fake.start_calls, fake.rescan_calls) == (1, 1)

    window.close()
