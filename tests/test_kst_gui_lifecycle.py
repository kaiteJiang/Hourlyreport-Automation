from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeKstApiManager(QObject):
    status_changed = Signal(bool, str)
    log_message = Signal(str)

    def __init__(self):
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


def _root():
    return Path(__file__).resolve().parents[1]


def test_window_starts_manager_and_updates_status(qapp):
    fake = FakeKstApiManager()
    window = MainWindow(
        _root(),
        kst_api_manager_factory=lambda *_: fake,
    )

    QApplication.processEvents()

    assert fake.start_calls == 1
    fake.status_changed.emit(True, "ready")
    QApplication.processEvents()
    assert window.kst_status_control.kst_button.property("apiReady") is True
    window.stop_kst_api()
    assert fake.stop_calls == 1
    window.close()


def test_window_stop_is_idempotent(qapp):
    fake = FakeKstApiManager()
    window = MainWindow(
        _root(),
        kst_api_manager_factory=lambda *_: fake,
    )

    window.stop_kst_api()
    window.stop_kst_api()

    assert fake.stop_calls == 1
    window.close()
