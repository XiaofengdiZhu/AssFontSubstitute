from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ScrollArea


class LibraryPage(ScrollArea):
    """字体库管理页（Task 6 填充）。"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("libraryPage")
        self.view = QWidget(self)
        QVBoxLayout(self.view).addWidget(BodyLabel("字体库管理（开发中）"))
        self.setWidget(self.view)
        self.setWidgetResizable(True)
