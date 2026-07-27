from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


class FakeKstApiManager(QObject):
    status_changed = Signal(bool, str)
    log_message = Signal(str)

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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

    window.stop_kst_api()
    window.close()
