from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from gui.branding import PRODUCT_DISPLAY_NAME


class UpdateInstallDialog(QDialog):
    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = str(version)
        self.setWindowTitle("正在安装更新")
        self.setObjectName("updateInstallDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.setFixedSize(420, 126)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(5)

        self.title_label = QLabel("正在安装更新")
        self.title_label.setObjectName("updateInstallTitle")
        self.detail_label = QLabel(f"安装完成后，{PRODUCT_DISPLAY_NAME}将重新启动。")
        self.detail_label.setObjectName("updateInstallDetail")
        layout.addWidget(self.title_label)
        layout.addWidget(self.detail_label)
        layout.addSpacing(7)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("updateInstallProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.stage_label = QLabel("正在准备…")
        self.stage_label.setObjectName("updateInstallStage")
        self.stage_label.setMinimumWidth(72)
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.stage_label)
        layout.addLayout(progress_row)

        self.setStyleSheet("""
            QDialog#updateInstallDialog {
                background: #fbfbfc;
                border: 1px solid #d9dde4;
                border-radius: 16px;
                font-family: "Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", sans-serif;
                color: #182134;
            }
            QLabel#updateInstallTitle { font-size: 13pt; font-weight: 700; }
            QLabel#updateInstallDetail, QLabel#updateInstallStage {
                color: #7b8493;
                font-size: 9pt;
                font-weight: 400;
            }
            QProgressBar#updateInstallProgress {
                min-height: 7px;
                max-height: 7px;
                border: 0;
                border-radius: 4px;
                background: #e8eaed;
            }
            QProgressBar#updateInstallProgress::chunk {
                border-radius: 4px;
                background: #79b9ef;
            }
        """)

    def set_progress(self, value: int, stage: str) -> None:
        self.progress_bar.setValue(max(0, min(100, int(value))))
        self.stage_label.setText(str(stage))
