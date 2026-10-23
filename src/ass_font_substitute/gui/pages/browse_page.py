from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ScrollArea


class BrowsePage(ScrollArea):
    """字体库预览页（Task 5 填充）。"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("browsePage")
        self.view = QWidget(self)
        QVBoxLayout(self.view).addWidget(BodyLabel("字体库预览（开发中）"))
        self.setWidget(self.view)
        self.setWidgetResizable(True)
