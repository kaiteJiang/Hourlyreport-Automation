import json
import subprocess
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication

from gui.kst_api_manager import KstApiManager
from gui.main_window import MainWindow


_worker_entered = threading.Event()


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _write_local_api_config(root):
    config_dir = root / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "app_config.json").write_text(
        json.dumps(
            {
                "default_project_id": "demo",
                "projects_dir": "configs/projects",
                "secrets_file": "secrets/secrets.json",
                "kst_data_source": "local_api",
            }
        ),
        encoding="utf-8",
    )


def test_main_window_leaves_delayed_real_kst_manager_for_fixture_cleanup(
    tmp_path,
):
    _write_local_api_config(tmp_path)

    class DelayedRegistry:
        def __init__(self, _root):
            pass

        def refresh(self):
            _worker_entered.set()
            time.sleep(0.4)
            subprocess.Popen(["old-kst-worker"])

    manager = KstApiManager(
        tmp_path,
        probe=lambda *_: False,
        registry_factory=DelayedRegistry,
    )
    window = MainWindow(
        tmp_path,
        kst_api_manager_factory=lambda *_: manager,
    )

    QApplication.processEvents()

    assert _worker_entered.wait(timeout=1)
    assert window.kst_api_manager is manager


def test_previous_kst_worker_cannot_reach_next_test_popen_patch(monkeypatch):
    commands = []

    class Process:
        pass

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **_kwargs: commands.append(command) or Process(),
    )

    time.sleep(0.6)

    assert commands == []
