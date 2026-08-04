from __future__ import annotations

import os
import json
import re
import shutil
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCalendarWidget,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.branding import PRODUCT_DISPLAY_NAME, WINDOW_HEADER_TITLE
from gui.clawd import ClawdAnimator
from gui.command_builder import (
    build_daily_command,
    build_hourly_command,
    build_multi_daily_command,
    build_multi_hourly_command,
    build_preflight_command,
    build_word_class_command,
)
from gui.desktop_pet import ClawdDesktopPet
from gui.excel_open_settings import load_auto_open_excel, save_auto_open_excel
from gui.environment_check import (
    environment_repair_command,
    initialize_kst_directories_once,
    run_environment_check,
)
from gui.log_formatter import format_log_fragment
from gui.log_history import append_history_line, typewriter_batch_size
from gui.kst_status_control import KstStatusControl
from gui.kst_api_manager import KstApiManager
from gui.pet_settings import (
    PET_CLAWD,
    PET_HIDDEN,
    normalize_pet_scale,
    load_pet_mode,
    load_pet_position,
    load_pet_scale,
    save_pet_mode,
    save_pet_position,
    save_pet_scale,
)
from gui.project_store import ProjectSummary, load_project_summaries
from gui.task_runner import QtTaskRunner, infer_pet_event, should_warn_no_output
from modules.task_stop_gate import (
    STOP_GATE_ENV,
    TASK_CANCELLED_EXIT_CODE,
    clear_task_stop_gate,
    request_task_stop,
)
from modules.multi_project_stop import MULTI_QUEUE_STOP_GATE_ENV
from modules.multi_project_selection import load_multi_project_selection, save_multi_project_selection
from gui.update_manager import APP_EXE_NAME, GitHubUpdateManager, ReleaseUpdate, launch_update_helper
from gui.version import CURRENT_VERSION
from modules.project_config import (
    get_data_source_preference,
    get_excel_path,
    get_kst_data_source,
    get_word_share_path,
    load_project_config,
    set_kst_data_source,
    set_data_source_preference as save_data_source_preference,
)
from modules.excel_path_config import EXCEL_ROOT_NAME, configure_excel_paths
from modules.kst_local.machine_settings import (
    load_kst_machine_settings,
    save_kst_machine_settings,
)
from modules.secrets_package import (
    SecretsPackageError,
    export_secrets_package,
    import_secrets_package,
)


STAGES = [
    ("environment", "环境检测", "shield"),
    ("config", "项目配置", "gear"),
    ("preflight", "快速自检", "stethoscope"),
    ("login", "登录账号", "login"),
    ("baidu", "百度数据", "paw"),
    ("kst", "快商通数据", "chat"),
    ("excel", "Excel写入", "sheet"),
    ("done", "报告输出", "report"),
]
TITLE_FONT_PT = 10
MAIN_FONT_PT = 9
SUB_FONT_PT = 8
FONT_LIGHT_FAMILY = "Microsoft YaHei UI"
FONT_REGULAR_FAMILY = "Microsoft YaHei UI"
FONT_TITLE_FAMILY = "Microsoft YaHei"
FONT_FAMILY = FONT_REGULAR_FAMILY
FONT_STACK = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
FONT_MENU_STACK = FONT_STACK
FONT_REGULAR_STACK = FONT_STACK
FONT_TITLE_STACK = '"Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif'
BASE_WINDOW_SIZE = QSize(966, 700)


def app_icon_path(root: str | Path) -> Path:
    png = Path(root) / "assets" / "app_icon.png"
    if png.exists():
        return png
    return Path(root) / "assets" / "app_icon.ico"


def make_line_icon(kind: str, color: str = "#087a46", size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), max(1.6, size / 13), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)

    if kind == "shield":
        points = [
            QPoint(round(s * 0.50), round(s * 0.12)),
            QPoint(round(s * 0.82), round(s * 0.25)),
            QPoint(round(s * 0.76), round(s * 0.66)),
            QPoint(round(s * 0.50), round(s * 0.88)),
            QPoint(round(s * 0.24), round(s * 0.66)),
            QPoint(round(s * 0.18), round(s * 0.25)),
        ]
        painter.drawPolygon(points)
        painter.drawLine(round(s * 0.36), round(s * 0.50), round(s * 0.46), round(s * 0.61))
        painter.drawLine(round(s * 0.46), round(s * 0.61), round(s * 0.66), round(s * 0.39))
    elif kind == "gear":
        painter.drawEllipse(QRectF(s * 0.34, s * 0.34, s * 0.32, s * 0.32))
        for x1, y1, x2, y2 in [(0.50, 0.10, 0.50, 0.24), (0.50, 0.76, 0.50, 0.90), (0.10, 0.50, 0.24, 0.50), (0.76, 0.50, 0.90, 0.50), (0.22, 0.22, 0.32, 0.32), (0.68, 0.68, 0.78, 0.78), (0.78, 0.22, 0.68, 0.32), (0.32, 0.68, 0.22, 0.78)]:
            painter.drawLine(round(s * x1), round(s * y1), round(s * x2), round(s * y2))
    elif kind == "stethoscope":
        painter.drawArc(QRectF(s * 0.18, s * 0.16, s * 0.36, s * 0.42), 180 * 16, 180 * 16)
        painter.drawLine(round(s * 0.18), round(s * 0.36), round(s * 0.18), round(s * 0.54))
        painter.drawLine(round(s * 0.54), round(s * 0.36), round(s * 0.54), round(s * 0.54))
        painter.drawLine(round(s * 0.36), round(s * 0.58), round(s * 0.36), round(s * 0.72))
        painter.drawLine(round(s * 0.36), round(s * 0.72), round(s * 0.68), round(s * 0.72))
        painter.drawEllipse(QRectF(s * 0.66, s * 0.64, s * 0.16, s * 0.16))
    elif kind == "loop":
        painter.drawArc(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64), 35 * 16, 285 * 16)
        painter.drawLine(round(s * 0.30), round(s * 0.29), round(s * 0.18), round(s * 0.30))
        painter.drawLine(round(s * 0.30), round(s * 0.29), round(s * 0.27), round(s * 0.42))
    elif kind == "download":
        painter.drawLine(round(s * 0.50), round(s * 0.18), round(s * 0.50), round(s * 0.62))
        painter.drawLine(round(s * 0.34), round(s * 0.48), round(s * 0.50), round(s * 0.64))
        painter.drawLine(round(s * 0.66), round(s * 0.48), round(s * 0.50), round(s * 0.64))
        painter.drawLine(round(s * 0.25), round(s * 0.80), round(s * 0.75), round(s * 0.80))
    elif kind == "flow":
        painter.drawRoundedRect(QRectF(s * 0.20, s * 0.22, s * 0.60, s * 0.46), s * 0.08, s * 0.08)
        painter.drawLine(round(s * 0.34), round(s * 0.38), round(s * 0.62), round(s * 0.38))
        painter.drawLine(round(s * 0.34), round(s * 0.52), round(s * 0.54), round(s * 0.52))
        painter.drawLine(round(s * 0.58), round(s * 0.74), round(s * 0.74), round(s * 0.74))
        painter.drawLine(round(s * 0.74), round(s * 0.74), round(s * 0.74), round(s * 0.58))
        painter.drawLine(round(s * 0.66), round(s * 0.58), round(s * 0.74), round(s * 0.58))
        painter.drawLine(round(s * 0.74), round(s * 0.58), round(s * 0.82), round(s * 0.66))
    elif kind == "login":
        painter.drawEllipse(QRectF(s * 0.18, s * 0.15, s * 0.28, s * 0.28))
        painter.drawArc(QRectF(s * 0.10, s * 0.48, s * 0.44, s * 0.34), 25 * 16, 130 * 16)
        painter.drawLine(round(s * 0.58), round(s * 0.50), round(s * 0.88), round(s * 0.50))
        painter.drawLine(round(s * 0.76), round(s * 0.38), round(s * 0.88), round(s * 0.50))
        painter.drawLine(round(s * 0.76), round(s * 0.62), round(s * 0.88), round(s * 0.50))
    elif kind == "paw":
        painter.drawEllipse(QRectF(s * 0.34, s * 0.42, s * 0.32, s * 0.34))
        for x, y in [(0.22, 0.24), (0.40, 0.16), (0.58, 0.16), (0.76, 0.24)]:
            painter.drawEllipse(QRectF(s * x - s * 0.055, s * y, s * 0.11, s * 0.16))
    elif kind == "chat":
        painter.drawRoundedRect(QRectF(s * 0.15, s * 0.22, s * 0.70, s * 0.48), s * 0.10, s * 0.10)
        painter.drawLine(round(s * 0.36), round(s * 0.70), round(s * 0.25), round(s * 0.84))
        for x in [0.34, 0.50, 0.66]:
            painter.drawPoint(round(s * x), round(s * 0.47))
    elif kind == "sheet":
        painter.drawRect(QRectF(s * 0.22, s * 0.14, s * 0.56, s * 0.72))
        painter.drawLine(round(s * 0.38), round(s * 0.34), round(s * 0.62), round(s * 0.66))
        painter.drawLine(round(s * 0.62), round(s * 0.34), round(s * 0.38), round(s * 0.66))
    elif kind == "report":
        painter.drawRect(QRectF(s * 0.24, s * 0.12, s * 0.52, s * 0.74))
        for y in [0.36, 0.52, 0.68]:
            painter.drawLine(round(s * 0.36), round(s * y), round(s * 0.64), round(s * y))
    elif kind == "clock":
        painter.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
        painter.drawLine(round(s * 0.50), round(s * 0.50), round(s * 0.50), round(s * 0.30))
        painter.drawLine(round(s * 0.50), round(s * 0.50), round(s * 0.64), round(s * 0.58))
    elif kind == "calendar":
        painter.drawRoundedRect(QRectF(s * 0.18, s * 0.22, s * 0.64, s * 0.58), s * 0.06, s * 0.06)
        painter.drawLine(round(s * 0.18), round(s * 0.38), round(s * 0.82), round(s * 0.38))
        painter.drawLine(round(s * 0.34), round(s * 0.14), round(s * 0.34), round(s * 0.28))
        painter.drawLine(round(s * 0.66), round(s * 0.14), round(s * 0.66), round(s * 0.28))
    elif kind == "play":
        painter.setBrush(QColor(color))
        points = [
            QPoint(round(s * 0.36), round(s * 0.24)),
            QPoint(round(s * 0.36), round(s * 0.76)),
            QPoint(round(s * 0.78), round(s * 0.50)),
        ]
        painter.drawPolygon(points)
    elif kind == "stop":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        side = round(s * 0.38)
        painter.drawRoundedRect(
            round((s - side) / 2),
            round((s - side) / 2),
            side,
            side,
            max(1, round(s * 0.05)),
            max(1, round(s * 0.05)),
        )
    elif kind == "check":
        painter.drawLine(round(s * 0.28), round(s * 0.52), round(s * 0.43), round(s * 0.67))
        painter.drawLine(round(s * 0.43), round(s * 0.67), round(s * 0.74), round(s * 0.34))
    elif kind == "minimize":
        painter.drawLine(round(s * 0.28), round(s * 0.58), round(s * 0.72), round(s * 0.58))
    elif kind == "maximize":
        painter.drawRect(QRectF(s * 0.30, s * 0.29, s * 0.40, s * 0.40))
    elif kind == "restore":
        painter.drawRect(QRectF(s * 0.25, s * 0.37, s * 0.36, s * 0.36))
        painter.drawRect(QRectF(s * 0.39, s * 0.24, s * 0.36, s * 0.36))
    elif kind == "close":
        painter.drawLine(round(s * 0.32), round(s * 0.32), round(s * 0.68), round(s * 0.68))
        painter.drawLine(round(s * 0.68), round(s * 0.32), round(s * 0.32), round(s * 0.68))
    elif kind == "task":
        painter.drawRoundedRect(QRectF(s * 0.25, s * 0.16, s * 0.50, s * 0.68), s * 0.06, s * 0.06)
        painter.drawLine(round(s * 0.36), round(s * 0.42), round(s * 0.64), round(s * 0.42))
        painter.drawLine(round(s * 0.36), round(s * 0.58), round(s * 0.58), round(s * 0.58))
    elif kind == "chevron_down":
        painter.drawLine(round(s * 0.30), round(s * 0.40), round(s * 0.50), round(s * 0.60))
        painter.drawLine(round(s * 0.50), round(s * 0.60), round(s * 0.70), round(s * 0.40))
    else:
        painter.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))

    painter.end()
    return QIcon(pixmap)


class HoverMenuButton(QPushButton):
    """A menu button that uses native click-to-open behavior."""


class PeriodButton(QPushButton):
    def __init__(self, text: str, period_value: str, parent=None):
        super().__init__(text, parent)
        self.setProperty("periodValue", period_value)
        self.setObjectName("periodButton")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIcon(make_line_icon("clock", "#3f7cf4", 18))
        self.setIconSize(QSize(17, 17))
        self.setFixedHeight(48)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPoint(self.width() - 17, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#55bd70"))
        painter.drawEllipse(center, 8, 8)
        painter.setPen(QPen(QColor("#ffffff"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(center.x() - 4, center.y(), center.x() - 1, center.y() + 3)
        painter.drawLine(center.x() - 1, center.y() + 3, center.x() + 4, center.y() - 3)
        painter.end()


class LogConsole(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ready_overlay = QFrame(self)
        self.ready_overlay.setObjectName("logReadyOverlay")
        overlay_layout = QHBoxLayout(self.ready_overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(6)
        self.status_control = KstStatusControl(self.ready_overlay)
        overlay_layout.addWidget(self.status_control)
        # Compatibility aliases for callers that still inspect the log badge.
        self.ready_dot = self.status_control.kst_button
        self.ready_label = self.status_control.live_label
        self.ready_overlay.adjustSize()
        self.setViewportMargins(0, 24, 0, 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.ready_overlay.adjustSize()
        self.ready_overlay.move(self.width() - self.ready_overlay.width() - 16, 11)
        self.ready_overlay.raise_()


class InlineMenuRow(QPushButton):
    def __init__(self, text: str, expandable: bool = False, parent=None):
        super().__init__(text, parent)
        self.setObjectName("inlineMenuRow")
        self.setProperty("expandable", expandable)
        self._expanded = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(32)

    def set_expanded(self, expanded: bool) -> None:
        value = bool(expanded)
        if self._expanded == value:
            return
        self._expanded = value
        self.update()

    def is_expanded(self) -> bool:
        return self._expanded

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.property("expandable"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#65758e"), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        center = QPoint(self.width() - 15, self.height() // 2)
        if self._expanded:
            painter.drawLine(center.x() - 3, center.y() - 2, center.x(), center.y() + 1)
            painter.drawLine(center.x(), center.y() + 1, center.x() + 3, center.y() - 2)
        else:
            painter.drawLine(center.x() - 2, center.y() - 3, center.x() + 1, center.y())
            painter.drawLine(center.x() + 1, center.y(), center.x() - 2, center.y() + 3)
        painter.end()


class DataSourceModeControl(QFrame):
    preference_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dataSourceModeControl")
        self.setFixedSize(94, 26)
        self._preference = "api"

        self.prefix_label = QLabel("模式：", self)
        self.prefix_label.setObjectName("dataSourceModePrefix")
        self.segment_frame = QFrame(self)
        self.segment_frame.setObjectName("dataSourceModeSegment")
        self.arrow_label = QLabel(">", self)
        self.arrow_label.setObjectName("dataSourceModeArrow")
        self.arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.browser_button = QPushButton("B", self)
        self.api_button = QPushButton("A", self)
        self.browser_button.setToolTip("B：强制使用浏览器")
        self.api_button.setToolTip("A：API 优先，失败自动切换浏览器")
        for button in (self.browser_button, self.api_button):
            button.setObjectName("dataSourceModeButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.api_button.setChecked(True)
        self.browser_button.clicked.connect(lambda: self.set_preference("browser"))
        self.api_button.clicked.connect(lambda: self.set_preference("api"))

        self.api_animation = QPropertyAnimation(self.api_button, b"geometry", self)
        self.browser_animation = QPropertyAnimation(self.browser_button, b"geometry", self)
        for animation in (self.api_animation, self.browser_animation):
            animation.setDuration(170)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setStyleSheet(f"""
            QFrame#dataSourceModeControl {{
                background: transparent;
                border: 0;
            }}
            QFrame#dataSourceModeSegment {{
                background: #f1f4f8;
                border: 1px solid #e1e7ef;
                border-radius: 8px;
            }}
            QLabel#dataSourceModePrefix, QLabel#dataSourceModeArrow {{
                color: #53647e;
                background: transparent;
                border: 0;
                font-family: {FONT_REGULAR_STACK};
                font-size: 8pt;
                font-weight: 400;
            }}
            QPushButton#dataSourceModeButton {{
                color: #69788f;
                background: transparent;
                border: 1px solid #b9d8ff;
                border-radius: 11px;
                padding: 0;
                font-family: {FONT_TITLE_STACK};
                font-size: 8pt;
                font-weight: 700;
            }}
            QPushButton#dataSourceModeButton:checked {{
                color: #ffffff;
                background: #3f83f8;
                border: 1px solid #2f6fed;
            }}
            QPushButton#dataSourceModeButton:disabled {{
                color: #a8b2c1;
                background: transparent;
                border: 1px solid #d8e3f0;
            }}
            QPushButton#dataSourceModeButton:checked:disabled {{
                color: #ffffff;
                background: #9eb8df;
                border-color: #91abd2;
            }}
        """)
        self._layout_children()

    def preference(self) -> str:
        return self._preference

    def set_preference(self, preference: str, animate: bool = True, emit: bool = True) -> None:
        normalized = "browser" if str(preference).strip().lower() == "browser" else "api"
        changed = normalized != self._preference
        self._preference = normalized
        self.browser_button.setChecked(normalized == "browser")
        self.api_button.setChecked(normalized == "api")
        self.api_animation.stop()
        self.browser_animation.stop()
        api_target, browser_target = self._target_geometries(normalized)
        if animate and self.isVisible():
            self.api_animation.setStartValue(self.api_button.geometry())
            self.api_animation.setEndValue(api_target)
            self.browser_animation.setStartValue(self.browser_button.geometry())
            self.browser_animation.setEndValue(browser_target)
            if normalized == "api":
                self.api_button.raise_()
            else:
                self.browser_button.raise_()
            self.api_animation.start()
            self.browser_animation.start()
        else:
            self.api_button.setGeometry(api_target)
            self.browser_button.setGeometry(browser_target)
            self.api_button.raise_()
            self.browser_button.raise_()
        if emit and changed:
            self.preference_changed.emit(normalized)

    def display_order(self) -> str:
        return "B>A" if self._preference == "browser" else "A>B"

    def _target_geometries(self, preference: str) -> tuple[QRect, QRect]:
        left = QRect(38, 2, 22, 22)
        right = QRect(70, 2, 22, 22)
        if preference == "browser":
            return right, left
        return left, right

    def _layout_children(self) -> None:
        self.prefix_label.setGeometry(0, 0, 36, self.height())
        self.segment_frame.setGeometry(36, 0, 58, self.height())
        self.arrow_label.setGeometry(60, 0, 9, self.height())
        if (
            self.api_animation.state() != QPropertyAnimation.State.Running
            and self.browser_animation.state() != QPropertyAnimation.State.Running
        ):
            api_target, browser_target = self._target_geometries(self._preference)
            self.api_button.setGeometry(api_target)
            self.browser_button.setGeometry(browser_target)
        self.segment_frame.lower()
        self.prefix_label.raise_()
        self.arrow_label.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_children()


class RunStopSplitControl(QFrame):
    diagonal_gap_width = 2

    def __init__(self, run_text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("runStopSplitControl")
        self.setMinimumHeight(42)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.run_button = QPushButton(run_text, self)
        self.run_button.setObjectName("runSegmentButton")
        self.run_button.setIcon(make_line_icon("play", "#ffffff", 18))
        self.run_button.setIconSize(QSize(16, 16))
        self.run_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.stop_button = QPushButton("停止", self)
        self.stop_button.setObjectName("stopSegmentButton")
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.setEnabled(False)

        self.setStyleSheet(f"""
            QPushButton#runSegmentButton, QPushButton#stopSegmentButton {{
                color: #ffffff;
                background: transparent;
                border: 0;
                padding: 0 5px;
                font-family: {FONT_TITLE_STACK};
                font-size: 9pt;
                font-weight: 700;
            }}
            QPushButton#runSegmentButton:hover, QPushButton#stopSegmentButton:hover {{
                background: rgba(255, 255, 255, 24);
            }}
            QPushButton#runSegmentButton:pressed, QPushButton#stopSegmentButton:pressed {{
                background: rgba(14, 43, 96, 36);
            }}
            QPushButton#runSegmentButton:disabled, QPushButton#stopSegmentButton:disabled {{
                color: rgba(255, 255, 255, 210);
            }}
        """)
        self._layout_children()

    def set_run_enabled(self, enabled: bool) -> None:
        self.run_button.setEnabled(bool(enabled))
        self.update()

    def set_stop_enabled(self, enabled: bool) -> None:
        self.stop_button.setEnabled(bool(enabled))
        self.update()

    def _layout_children(self) -> None:
        split_x = round(self.width() * 0.70)
        self.run_button.setGeometry(0, 0, split_x, self.height())
        self.stop_button.setGeometry(
            split_x - 2,
            2,
            self.width() - split_x + 2,
            max(0, self.height() - 2),
        )
        self.stop_button.raise_()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.setClipPath(path)

        run_color = QColor("#3f83f8") if self.run_button.isEnabled() else QColor("#aabbd5")
        painter.fillPath(path, run_color)

        split_x = round(self.width() * 0.70)
        stop_color = QColor("#2f66d5") if self.stop_button.isEnabled() else QColor("#aeb8c8")
        stop_area = QPainterPath()
        stop_area.moveTo(split_x + 5, 0)
        stop_area.lineTo(self.width(), 0)
        stop_area.lineTo(self.width(), self.height())
        stop_area.lineTo(split_x - 5, self.height())
        stop_area.closeSubpath()
        painter.fillPath(stop_area, stop_color)

        painter.setPen(
            QPen(
                QColor("#ffffff"),
                self.diagonal_gap_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.FlatCap,
            )
        )
        painter.drawLine(split_x + 5, 0, split_x - 5, self.height())
        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_children()


class InlineConfigMenu(QFrame):
    project_check_requested = Signal()
    update_path_requested = Signal()
    update_credentials_requested = Signal()
    import_secrets_requested = Signal()
    export_secrets_requested = Signal()
    restore_backup_requested = Signal()
    excel_path_config_requested = Signal()
    excel_auto_open_requested = Signal(bool)
    kst_data_source_requested = Signal(str)
    kst_installation_root_requested = Signal()
    kst_data_root_requested = Signal()
    kst_rescan_requested = Signal()
    pet_mode_requested = Signal(str)
    pet_scale_requested = Signal(float)
    exit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("inlineConfigMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(224)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(0)

        layout.addWidget(self._action_row("项目配置检查", self.project_check_requested.emit))
        layout.addWidget(self._separator())
        # 配置文件编辑能力继续保留给现有脚本，仅暂时从普通用户菜单隐藏。
        # layout.addWidget(self._action_row("更新 Excel 路径", self.update_path_requested.emit))
        # layout.addWidget(self._action_row("更新账号密码", self.update_credentials_requested.emit))
        layout.addWidget(self._action_row("导入授权配置", self.import_secrets_requested.emit))
        layout.addWidget(self._action_row("导出授权配置", self.export_secrets_requested.emit))
        layout.addWidget(self._action_row("恢复备份", self.restore_backup_requested.emit))
        layout.addWidget(self._action_row("Excel 路径配置", self.excel_path_config_requested.emit))
        layout.addWidget(self._separator())

        self.kst_mode_toggle = InlineMenuRow("快商通模式", expandable=True)
        self.kst_mode_toggle.clicked.connect(self._toggle_kst_mode_section)
        layout.addWidget(self.kst_mode_toggle)

        self.kst_mode_section = QWidget()
        self.kst_mode_section.setObjectName("inlineMenuSection")
        kst_mode_layout = QVBoxLayout(self.kst_mode_section)
        kst_mode_layout.setContentsMargins(10, 2, 0, 3)
        kst_mode_layout.setSpacing(3)
        self.kst_api_choice = self._choice_row(
            "API 自动获取",
            lambda: self.kst_data_source_requested.emit("local_api"),
        )
        self.kst_export_choice = self._choice_row(
            "人工导出对话",
            lambda: self.kst_data_source_requested.emit("export"),
        )
        self.kst_installation_root_choice = self._choice_row(
            "选择快商通程序目录",
            lambda: self._run_and_close(
                self.kst_installation_root_requested.emit
            ),
        )
        self.kst_data_root_choice = self._choice_row(
            "选择快商通数据目录",
            lambda: self._run_and_close(
                self.kst_data_root_requested.emit
            ),
        )
        self.kst_rescan_choice = self._choice_row(
            "重新扫描快商通",
            lambda: self._run_and_close(
                self.kst_rescan_requested.emit
            ),
        )
        kst_mode_layout.addWidget(self.kst_api_choice)
        kst_mode_layout.addWidget(self.kst_export_choice)
        kst_mode_layout.addWidget(self.kst_installation_root_choice)
        kst_mode_layout.addWidget(self.kst_data_root_choice)
        kst_mode_layout.addWidget(self.kst_rescan_choice)
        self.kst_mode_section.hide()
        layout.addWidget(self.kst_mode_section)

        layout.addWidget(self._separator())

        self.excel_auto_toggle = InlineMenuRow("Excel 自动打开", expandable=True)
        self.excel_auto_toggle.clicked.connect(self._toggle_excel_auto_section)
        layout.addWidget(self.excel_auto_toggle)

        self.excel_auto_section = QWidget()
        self.excel_auto_section.setObjectName("inlineMenuSection")
        excel_auto_layout = QVBoxLayout(self.excel_auto_section)
        excel_auto_layout.setContentsMargins(10, 2, 0, 3)
        excel_auto_layout.setSpacing(3)
        self.excel_start_choice = self._choice_row("启动", lambda: self.excel_auto_open_requested.emit(True))
        self.excel_stop_choice = self._choice_row("停止", lambda: self.excel_auto_open_requested.emit(False))
        excel_auto_layout.addWidget(self.excel_start_choice)
        excel_auto_layout.addWidget(self.excel_stop_choice)
        self.excel_auto_section.hide()
        layout.addWidget(self.excel_auto_section)

        layout.addWidget(self._separator())

        self.pet_toggle = InlineMenuRow("桌面宠物", expandable=True)
        self.pet_toggle.clicked.connect(self._toggle_pet_section)
        layout.addWidget(self.pet_toggle)

        self.pet_section = QWidget()
        self.pet_section.setObjectName("inlineMenuSection")
        self.pet_layout = QVBoxLayout(self.pet_section)
        self.pet_layout.setContentsMargins(10, 2, 0, 3)
        self.pet_layout.setSpacing(3)
        self.clawd_choice = self._choice_row("Clawd 小螃蟹", lambda: self.pet_mode_requested.emit(PET_CLAWD))
        self.hidden_choice = self._choice_row("隐藏宠物", lambda: self.pet_mode_requested.emit(PET_HIDDEN))
        self.pet_layout.addWidget(self.clawd_choice)
        self.pet_layout.addWidget(self.hidden_choice)

        self.size_toggle = InlineMenuRow("宠物大小", expandable=True)
        self.size_toggle.clicked.connect(self._toggle_size_section)
        self.pet_layout.addWidget(self.size_toggle)

        self.size_section = QWidget()
        self.size_section.setObjectName("inlineMenuSection")
        size_layout = QVBoxLayout(self.size_section)
        size_layout.setContentsMargins(12, 5, 8, 6)
        size_layout.setSpacing(3)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        self.size_min_label = QLabel("50%")
        self.size_min_label.setObjectName("petScaleHint")
        self.size_value_label = QLabel("100%")
        self.size_value_label.setObjectName("petScaleValue")
        self.size_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.size_max_label = QLabel("120%")
        self.size_max_label.setObjectName("petScaleHint")
        self.size_max_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_row.addWidget(self.size_min_label)
        value_row.addStretch(1)
        value_row.addWidget(self.size_value_label)
        value_row.addStretch(1)
        value_row.addWidget(self.size_max_label)
        size_layout.addLayout(value_row)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setObjectName("petScaleSlider")
        self.size_slider.setRange(50, 120)
        self.size_slider.setSingleStep(1)
        self.size_slider.setPageStep(5)
        self.size_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.size_slider.valueChanged.connect(self._on_scale_changed)
        size_layout.addWidget(self.size_slider)
        self.size_section.hide()
        self.pet_layout.addWidget(self.size_section)
        self.pet_section.hide()
        layout.addWidget(self.pet_section)

        layout.addWidget(self._separator())
        layout.addWidget(self._action_row("退出程序", self.exit_requested.emit, danger=True))

        self.setStyleSheet(f"""
            QFrame#inlineConfigMenu {{
                background: #ffffff;
                border: 1px solid #cfd8e5;
                border-radius: 11px;
                font-family: {FONT_MENU_STACK};
            }}
            QPushButton#inlineMenuRow, QPushButton#inlineMenuChoice {{
                min-height: 30px;
                background: transparent;
                border: none;
                border-radius: 7px;
                color: #1d2a40;
                padding: 0 24px 0 10px;
                text-align: left;
                font-family: {FONT_MENU_STACK};
                font-size: 9pt;
            }}
            QPushButton#inlineMenuRow:hover, QPushButton#inlineMenuChoice:hover {{
                background: #eef3fa;
            }}
            QPushButton#inlineMenuChoice {{
                min-height: 28px;
                color: #384861;
                padding-left: 12px;
                font-size: 8pt;
            }}
            QPushButton#inlineMenuChoice[selected="true"] {{
                color: #1f65c5;
                background: #eaf2ff;
            }}
            QPushButton#inlineMenuRow[danger="true"] {{ color: #a43a49; }}
            QFrame#inlineMenuSeparator {{
                min-height: 1px;
                max-height: 1px;
                margin: 5px 4px;
                border: none;
                background: #e3e9f1;
            }}
            QWidget#inlineMenuSection {{ background: transparent; }}
            QLabel#petScaleHint {{
                color: #8593a8;
                font-size: 7pt;
            }}
            QLabel#petScaleValue {{
                min-width: 32px;
                color: #246bce;
                font-size: 8pt;
            }}
            QSlider#petScaleSlider {{ min-height: 22px; }}
            QSlider#petScaleSlider::groove:horizontal {{
                height: 4px;
                background: #dce5f1;
                border-radius: 2px;
            }}
            QSlider#petScaleSlider::sub-page:horizontal {{
                background: #4b8df8;
                border-radius: 2px;
            }}
            QSlider#petScaleSlider::handle:horizontal {{
                width: 14px;
                height: 14px;
                margin: -5px 0;
                background: #ffffff;
                border: 2px solid #3f83ee;
                border-radius: 7px;
            }}
            QSlider#petScaleSlider::handle:horizontal:hover {{
                background: #eef5ff;
                border-color: #276fd6;
            }}
        """)
        layout.activate()
        self._collapsed_height = layout.sizeHint().height()
        self.setFixedHeight(self._collapsed_height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 11, 11)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _action_row(self, text: str, callback, danger: bool = False) -> InlineMenuRow:
        row = InlineMenuRow(text)
        row.setProperty("danger", danger)
        row.clicked.connect(lambda: self._run_and_close(callback))
        return row

    def _choice_row(self, text: str, callback) -> QPushButton:
        row = QPushButton(text)
        row.setObjectName("inlineMenuChoice")
        row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.setFixedHeight(28)
        row.clicked.connect(callback)
        return row

    @staticmethod
    def _separator() -> QFrame:
        separator = QFrame()
        separator.setObjectName("inlineMenuSeparator")
        separator.setFrameShape(QFrame.Shape.NoFrame)
        return separator

    def _run_and_close(self, callback) -> None:
        self.hide()
        callback()

    def _toggle_pet_section(self) -> None:
        expanded = not self.pet_section.isVisible()
        self.kst_mode_section.hide()
        self.kst_mode_toggle.set_expanded(False)
        self.excel_auto_section.hide()
        self.excel_auto_toggle.set_expanded(False)
        self.pet_section.setVisible(expanded)
        self.pet_toggle.set_expanded(expanded)
        if not expanded:
            self.size_section.hide()
            self.size_toggle.set_expanded(False)
        self._refresh_height()

    def _toggle_excel_auto_section(self) -> None:
        expanded = not self.excel_auto_section.isVisible()
        self.kst_mode_section.hide()
        self.kst_mode_toggle.set_expanded(False)
        self.pet_section.hide()
        self.pet_toggle.set_expanded(False)
        self.size_section.hide()
        self.size_toggle.set_expanded(False)
        self.excel_auto_section.setVisible(expanded)
        self.excel_auto_toggle.set_expanded(expanded)
        self._refresh_height()

    def _toggle_kst_mode_section(self) -> None:
        expanded = not self.kst_mode_section.isVisible()
        self.excel_auto_section.hide()
        self.excel_auto_toggle.set_expanded(False)
        self.pet_section.hide()
        self.pet_toggle.set_expanded(False)
        self.size_section.hide()
        self.size_toggle.set_expanded(False)
        self.kst_mode_section.setVisible(expanded)
        self.kst_mode_toggle.set_expanded(expanded)
        self._refresh_height()

    def _toggle_size_section(self) -> None:
        expanded = not self.size_section.isVisible()
        self.size_section.setVisible(expanded)
        self.size_toggle.set_expanded(expanded)
        self._refresh_height()

    def _on_scale_changed(self, value: int) -> None:
        self.size_value_label.setText(f"{value}%")
        self.pet_scale_requested.emit(value / 100.0)

    def _refresh_height(self) -> None:
        height = self._collapsed_height
        if not self.pet_section.isHidden():
            section_layout = self.pet_section.layout()
            section_layout.invalidate()
            section_layout.activate()
            height += self.pet_section.sizeHint().height()
        if not self.excel_auto_section.isHidden():
            section_layout = self.excel_auto_section.layout()
            section_layout.invalidate()
            section_layout.activate()
            height += self.excel_auto_section.sizeHint().height()
        if not self.kst_mode_section.isHidden():
            section_layout = self.kst_mode_section.layout()
            section_layout.invalidate()
            section_layout.activate()
            height += self.kst_mode_section.sizeHint().height()
        self.setFixedHeight(height)

    def sync(
        self,
        pet_mode: str,
        pet_scale: float,
        excel_auto_open: bool,
        kst_data_source: str = "local_api",
    ) -> None:
        self._set_selected(self.clawd_choice, pet_mode == PET_CLAWD)
        self._set_selected(self.hidden_choice, pet_mode == PET_HIDDEN)
        self._set_selected(self.excel_start_choice, excel_auto_open)
        self._set_selected(self.excel_stop_choice, not excel_auto_open)
        self._set_selected(
            self.kst_api_choice,
            kst_data_source == "local_api",
        )
        self._set_selected(
            self.kst_export_choice,
            kst_data_source == "export",
        )
        self.kst_rescan_choice.setEnabled(True)
        value = round(normalize_pet_scale(pet_scale) * 100)
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(value)
        self.size_slider.blockSignals(False)
        self.size_value_label.setText(f"{value}%")

    @staticmethod
    def _set_selected(widget: QWidget, selected: bool) -> None:
        widget.setProperty("selected", selected)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def popup_below(self, anchor: QWidget) -> None:
        self._refresh_height()
        position = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            x = min(max(position.x(), available.left() + 6), available.right() - self.width() - 6)
            y = min(position.y(), available.bottom() - self.height() - 6)
            position = QPoint(x, max(y, available.top() + 6))
        self.move(position)
        self.show()
        self.raise_()


class SlidingProjectModeControl(QFrame):
    mode_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectModeSegment")
        self.setFixedSize(106, 29)
        self._multi = False
        self.indicator = QFrame(self)
        self.indicator.setObjectName("projectModeIndicator")
        self.single_button = QPushButton("单项目", self)
        self.multi_button = QPushButton("多项目", self)
        for button in (self.single_button, self.multi_button):
            button.setObjectName("projectModeButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.single_button.setChecked(True)
        self.single_button.clicked.connect(lambda: self.set_multi_mode(False))
        self.multi_button.clicked.connect(lambda: self.set_multi_mode(True))
        self.animation = QPropertyAnimation(self.indicator, b"geometry", self)
        self.animation.setDuration(150)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._layout_children()

    def _target_geometry(self, multi: bool) -> QRect:
        inner_width = self.width() - 2
        half = inner_width // 2
        return QRect(1 + (half if multi else 0), 1, half, self.height() - 2)

    def _layout_children(self) -> None:
        half = self.width() // 2
        self.single_button.setGeometry(0, 0, half, self.height())
        self.multi_button.setGeometry(half, 0, self.width() - half, self.height())
        if self.animation.state() != QPropertyAnimation.State.Running:
            self.indicator.setGeometry(self._target_geometry(self._multi))
        self.indicator.lower()
        self.single_button.raise_()
        self.multi_button.raise_()

    def set_multi_mode(self, multi: bool, animate: bool = True) -> None:
        value = bool(multi)
        if value == self._multi:
            self.single_button.setChecked(not value)
            self.multi_button.setChecked(value)
            return
        self._multi = value
        self.single_button.setChecked(not value)
        self.multi_button.setChecked(value)
        self.indicator.setProperty("preview", False)
        self.indicator.style().unpolish(self.indicator)
        self.indicator.style().polish(self.indicator)
        self.animation.stop()
        if animate and self.isVisible():
            self.animation.setStartValue(self.indicator.geometry())
            self.animation.setEndValue(self._target_geometry(value))
            self.animation.start()
        else:
            self.indicator.setGeometry(self._target_geometry(value))
        self.mode_changed.emit(value)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_children()


PROJECT_SEARCH_ALIASES = {
    "长沙": "cs",
    "合肥": "hf",
    "昆明": "km",
    "南京": "nj",
    "宁波": "nb",
    "青岛": "qd",
    "沈阳": "sy",
    "深圳": "sz",
}


def project_matches_search(name: str, project_id: str, query: str) -> bool:
    normalized = query.strip().lower()
    if not normalized:
        return True
    if normalized == "b":
        return name.endswith("白")
    if normalized == "n":
        return name.endswith("牛")
    aliases = []
    for city, initials in PROJECT_SEARCH_ALIASES.items():
        if city in name:
            suffix = "b" if name.endswith("白") else "n" if name.endswith("牛") else ""
            aliases.extend([initials, initials + suffix])
    candidates = [name.lower(), project_id.lower(), *aliases]
    return any(normalized in candidate for candidate in candidates)


class ProjectSelectionPopup(QFrame):
    selection_confirmed = Signal(list)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("projectSelectionPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(196)
        self._items: list[tuple[str, str]] = []
        self._selected_ids: list[str] = []
        self._multi = False
        self._default_project_id = ""
        self._project_buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setObjectName("projectSearchInput")
        self.search.setPlaceholderText("输入 B/N 快速检索对应项目")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._rebuild_rows)
        layout.addWidget(self.search)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("projectSelectionScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows = QWidget()
        self.rows.setObjectName("projectSelectionRows")
        self.rows_layout = QGridLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setHorizontalSpacing(5)
        self.rows_layout.setVerticalSpacing(5)
        for column in range(3):
            self.rows_layout.setColumnStretch(column, 1)
        self.scroll.setWidget(self.rows)
        layout.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 0, 0)
        self.summary = QLabel()
        self.summary.setObjectName("projectSelectionSummary")
        footer.addWidget(self.summary)
        footer.addStretch(1)
        self.clear_button = QPushButton("清除")
        self.clear_button.setObjectName("projectSelectionClear")
        self.clear_button.setFixedSize(48, 27)
        self.clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_button.clicked.connect(self._clear_selection)
        footer.addWidget(self.clear_button)
        self.confirm_button = QPushButton("确定")
        self.confirm_button.setObjectName("projectSelectionConfirm")
        self.confirm_button.setFixedSize(48, 27)
        self.confirm_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.confirm_button.clicked.connect(self._confirm)
        footer.addWidget(self.confirm_button)
        layout.addLayout(footer)

        self.setStyleSheet(f"""
            QFrame#projectSelectionPopup {{
                background: #ffffff;
                border: 1px solid #cfd9e7;
                border-radius: 11px;
                font-family: {FONT_STACK};
            }}
            QLineEdit#projectSearchInput {{
                height: 29px;
                padding: 0 9px;
                color: #31435e;
                background: #f8fafc;
                border: 1px solid #d7e0ec;
                border-radius: 8px;
                font-size: 9pt;
            }}
            QLineEdit#projectSearchInput:focus {{ border-color: #79a9f4; }}
            QScrollArea#projectSelectionScroll {{
                background: #ffffff;
                border: 0;
            }}
            QWidget#projectSelectionRows {{ background: #ffffff; }}
            QPushButton#projectChoice {{
                min-height: 29px;
                padding: 0 5px;
                color: #263751;
                background: #ffffff;
                border: 1px solid #d4deeb;
                border-radius: 7px;
                font-size: 9pt;
            }}
            QPushButton#projectChoice:hover {{ background: #eef4fd; border-color: #a9c5ee; }}
            QPushButton#projectChoice:checked {{
                color: #1f65c5;
                background: #eaf2ff;
                border-color: #76a7ef;
            }}
            QLabel#projectSelectionSummary {{ color: #65758e; font-size: 8pt; }}
            QPushButton#projectSelectionConfirm, QPushButton#projectSelectionClear {{
                color: #ffffff;
                background: #3f83ee;
                border: 0;
                border-radius: 7px;
                font-size: 8pt;
            }}
            QPushButton#projectSelectionConfirm:hover {{ background: #3276db; }}
            QPushButton#projectSelectionConfirm:disabled {{ background: #b8c8de; }}
            QPushButton#projectSelectionClear {{
                color: #52647e;
                background: #f4f7fb;
                border: 1px solid #d5dfeb;
            }}
            QPushButton#projectSelectionClear:hover {{ background: #eaf0f7; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{ min-height: 24px; background: #cbd6e4; border-radius: 2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def set_data(self, items: list[tuple[str, str]], selected_ids: list[str], multi: bool, default_project_id: str) -> None:
        self._items = list(items)
        valid_ids = {data for _, data in self._items}
        self._selected_ids = [item for item in selected_ids if item in valid_ids]
        self._multi = bool(multi)
        self._default_project_id = default_project_id if default_project_id in valid_ids else (self._items[0][1] if self._items else "")
        self.clear_button.setVisible(self._multi)
        self.search.clear()
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._project_buttons.clear()
        query = self.search.text().strip().lower()
        visible_index = 0
        for name, project_id in self._items:
            if not project_matches_search(name, project_id, query):
                continue
            button = QPushButton(name)
            button.setObjectName("projectChoice")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setChecked(project_id in self._selected_ids)
            button.toggled.connect(lambda checked, value=project_id: self._toggle_project(value, checked))
            self._project_buttons[project_id] = button
            self.rows_layout.addWidget(button, visible_index // 3, visible_index % 3)
            visible_index += 1
        self._refresh_selection_state()

    def _toggle_project(self, project_id: str, checked: bool) -> None:
        if self._multi:
            if checked and project_id not in self._selected_ids:
                if len(self._selected_ids) >= 3:
                    self.summary.setText("最多选择 3 个项目")
                    button = self._project_buttons.get(project_id)
                    if button:
                        button.blockSignals(True)
                        button.setChecked(False)
                        button.blockSignals(False)
                    return
                self._selected_ids.append(project_id)
            elif not checked and project_id in self._selected_ids:
                self._selected_ids.remove(project_id)
            self._refresh_selection_state()
            return
        if checked:
            self._selected_ids = [project_id]
            self.selection_confirmed.emit(list(self._selected_ids))
            self.hide()

    def _refresh_selection_state(self) -> None:
        for project_id, button in self._project_buttons.items():
            button.blockSignals(True)
            button.setChecked(project_id in self._selected_ids)
            button.blockSignals(False)
        count = len(self._selected_ids)
        self.summary.setText(f"已选择 {count} 个项目")
        self.confirm_button.setEnabled(count >= 1)

    def _clear_selection(self) -> None:
        self._selected_ids = [self._default_project_id] if self._default_project_id else []
        self._refresh_selection_state()

    def _confirm(self) -> None:
        if not self.confirm_button.isEnabled():
            return
        self.selection_confirmed.emit(list(self._selected_ids))
        self.hide()

    def popup_below(self, anchor: QWidget) -> None:
        self.setFixedSize(anchor.width(), 196)
        position = anchor.mapToGlobal(QPoint(0, anchor.height() + 2))
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            x = min(max(position.x(), available.left() + 6), available.right() - self.width() - 6)
            y = min(position.y(), available.bottom() - self.height() - 6)
            position = QPoint(x, max(y, available.top() + 6))
        self.move(position)
        self.show()
        self.raise_()
        self.search.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 11, 11)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class ProjectSelectorBorderOverlay(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("projectSelectorBorderOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent; border: 0;")

    def paintEvent(self, event) -> None:
        del event
        parent = self.parentWidget()
        color = QColor("#7fa9ef") if parent is not None and parent.underMouse() else QColor("#cad7e8")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1))
        frame = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.drawRoundedRect(frame, 9.25, 9.25)
        painter.end()


class ProjectSelectorButton(QPushButton):
    selection_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectCombo")
        self.setText("")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._items: list[tuple[str, str]] = []
        self._current_index = -1
        self._selected_ids: list[str] = []
        self._default_project_id = ""
        self._multi = False
        self.popup = ProjectSelectionPopup()
        self.popup.selection_confirmed.connect(self._apply_selection)
        self.clicked.connect(self.show_project_popup)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 10, 0)
        layout.setSpacing(8)
        self.summary_label = QLabel("请选择项目")
        self.summary_label.setObjectName("projectSelectorSummary")
        self.summary_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.summary_label, 1)
        self.chips_widget = QWidget()
        self.chips_widget.setObjectName("projectSelectionChips")
        self.chips_layout = QHBoxLayout(self.chips_widget)
        self.chips_layout.setContentsMargins(0, 6, 0, 6)
        self.chips_layout.setSpacing(5)
        self.chips_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.chips_widget.hide()
        layout.addWidget(self.chips_widget, 1)
        self.arrow_label = QLabel()
        self.arrow_label.setObjectName("projectSelectorArrow")
        self.arrow_label.setFixedSize(12, 12)
        self.arrow_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        arrow = QPixmap(12, 12)
        arrow.fill(Qt.GlobalColor.transparent)
        painter = QPainter(arrow)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#6680a5"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(3, 4, 6, 7)
        painter.drawLine(6, 7, 9, 4)
        painter.end()
        self.arrow_label.setPixmap(arrow)
        layout.addWidget(self.arrow_label)
        self.border_overlay = ProjectSelectorBorderOverlay(self)
        self.border_overlay.setGeometry(self.rect())
        self.border_overlay.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.border_overlay.setGeometry(self.rect())
        self.border_overlay.raise_()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.border_overlay.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.border_overlay.update()

    def clear(self) -> None:
        self._items.clear()
        self._selected_ids.clear()
        self._current_index = -1
        self._refresh_display()

    def addItem(self, text: str, user_data: str) -> None:
        self._items.append((str(text), str(user_data)))
        if self._current_index < 0:
            self.setCurrentIndex(0)

    def findData(self, value: str) -> int:
        for index, (_, data) in enumerate(self._items):
            if data == value:
                return index
        return -1

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        self._current_index = index
        project_id = self._items[index][1]
        if not self._multi or len(self._selected_ids) <= 1:
            self._selected_ids = [project_id]
        self._refresh_display()

    def set_default_project_id(self, project_id: str) -> None:
        self._default_project_id = str(project_id or "")

    def currentData(self) -> str | None:
        if self._selected_ids:
            return self._selected_ids[0]
        if 0 <= self._current_index < len(self._items):
            return self._items[self._current_index][1]
        return None

    def currentText(self) -> str:
        current = self.currentData()
        for text, data in self._items:
            if data == current:
                return text
        return ""

    def set_multi_mode(self, enabled: bool) -> None:
        self._multi = bool(enabled)
        if not self._multi and self._selected_ids:
            self._selected_ids = self._selected_ids[:1]
        self._refresh_display()

    def is_multi_mode(self) -> bool:
        return self._multi

    def selected_project_ids(self) -> list[str]:
        return list(self._selected_ids)

    def set_selected_project_ids(self, selected_ids: list[str], emit: bool = False) -> None:
        valid_ids = {data for _, data in self._items}
        selected: list[str] = []
        for project_id in selected_ids:
            value = str(project_id or "")
            if value in valid_ids and value not in selected:
                selected.append(value)
            if len(selected) == 3:
                break
        if not self._multi and selected:
            selected = selected[:1]
        self._selected_ids = selected
        if selected:
            index = self.findData(selected[0])
            if index >= 0:
                self._current_index = index
        self._refresh_display()
        if emit:
            self.selection_changed.emit(self.selected_project_ids())

    def show_project_popup(self) -> None:
        default_project_id = self._default_project_id or str(self.currentData() or "")
        self.popup.set_data(self._items, self._selected_ids, self._multi, default_project_id)
        self.popup.popup_below(self)

    def _apply_selection(self, selected_ids: list[str]) -> None:
        self._selected_ids = list(selected_ids)
        if self._selected_ids:
            index = self.findData(self._selected_ids[0])
            if index >= 0:
                self._current_index = index
        self._refresh_display()
        self.selection_changed.emit(self.selected_project_ids())

    def _refresh_display(self) -> None:
        names = {data: text for text, data in self._items}
        selected_names = [names[item] for item in self._selected_ids if item in names]
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self._multi:
            self.summary_label.hide()
            self.chips_widget.show()
            for name in selected_names[:3]:
                chip = QLabel(name)
                chip.setObjectName("projectSelectionChip")
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                chip.setFixedHeight(22)
                self.chips_layout.addWidget(chip)
            self.chips_layout.addStretch(1)
        else:
            self.chips_widget.hide()
            self.summary_label.show()
            self.summary_label.setText(selected_names[0] if selected_names else "请选择项目")


class ModernCalendarDialog(QDialog):
    def __init__(self, selected_date: date, parent=None):
        super().__init__(parent)
        self.setObjectName("calendarPopup")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(324)

        surface = QFrame(self)
        self.surface = surface
        surface.setObjectName("calendarSurface")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.addWidget(surface)

        layout = QVBoxLayout(surface)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.previous_button = QPushButton("‹")
        self.previous_button.setObjectName("calendarNavButton")
        self.previous_button.setFixedSize(30, 30)
        self.month_label = QLabel()
        self.month_label.setObjectName("calendarMonthLabel")
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = QPushButton("›")
        self.next_button.setObjectName("calendarNavButton")
        self.next_button.setFixedSize(30, 30)
        nav.addWidget(self.previous_button)
        nav.addWidget(self.month_label, 1)
        nav.addWidget(self.next_button)
        layout.addLayout(nav)

        self.calendar = QCalendarWidget(surface)
        self.calendar.setObjectName("modernCalendar")
        self.calendar.setNavigationBarVisible(False)
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setSelectedDate(QDate(selected_date.year, selected_date.month, selected_date.day))
        self.calendar.setMinimumDate(QDate(2020, 1, 1))
        self.calendar.setMaximumDate(QDate.currentDate())
        self.calendar.setFixedHeight(228)
        layout.addWidget(self.calendar)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.today_button = QPushButton("今天")
        self.today_button.setObjectName("calendarTextButton")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("calendarTextButton")
        self.confirm_button = QPushButton("确定")
        self.confirm_button.setObjectName("calendarConfirmButton")
        footer.addWidget(self.today_button)
        footer.addStretch(1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.confirm_button)
        layout.addLayout(footer)

        self.previous_button.clicked.connect(self.calendar.showPreviousMonth)
        self.next_button.clicked.connect(self.calendar.showNextMonth)
        self.today_button.clicked.connect(self._select_today)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.calendar.activated.connect(lambda _: self.accept())
        self.calendar.currentPageChanged.connect(self._update_month_label)
        self._update_month_label(self.calendar.yearShown(), self.calendar.monthShown())

        self.setStyleSheet(f"""
            QDialog#calendarPopup {{
                background: transparent;
                border: 0;
            }}
            QFrame#calendarSurface {{
                background: transparent;
                border: 0;
            }}
            QLabel#calendarMonthLabel {{
                color: #16233b;
                font-family: {FONT_STACK};
                font-size: 10pt;
            }}
            QPushButton#calendarNavButton {{
                border: 0;
                border-radius: 7px;
                background: transparent;
                color: #49617f;
                font-family: "Segoe UI";
                font-size: 18pt;
                padding: 0;
            }}
            QPushButton#calendarNavButton:hover {{ background: #edf4ff; color: #2f6fed; }}
            QCalendarWidget#modernCalendar QWidget {{
                alternate-background-color: #ffffff;
                background: #ffffff;
                color: #253650;
                font-family: {FONT_STACK};
                font-size: 9pt;
            }}
            QCalendarWidget#modernCalendar QAbstractItemView:enabled {{
                selection-background-color: #3f7cf4;
                selection-color: #ffffff;
                border: 0;
                outline: 0;
            }}
            QPushButton#calendarTextButton, QPushButton#calendarConfirmButton {{
                min-width: 52px;
                min-height: 30px;
                border-radius: 8px;
                padding: 0 10px;
                font-family: {FONT_STACK};
                font-size: 9pt;
            }}
            QPushButton#calendarTextButton {{
                color: #49617f;
                background: transparent;
                border: 1px solid transparent;
            }}
            QPushButton#calendarTextButton:hover {{ background: #f1f5fb; }}
            QPushButton#calendarConfirmButton {{
                color: #ffffff;
                background: #3f7cf4;
                border: 1px solid #2f6fed;
            }}
            QPushButton#calendarConfirmButton:hover {{ background: #326ee9; }}
        """)

    def _select_today(self) -> None:
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.showToday()

    def _update_month_label(self, year: int, month: int) -> None:
        self.month_label.setText(f"{year}年 {month}月")

    def selected_date(self) -> date:
        return self.calendar.selectedDate().toPython()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#cad8ea"), 1))
        frame = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.drawRoundedRect(frame, 11.25, 11.25)
        painter.end()
        super().paintEvent(event)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.reject()


class MainWindow(QMainWindow):
    def __init__(
        self,
        root: str | Path,
        *,
        kst_api_manager_factory: Callable[..., KstApiManager] = KstApiManager,
    ):
        super().__init__()
        self.root = Path(root)
        self.projects: list[ProjectSummary] = []
        self.stage_labels: dict[str, QPushButton] = {}
        self.stage_buttons: list[QPushButton] = []
        self.period_values = ["11点", "15点", "18点"]
        self.current_task_type = ""
        self.current_project_name = ""
        self.current_hour = ""
        self.current_daily_date_text = ""
        self.current_status = "idle"
        self.current_start_time = ""
        self._last_pet_event = ""
        self._quitting = False
        self._log_queue: deque[str] = deque()
        self._log_current_line: str | None = None
        self._log_current_visible = 0
        self._log_current_block = -1
        self._log_pending_chars = 0
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(16)
        self._log_timer.timeout.connect(self.drain_log_display)
        self.pending_update_release: ReleaseUpdate | None = None
        self.pending_update_version = ""
        self.pending_update_archive: Path | None = None
        self.calendar_dialog: ModernCalendarDialog | None = None
        self.data_source_preference = get_data_source_preference(self.root)
        self.kst_data_source = get_kst_data_source(self.root)
        self.open_excel_automatically = load_auto_open_excel(self.root)
        self._manual_update_check_requested = False
        self._task_stop_requested = False
        self._task_stop_locked = False
        self._task_active = False
        self._task_observed_stage = ""
        self._task_last_output_at = 0.0
        self._task_output_warning_sent = False
        self._task_stop_gate: Path | None = None
        self._multi_task_active = False
        self._saved_multi_project_ids: list[str] = []
        self._quit_after_task = False
        self._kst_api_requested = self.kst_data_source == "local_api"
        self._kst_api_manager_active = False
        self._application_exiting = False
        self._kst_path_prompted = False
        self._pet_scale_save_timer = QTimer(self)
        self._pet_scale_save_timer.setSingleShot(True)
        self._pet_scale_save_timer.setInterval(250)
        self._pet_scale_save_timer.timeout.connect(self._persist_desktop_pet_scale)

        self.runner = QtTaskRunner(self)
        self.runner.output.connect(self.on_task_output)
        self.runner.stage_changed.connect(self.mark_stage)
        self.runner.started.connect(self.on_task_started)
        self.runner.finished.connect(self.on_task_finished)
        self.runner.failed_to_start.connect(self.show_task_error)
        self._task_output_watchdog = QTimer(self)
        self._task_output_watchdog.setInterval(1000)
        self._task_output_watchdog.timeout.connect(self.check_task_output_watchdog)

        self.environment_runner = QtTaskRunner(self)
        self.environment_runner.output.connect(self.on_environment_install_output)
        self.environment_runner.finished.connect(self.on_environment_install_finished)
        self.environment_runner.failed_to_start.connect(self.on_environment_install_failed)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        icon = QIcon(str(app_icon_path(self.root)))
        self.setWindowIcon(icon)
        self.setWindowTitle(WINDOW_HEADER_TITLE)
        base_font = QFont(FONT_FAMILY, MAIN_FONT_PT)
        base_font.setWeight(QFont.Weight.Normal)
        self.setFont(base_font)
        self.setFixedSize(BASE_WINDOW_SIZE)
        self._drag_offset = None
        self._build_ui()
        self._apply_style()
        self._build_tray()
        self.kst_api_manager = kst_api_manager_factory(self.root)
        self.kst_api_manager.status_changed.connect(
            self.on_kst_api_status_changed
        )
        self.kst_api_manager.log_message.connect(self.append_log)
        activity_message = getattr(
            self.kst_api_manager,
            "activity_message",
            None,
        )
        if activity_message is not None:
            activity_message.connect(self.append_log)
        self.update_manager = GitHubUpdateManager(self)
        self.update_manager.checking.connect(self.on_update_checking)
        self.update_manager.available.connect(self.on_update_available)
        self.update_manager.download_progress.connect(self.on_update_download_progress)
        self.update_manager.ready.connect(self.on_update_ready)
        self.update_manager.up_to_date.connect(self.on_update_up_to_date)
        self.update_manager.failed.connect(self.on_update_failed)
        self.pet_mode = load_pet_mode(self.root)
        self.pet_scale = load_pet_scale(self.root)
        self.pet_position = load_pet_position(self.root)
        self.desktop_pet = ClawdDesktopPet(self.root, self.toggle_console_visibility, self.save_desktop_pet_position)
        self.desktop_pet.set_pet_scale(self.pet_scale)
        self.desktop_pet.restore_position(self.pet_position)
        self._sync_pet_menu()
        self._sync_excel_auto_open_menu()
        self.desktop_pet.set_enabled(self.pet_mode == PET_CLAWD)
        self.refresh_projects()
        self.set_current_flow_idle()
        if self._kst_api_requested:
            QTimer.singleShot(0, self._apply_kst_api_request)
        QTimer.singleShot(0, self.run_startup_check)
        QTimer.singleShot(450, self.show_pet_greeting)

    def _build_tray(self) -> None:
        self.tray_menu = QMenu(self)
        self.tray_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.tray_open_action = QAction("打开控制台", self.tray_menu)
        self.tray_exit_action = QAction("退出程序", self.tray_menu)
        self.tray_open_action.triggered.connect(self.show_console)
        self.tray_exit_action.triggered.connect(self.exit_application)
        self.tray_menu.addAction(self.tray_open_action)
        self.tray_menu.addAction(self.tray_exit_action)
        self._style_menu(self.tray_menu, 156)

        self.tray_icon = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_icon.setToolTip(PRODUCT_DISPLAY_NAME)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_console()

    def _make_card(self, object_name: str = "dashboardCard") -> QFrame:
        card = QFrame()
        card.setObjectName(object_name)
        return card

    def _make_icon_label(self, text: str, name: str = "cardIcon") -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(28, 28)
        return label

    def _make_icon_box(self, kind: str, color: str = "#2f80ed", box_size: int = 28, icon_size: int = 20) -> QLabel:
        label = QLabel()
        label.setObjectName("cardIcon")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(box_size, box_size)
        label.setPixmap(make_line_icon(kind, color, icon_size).pixmap(icon_size, icon_size))
        return label

    def _make_primary_button(self, text: str, icon: str = "play") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("primaryActionButton")
        button.setIcon(make_line_icon(icon, "#ffffff", 20))
        button.setIconSize(QSize(18, 18))
        button.setMinimumHeight(42)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _make_secondary_button(self, text: str, icon: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("secondaryActionButton")
        if icon:
            button.setIcon(make_line_icon(icon, "#2f80ed", 20))
            button.setIconSize(QSize(18, 18))
        button.setMinimumHeight(40)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _style_menu(self, menu: QMenu, width: int = 228) -> None:
        menu.setMinimumWidth(width)
        menu.setFont(QFont(FONT_LIGHT_FAMILY, MAIN_FONT_PT))
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.setWindowFlags(
            menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        menu.setStyleSheet(f"""
            QMenu {{
                background: #ffffff;
                color: #1d2a40;
                border: 1px solid #cfd9e7;
                border-radius: 10px;
                padding: 7px;
                font-family: {FONT_STACK};
                font-size: 9pt;
            }}
            QMenu::item {{
                min-height: 32px;
                padding: 2px 28px 2px 12px;
                border-radius: 7px;
            }}
            QMenu::item:selected {{
                color: #173765;
                background: #edf4ff;
            }}
            QMenu::item:disabled {{ color: #9aa8ba; }}
            QMenu::separator {{
                height: 1px;
                margin: 6px 4px;
                background: #e4eaf2;
            }}
            QMenu::indicator {{
                width: 14px;
                height: 14px;
                left: 10px;
            }}
        """)

    def _build_ui(self) -> None:
        root_widget = QWidget()
        root_widget.setObjectName("appSurface")
        root_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(root_widget)
        root_layout = QVBoxLayout(root_widget)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(0)

        self.window_surface = QFrame()
        self.window_surface.setObjectName("windowSurface")
        self.window_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.window_shadow = QGraphicsDropShadowEffect(self.window_surface)
        self.window_shadow.setBlurRadius(8)
        self.window_shadow.setOffset(0, 0)
        self.window_shadow.setColor(QColor(70, 80, 94, 38))
        self.window_surface.setGraphicsEffect(self.window_shadow)
        surface_layout = QVBoxLayout(self.window_surface)
        surface_layout.setContentsMargins(1, 1, 1, 1)
        surface_layout.setSpacing(0)
        root_layout.addWidget(self.window_surface)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.title_bar.setFixedHeight(39)
        self.title_layout = QHBoxLayout(self.title_bar)
        title_layout = self.title_layout
        title_layout.setContentsMargins(14, 0, 10, 0)
        title_layout.setSpacing(2)
        self.spinner = ClawdAnimator(self.title_bar, width=46, height=24, background="#edf0f4")
        title_layout.addWidget(self.spinner)
        self.title_label = QLabel(WINDOW_HEADER_TITLE)
        self.title_label.setObjectName("windowTitleLabel")
        title_font = QFont(FONT_TITLE_FAMILY, TITLE_FONT_PT)
        title_font.setWeight(QFont.Weight.Bold)
        self.title_label.setFont(title_font)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_layout.addWidget(self.title_label)
        title_layout.addSpacing(10)

        self.system_config_button = HoverMenuButton("系统")
        self.system_config_button.setObjectName("systemConfigButton")
        config_font = QFont(FONT_LIGHT_FAMILY, MAIN_FONT_PT)
        config_font.setWeight(QFont.Weight.Normal)
        self.system_config_button.setFont(config_font)
        config_metrics = self.system_config_button.fontMetrics()
        title_text_height = max(self.title_label.fontMetrics().height(), config_metrics.height()) + 4
        self.title_label.setFixedHeight(title_text_height)
        self.system_config_button.setFixedSize(
            config_metrics.horizontalAdvance(self.system_config_button.text()) + 10,
            title_text_height,
        )
        self.system_config_menu = QMenu(self.system_config_button)
        self.system_config_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.project_check_action = QAction("项目配置检查", self.system_config_menu)
        update_path_action = QAction("更新 Excel 路径", self.system_config_menu)
        update_credentials_action = QAction("更新账号密码", self.system_config_menu)
        import_secrets_action = QAction("导入授权配置", self.system_config_menu)
        export_secrets_action = QAction("导出授权配置", self.system_config_menu)
        restore_backup_action = QAction("恢复备份", self.system_config_menu)
        excel_path_config_action = QAction("Excel 路径配置", self.system_config_menu)
        self.project_check_action.triggered.connect(self.run_environment_preflight)
        update_path_action.triggered.connect(self.open_selected_project_config)
        update_credentials_action.triggered.connect(self.open_credentials_config)
        import_secrets_action.triggered.connect(self.import_authorization_config)
        export_secrets_action.triggered.connect(self.export_authorization_config)
        restore_backup_action.triggered.connect(self.restore_backup)
        excel_path_config_action.triggered.connect(self.configure_excel_paths_from_folder)
        self.system_config_menu.addAction(self.project_check_action)
        self.system_config_menu.addSeparator()
        # 保留 QAction 和处理函数，暂时不向菜单暴露路径、账号密码编辑入口。
        # self.system_config_menu.addAction(update_path_action)
        # self.system_config_menu.addAction(update_credentials_action)
        self.system_config_menu.addAction(import_secrets_action)
        self.system_config_menu.addAction(export_secrets_action)
        self.system_config_menu.addAction(restore_backup_action)
        self.system_config_menu.addAction(excel_path_config_action)
        self.system_config_menu.addSeparator()
        self.kst_mode_menu = QMenu("快商通模式", self.system_config_menu)
        self.kst_mode_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.kst_mode_group = QActionGroup(self.kst_mode_menu)
        self.kst_mode_group.setExclusive(True)
        self.kst_api_action = QAction("API 自动获取", self.kst_mode_group)
        self.kst_export_action = QAction("人工导出对话", self.kst_mode_group)
        self.kst_api_action.setCheckable(True)
        self.kst_export_action.setCheckable(True)
        self.kst_api_action.triggered.connect(
            lambda checked: checked and self.set_global_kst_data_source("local_api")
        )
        self.kst_export_action.triggered.connect(
            lambda checked: checked and self.set_global_kst_data_source("export")
        )
        self.kst_mode_menu.addAction(self.kst_api_action)
        self.kst_mode_menu.addAction(self.kst_export_action)
        self.kst_mode_menu.addSeparator()
        self.kst_installation_root_action = QAction(
            "选择快商通程序目录",
            self.kst_mode_menu,
        )
        self.kst_data_root_action = QAction(
            "选择快商通数据目录",
            self.kst_mode_menu,
        )
        self.kst_rescan_action = QAction(
            "重新扫描快商通",
            self.kst_mode_menu,
        )
        self.kst_installation_root_action.triggered.connect(
            self.choose_kst_installation_root
        )
        self.kst_data_root_action.triggered.connect(
            self.choose_kst_data_root
        )
        self.kst_rescan_action.triggered.connect(self.rescan_kst_api)
        self.kst_mode_menu.addAction(self.kst_installation_root_action)
        self.kst_mode_menu.addAction(self.kst_data_root_action)
        self.kst_mode_menu.addAction(self.kst_rescan_action)
        self.system_config_menu.addMenu(self.kst_mode_menu)
        self.system_config_menu.addSeparator()
        self.excel_auto_open_menu = QMenu("Excel 自动打开", self.system_config_menu)
        self.excel_auto_open_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.excel_auto_open_group = QActionGroup(self.excel_auto_open_menu)
        self.excel_auto_open_group.setExclusive(True)
        self.excel_auto_start_action = QAction("启动", self.excel_auto_open_group)
        self.excel_auto_stop_action = QAction("停止", self.excel_auto_open_group)
        self.excel_auto_start_action.setCheckable(True)
        self.excel_auto_stop_action.setCheckable(True)
        self.excel_auto_start_action.triggered.connect(lambda checked: checked and self.set_excel_auto_open(True))
        self.excel_auto_stop_action.triggered.connect(lambda checked: checked and self.set_excel_auto_open(False))
        self.excel_auto_open_menu.addAction(self.excel_auto_start_action)
        self.excel_auto_open_menu.addAction(self.excel_auto_stop_action)
        self.system_config_menu.addMenu(self.excel_auto_open_menu)
        self.system_config_menu.addSeparator()
        self.pet_menu = QMenu("桌面宠物", self.system_config_menu)
        self.pet_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.system_config_menu.addMenu(self.pet_menu)
        self.pet_action_group = QActionGroup(self.pet_menu)
        self.pet_action_group.setExclusive(True)
        self.clawd_pet_action = QAction("Clawd 小螃蟹", self.pet_action_group)
        self.hidden_pet_action = QAction("隐藏宠物", self.pet_action_group)
        self.clawd_pet_action.setCheckable(True)
        self.hidden_pet_action.setCheckable(True)
        self.clawd_pet_action.triggered.connect(lambda checked: checked and self.set_desktop_pet_mode(PET_CLAWD))
        self.hidden_pet_action.triggered.connect(lambda checked: checked and self.set_desktop_pet_mode(PET_HIDDEN))
        self.pet_menu.addAction(self.clawd_pet_action)
        self.pet_menu.addAction(self.hidden_pet_action)
        self.pet_menu.addSeparator()
        self.system_config_menu.addSeparator()
        exit_action = QAction("退出程序", self.system_config_menu)
        exit_action.triggered.connect(self.exit_application)
        self.system_config_menu.addAction(exit_action)
        self._style_menu(self.system_config_menu, 236)
        self._style_menu(self.kst_mode_menu, 206)
        self._style_menu(self.excel_auto_open_menu, 206)
        self._style_menu(self.pet_menu, 206)
        self.inline_config_menu = InlineConfigMenu(self)
        self.inline_config_menu.project_check_requested.connect(self.run_environment_preflight)
        self.inline_config_menu.update_path_requested.connect(self.open_selected_project_config)
        self.inline_config_menu.update_credentials_requested.connect(self.open_credentials_config)
        self.inline_config_menu.import_secrets_requested.connect(self.import_authorization_config)
        self.inline_config_menu.export_secrets_requested.connect(self.export_authorization_config)
        self.inline_config_menu.restore_backup_requested.connect(self.restore_backup)
        self.inline_config_menu.excel_path_config_requested.connect(self.configure_excel_paths_from_folder)
        self.inline_config_menu.excel_auto_open_requested.connect(self.set_excel_auto_open)
        self.inline_config_menu.kst_data_source_requested.connect(
            self.set_global_kst_data_source
        )
        self.inline_config_menu.kst_installation_root_requested.connect(
            self.choose_kst_installation_root
        )
        self.inline_config_menu.kst_data_root_requested.connect(
            self.choose_kst_data_root
        )
        self.inline_config_menu.kst_rescan_requested.connect(
            self.rescan_kst_api
        )
        self.inline_config_menu.pet_mode_requested.connect(self.set_desktop_pet_mode)
        self.inline_config_menu.pet_scale_requested.connect(self.set_desktop_pet_scale)
        self.inline_config_menu.exit_requested.connect(self.exit_application)
        self._sync_kst_mode_menu()
        self.system_config_button.clicked.connect(self.show_system_config_menu)
        title_layout.addWidget(self.system_config_button)

        self.help_button = HoverMenuButton("帮助")
        self.help_button.setObjectName("systemConfigButton")
        self.help_button.setFont(config_font)
        self.help_button.setFixedSize(
            config_metrics.horizontalAdvance(self.help_button.text()) + 10,
            title_text_height,
        )
        self.help_menu = QMenu(self.help_button)
        self.help_menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        about_clawd_action = QAction("关于小螃蟹", self.help_menu)
        check_version_action = QAction("检查版本", self.help_menu)
        about_clawd_action.triggered.connect(self.show_about_clawd)
        check_version_action.triggered.connect(self.check_updates_manually)
        self.help_menu.addAction(about_clawd_action)
        self.help_menu.addAction(check_version_action)
        self._style_menu(self.help_menu, 164)
        self.help_button.clicked.connect(self.show_help_menu)
        title_layout.addWidget(self.help_button)

        self.update_button = QPushButton("更新")
        self.update_button.setObjectName("updateButton")
        self.update_button.setProperty("updateState", "hidden")
        self.update_button.setIconSize(QSize(11, 11))
        self.update_button.setFixedSize(20, 20)
        self.update_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_button.setToolTip("检查更新")
        self.update_button.clicked.connect(self.handle_update_button_clicked)
        self.update_button.hide()
        title_layout.addWidget(self.update_button)
        title_layout.addStretch(1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        self.minimize_button = QPushButton("")
        self.minimize_button.setObjectName("windowControlButton")
        self.minimize_button.setIcon(make_line_icon("minimize", "#40536d", 18))
        self.minimize_button.setToolTip("最小化")
        self.maximize_button = QPushButton("")
        self.maximize_button.setObjectName("windowControlButton")
        self.maximize_button.setIcon(make_line_icon("maximize", "#40536d", 18))
        self.maximize_button.setToolTip("最大化")
        self.close_button = QPushButton("")
        self.close_button.setObjectName("windowCloseButton")
        self.close_button.setIcon(make_line_icon("close", "#40536d", 18))
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setFixedSize(34, 32)
            button.setIconSize(QSize(16, 16))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setToolTip("关闭界面")
        controls.addWidget(self.minimize_button, 0, Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.maximize_button, 0, Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignCenter)
        title_layout.addLayout(controls)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximize)
        self.close_button.clicked.connect(self.request_console_close)
        surface_layout.addWidget(self.title_bar)

        self.shell_surface = QFrame()
        self.shell_surface.setObjectName("shellSurface")
        self.shell_surface.setFrameShape(QFrame.Shape.NoFrame)
        self.shell_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shell = QHBoxLayout(self.shell_surface)
        shell.setContentsMargins(4, 4, 4, 5)
        shell.setSpacing(14)
        self.shell_layout = shell
        surface_layout.addWidget(self.shell_surface, 1)

        self.left_panel = QFrame()
        self.left_panel.setObjectName("leftRail")
        self.left_panel.setFixedWidth(372)
        self.left_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.left_layout = QVBoxLayout(self.left_panel)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(14)
        self._build_left_panel(self.left_layout)

        self.content_panel = QFrame()
        self.content_panel.setObjectName("contentPanel")
        self.content_panel.setMinimumWidth(520)
        self.content_layout = QVBoxLayout(self.content_panel)
        self.content_layout.setContentsMargins(20, 18, 20, 18)
        self.content_layout.setSpacing(14)
        self._build_right_panel(self.content_layout)

        shell.addWidget(self.left_panel)
        shell.addWidget(self.content_panel, 1)

    def _build_left_panel(self, left_layout: QVBoxLayout) -> None:
        self.task_control_card = self._make_card()
        self.task_control_card.setFixedHeight(208)
        task_layout = QVBoxLayout(self.task_control_card)
        task_layout.setContentsMargins(18, 14, 18, 14)
        task_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.addWidget(self._make_icon_box("play", box_size=42, icon_size=22))
        task_heading = QVBoxLayout()
        task_heading.setContentsMargins(0, 0, 0, 0)
        task_heading.setSpacing(2)
        self.task_title = QLabel("项目控制台")
        self.task_title.setObjectName("cardTitle")
        subtitle = QLabel("选择项目和任务，执行过程会实时显示")
        subtitle.setObjectName("mutedText")
        task_heading.addWidget(self.task_title)
        task_heading.addWidget(subtitle)
        title_row.addLayout(task_heading)
        title_row.addStretch(1)
        task_layout.addLayout(title_row)

        project_title_row = QHBoxLayout()
        project_title_row.setSpacing(8)
        self.data_source_control = DataSourceModeControl()
        self.data_source_control.set_preference(
            self.data_source_preference,
            animate=False,
            emit=False,
        )
        self.data_source_control.preference_changed.connect(self.set_global_data_source_preference)
        project_title_row.addWidget(self.data_source_control)
        project_title_row.addStretch(1)
        self.multi_preview_hint = QLabel("功能暂未上线，仅供预览")
        self.multi_preview_hint.setObjectName("multiPreviewHint")
        self.multi_preview_hint.hide()
        project_title_row.addWidget(self.multi_preview_hint)
        self.project_mode_segment = SlidingProjectModeControl()
        self.single_project_button = self.project_mode_segment.single_button
        self.multi_project_button = self.project_mode_segment.multi_button
        self.project_mode_segment.mode_changed.connect(self.set_project_selection_mode)
        project_title_row.addWidget(self.project_mode_segment)
        task_layout.addLayout(project_title_row)

        self.project_combo = ProjectSelectorButton()
        self.project_combo.setFixedHeight(42)
        self.project_combo.selection_changed.connect(self.on_project_selection_changed)
        task_layout.addWidget(self.project_combo)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 0, 0)
        status_row.setSpacing(8)
        self.ready_dot = QLabel()
        self.ready_dot.setObjectName("readyDot")
        self.ready_dot.setFixedSize(9, 9)
        status_row.addWidget(self.ready_dot)
        self.progress_text = QLabel("环境已就绪，请选择任务开始执行")
        self.progress_text.setObjectName("taskProgressText")
        self.progress_text.setFont(QFont(FONT_FAMILY, SUB_FONT_PT))
        self.progress_text.setWordWrap(True)
        status_row.addWidget(self.progress_text, 1)
        task_layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setObjectName("taskProgress")
        self.progress.setRange(0, len(STAGES))
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        left_layout.addWidget(self.task_control_card)

        self.hourly_card = self._make_card()
        self.hourly_card.setFixedHeight(202)
        hourly_layout = QVBoxLayout(self.hourly_card)
        hourly_layout.setContentsMargins(18, 14, 18, 14)
        hourly_layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self._make_icon_box("clock"))
        self.hourly_title = QLabel("小时报")
        self.hourly_title.setObjectName("cardTitle")
        header.addWidget(self.hourly_title)
        header.addStretch(1)
        hourly_layout.addLayout(header)

        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(12)
        action_grid.setVerticalSpacing(10)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        self.hourly_action_control = RunStopSplitControl("运行小时报")
        self.hourly_action_control.setFixedHeight(48)
        self.hourly_button = self.hourly_action_control.run_button
        self.hourly_stop_button = self.hourly_action_control.stop_button
        action_grid.addWidget(self.hourly_action_control, 0, 0)
        self.period_group = QButtonGroup(self)
        self.period_group.setExclusive(True)
        self.period_buttons: list[QPushButton] = []
        period_positions = [(0, 1), (1, 0), (1, 1)]
        for text, position in zip(self.period_values, period_positions):
            button = PeriodButton(text.replace("点", ":00"), text)
            button.toggled.connect(self.update_period_button_texts)
            self.period_group.addButton(button)
            self.period_buttons.append(button)
            action_grid.addWidget(button, *position)
        self.period_buttons[0].setChecked(True)
        self.update_period_button_texts()
        hourly_layout.addLayout(action_grid)
        left_layout.addWidget(self.hourly_card)

        self.daily_card = self._make_card()
        self.date_card = self.daily_card
        self.daily_card.setFixedHeight(204)
        daily_layout = QVBoxLayout(self.daily_card)
        daily_layout.setContentsMargins(18, 14, 18, 14)
        daily_layout.setSpacing(8)
        daily_header = QHBoxLayout()
        daily_header.setSpacing(12)
        daily_header.addWidget(self._make_icon_box("calendar"))
        self.daily_title = QLabel("日报")
        self.daily_title.setObjectName("dailyCardTitle")
        daily_header.addWidget(self.daily_title)
        daily_header.addStretch(1)
        daily_layout.addLayout(daily_header)

        daily_button_row = QHBoxLayout()
        daily_button_row.setSpacing(12)
        self.daily_action_control = RunStopSplitControl("运行日报")
        self.daily_action_control.setFixedHeight(44)
        self.daily_button = self.daily_action_control.run_button
        self.daily_stop_button = self.daily_action_control.stop_button
        self.word_class_button = self._make_secondary_button("词类占比")
        self.word_class_button.setObjectName("dateHintButton")
        self.word_class_button.setFixedHeight(44)
        self.word_class_button.clicked.connect(self.run_word_class)
        daily_button_row.addWidget(self.daily_action_control, 3)
        daily_button_row.addWidget(self.word_class_button, 1)
        daily_layout.addLayout(daily_button_row)

        date_title = QLabel("日报日期")
        date_title.setObjectName("sectionTitle")
        daily_layout.addWidget(date_title)
        yesterday = date.today() - timedelta(days=1)
        self.current_daily_date = yesterday
        self.date_button = QPushButton(yesterday.isoformat())
        self.date_button.setObjectName("datePickerButton")
        self.date_button.setIcon(make_line_icon("calendar", "#2f80ed", 20))
        self.date_button.setIconSize(QSize(18, 18))
        self.date_button.setFixedHeight(42)
        self.date_button.clicked.connect(self.pick_daily_date)
        daily_layout.addWidget(self.date_button)
        left_layout.addWidget(self.daily_card)

        self.hourly_button.clicked.connect(self.run_hourly)
        self.daily_button.clicked.connect(self.run_daily)
        self.word_class_button.clicked.connect(self.run_word_class)
        self.hourly_stop_button.clicked.connect(self.stop_current_task)
        self.daily_stop_button.clicked.connect(self.stop_current_task)

    def _build_right_panel(self, content_layout: QVBoxLayout) -> None:
        self.status_title = QLabel("准备就绪")
        self.status_detail = QLabel("选择左侧任务开始执行。")
        self.status_title.hide()
        self.status_detail.hide()

        self.stage_panel = QWidget()
        self.stage_panel.setObjectName("stagePanel")
        self.stage_panel.setFixedHeight(0)
        self.stage_panel.hide()
        self.stage_labels.clear()
        self.stage_buttons.clear()

        self.current_flow_panel = self._make_card("currentFlowPanel")
        self.current_flow_panel.setFixedHeight(224)
        flow_layout = QVBoxLayout(self.current_flow_panel)
        flow_layout.setContentsMargins(0, 10, 0, 0)
        flow_layout.setSpacing(12)
        flow_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        flow_header = QHBoxLayout()
        self.flow_header_icon = QLabel()
        self.flow_header_icon.setObjectName("flowHeaderIcon")
        self.flow_header_icon.setProperty("iconKind", "flow")
        self.flow_header_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.flow_header_icon.setFixedSize(30, 30)
        self.flow_header_icon.setPixmap(make_line_icon("flow", "#2f6fed", 24).pixmap(24, 24))
        self.current_flow_title = QLabel("当前流程")
        self.current_flow_title.setObjectName("flowTitle")
        flow_header.addWidget(self.flow_header_icon)
        flow_header.addWidget(self.current_flow_title)
        flow_header.addStretch(1)
        flow_layout.addLayout(flow_header)

        flow_card = QFrame()
        self.flow_status_card = flow_card
        flow_card.setObjectName("flowStatusCard")
        flow_card.setFixedHeight(156)
        flow_card_layout = QHBoxLayout(flow_card)
        flow_card_layout.setContentsMargins(18, 16, 20, 16)
        flow_card_layout.setSpacing(18)
        self.flow_idle_icon = QLabel()
        self.flow_idle_icon.setObjectName("flowIdleIcon")
        self.flow_idle_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.flow_idle_icon.setFixedSize(0, 0)
        self.flow_idle_icon.hide()
        self.flow_crab = ClawdAnimator(flow_card, width=138, height=104, background="#ffffff", zoom=1.2)
        self.flow_spinner = self.flow_crab
        flow_card_layout.addWidget(self.flow_crab)
        flow_text = QVBoxLayout()
        flow_text.setSpacing(6)
        flow_text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.current_task_title = QLabel("暂无运行任务")
        self.current_task_title.setObjectName("currentTaskTitle")
        self.current_task_subtitle = QLabel("请选择左侧任务开始执行")
        self.current_task_subtitle.setObjectName("currentTaskSubtitle")
        flow_text.addWidget(self.current_task_title)
        flow_text.addWidget(self.current_task_subtitle)
        flow_card_layout.addLayout(flow_text, 1)
        flow_right = QVBoxLayout()
        flow_right.setSpacing(14)
        flow_right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.current_status_badge = QLabel("空闲")
        self.current_status_badge.setObjectName("statusBadge")
        self.current_status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_start_time_label = QLabel("开始时间：--")
        self.current_start_time_label.setObjectName("startTimeText")
        self.current_start_time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        flow_right.addWidget(self.current_status_badge)
        flow_right.addWidget(self.current_start_time_label)
        flow_card_layout.addLayout(flow_right)
        flow_layout.addWidget(flow_card)
        content_layout.addWidget(self.current_flow_panel)

        log_title_row = QHBoxLayout()
        log_icon = QLabel()
        log_icon.setObjectName("logHeaderIcon")
        log_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        log_icon.setFixedSize(24, 24)
        log_icon.setPixmap(make_line_icon("report", "#2f6fed", 20).pixmap(20, 20))
        log_header = QLabel("实时日志")
        log_header.setObjectName("logTitle")
        log_title_row.addWidget(log_icon)
        log_title_row.addWidget(log_header)
        log_title_row.addStretch(1)
        content_layout.addLayout(log_title_row)

        self.log_view = LogConsole()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logConsole")
        log_font = QFont(FONT_REGULAR_FAMILY, MAIN_FONT_PT)
        log_font.setWeight(QFont.Weight.Normal)
        self.log_view.setFont(log_font)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.kst_status_control = self.log_view.status_control
        self.log_ready_dot = self.log_view.ready_dot
        self.log_ready_badge = self.log_view.ready_label
        content_layout.addWidget(self.log_view, 1)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow {
                background: #eef3f8;
                color: #16233b;
                font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 9pt;
                font-weight: 700;
            }
            QFrame#titleBar {
                background: #eef3f8;
                border: 0;
            }
            QLabel#windowTitleLabel {
                color: #0f172a;
                font-size: 10pt;
                font-weight: 700;
            }
            QPushButton#systemConfigButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 4px 10px;
                color: #6f7b8d;
                font-size: 10pt;
                text-align: left;
            }
            QPushButton#systemConfigButton:hover {
                background: #e5eef8;
                border-color: #cbd8e8;
            }
            QPushButton#windowCloseButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                color: #12213d;
                font-size: 11pt;
                padding: 0;
            }
            QPushButton#windowCloseButton:hover {
                background: #fde8eb;
                border-color: #efb8c2;
                color: #8f1d2c;
            }
            QPushButton#windowCloseButton:pressed {
                background: #f7cfd6;
                border-color: #e99aa8;
            }
            QFrame#leftRail {
                background: transparent;
                border: 0;
            }
            QFrame#contentPanel, QFrame#dashboardCard, QFrame#currentFlowPanel {
                background: #ffffff;
                border: 1px solid #d8e3f0;
                border-radius: 12px;
            }
            QLabel#cardIcon {
                color: #2f80ed;
                background: #eaf2ff;
                border: 1px solid #cfe1fb;
                border-radius: 9px;
            }
            QLabel#cardTitle {
                color: #0f172a;
                font-size: 10pt;
                font-weight: 400;
            }
            QLabel#sectionTitle {
                color: #111827;
                font-size: 9pt;
                font-weight: 400;
            }
            QLabel#mutedText, QLabel#taskProgressText {
                color: #53647e;
                font-size: 8pt;
            }
            QLabel#pillHint {
                color: #2f80ed;
                background: #eaf2ff;
                border: 1px solid #c7ddf4;
                border-radius: 13px;
                padding: 4px 10px;
                font-size: 8pt;
            }
            QPushButton#projectCombo, QPushButton#datePickerButton {
                min-height: 34px;
                border-radius: 10px;
                border: 0;
                padding: 4px 12px;
                background: #ffffff;
                color: #1f2a44;
                font-size: 9pt;
                text-align: left;
            }
            QPushButton#projectCombo:hover, QPushButton#datePickerButton:hover {
                background: #fafdff;
            }
            QPushButton#datePickerButton { border: 1px solid #cad8ea; text-align: center; }
            QPushButton#datePickerButton:hover { border-color: #7fb2ea; }
            QFrame#projectModeSegment { background: #f1f4f8; border: 1px solid #e1e7ef; border-radius: 8px; }
            QFrame#projectModeIndicator { background: #ffffff; border: 1px solid #d5e0ed; border-radius: 7px; }
            QFrame#projectModeIndicator[preview="true"] { background: #fff1f2; border-color: #f3b8bf; }
            QPushButton#projectModeButton { color: #69788f; background: transparent; border: 0; border-radius: 6px; font-size: 8pt; }
            QPushButton#projectModeButton:checked { color: #246bce; background: transparent; border: 0; }
            QPushButton#projectModeButton:checked[text="多项目"] { color: #b94a58; }
            QLabel#multiPreviewHint { color: #c65c68; font-size: 7pt; }
            QLabel#projectSelectorSummary { color: #1e2d45; font-size: 9pt; }
            QLabel#projectSelectionChip { color: #246bce; background: #edf4ff; border: 1px solid #d2e2fb; border-radius: 7px; padding: 3px 7px; font-size: 8pt; }
            QPushButton {
                outline: 0;
            }
            QPushButton#primaryActionButton {
                background: #3b82f6;
                border: 1px solid #2f80ed;
                border-radius: 10px;
                color: #ffffff;
                padding: 6px 14px;
                font-size: 9pt;
                text-align: center;
            }
            QPushButton#primaryActionButton:hover {
                background: #2f75e8;
            }
            QPushButton#secondaryActionButton, QPushButton#dateHintButton {
                background: #ffffff;
                border: 1px solid #cad8ea;
                border-radius: 10px;
                color: #1f2a44;
                padding: 6px 14px;
                font-size: 9pt;
                text-align: center;
            }
            QPushButton#secondaryActionButton:hover, QPushButton#dateHintButton:hover {
                background: #f5f9ff;
                border-color: #9bb7dc;
            }
            QFrame#verticalDivider {
                background: #dce5ef;
                border: 0;
            }
            QPushButton#periodButton {
                background: #ffffff;
                border: 1px solid #cad8ea;
                border-radius: 10px;
                padding: 3px 10px;
                color: #1f2a44;
                text-align: center;
                font-size: 9pt;
                outline: 0;
            }
            QPushButton#periodButton:checked {
                background: #dff7ea;
                border: 1px solid #45c27a;
                color: #087a46;
            }
            QPushButton#stageActionButton {
                background: #e9f8ef;
                border: 1px solid #98ddb8;
                border-radius: 12px;
                color: #087a46;
                padding: 6px 12px;
                font-size: 9pt;
                text-align: center;
            }
            QPushButton#stageActionButton:hover {
                background: #def4e8;
                border-color: #6dcc9b;
            }
            QPushButton#stageActionButton[active="true"] {
                background: #d9ecff;
                color: #145ea8;
                border-color: #7fb2ea;
            }
            QPushButton#stageActionButton[done="true"] {
                background: #dff7ea;
                color: #087a46;
                border-color: #45c27a;
            }
            QLabel#flowHeaderIcon, QLabel#logHeaderIcon {
                color: #1f2a44;
                font-size: 11pt;
            }
            QLabel#flowTitle, QLabel#logTitle {
                color: #0f172a;
                font-size: 10pt;
                font-weight: 400;
            }
            QFrame#flowStatusCard {
                background: #fbfdff;
                border: 1px solid #d8e3f0;
                border-radius: 12px;
            }
            QFrame#flowAccent {
                background: #3b82f6;
                border: 0;
                border-radius: 2px;
            }
            QLabel#currentTaskTitle {
                color: #0f172a;
                font-size: 10pt;
                font-weight: 400;
            }
            QLabel#currentTaskSubtitle {
                color: #1f2a44;
                font-size: 9pt;
            }
            QLabel#statusBadge {
                min-width: 88px;
                min-height: 28px;
                color: #2f80ed;
                background: #eaf2ff;
                border: 1px solid #d4e6ff;
                border-radius: 14px;
                font-size: 8pt;
            }
            QLabel#statusBadge[status="idle"] {
                color: #64748b;
                background: #f1f5f9;
                border-color: #dbe5ef;
            }
            QLabel#statusBadge[status="done"] {
                color: #087a46;
                background: #dff7ea;
                border-color: #9bd8b6;
            }
            QLabel#statusBadge[status="failed"] {
                color: #9f1239;
                background: #ffe4e6;
                border-color: #fecdd3;
            }
            QLabel#startTimeText {
                color: #1f2a44;
                font-size: 8pt;
            }
            QTextEdit#logConsole {
                background: #08172b;
                color: #e6edf7;
                border-radius: 10px;
                border: 1px solid #12213d;
                padding: 16px;
            }
            QTextEdit#logConsole QScrollBar:vertical {
                width: 0px;
                background: transparent;
                border: 0;
            }
            QTextEdit#logConsole QScrollBar::handle:vertical {
                background: transparent;
                border: 0;
            }
            QTextEdit#logConsole .log-path {
                color: #38d7ff;
            }
            QTextEdit#logConsole .log-pass {
                color: #56e58f;
            }
            QTextEdit#logConsole .log-error {
                color: #fca5a5;
            }
            QTextEdit#logConsole .log-project {
                color: #fcd34d;
            }
            QTextEdit#logConsole .log-excel {
                color: #c4b5fd;
            }
            QProgressBar#taskProgress {
                height: 10px;
                border: 0;
                border-radius: 5px;
                background: #e6edf6;
            }
            QProgressBar#taskProgress::chunk {
                border-radius: 5px;
                background: #5c9af4;
            }
        """)
        self.setStyleSheet(f"""
            QMainWindow {{
                background: transparent;
                color: #17243b;
                font-family: {FONT_STACK};
                font-size: 9pt;
            }}
            QWidget#appSurface {{
                background: transparent;
                border: none;
                border-radius: 0;
            }}
            QFrame#windowSurface {{
                background: #f6f8fc;
                border: 1px solid #e6e9ee;
                border-radius: 0;
            }}
            QFrame#titleBar {{
                background: #edf0f4;
                border: 0;
                border-radius: 0;
            }}
            QLabel#windowTitleLabel {{
                color: #101b2e;
                font-family: {FONT_TITLE_STACK};
                font-size: 10pt;
                font-weight: 700;
            }}
            QPushButton {{ outline: 0; }}
            QPushButton#systemConfigButton {{
                min-height: 0;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 0 4px;
                color: #6f7b8d;
                font-family: {FONT_MENU_STACK};
                font-size: 9pt;
                font-weight: 400;
                text-align: center;
            }}
            QPushButton#systemConfigButton:hover {{
                background: #eef1f5;
                border-color: #e1e6ed;
            }}
            QPushButton#systemConfigButton::menu-indicator {{ image: none; width: 0; }}
            QPushButton#updateButton {{
                min-height: 0;
                background: #3897eb;
                border: 1px solid #2e8ddd;
                border-radius: 10px;
                padding: 0;
                color: #ffffff;
                font-size: 8pt;
                text-align: center;
            }}
            QPushButton#updateButton[updateState="available"],
            QPushButton#updateButton[updateState="ready"],
            QPushButton#updateButton[updateState="failed"] {{ padding: 0 4px; }}
            QPushButton#updateButton[updateState="failed"] {{
                background: #f1f6ff;
                border-color: #b9d2f4;
                color: #2f6fed;
            }}
            QPushButton#updateButton[updateState="current"] {{
                background: #eef6ff;
                border-color: #cfe2ff;
                color: #2f6fed;
                padding: 0 4px;
            }}
            QPushButton#updateButton:hover {{ background: #2689df; border-color: #237dc9; }}
            QPushButton#updateButton[updateState="failed"]:hover {{
                background: #e6f0ff;
                border-color: #9fc3f4;
            }}
            QPushButton#updateButton[updateState="current"]:hover {{
                background: #eef6ff;
                border-color: #cfe2ff;
            }}
            QPushButton#updateButton:pressed {{ background: #1f7dcc; }}
            QPushButton#updateButton:disabled {{ color: #ffffff; }}
            QPushButton#windowControlButton, QPushButton#windowCloseButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 0;
            }}
            QPushButton#windowControlButton:hover {{
                background: #edf1f6;
                border-color: #e0e6ee;
            }}
            QPushButton#windowControlButton:pressed {{ background: #dfe6ef; }}
            QPushButton#windowCloseButton:hover {{
                background: #fde8eb;
                border-color: #efb8c2;
            }}
            QPushButton#windowCloseButton:pressed {{ background: #f7cfd6; }}
            QFrame#shellSurface {{
                background: #f6f8fc;
                border: none;
                border-radius: 0;
            }}
            QFrame#leftRail {{ background: transparent; border: 0; }}
            QFrame#dashboardCard, QFrame#contentPanel {{
                background: #ffffff;
                border: 1px solid #d8e2ef;
                border-radius: 14px;
            }}
            QFrame#currentFlowPanel {{ background: transparent; border: 0; }}
            QLabel#cardIcon {{
                color: #2f6fed;
                background: #edf4ff;
                border: 1px solid #cfe0ff;
                border-radius: 10px;
            }}
            QLabel#cardTitle {{
                color: #101b2e;
                font-family: {FONT_TITLE_STACK};
                font-size: 11pt;
                font-weight: 700;
            }}
            QLabel#dailyCardTitle {{
                color: #101b2e;
                font-family: {FONT_TITLE_STACK};
                font-size: 11pt;
                font-weight: 700;
            }}
            QLabel#sectionTitle {{
                color: #17243b;
                font-family: {FONT_REGULAR_STACK};
                font-size: 9pt;
                font-weight: 400;
            }}
            QLabel#mutedText, QLabel#taskProgressText {{
                color: #65758e;
                font-size: 8pt;
                font-weight: 400;
            }}
            QLabel#readyDot {{
                background: #4fbd68;
                border: 0;
                border-radius: 4px;
            }}
            QLabel#pillHint {{
                min-height: 22px;
                color: #2f6fed;
                background: #edf4ff;
                border: 1px solid #cbdcff;
                border-radius: 11px;
                padding: 0 10px;
                font-size: 8pt;
            }}
            QPushButton#projectCombo, QPushButton#datePickerButton {{
                border-radius: 10px;
                border: 0;
                padding: 0;
                background: #ffffff;
                color: #1e2d45;
                font-family: {FONT_REGULAR_STACK};
                font-size: 9pt;
                font-weight: 400;
                text-align: left;
            }}
            QPushButton#projectCombo:hover, QPushButton#projectCombo:pressed,
            QPushButton#datePickerButton:hover {{
                background: #fbfdff;
            }}
            QPushButton#datePickerButton {{
                border: 1px solid #cad7e8;
                padding: 0 12px;
                text-align: center;
            }}
            QPushButton#datePickerButton:hover {{ border-color: #7fa9ef; }}
            QFrame#projectModeSegment {{
                background: #f1f4f8;
                border: 1px solid #e1e7ef;
                border-radius: 8px;
            }}
            QFrame#projectModeIndicator {{
                background: #ffffff;
                border: 1px solid #d5e0ed;
                border-radius: 7px;
            }}
            QFrame#projectModeIndicator[preview="true"] {{
                background: #fff1f2;
                border-color: #f3b8bf;
            }}
            QPushButton#projectModeButton {{
                color: #69788f;
                background: transparent;
                border: 0;
                border-radius: 6px;
                padding: 0;
                font-size: 8pt;
                font-weight: 400;
            }}
            QPushButton#projectModeButton:checked {{
                color: #246bce;
                background: transparent;
                border: 0;
            }}
            QPushButton#projectModeButton:checked[text="多项目"] {{ color: #b94a58; }}
            QLabel#multiPreviewHint {{ color: #c65c68; font-size: 7pt; }}
            QLabel#projectSelectorSummary {{
                color: #1e2d45;
                background: transparent;
                border: 0;
                font-size: 9pt;
            }}
            QLabel#projectSelectionChip {{
                color: #246bce;
                background: #edf4ff;
                border: 1px solid #d2e2fb;
                border-radius: 7px;
                padding: 3px 7px;
                font-size: 8pt;
            }}
            QPushButton#primaryActionButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #3f83f8, stop:1 #346fe9);
                border: 1px solid #2f6fed;
                border-radius: 10px;
                color: #ffffff;
                padding: 4px 12px;
                font-family: {FONT_TITLE_STACK};
                font-size: 9pt;
                font-weight: 700;
                text-align: center;
            }}
            QPushButton#primaryActionButton:hover {{ background: #326ee9; }}
            QPushButton#primaryActionButton:pressed {{ background: #285fcf; }}
            QPushButton#primaryActionButton:disabled {{
                color: #f3f6fa;
                background: #aabbd5;
                border-color: #9eafc8;
            }}
            QPushButton#secondaryActionButton, QPushButton#dateHintButton {{
                background: #ffffff;
                border: 1px solid #cad7e8;
                border-radius: 10px;
                color: #263750;
                padding: 4px 12px;
                font-family: {FONT_REGULAR_STACK};
                font-size: 9pt;
                font-weight: 400;
                text-align: center;
            }}
            QPushButton#secondaryActionButton:hover, QPushButton#dateHintButton:hover {{
                background: #f5f8fd;
                border-color: #9db8de;
            }}
            QPushButton#periodButton {{
                background: #ffffff;
                border: 1px solid #cad7e8;
                border-radius: 10px;
                padding: 3px 16px;
                color: #23344d;
                font-family: {FONT_REGULAR_STACK};
                text-align: left;
                font-size: 9pt;
                font-weight: 400;
            }}
            QPushButton#periodButton:hover {{ border-color: #91afe0; background: #f8fbff; }}
            QPushButton#periodButton:checked {{
                background: #ffffff;
                border: 1px solid #56bd78;
                color: #23344d;
            }}
            QLabel#flowHeaderIcon, QLabel#logHeaderIcon {{ color: #2f6fed; }}
            QLabel#flowTitle, QLabel#logTitle {{
                color: #101b2e;
                font-family: {FONT_TITLE_STACK};
                font-size: 11pt;
                font-weight: 700;
            }}
            QFrame#flowStatusCard {{
                background: #fbfdff;
                border: 1px solid #d6e2f1;
                border-radius: 14px;
            }}
            QLabel#currentTaskTitle {{
                color: #101b2e;
                font-family: {FONT_TITLE_STACK};
                font-size: 11pt;
                font-weight: 700;
            }}
            QLabel#currentTaskSubtitle {{ color: #65758e; font-size: 9pt; }}
            QLabel#statusBadge {{
                min-width: 82px;
                min-height: 28px;
                color: #2f6fed;
                background: #edf4ff;
                border: 1px solid #d2e1fb;
                border-radius: 14px;
                font-size: 8pt;
            }}
            QLabel#statusBadge[status="idle"] {{
                color: #65758e;
                background: #f5f7fa;
                border-color: #dbe3ed;
            }}
            QLabel#statusBadge[status="done"] {{
                color: #087a46;
                background: #e4f7eb;
                border-color: #a7ddba;
            }}
            QLabel#statusBadge[status="failed"] {{
                color: #9f1239;
                background: #ffe7eb;
                border-color: #f4c5ce;
            }}
            QLabel#statusBadge[status="stopped"] {{
                color: #536176;
                background: #eef1f5;
                border-color: #cfd7e2;
            }}
            QLabel#startTimeText {{ color: #65758e; font-size: 8pt; }}
            QFrame#logReadyOverlay {{ background: transparent; border: 0; }}
            QLabel#logReadyBadge {{
                color: #16914f;
                background: transparent;
                font-size: 8pt;
            }}
            QLabel#logReadyDot {{
                background: #45c96f;
                border: 2px solid #b9efca;
                border-radius: 5px;
            }}
            QTextEdit#logConsole {{
                background: #07172b;
                color: #e8eef8;
                border-radius: 11px;
                border: 1px solid #142943;
                padding: 14px;
                selection-background-color: #285b9f;
            }}
            QTextEdit#logConsole QScrollBar:vertical {{
                width: 0;
                background: transparent;
                border: 0;
            }}
            QTextEdit#logConsole QScrollBar::handle:vertical {{ background: transparent; }}
            QTextEdit#logConsole .log-path {{ color: #38d7ff; }}
            QTextEdit#logConsole .log-pass {{ color: #56e58f; }}
            QTextEdit#logConsole .log-error {{ color: #fca5a5; }}
            QTextEdit#logConsole .log-project {{ color: #fcd34d; }}
            QTextEdit#logConsole .log-excel {{ color: #c4b5fd; }}
        """)

    def show_system_config_menu(self) -> None:
        if self.inline_config_menu.isVisible():
            self.inline_config_menu.hide()
            return
        self.inline_config_menu.sync(
            self.pet_mode,
            self.pet_scale,
            self.open_excel_automatically,
            self.kst_data_source,
        )
        self.inline_config_menu.popup_below(self.system_config_button)

    def show_help_menu(self) -> None:
        self.inline_config_menu.hide()
        self.help_menu.popup(self.help_button.mapToGlobal(QPoint(0, self.help_button.height() + 4)))

    def show_about_clawd(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("aboutClawdDialog")
        dialog.setWindowTitle("关于小螃蟹")
        dialog.setFixedSize(360, 330)
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 26, 28, 18)
        layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = self.root / "assets" / "app_icon.png"
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaled(
                82,
                82,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label.setPixmap(pixmap)
        layout.addWidget(icon_label)

        name_label = QLabel("Clawd 小螃蟹")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setObjectName("aboutClawdTitle")
        layout.addWidget(name_label)

        product_label = QLabel("蚁之力 · 竞价数据自动化")
        product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        product_label.setObjectName("aboutClawdText")
        layout.addWidget(product_label)

        version_label = QLabel(f"版本 {CURRENT_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setObjectName("aboutClawdText")
        layout.addWidget(version_label)

        author_label = QLabel("来源：kaiteJiang / Hourlyreport-Automation")
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_label.setObjectName("aboutClawdText")
        layout.addWidget(author_label)

        copyright_label = QLabel("© kaiteJiang")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setObjectName("aboutClawdText")
        layout.addWidget(copyright_label)
        layout.addStretch(1)

        ok_button = QPushButton("确定")
        ok_button.setObjectName("aboutClawdOkButton")
        ok_button.setFixedSize(82, 32)
        ok_button.clicked.connect(dialog.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(ok_button)
        layout.addLayout(button_row)

        dialog.setStyleSheet(f"""
            QDialog#aboutClawdDialog {{
                background: #ffffff;
                border: 1px solid #d6dee9;
                font-family: {FONT_STACK};
            }}
            QLabel#aboutClawdTitle {{
                color: #111827;
                font-family: {FONT_TITLE_STACK};
                font-size: 12pt;
                font-weight: 700;
            }}
            QLabel#aboutClawdText {{
                color: #4b5563;
                font-family: {FONT_REGULAR_STACK};
                font-size: 9pt;
                font-weight: 400;
            }}
            QPushButton#aboutClawdOkButton {{
                color: #0f172a;
                background: #ffffff;
                border: 1px solid #2f80ed;
                border-radius: 4px;
                font-family: {FONT_REGULAR_STACK};
                font-size: 9pt;
            }}
            QPushButton#aboutClawdOkButton:hover {{
                background: #edf6ff;
            }}
        """)
        self._last_about_dialog = dialog
        dialog.exec()

    def check_updates_manually(self) -> None:
        self._manual_update_check_requested = True
        self.update_manager.start()

    def start_update_check(self) -> None:
        self.update_manager.start()

    def _set_update_button_state(
        self,
        state: str,
        *,
        tooltip: str = "",
        progress: int | None = None,
    ) -> None:
        self.update_button.setProperty("updateState", state)
        if state == "available":
            self.update_button.setIcon(QIcon())
            self.update_button.setText("更新")
            self.update_button.setFixedSize(34, 20)
            self.update_button.setEnabled(True)
            self.update_button.setToolTip(tooltip)
            self.update_button.show()
        elif state == "checking":
            self.update_button.setIcon(make_line_icon("download", "#ffffff", 14))
            self.update_button.setText("")
            self.update_button.setFixedSize(20, 20)
            self.update_button.setEnabled(False)
            self.update_button.setToolTip(tooltip or "正在检查更新")
            self.update_button.show()
        elif state == "downloading":
            text = "" if progress is None or progress <= 0 else f"{progress}%"
            self.update_button.setText(text)
            self.update_button.setIcon(make_line_icon("download", "#ffffff", 14) if not text else QIcon())
            self.update_button.setFixedSize(34 if text else 20, 20)
            self.update_button.setEnabled(False)
            self.update_button.setToolTip(tooltip)
            self.update_button.show()
        elif state == "ready":
            self.update_button.setIcon(QIcon())
            self.update_button.setText("重启")
            self.update_button.setFixedSize(34, 20)
            self.update_button.setEnabled(True)
            self.update_button.setToolTip(tooltip)
            self.update_button.show()
        elif state == "installing":
            self.update_button.setIcon(make_line_icon("loop", "#ffffff", 14))
            self.update_button.setText("")
            self.update_button.setFixedSize(20, 20)
            self.update_button.setEnabled(False)
            self.update_button.setToolTip(tooltip or "正在安装更新")
            self.update_button.show()
        elif state == "failed":
            self.update_button.setIcon(QIcon())
            self.update_button.setText("重试")
            self.update_button.setFixedSize(34, 20)
            self.update_button.setEnabled(True)
            self.update_button.setToolTip(tooltip or "检查更新失败，点击重试")
            self.update_button.show()
        elif state == "current":
            self.update_button.setIcon(QIcon())
            self.update_button.setText("最新")
            self.update_button.setFixedSize(34, 20)
            self.update_button.setEnabled(False)
            self.update_button.setToolTip(tooltip or "当前已是最新版本")
            self.update_button.show()
        else:
            self.update_button.hide()
        self._refresh_widget_style(self.update_button)

    def on_update_checking(self) -> None:
        self._set_update_button_state("checking", tooltip="正在检查更新")

    def on_update_available(self, update: ReleaseUpdate) -> None:
        self._manual_update_check_requested = False
        self.pending_update_release = update
        self.pending_update_version = ""
        self.pending_update_archive = None
        self._set_update_button_state("available", tooltip=f"发现新版本 {update.version}")

    def handle_update_button_clicked(self) -> None:
        state = str(self.update_button.property("updateState") or "")
        if state == "available" and self.pending_update_release is not None:
            if self.update_manager.start_download(self.pending_update_release):
                self._set_update_button_state(
                    "downloading",
                    tooltip="正在下载更新 0%",
                    progress=0,
                )
            return
        if state == "ready":
            self.install_downloaded_update()
            return
        if state == "failed":
            self.start_update_check()

    def on_update_download_progress(self, value: int) -> None:
        progress = max(0, min(100, int(value)))
        self._set_update_button_state(
            "downloading",
            tooltip=f"正在下载更新 {progress}%",
            progress=progress,
        )

    def on_update_ready(self, version: str, archive_path: str | Path) -> None:
        self.pending_update_version = version
        self.pending_update_archive = Path(archive_path)
        self._set_update_button_state("ready", tooltip=f"更新到 {version}")

    def on_update_up_to_date(self) -> None:
        self._manual_update_check_requested = False
        self._set_update_button_state("hidden")

    def on_update_failed(self, message: str) -> None:
        manual_check = self._manual_update_check_requested
        self._manual_update_check_requested = False
        if self.pending_update_release is not None and self.update_button.property("updateState") == "downloading":
            self._set_update_button_state(
                "available",
                tooltip="下载失败，点击重新下载",
            )
            return
        if not manual_check:
            self._set_update_button_state("hidden")
            return
        detail = f"检查更新失败，点击重试：{message}" if message else "检查更新失败，点击重试"
        self._set_update_button_state("failed", tooltip=detail)

    def _hide_current_update_badge(self) -> None:
        if str(self.update_button.property("updateState") or "") == "current":
            self._set_update_button_state("hidden")

    def install_downloaded_update(self) -> None:
        if not self.pending_update_archive or not self.pending_update_version:
            return
        if self.runner.is_running() or self.environment_runner.is_running():
            QMessageBox.information(self, "任务运行中", "请等待当前任务完成后再更新。")
            return
        try:
            launcher = self.root / APP_EXE_NAME
            if not launcher.exists():
                launcher = Path(sys.executable)
            launch_update_helper(
                self.root,
                self.pending_update_archive,
                self.pending_update_version,
                launcher,
            )
        except Exception as exc:
            QMessageBox.warning(self, "更新失败", f"无法启动更新程序：{exc}")
            return
        self._set_update_button_state("installing")
        self.exit_application()

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.setFixedSize(BASE_WINDOW_SIZE)
            self.maximize_button.setIcon(make_line_icon("maximize", "#40536d", 18))
            self.maximize_button.setToolTip("最大化")
            return
        self.setMinimumSize(BASE_WINDOW_SIZE)
        self.setMaximumSize(16777215, 16777215)
        self.showMaximized()
        self.maximize_button.setIcon(make_line_icon("restore", "#40536d", 18))
        self.maximize_button.setToolTip("还原")

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self.title_bar.height():
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self.isMaximized() and event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self.title_bar.height():
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def refresh_projects(self) -> None:
        self.projects = load_project_summaries(self.root)
        self.project_combo.clear()
        for project in self.projects:
            self.project_combo.addItem(project.label, project.project_id)
        default_index = self.project_combo.findData("kunming_niu")
        if default_index >= 0:
            self.project_combo.setCurrentIndex(default_index)
            self.project_combo.set_default_project_id("kunming_niu")
        elif self.project_combo.currentData():
            self.project_combo.set_default_project_id(str(self.project_combo.currentData()))
        available_ids = [project.project_id for project in self.projects]
        fallback_id = str(self.project_combo.currentData() or "")
        self._saved_multi_project_ids = load_multi_project_selection(
            self.root,
            available_ids=available_ids,
            fallback_id=fallback_id,
        )
        has_projects = bool(self.projects)
        self.set_task_buttons_enabled(has_projects)

    def set_project_selection_mode(self, multi: bool) -> None:
        if not multi and self.project_combo.is_multi_mode():
            self._saved_multi_project_ids = self.project_combo.selected_project_ids()
        self.project_combo.set_multi_mode(multi)
        self.multi_preview_hint.hide()
        if multi:
            self.project_combo.set_selected_project_ids(self._saved_multi_project_ids, emit=False)
        self.on_project_selection_changed(self.project_combo.selected_project_ids())

    def on_project_selection_changed(self, selected_ids: list[str]) -> None:
        if (
            hasattr(self, "kst_api_manager")
            and hasattr(self.kst_api_manager, "set_current_project")
        ):
            self.kst_api_manager.set_current_project(
                self.selected_project_id(),
                self.selected_project_name(),
            )
        if not self.project_combo.is_multi_mode():
            self.progress_text.setText("环境已就绪，请选择任务开始执行")
            return
        count = len(selected_ids)
        self._saved_multi_project_ids = list(selected_ids)
        if selected_ids:
            try:
                save_multi_project_selection(self.root, selected_ids)
            except OSError as exc:
                self.append_log(f"[失败] 多项目选择保存失败：{exc}")
        self.progress_text.setText(f"多项目模式：已按顺序选择 {count} 个项目")

    def multi_project_execution_pending(self) -> bool:
        return False

    def _sync_pet_menu(self) -> None:
        self.clawd_pet_action.setChecked(self.pet_mode == PET_CLAWD)
        self.hidden_pet_action.setChecked(self.pet_mode == PET_HIDDEN)
        if hasattr(self, "inline_config_menu"):
            self.inline_config_menu.sync(
                self.pet_mode,
                self.pet_scale,
                self.open_excel_automatically,
                self.kst_data_source,
            )

    def set_global_data_source_preference(self, preference: str) -> None:
        previous = self.data_source_preference
        try:
            saved = save_data_source_preference(self.root, preference)
        except Exception:
            self.data_source_preference = previous
            self.data_source_control.set_preference(
                previous,
                animate=True,
                emit=False,
            )
            message = "数据源设置未保存，已继续使用原设置。"
            self.progress_text.setText(message)
            self.append_log(f"[失败] {message}")
            return
        self.data_source_preference = saved
        if hasattr(self, "data_source_control"):
            self.data_source_control.set_preference(
                self.data_source_preference,
                animate=False,
                emit=False,
            )

    def set_global_kst_data_source(self, mode: str) -> None:
        previous = self.kst_data_source
        try:
            self.kst_data_source = set_kst_data_source(self.root, mode)
        except Exception:
            self.kst_data_source = previous
            self._sync_kst_mode_menu()
            self.append_log("[失败] 快商通模式未保存，已继续使用原设置")
            return
        self._sync_kst_mode_menu()
        label = "API 自动获取" if mode == "local_api" else "人工导出对话"
        self.append_log(f"[设置] 快商通模式已切换为{label}")
        if self.kst_data_source == "local_api":
            self.start_kst_api()
        else:
            self.stop_kst_api()

    def _sync_kst_mode_menu(self) -> None:
        is_api = self.kst_data_source == "local_api"
        self.kst_api_action.setChecked(is_api)
        self.kst_export_action.setChecked(not is_api)
        self.kst_rescan_action.setEnabled(True)
        if hasattr(self, "inline_config_menu"):
            self.inline_config_menu.sync(
                getattr(self, "pet_mode", PET_CLAWD),
                getattr(self, "pet_scale", 1.0),
                self.open_excel_automatically,
                self.kst_data_source,
            )

    def start_kst_api(self) -> None:
        if (
            self._application_exiting
            or self.kst_data_source != "local_api"
        ):
            return
        self._kst_api_requested = True
        self._apply_kst_api_request()

    def _apply_kst_api_request(self) -> None:
        if (
            not self._kst_api_requested
            or self._kst_api_manager_active
            or self._application_exiting
        ):
            return
        self._kst_api_manager_active = True
        self.kst_api_manager.start()

    def stop_kst_api(self) -> None:
        if not self._kst_api_requested and not self._kst_api_manager_active:
            return
        self._kst_api_requested = False
        self._kst_api_manager_active = False
        self.kst_api_manager.stop()
        self.on_kst_api_status_changed(False, "商务通 API 已停止")

    def choose_kst_installation_root(self) -> None:
        try:
            settings = load_kst_machine_settings(self.root)
        except Exception:
            self.append_log("[失败] 快商通本机路径设置无法读取")
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择快商通程序目录",
            str(settings.installation_root or self.root),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        try:
            save_kst_machine_settings(
                self.root,
                installation_root=Path(selected),
                data_root=settings.data_root,
            )
        except Exception:
            self.append_log("[失败] 快商通程序目录未保存")
            return
        self.append_log("[设置] 快商通程序目录已更新")
        self.rescan_kst_api()

    def choose_kst_data_root(self) -> None:
        try:
            settings = load_kst_machine_settings(self.root)
        except Exception:
            self.append_log("[失败] 快商通本机路径设置无法读取")
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择快商通数据目录",
            str(settings.data_root or self.root),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        try:
            save_kst_machine_settings(
                self.root,
                installation_root=settings.installation_root,
                data_root=Path(selected),
            )
        except Exception:
            self.append_log("[失败] 快商通数据目录未保存")
            return
        self.append_log("[设置] 快商通数据目录已更新")
        self.rescan_kst_api()

    def rescan_kst_api(self) -> None:
        if self._application_exiting:
            return
        if self.kst_data_source != "local_api":
            self.set_global_kst_data_source("local_api")
            if self.kst_data_source != "local_api":
                return
        self.start_kst_api()
        self.kst_api_manager.rescan()

    def on_kst_api_status_changed(self, ready: bool, detail: str) -> None:
        self.kst_status_control.set_api_ready(ready, detail)

    def set_excel_auto_open(self, enabled: bool) -> None:
        previous = self.open_excel_automatically
        try:
            save_auto_open_excel(self.root, enabled)
        except Exception as exc:
            message = f"Excel 打开偏好保存失败：{exc}"
            self.progress_text.setText(message)
            self.append_log(f"[失败] {message}")
            self.open_excel_automatically = previous
            self._sync_excel_auto_open_menu()
            return
        self.open_excel_automatically = bool(enabled)
        self._sync_excel_auto_open_menu()

    def should_open_excel_after_task(self, task: str) -> bool:
        return task in {"hourly", "daily", "word_class"} and bool(self.open_excel_automatically)

    def _sync_excel_auto_open_menu(self) -> None:
        enabled = bool(self.open_excel_automatically)
        self.excel_auto_start_action.setChecked(enabled)
        self.excel_auto_stop_action.setChecked(not enabled)
        self.inline_config_menu.sync(
            self.pet_mode,
            self.pet_scale,
            enabled,
            self.kst_data_source,
        )

    def set_desktop_pet_mode(self, mode: str) -> None:
        self.pet_mode = mode
        save_pet_mode(self.root, mode)
        self._sync_pet_menu()
        enabled = mode == PET_CLAWD
        self.desktop_pet.set_enabled(enabled)
        if enabled:
            self.desktop_pet.announce("Clawd 回来啦，点我可以显示或隐藏控制台。", "waving", 5000)

    def set_desktop_pet_scale(self, scale: float) -> None:
        self.pet_scale = normalize_pet_scale(scale)
        self.desktop_pet.set_pet_scale(self.pet_scale)
        self.pet_position = (self.desktop_pet.x(), self.desktop_pet.y())
        self._sync_pet_menu()
        self._pet_scale_save_timer.start()

    def _persist_desktop_pet_scale(self) -> None:
        save_pet_scale(self.root, self.pet_scale)
        save_pet_position(self.root, *self.pet_position)

    def save_desktop_pet_position(self, x: int, y: int) -> None:
        self.pet_position = (int(x), int(y))
        save_pet_position(self.root, *self.pet_position)

    def show_pet_greeting(self) -> None:
        if self.pet_mode == PET_CLAWD and self.desktop_pet.available:
            self.desktop_pet.announce("准备好了。执行任务时，我会在这里汇报进度。", "waving", 5200)

    def toggle_console_visibility(self) -> None:
        self.show_console()

    def show_console(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def request_console_close(self) -> None:
        self.hide()
        if self.pet_mode == PET_CLAWD and self.desktop_pet.is_enabled():
            self.desktop_pet.announce("我会留在这里。点我可以重新打开控制台。", "waving", 5200)

    def exit_application(self) -> None:
        if self._quitting:
            return
        if self.runner.is_running():
            self._quit_after_task = True
            self.request_console_close()
            return
        if self._pet_scale_save_timer.isActive():
            self._pet_scale_save_timer.stop()
            self._persist_desktop_pet_scale()
        self._application_exiting = True
        self._quitting = True
        self.stop_kst_api()
        self.tray_icon.hide()
        self.tray_icon.setContextMenu(None)
        self.desktop_pet.close_pet()
        QApplication.instance().quit()

    def _schedule_deferred_exit(self) -> None:
        if not self._quit_after_task:
            return
        self._quit_after_task = False
        QTimer.singleShot(0, self.exit_application)

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        event.ignore()
        if self.isVisible():
            self.request_console_close()

    def selected_project_id(self) -> str:
        return str(self.project_combo.currentData() or "")

    def selected_project_name(self) -> str:
        text = self.project_combo.currentText()
        if " (" in text:
            return text.split(" (", 1)[0]
        return text

    def selected_project_names(self) -> list[str]:
        names = {project.project_id: project.project_name for project in self.projects}
        return [names[project_id] for project_id in self.project_combo.selected_project_ids() if project_id in names]

    def selected_project_config_path(self) -> Path:
        project_id = self.selected_project_id()
        for project in self.projects:
            if project.project_id == project_id:
                return Path(project.path)
        return self.root / "configs" / "projects" / f"{project_id}.json"

    def credentials_config_path(self) -> Path:
        return self.root / "secrets" / "secrets.json"

    def selected_project_excel_path(self) -> Path:
        project_id = self.selected_project_id()
        project = load_project_config(self.root, project_id)
        return get_excel_path(project, self.root)

    def ensure_credentials_file(self) -> Path:
        path = self.credentials_config_path()
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        example = path.parent / "secrets.example.json"
        if example.exists():
            shutil.copyfile(example, path)
        else:
            path.write_text('{\n  "baidu": {}\n}\n', encoding="utf-8")
        return path

    def configure_excel_paths_from_folder(self) -> None:
        initial_dir = self.root
        try:
            excel_path = self.selected_project_excel_path()
            initial_dir = next(
                (path for path in (excel_path.parent, *excel_path.parents) if path.name == EXCEL_ROOT_NAME),
                excel_path.parent,
            )
        except Exception:
            pass
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择【竞价】文件夹",
            str(initial_dir),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return

        result = configure_excel_paths(self.root, Path(selected))
        if result.errors:
            detail = "\n".join(f"• {error}" for error in result.errors)
            self.append_log("[失败] Excel 路径配置未修改：" + "；".join(result.errors))
            QMessageBox.warning(
                self,
                "Excel 路径配置未修改",
                "未修改任何项目配置，请检查以下路径：\n\n" + detail,
            )
            return

        self.refresh_projects()
        backup_text = str(result.backup_dir) if result.backup_dir else ""
        self.append_log(f"[通过] Excel 路径配置完成：{result.updated} 个项目；备份：{backup_text}")
        QMessageBox.information(
            self,
            "Excel 路径配置完成",
            f"已成功更新 {result.updated} 个项目的 Excel 路径。\n\n配置备份：\n{backup_text}",
        )

    def open_selected_project_config(self) -> None:
        path = self.selected_project_config_path()
        if path.exists():
            self.open_path(path)
            self.append_log(f"已打开当前项目配置：{path}")
        else:
            QMessageBox.warning(self, "未找到项目配置", f"没有找到项目配置文件：{path}")

    def open_credentials_config(self) -> None:
        path = self.ensure_credentials_file()
        self.open_path(path)
        self.append_log(f"已打开账号密码配置：{path}")

    def export_authorization_config(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_dir = Path.home() / "Documents"
        if not default_dir.exists():
            default_dir = Path.home()
        default_path = default_dir / f"百度授权配置_{timestamp}.baidu-secrets"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出授权配置",
            str(default_path),
            "授权配置包 (*.baidu-secrets);;所有文件 (*.*)",
        )
        if not selected:
            return

        output_path = Path(selected)
        if output_path.suffix.casefold() != ".baidu-secrets":
            output_path = Path(str(output_path) + ".baidu-secrets")
        try:
            report = export_secrets_package(self.credentials_config_path(), output_path)
        except SecretsPackageError as exc:
            QMessageBox.warning(self, "导出授权配置失败", str(exc))
            self.append_log(f"导出授权配置失败：{exc}")
            return

        package_path = report.get("package_path") or str(output_path)
        self.append_log(f"授权配置已导出：{package_path}")
        QMessageBox.information(
            self,
            "授权配置已导出",
            f"配置包已保存到：\n{package_path}\n\n这是包含账号密码和 OAuth Token 的明文文件，请仅在公司内部妥善传递。",
        )

    def import_authorization_config(self) -> None:
        if self.runner.is_running() or self.environment_runner.is_running():
            QMessageBox.warning(
                self,
                "任务正在运行",
                "当前任务尚未结束，请等待任务完成后再导入授权配置。",
            )
            return
        while True:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "导入授权配置",
                str(Path.home()),
                "授权配置包 (*.baidu-secrets);;所有文件 (*.*)",
            )
            if not selected:
                return
            try:
                report = import_secrets_package(
                    selected,
                    self.credentials_config_path(),
                    self.root / "backups",
                )
            except SecretsPackageError as exc:
                self.append_log(f"导入授权配置失败：{exc}")
                choice = QMessageBox.warning(
                    self,
                    "导入授权配置失败",
                    str(exc),
                    QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Retry,
                )
                if choice == QMessageBox.StandardButton.Retry:
                    continue
                return

            package_path = report.get("package_path") or selected
            self.append_log(f"授权配置导入完成：{package_path}")
            self.run_environment_preflight(allow_multi=True)
            return

    def restore_backup(self) -> None:
        try:
            target_path = self.selected_project_excel_path()
        except Exception as exc:
            QMessageBox.warning(self, "无法恢复备份", f"读取当前项目 Excel 路径失败：{exc}")
            return

        backup_dir = self.root / "backups"
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择要恢复的备份",
            str(backup_dir),
            "Excel 备份 (*.xlsx *.xlsm *.xls);;所有文件 (*.*)",
        )
        if not selected:
            return

        backup_path = Path(selected)
        if not backup_path.exists():
            QMessageBox.warning(self, "无法恢复备份", f"没有找到备份文件：{backup_path}")
            return
        if not target_path.exists():
            QMessageBox.warning(self, "无法恢复备份", f"没有找到当前项目 Excel：{target_path}")
            return

        message = (
            f"当前项目：{self.project_combo.currentText()}\n\n"
            f"将被覆盖的 Excel：\n{target_path}\n\n"
            f"用于恢复的备份：\n{backup_path}\n\n"
            "确认恢复后，程序会先保存当前 Excel 的一份安全备份。"
        )
        answer = QMessageBox.question(
            self,
            "确认恢复备份",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.append_log("已取消恢复备份。")
            return

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety_backup = backup_dir / f"{target_path.stem}_before_manual_restore_{timestamp}{target_path.suffix}"
            shutil.copy2(target_path, safety_backup)
            shutil.copy2(backup_path, target_path)
        except Exception as exc:
            QMessageBox.warning(self, "恢复备份失败", str(exc))
            self.append_log(f"恢复备份失败：{exc}")
            return

        self.append_log(f"恢复备份完成：{backup_path} -> {target_path}")
        self.append_log(f"恢复前安全备份已保存：{safety_backup}")
        QMessageBox.information(self, "恢复完成", f"已恢复备份。\n\n恢复前安全备份：\n{safety_backup}")

    def selected_period(self) -> str:
        for button in self.period_buttons:
            if button.isChecked():
                return str(button.property("periodValue") or button.text().replace("✓", "").strip())
        return "11点"

    def update_period_button_texts(self) -> None:
        for button in self.period_buttons:
            value = str(button.property("periodValue") or button.text().replace("✓", "").strip())
            button.setText(value.replace("点", ":00"))
            button.setIcon(make_line_icon("clock", "#3f7cf4", 18))
            button.setIconSize(QSize(17, 17))

    def selected_daily_date(self) -> str:
        return self.current_daily_date.isoformat()

    def reset_daily_date_to_yesterday(self) -> None:
        self.current_daily_date = date.today() - timedelta(days=1)
        self.date_button.setText(self.current_daily_date.isoformat())

    def display_daily_date(self) -> str:
        return f"{self.current_daily_date.month}月{self.current_daily_date.day}日"

    def pick_daily_date(self) -> None:
        if self.calendar_dialog is not None and self.calendar_dialog.isVisible():
            self.calendar_dialog.reject()
            return
        dialog = ModernCalendarDialog(self.current_daily_date, self)
        self.calendar_dialog = dialog
        dialog.ensurePolished()
        dialog.adjustSize()
        self._position_calendar_dialog(dialog)
        dialog.accepted.connect(lambda current=dialog: self._apply_calendar_date(current))
        dialog.finished.connect(lambda _result, current=dialog: self._clear_calendar_dialog(current))
        dialog.show()
        dialog.raise_()

    def _position_calendar_dialog(self, dialog: ModernCalendarDialog) -> None:
        button_top_left = self.date_button.mapToGlobal(QPoint(0, 0))
        x = button_top_left.x() + (self.date_button.width() - dialog.width()) // 2
        above_y = button_top_left.y() - dialog.height() - 8
        below_y = button_top_left.y() + self.date_button.height() + 8
        y = above_y
        screen = QApplication.screenAt(button_top_left) or QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            x = max(available.left() + 8, min(x, available.right() - dialog.width() - 8))
            if above_y >= available.top() + 8:
                y = above_y
            elif below_y + dialog.height() <= available.bottom() - 7:
                y = below_y
            else:
                y = max(
                    available.top() + 8,
                    min(above_y, available.bottom() - dialog.height() - 7),
                )
        dialog.move(x, y)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        dialog = getattr(self, "calendar_dialog", None)
        if dialog is not None and dialog.isVisible():
            self._position_calendar_dialog(dialog)

    def _apply_calendar_date(self, dialog: ModernCalendarDialog) -> None:
        selected = dialog.selected_date()
        self.current_daily_date = selected
        self.date_button.setText(selected.isoformat())

    def _clear_calendar_dialog(self, dialog: ModernCalendarDialog) -> None:
        if self.calendar_dialog is dialog:
            self.calendar_dialog = None
        dialog.deleteLater()

    def run_startup_check(self) -> None:
        if self.environment_runner.is_running():
            return
        self.desktop_pet.set_busy(True)
        self.desktop_pet.announce("正在检查运行环境...", "review")
        self.reset_stages()
        self.progress_text.setText("正在检查环境...")
        report = run_environment_check(self.root)
        self.reset_log_display()
        self.append_log("环境检测开始")
        startup_kst = getattr(self, "startup_kst_initialization", None)
        if startup_kst and startup_kst.get("status") != "skipped":
            status = "通过" if startup_kst.get("passed") else "需要处理"
            self.append_log(f"[{status}] 商务通目录: {startup_kst.get('detail', '')}")
        for item in report["checks"]:
            status = "通过" if item["passed"] else "需要处理"
            self.append_log(f"[{status}] {item['name']}: {item['detail']}")
        if report["passed"]:
            self.progress.setRange(0, len(STAGES))
            self.progress.setValue(0)
            self.set_task_buttons_enabled(True)
            self.set_data_source_control_locked(False)
            self.progress_text.setText("环境已就绪，请选择项目和任务。")
            self.desktop_pet.set_busy(False)
            self.desktop_pet.announce("运行环境正常，可以开始任务。", "waving", 4200)
            return

        self.set_task_buttons_enabled(False)
        self.set_data_source_control_locked(True)
        self.progress.setRange(0, 0)
        self.progress_text.setText("首次准备运行环境，请不要关闭程序...")
        self.append_log("检测到运行环境缺失，开始自动下载并安装。")
        self.desktop_pet.announce("首次使用，正在准备运行环境，请稍等。", "running")
        self.environment_runner.start(
            environment_repair_command(self.root),
            self.root,
            extra_env={"HURLY_REPORT_BOT_AUTO_INSTALL": "1"},
        )

    def on_environment_install_output(self, text: str) -> None:
        self.append_log(text)

        if "Preparing Python" in text or "未检测到 Python" in text:
            self.progress.setRange(0, 0)
            self.progress_text.setText("正在下载项目专用 Python，请保持网络连接...")
            self.desktop_pet.announce("正在下载项目运行环境，请保持网络连接。", "running")
            return
        if "Installing project Python silently" in text:
            self.progress.setRange(0, 0)
            self.progress_text.setText("正在安装项目专用 Python，请不要关闭程序...")
            self.desktop_pet.announce("正在安装项目运行环境，请不要关闭程序。", "running")
            return
        if "正在安装运行依赖" in text:
            self.progress.setRange(0, 0)
            self.progress_text.setText("正在安装运行依赖，首次安装需要几分钟...")
            self.desktop_pet.announce("正在安装运行组件，第一次会稍慢一点。", "running")
            return

        match = re.search(r"\[(\d+)/(\d+)\]", text)
        if not match:
            return
        current, total = int(match.group(1)), int(match.group(2))
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        cleaned = re.sub(r"^.*?\[\d+/\d+\]\s*", "", text).strip()
        if cleaned:
            self.progress_text.setText(cleaned)

    def on_environment_install_finished(self, exit_code: int) -> None:
        self.desktop_pet.set_busy(False)
        self.set_data_source_control_locked(False)
        self.progress.setRange(0, len(STAGES))
        self.progress.setValue(0)
        if exit_code != 0:
            self.set_task_buttons_enabled(False)
            self.progress_text.setText("环境安装失败，请检查网络后重新打开程序。")
            self.append_log(f"[失败] 环境安装退出码：{exit_code}")
            self.desktop_pet.announce("环境安装没有完成，请打开控制台查看。", "failed", 12000, "failed")
            return

        self.append_log("[通过] 环境安装完成，正在做最后检查。")
        report = run_environment_check(self.root)
        for item in report["checks"]:
            status = "通过" if item["passed"] else "需要处理"
            self.append_log(f"[{status}] {item['name']}: {item['detail']}")
        self.set_task_buttons_enabled(report["passed"])
        self.progress_text.setText(
            "环境已就绪，请选择项目和任务。" if report["passed"] else "环境仍未完全就绪，请查看日志。"
        )
        if report["passed"]:
            self.desktop_pet.announce("环境准备完成，现在可以运行任务。", "jumping", 5200)
        else:
            self.desktop_pet.announce("环境仍有问题，请打开控制台查看。", "failed", 12000, "failed")

    def on_environment_install_failed(self, message: str) -> None:
        self.desktop_pet.set_busy(False)
        self.set_data_source_control_locked(False)
        self.progress.setRange(0, len(STAGES))
        self.progress.setValue(0)
        self.set_task_buttons_enabled(False)
        self.progress_text.setText("环境安装无法启动，请查看日志。")
        self.append_log(f"[失败] 环境安装无法启动：{message}")
        self.desktop_pet.announce("环境安装无法启动，请打开控制台查看。", "failed", 12000, "failed")

    def run_hourly(self) -> None:
        period = self.selected_period()
        selected_ids = self.project_combo.selected_project_ids()
        self._multi_task_active = self.project_combo.is_multi_mode()
        if self._multi_task_active:
            command = build_multi_hourly_command(self.root, period, selected_ids)
            names = self.selected_project_names()
            subtitle = f"{'、'.join(names)} {period}"
        else:
            command = build_hourly_command(self.root, period, project_id=self.selected_project_id())
            subtitle = f"{self.selected_project_name()} {period}"
        self.set_current_flow("hourly", "运行小时报", subtitle, "运行中")
        if self._multi_task_active:
            self.current_project_name = "、".join(self.selected_project_names())
        self._last_pet_event = ""
        self.desktop_pet.announce(f"{subtitle}小时报正在做。", "running")
        self.start_command("小时报执行中", command)

    def run_daily(self) -> None:
        date_text = self.selected_daily_date()
        selected_ids = self.project_combo.selected_project_ids()
        self._multi_task_active = self.project_combo.is_multi_mode()
        if self._multi_task_active:
            command = build_multi_daily_command(self.root, date_text, selected_ids)
            names = self.selected_project_names()
            subtitle = f"{'、'.join(names)} {self.display_daily_date()}"
        else:
            command = build_daily_command(self.root, date_text, project_id=self.selected_project_id())
            subtitle = f"{self.selected_project_name()} {self.display_daily_date()}"
        self.set_current_flow("daily", "运行日报", subtitle, "运行中")
        if self._multi_task_active:
            self.current_project_name = "、".join(self.selected_project_names())
        self._last_pet_event = ""
        self.desktop_pet.announce(f"{subtitle}日报正在做。", "running")
        self.start_command("日报执行中", command)

    def run_word_class(self) -> None:
        if self.multi_project_execution_pending():
            return
        date_text = self.selected_daily_date()
        command = build_word_class_command(
            self.root,
            date_text,
            project_id=self.selected_project_id(),
        )
        subtitle = f"{self.selected_project_name()} {self.display_daily_date()}"
        self.set_current_flow("word_class", "词类占比", subtitle, "运行中")
        self._last_pet_event = ""
        self.desktop_pet.announce(f"{subtitle}词类占比正在统计。", "running")
        self.start_command("词类占比执行中", command)

    def run_environment_preflight(self, allow_multi: bool = False) -> None:
        self.run_preflight("hourly", allow_multi=allow_multi)

    def run_preflight(self, task: str, allow_multi: bool = False) -> None:
        if not allow_multi and self.multi_project_execution_pending():
            return
        project_id = self.selected_project_id()
        command = build_preflight_command(self.root, task, project_id=project_id)
        self.set_current_flow("preflight", "项目配置检查", self.selected_project_name(), "运行中")
        self._last_pet_event = ""
        self.desktop_pet.announce(f"正在检查 {self.selected_project_name()} 的项目配置。", "review")
        self.start_command("项目配置检查中", command)

    def set_current_flow(self, task_type: str, title: str, subtitle: str, status: str) -> None:
        self.current_task_type = task_type
        self.current_project_name = self.selected_project_name()
        self.current_status = status
        self.current_start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.current_task_title.setText(title)
        self.current_task_subtitle.setText(subtitle)
        self.current_status_badge.setText(status)
        self.current_status_badge.setProperty("status", "running")
        self.current_start_time_label.setText(f"开始时间：{self.current_start_time}")
        self.flow_idle_icon.hide()
        self.flow_crab.set_mode("dance")
        self.flow_crab.show()
        self._refresh_widget_style(self.current_status_badge)

    def set_current_flow_idle(self) -> None:
        self.current_status = "idle"
        self.current_task_title.setText("暂无运行任务")
        self.current_task_subtitle.setText("请选择左侧任务开始执行")
        self.current_status_badge.setText("空闲")
        self.current_status_badge.setProperty("status", "idle")
        self.current_start_time_label.setText("开始时间：--")
        self.flow_idle_icon.hide()
        self.flow_crab.set_mode("idle")
        self.flow_crab.show()
        self._refresh_widget_style(self.current_status_badge)

    def start_command(self, title: str, command: list[str]) -> None:
        clear_task_stop_gate(self._task_stop_gate)
        self._task_stop_gate = None
        self._task_stop_requested = False
        self._task_stop_locked = False
        self.desktop_pet.set_busy(True)
        self.set_task_buttons_enabled(False)
        self.set_stop_controls()
        self.set_data_source_control_locked(True)
        self.reset_stages()
        self.status_title.setText(title)
        self.status_detail.setText("任务正在运行，请不要关闭窗口。")
        self.progress_text.setText("任务已创建，等待启动...")
        self.reset_log_display()
        if self.current_task_type in {"hourly", "daily", "word_class"}:
            gate_name = ".gui_multi_queue_stop" if self._multi_task_active else ".gui_task_stop"
            self._task_stop_gate = self.root / "reports" / f"{gate_name}_{os.getpid()}.gate"
            clear_task_stop_gate(self._task_stop_gate)
            gate_env = MULTI_QUEUE_STOP_GATE_ENV if self._multi_task_active else STOP_GATE_ENV
            self.runner.start(command, self.root, {gate_env: str(self._task_stop_gate)})
        else:
            self.runner.start(command, self.root)

    def on_task_started(self) -> None:
        self._task_active = True
        self._task_last_output_at = time.monotonic()
        self._task_output_warning_sent = False
        self._task_output_watchdog.start()
        self.set_task_buttons_enabled(False)
        self.set_stop_controls()
        self.set_data_source_control_locked(True)
        self.mark_stage("config")

    def stop_current_task(self) -> None:
        if self.current_task_type not in {"hourly", "daily", "word_class"}:
            return
        if self._task_stop_locked or self._task_stop_requested or not self.runner.is_running():
            return
        if not request_task_stop(self._task_stop_gate):
            self._task_stop_locked = True
            self.set_stop_controls()
            return
        self._task_stop_requested = True
        self._task_stop_locked = True
        self.set_stop_controls()
        if self._multi_task_active:
            self.progress_text.setText("已提交停止请求，当前项目完成后停止队列...")
            self.append_log("[停止] 当前项目将继续完成，不再开始下一个排队项目。")
            self.desktop_pet.announce("收到停止请求，当前项目做完就不再开始下一项。", "review", 5200)
        else:
            self.progress_text.setText("正在停止当前任务...")
            self.append_log("[停止] Excel 尚未开始写入，正在终止当前任务进程。")
            self.desktop_pet.announce("收到停止请求，正在安全停止当前任务。", "review", 4200)
            self.runner.stop()

    def on_task_output(self, text: str) -> None:
        self._task_last_output_at = time.monotonic()
        self._task_output_warning_sent = False
        self.append_log(text)
        event = infer_pet_event(text)
        if not event or event == self._last_pet_event:
            return
        self._last_pet_event = event
        project = self.current_project_name or self.selected_project_name()
        messages = {
            "login": (f"{project}：正在切换百度账号...", "look_a"),
            "login_ready": (f"{project}：账号切换成功，开始读数据。", "waving"),
            "baidu": (f"{project}：正在读取百度数据...", "running"),
            "baidu_ready": ("百度数据读取完成，准备处理快商通数据。", "look_b"),
            "kst": ("正在解析快商通导出数据...", "review"),
            "merge": ("正在核对并合并两边的数据...", "look_a"),
            "excel": ("数据已核对，正在写入 Excel...", "running"),
            "failed": ("任务遇到问题，请点我打开控制台查看。", "failed"),
        }
        message = messages.get(event)
        if message:
            self.desktop_pet.announce(message[0], message[1])

    def check_task_output_watchdog(self) -> None:
        if not self._task_active:
            return
        elapsed = time.monotonic() - self._task_last_output_at
        if not should_warn_no_output(
            self._task_observed_stage,
            elapsed,
            already_warned=self._task_output_warning_sent,
        ):
            return
        stage_names = {
            "preflight": "运行前检查",
            "login": "百度登录",
            "baidu": "百度数据读取",
            "kst": "快商通数据读取",
            "merge": "数据合并",
            "excel": "Excel 处理",
        }
        stage_name = stage_names.get(self._task_observed_stage, "当前步骤")
        self._task_last_output_at = time.monotonic()
        self.progress_text.setText(
            f"{stage_name}暂无新输出，任务仍在运行，可点击停止。"
        )
        self.append_log(
            f"[提示] {stage_name}已 45 秒没有新输出；"
            "任务仍在运行，停止按钮仍可使用。"
        )

    def on_task_finished(self, exit_code: int) -> None:
        self._task_output_watchdog.stop()
        stopped_by_user = exit_code == TASK_CANCELLED_EXIT_CODE or (
            self._task_stop_requested and not self._multi_task_active
        )
        clear_task_stop_gate(self._task_stop_gate)
        self._task_stop_gate = None
        self._task_active = False
        self.desktop_pet.set_busy(False)
        self.set_task_buttons_enabled(True)
        self.set_data_source_control_locked(False)
        self._task_stop_locked = True
        self.set_stop_controls()
        if exit_code == 0 and self._multi_task_active:
            self.finish_multi_project_task()
            self._task_stop_requested = False
            self._task_stop_locked = False
            self._multi_task_active = False
            self.set_stop_controls()
            self._refresh_widget_style(self.current_status_badge)
            self._schedule_deferred_exit()
            return
        if stopped_by_user:
            self.status_title.setText("任务已停止")
            self.status_detail.setText("任务已在 Excel 写入前停止。")
            self.progress_text.setText("任务已停止，未继续写入 Excel。")
            self.current_status_badge.setText("已停止")
            self.current_status_badge.setProperty("status", "stopped")
            self.flow_idle_icon.hide()
            self.flow_crab.set_mode("idle")
            self.flow_crab.show()
            self.append_log("[停止] 任务已停止，未执行后续 Excel 写入。")
            self.desktop_pet.announce("任务已经停下来了。", "idle", 5200)
            self._task_stop_requested = False
            self._task_stop_locked = False
            self._multi_task_active = False
            self.set_stop_controls()
            self._refresh_widget_style(self.current_status_badge)
            self._schedule_deferred_exit()
            return
        if exit_code == 0:
            self.mark_stage("done")
            self.status_title.setText("任务完成")
            self.status_detail.setText("运行结束，可以打开报告或日志复核。")
            self.progress_text.setText("任务完成，可以打开报告复核。")
            self.current_status_badge.setText("已完成")
            self.current_status_badge.setProperty("status", "done")
            self.flow_idle_icon.hide()
            self.flow_crab.set_mode("idle")
            self.flow_crab.show()
            self.append_log("任务完成，退出码 0")
            if self.current_task_type in {"hourly", "daily", "word_class"}:
                task_name = {
                    "hourly": "小时报",
                    "daily": "日报",
                    "word_class": "词类占比",
                }[self.current_task_type]
                if self.should_open_excel_after_task(self.current_task_type):
                    if self.current_task_type == "word_class":
                        self.open_current_project_word_share_excel()
                    else:
                        self.open_current_project_excel()
                    self.desktop_pet.announce(
                        f"{self.current_project_name} {task_name}完成啦，Excel 已经打开。",
                        "jumping",
                        8500,
                    )
                else:
                    self.append_log(f"[通知] {task_name}已完成，按 open 设置跳过打开 Excel。")
                    self.desktop_pet.announce(
                        f"{self.current_project_name} {task_name}完成啦。",
                        "jumping",
                        6500,
                    )
            else:
                self.desktop_pet.announce(
                    f"{self.current_project_name} 项目配置检查完成。",
                    "waving",
                    6500,
                )
        else:
            self.status_title.setText("任务失败")
            self.status_detail.setText("请查看错误日志和 reports 目录下的报告。")
            self.progress_text.setText("任务失败，请查看实时日志和报告。")
            self.current_status_badge.setText("失败")
            self.current_status_badge.setProperty("status", "failed")
            self.flow_idle_icon.hide()
            self.flow_crab.set_mode("idle")
            self.flow_crab.show()
            self.append_log(f"任务失败，退出码 {exit_code}")
            self.desktop_pet.announce(
                f"{self.current_project_name} 任务没有完成，请点我查看原因。",
                "failed",
                15000,
                "failed",
            )
        self._task_stop_requested = False
        self._task_stop_locked = False
        self._multi_task_active = False
        self.set_stop_controls()
        self._refresh_widget_style(self.current_status_badge)
        self._schedule_deferred_exit()

    def show_task_error(self, message: str) -> None:
        self._task_output_watchdog.stop()
        clear_task_stop_gate(self._task_stop_gate)
        self._task_stop_gate = None
        self._task_active = False
        self._task_stop_requested = False
        self._task_stop_locked = False
        self._multi_task_active = False
        self.desktop_pet.set_busy(False)
        self.set_task_buttons_enabled(True)
        self.set_stop_controls()
        self.set_data_source_control_locked(False)
        self.status_title.setText("任务无法启动")
        self.status_detail.setText(message)
        self.progress_text.setText("任务没有启动，请查看提示。")
        self.current_status_badge.setText("失败")
        self.current_status_badge.setProperty("status", "failed")
        self.flow_idle_icon.hide()
        self.flow_crab.set_mode("idle")
        self.flow_crab.show()
        self._refresh_widget_style(self.current_status_badge)
        self.append_log("任务无法启动：" + message)
        self.desktop_pet.announce("任务没有启动，请点我打开控制台查看。", "failed", 12000, "failed")
        self._schedule_deferred_exit()

    def set_task_buttons_enabled(self, enabled: bool) -> None:
        has_projects = bool(self.projects)
        run_enabled = enabled and has_projects
        self.hourly_action_control.set_run_enabled(run_enabled)
        self.daily_action_control.set_run_enabled(run_enabled)
        self.word_class_button.setEnabled(run_enabled)
        self.project_check_action.setEnabled(enabled and has_projects)

    def set_stop_controls(self) -> None:
        can_stop = (
            self._task_active
            and self.runner.is_running()
            and not self._task_stop_requested
            and not self._task_stop_locked
        )
        self.hourly_action_control.set_stop_enabled(can_stop and self.current_task_type == "hourly")
        self.daily_action_control.set_stop_enabled(can_stop and self.current_task_type in {"daily", "word_class"})

    def set_data_source_control_locked(self, locked: bool) -> None:
        self.data_source_control.setEnabled(not locked)

    def reset_stages(self) -> None:
        self.progress.setValue(0)
        self.progress_text.setText("项目就绪后会显示任务进度")
        for label in self.stage_labels.values():
            label.setProperty("active", False)
            label.setProperty("done", False)
            self._refresh_widget_style(label)

    def mark_stage(self, stage: str) -> None:
        self._task_observed_stage = stage
        keys = [item[0] for item in STAGES]
        if stage not in keys:
            return
        if stage == "excel" and not self._multi_task_active:
            self._task_stop_locked = True
            self.set_stop_controls()
        index = keys.index(stage)
        self.progress.setValue(index + 1)
        self.progress_text.setText(f"当前进度：{STAGES[index][1]}")
        for pos, key in enumerate(keys):
            label = self.stage_labels.get(key)
            if label is None:
                continue
            label.setProperty("active", key == stage)
            label.setProperty("done", pos < index or stage == "done")
            self._refresh_widget_style(label)

    def append_log(self, text: str) -> None:
        value = str(text or "")
        lines = value.splitlines() or [""]
        for line in lines:
            try:
                append_history_line(self.root, line)
            except OSError:
                pass
            self._log_queue.append(line)
            self._log_pending_chars += max(1, len(line))
        if not self._log_timer.isActive():
            self._log_timer.start()

    def reset_log_display(self) -> None:
        self._log_timer.stop()
        self._log_queue.clear()
        self._log_current_line = None
        self._log_current_visible = 0
        self._log_current_block = -1
        self._log_pending_chars = 0
        self.log_view.clear()

    def has_pending_log_display(self) -> bool:
        return self._log_current_line is not None or bool(self._log_queue)

    def _begin_log_line(self) -> None:
        self._log_current_line = self._log_queue.popleft()
        self._log_current_visible = 0
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock()
        self._log_current_block = cursor.blockNumber()

    def _render_current_log_line(self) -> None:
        block = self.log_view.document().findBlockByNumber(self._log_current_block)
        if not block.isValid() or self._log_current_line is None:
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(format_log_fragment(self._log_current_line[: self._log_current_visible]))

    def drain_log_display(self) -> None:
        budget = typewriter_batch_size(self._log_pending_chars)
        while budget > 0 and self.has_pending_log_display():
            if self._log_current_line is None:
                self._begin_log_line()
            if self._log_current_line is None:
                break
            remaining = len(self._log_current_line) - self._log_current_visible
            if remaining <= 0:
                self._log_pending_chars = max(0, self._log_pending_chars - 1)
                self._log_current_line = None
                continue
            count = min(budget, remaining)
            self._log_current_visible += count
            self._log_pending_chars = max(0, self._log_pending_chars - count)
            budget -= count
            self._render_current_log_line()
            if self._log_current_visible >= len(self._log_current_line):
                self._log_current_line = None
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        if not self.has_pending_log_display():
            self._log_timer.stop()

    def open_path(self, path: Path) -> None:
        path.mkdir(exist_ok=True) if path.suffix == "" else None
        try:
            os.startfile(str(path))
        except Exception as exc:
            QMessageBox.warning(self, "无法打开", str(exc))

    def open_current_project_excel(self) -> None:
        try:
            excel_path = self.selected_project_excel_path()
        except Exception as exc:
            self.append_log(f"任务完成，但读取 Excel 路径失败：{exc}")
            return
        if not excel_path.exists():
            self.append_log(f"任务完成，但 Excel 文件不存在：{excel_path}")
            return
        self.open_path(excel_path)
        self.append_log(f"已打开当前项目 Excel：{excel_path}")

    def open_current_project_word_share_excel(self) -> None:
        try:
            project_id = self.selected_project_id()
            project = load_project_config(self.root, project_id)
            date_text = self.selected_daily_date()
            year = date_text[:4] if date_text else str(date.today().year)
            word_share_path = get_word_share_path(project, year, self.root)
        except Exception as exc:
            self.append_log(f"任务完成，但读取词类占比路径失败：{exc}")
            return
        if not word_share_path.exists():
            self.append_log(f"任务完成，但词类占比 Excel 不存在：{word_share_path}")
            return
        self.open_path(word_share_path)
        self.append_log(f"已打开当前项目词类占比 Excel：{word_share_path}")

    def load_multi_project_report(self) -> dict:
        path = self.root / "reports" / "multi_project_run_report.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            self.append_log(f"[失败] 多项目汇总报告读取失败：{exc}")
            return {}
        return payload if isinstance(payload, dict) else {}

    def open_multi_project_excels(self, report: dict | None = None) -> list[Path]:
        payload = report or self.load_multi_project_report()
        opened: list[Path] = []
        seen: set[str] = set()
        for item in payload.get("projects") or []:
            if not isinstance(item, dict) or item.get("status") != "success":
                continue
            value = str(item.get("excel_path") or "").strip()
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute():
                path = self.root / path
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            if not path.exists():
                self.append_log(f"任务完成，但 Excel 文件不存在：{path}")
                continue
            self.open_path(path)
            self.append_log(f"已打开项目 Excel：{path}")
            opened.append(path)
        return opened

    def finish_multi_project_task(self) -> None:
        report = self.load_multi_project_report()
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        success = int(summary.get("success") or 0)
        failed = int(summary.get("failed") or 0)
        stopped = int(summary.get("stopped") or 0)
        if not report:
            failed = max(failed, 1)

        if failed == 0 and stopped == 0:
            title = "多项目任务完成"
            badge = "已完成"
            badge_status = "done"
            detail = f"成功完成 {success} 个项目。"
            pet_mode = "jumping"
        elif success > 0:
            title = "多项目任务部分完成"
            badge = "部分完成"
            badge_status = "stopped" if stopped and not failed else "failed"
            detail = f"成功 {success}，失败 {failed}，停止 {stopped}。"
            pet_mode = "review"
        else:
            title = "多项目任务未完成"
            badge = "已停止" if stopped and not failed else "失败"
            badge_status = "stopped" if stopped and not failed else "failed"
            detail = f"成功 {success}，失败 {failed}，停止 {stopped}。"
            pet_mode = "failed" if failed else "idle"

        if success:
            self.mark_stage("done")
        self.status_title.setText(title)
        self.status_detail.setText(detail)
        self.progress_text.setText(detail)
        self.current_status_badge.setText(badge)
        self.current_status_badge.setProperty("status", badge_status)
        self.flow_idle_icon.hide()
        self.flow_crab.set_mode("idle")
        self.flow_crab.show()
        self.append_log(f"[多项目][汇总] {detail}")

        opened = []
        if success and self.should_open_excel_after_task(self.current_task_type):
            opened = self.open_multi_project_excels(report)
        elif success:
            self.append_log("[通知] 多项目任务已结束，按 Excel 自动模式设置跳过打开文件。")
        open_text = f"，已打开 {len(opened)} 个 Excel" if opened else ""
        self.desktop_pet.announce(f"多项目任务结束：{detail}{open_text}", pet_mode, 8500)

    def _refresh_widget_style(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

def create_window(
    root: str | Path,
    *,
    kst_api_manager_factory: Callable[..., KstApiManager] = KstApiManager,
) -> MainWindow:
    startup_kst_initialization = initialize_kst_directories_once()
    if kst_api_manager_factory is KstApiManager:
        window = MainWindow(root)
    else:
        window = MainWindow(
            root,
            kst_api_manager_factory=kst_api_manager_factory,
        )
    window.startup_kst_initialization = startup_kst_initialization
    window.show()
    window.start_update_check()
    return window
