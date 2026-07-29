import threading
import time

import pytest
from PySide6.QtWidgets import QApplication


_KST_THREAD_NAMES = {
    "kst-api-manager",
    "kst-api-server",
    "kst-api-shutdown",
}
_KST_CLEANUP_TIMEOUT_SECONDS = 6.0


def _active_kst_threads():
    return [
        thread
        for thread in threading.enumerate()
        if thread is not threading.current_thread()
        and thread.name in _KST_THREAD_NAMES
        and thread.is_alive()
    ]


@pytest.fixture(autouse=True)
def cleanup_qt_kst_lifecycle():
    yield

    app = QApplication.instance()
    if app is None:
        return

    manager_windows = []
    managers = {}
    for widget in QApplication.topLevelWidgets():
        manager = getattr(widget, "kst_api_manager", None)
        if manager is None:
            continue
        managers[id(manager)] = manager
        manager_windows.append(widget)

    for manager in managers.values():
        manager.stop()

    for window in manager_windows:
        window._application_exiting = True
        window._quitting = True
        tray_icon = getattr(window, "tray_icon", None)
        if tray_icon is not None:
            tray_icon.hide()
        desktop_pet = getattr(window, "desktop_pet", None)
        if desktop_pet is not None:
            desktop_pet.close_pet()
        window.close()

    deadline = time.monotonic() + _KST_CLEANUP_TIMEOUT_SECONDS
    while True:
        app.processEvents()
        active = _active_kst_threads()
        if not active:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            names = ", ".join(thread.name for thread in active)
            pytest.fail(f"测试结束后快商通后台线程未退出：{names}")
        for thread in active:
            thread.join(timeout=min(0.02, remaining))

    app.processEvents()
