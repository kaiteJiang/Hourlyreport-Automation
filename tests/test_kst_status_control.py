import pytest
from PySide6.QtWidgets import QApplication

from gui.kst_status_control import KstStatusControl


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_status_control_exact_text_and_colors(qapp):
    control = KstStatusControl()

    assert control.kst_button.text() == "● KST"
    assert control.live_label.text() == "● 实时"
    control.set_api_ready(False, "未启动")
    assert control.kst_button.property("apiReady") is False
    assert "#9aa5b1" in control.kst_button.styleSheet()
    control.set_api_ready(True, "127.0.0.1:18766")
    assert control.kst_button.property("apiReady") is True
    assert "#34c759" in control.kst_button.styleSheet()


def test_status_control_emits_exclusive_source_mode(qapp):
    control = KstStatusControl()
    values = []
    control.source_selected.connect(values.append)

    control.manual_action.trigger()
    control.api_action.trigger()

    assert values == ["export", "local_api"]
    assert control.api_action.isChecked()
    assert not control.manual_action.isChecked()
