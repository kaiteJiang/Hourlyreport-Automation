from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)


class KstStatusControl(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.kst_button = QToolButton(self)
        self.kst_button.setText("● KST")
        self.kst_button.setAutoRaise(True)
        self.kst_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.kst_button.setProperty("apiReady", False)

        self.live_label = QLabel("● 实时", self)
        self.live_label.setStyleSheet(
            "color: #34c759; font-weight: 700; background: transparent;"
        )
        layout.addWidget(self.kst_button)
        layout.addWidget(self.live_label)
        self.set_api_ready(False, "商务通 API 未启动")

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
