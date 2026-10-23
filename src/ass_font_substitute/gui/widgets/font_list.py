from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, StrongBodyLabel

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.gui.preview_vm import weight_tag
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

ROW_H = 56
_META_FMT = "{script} · {weight} · {category}"


class FontListWidget(QListWidget):
    """预览页 / 选择对话框共享的字体列表：固定行高 + 可视区异步渲染。"""

    count_changed = Signal(int)
    entity_picked = Signal(FontEntity)

    def __init__(self, worker, pvm, preview_text_fn, parent=None):
        super().__init__(parent)
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn   # () -> str；返回空串则用字体名
        self._rows: list[FontEntity] = []
        self.setIconSize(self.iconSize())
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.itemClicked.connect(self._on_click)
        self.verticalScrollBar().valueChanged.connect(lambda _: self._render_visible())

    def set_rows(self, entities: list[FontEntity]):
        self._rows = list(entities)
        self.clear()
        for e in self._rows:
            item = QListWidgetItem()
            item.setSizeHint(QSize(10, ROW_H))
            w = _RowWidget(self._worker, self._pvm, e,
                           self._preview_text_fn() or e.display_name,
                           f"plist:{e.fingerprint}:{id(self)}")
            self.addItem(item)
            self.setItemWidget(item, w)
        self.count_changed.emit(len(self._rows))
        self._render_visible()

    def _on_click(self, item):
        row = self.row(item)
        if 0 <= row < len(self._rows):
            self.entity_picked.emit(self._rows[row])

    def _render_visible(self):
        """仅可视区行触发预览渲染（PreviewBar 内部有缓存，重复触发廉价）。"""
        first = self.indexAt(self.viewport().rect().topLeft()).row()
        last = self.indexAt(self.viewport().rect().bottomLeft()).row()
        for i in range(max(0, first), min(len(self._rows), last + 1)):
            w = self.itemWidget(self.item(i))
            if isinstance(w, _RowWidget):
                w.bar._render()


class _RowWidget(QWidget):
    def __init__(self, worker, pvm, e: FontEntity, text, tag):
        super().__init__()
        h = QVBoxLayout(self)
        h.setContentsMargins(8, 2, 8, 2)
        h.addWidget(StrongBodyLabel(e.display_name))
        meta = CaptionLabel(_META_FMT.format(script=e.script,
                                             weight=weight_tag(e.weight),
                                             category=e.category))
        h.addWidget(meta)
        self.bar = PreviewBar(worker, pvm, e, text, tag)
        h.addWidget(self.bar)
