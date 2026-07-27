from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
)


class KstStatusControl(QWidget):
    source_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.kst_button = QToolButton(self)
        self.kst_button.setText("● KST")
        self.kst_button.setAutoRaise(True)
        self.kst_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.kst_button.setProperty("apiReady", False)

        self.source_menu = QMenu(self.kst_button)
        self.source_group = QActionGroup(self.source_menu)
        self.source_group.setExclusive(True)
        self.api_action = QAction("API 自动获取", self.source_group)
        self.manual_action = QAction("人工导出", self.source_group)
        self.api_action.setCheckable(True)
        self.manual_action.setCheckable(True)
        self.source_menu.addAction(self.api_action)
        self.source_menu.addAction(self.manual_action)
        self.kst_button.setMenu(self.source_menu)
        self.api_action.triggered.connect(
            lambda: self.source_selected.emit("local_api")
        )
        self.manual_action.triggered.connect(
            lambda: self.source_selected.emit("export")
        )

        self.live_label = QLabel("● 实时", self)
        self.live_label.setStyleSheet(
            "color: #34c759; font-weight: 700; background: transparent;"
        )
        layout.addWidget(self.kst_button)
        layout.addWidget(self.live_label)
        self.set_api_ready(False, "商务通 API 未启动")
        self.set_source_mode("export")

    def set_api_ready(self, ready: bool, detail: str) -> None:
        self.kst_button.setProperty("apiReady", bool(ready))
        color = "#34c759" if ready else "#9aa5b1"
        self.kst_button.setStyleSheet(
            f"""
            QToolButton {{
                color: {color};
                font-weight: 700;
                border: none;
                padding: 0;
                background: transparent;
            }}
            QToolButton::menu-indicator {{
                image: none;
                width: 0;
            }}
            """
        )
        self.kst_button.setToolTip(detail)

    def set_source_mode(self, mode: str) -> None:
        is_api = mode == "local_api"
        self.api_action.setChecked(is_api)
        self.manual_action.setChecked(not is_api)
