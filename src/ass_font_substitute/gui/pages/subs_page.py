from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ScrollArea


class SubsPage(ScrollArea):
    """处理字幕页（Task 3/4 填充）。"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("subsPage")
        self.view = QWidget(self)
        QVBoxLayout(self.view).addWidget(BodyLabel("处理字幕（开发中）"))
        self.setWidget(self.view)
        self.setWidgetResizable(True)
