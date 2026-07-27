from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from gui.branding import PRODUCT_DISPLAY_NAME
from gui.font_manager import load_private_ui_font
from gui.main_window import create_window
from gui.single_instance import SingleInstanceGuard
from gui.update_manager import UPDATE_HELPER_MODE, run_update_helper_from_argv


GUI_SCALE_FACTOR = "1.0"
WINDOWS_APP_ID_PREFIX = "HourlyreportAutomation.Console"


class IncompleteInstallationError(RuntimeError):
    """Raised when the GUI is launched without its required companion files."""


def _app_root_candidates() -> list[Path]:
    candidates = [Path.cwd()]
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend([executable_dir, executable_dir.parent])
    candidates.append(Path(__file__).resolve().parents[1])
    return candidates


def resolve_app_root(candidates: list[Path] | None = None) -> Path:
    search_candidates = list(candidates) if candidates is not None else _app_root_candidates()

    for base in search_candidates:
        for path in [base, *base.parents]:
            if (
                (path / "main.py").is_file()
                and (path / "configs" / "app_config.json").is_file()
                and (path / "configs" / "projects").is_dir()
            ):
                return path

    if any(path.name.lower().startswith("hourlyreport_automation_v") for path in search_candidates):
        raise IncompleteInstallationError(
            "当前目录是在线更新包，不能单独用于新电脑首次安装。请改用首次安装包完整解压后再运行。"
        )
    raise IncompleteInstallationError(
        "程序文件不完整，缺少应用或项目配置。请重新完整解压首次安装包后再运行。"
    )


def show_startup_error(message: str) -> None:
    os.environ["QT_SCALE_FACTOR"] = GUI_SCALE_FACTOR
    app = QApplication.instance() or QApplication(list(sys.argv))
    QMessageBox.critical(None, f"无法启动{PRODUCT_DISPLAY_NAME}", message)
    app.quit()


def windows_app_user_model_id(root: str | Path) -> str:
    root_path = Path(root)
    # Follow the runtime icon so Windows refreshes the taskbar cache when it changes.
    icon = root_path / "assets" / "app_icon.png"
    if not icon.exists():
        icon = root_path / "assets" / "app_icon.ico"
    icon_bytes = icon.read_bytes() if icon.exists() else b"default-icon"
    digest = hashlib.sha256(icon_bytes).hexdigest()[:12]
    return f"{WINDOWS_APP_ID_PREFIX}.{digest}"


def configure_windows_app_identity(root: str | Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        return setter(windows_app_user_model_id(root)) == 0
    except (AttributeError, OSError):
        return False


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == UPDATE_HELPER_MODE:
        return run_update_helper_from_argv(list(sys.argv))
    try:
        root = resolve_app_root()
    except IncompleteInstallationError as exc:
        show_startup_error(str(exc))
        return 2
    os.environ["QT_SCALE_FACTOR"] = GUI_SCALE_FACTOR
    configure_windows_app_identity(root)
    app = QApplication(sys.argv)
    load_private_ui_font(root)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(PRODUCT_DISPLAY_NAME)
    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        instance_guard.notify_existing()
        return 0
    icon = root / "assets" / "app_icon.png"
    if not icon.exists():
        icon = root / "assets" / "app_icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = create_window(root)
    instance_guard.activate_requested.connect(window.show_console)
    app.aboutToQuit.connect(window.stop_kst_api)
    app.aboutToQuit.connect(instance_guard.close)
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
