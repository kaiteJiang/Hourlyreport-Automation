import json
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


def _set_kst_data_source(root: Path, mode: str) -> None:
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


@pytest.mark.parametrize(
    ("mode", "starts"),
    [("local_api", 1), ("export", 0)],
)
def test_window_starts_manager_only_in_api_mode(
    qapp,
    tmp_path,
    mode,
    starts,
):
    _set_kst_data_source(tmp_path, mode)
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )

    QApplication.processEvents()

    assert fake.start_calls == starts
    window.stop_kst_api()
    window.close()


def test_mode_switch_stops_and_restarts_manager(qapp, tmp_path):
    _set_kst_data_source(tmp_path, "local_api")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    QApplication.processEvents()

    window.set_global_kst_data_source("export")
    window.set_global_kst_data_source("local_api")

    assert (fake.stop_calls, fake.start_calls) == (1, 2)
    window.stop_kst_api()
    window.close()


def test_switching_to_export_clears_green_api_status(qapp, tmp_path):
    _set_kst_data_source(tmp_path, "local_api")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    QApplication.processEvents()
    fake.status_changed.emit(True, "ready")
    QApplication.processEvents()
    assert window.kst_status_control.kst_button.property("apiReady") is True

    window.set_global_kst_data_source("export")

    assert window.kst_status_control.kst_button.property("apiReady") is False
    window.close()


def test_mode_save_failure_does_not_change_manager_lifecycle(
    qapp,
    tmp_path,
    monkeypatch,
):
    _set_kst_data_source(tmp_path, "local_api")
    fake = FakeKstApiManager()
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: fake,
    )
    QApplication.processEvents()
    monkeypatch.setattr(
        "gui.main_window.set_kst_data_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("save failed")
        ),
    )

    window.set_global_kst_data_source("export")

    assert window.kst_data_source == "local_api"
    assert (fake.stop_calls, fake.start_calls) == (0, 1)
    assert window.kst_api_action.isChecked() is True
    window.stop_kst_api()
    window.close()
