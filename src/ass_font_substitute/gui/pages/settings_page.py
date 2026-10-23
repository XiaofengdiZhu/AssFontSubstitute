from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ScrollArea


class SettingsPage(ScrollArea):
    """设置页（Task 7 填充）。"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("settingsPage")
        self.view = QWidget(self)
        QVBoxLayout(self.view).addWidget(BodyLabel("设置（开发中）"))
        self.setWidget(self.view)
        self.setWidgetResizable(True)
