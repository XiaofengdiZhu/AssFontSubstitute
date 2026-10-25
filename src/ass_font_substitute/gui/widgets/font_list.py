from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel
from qfluentwidgets import SmoothScrollBar
from qfluentwidgets import StrongBodyLabel

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.gui.preview_vm import weight_tag
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

ROW_H = 84                 # 行高初值：名 + 元信息 + 28px 预览条；物化首行后
ROW_SPACING = 4            # 按内容实测校准（见 _calibrate_row_height）
WINDOW_PAD = 20            # 可视区窗口向外各扩的行数（懒物化缓冲）
_META_FMT = "{script} · {weight} · {category}"


class FontListWidget(QListWidget):
    """预览页 / 选择对话框共享的字体列表：固定行高 + 可视区懒物化。

    set_rows 只建轻量 QListWidgetItem（17k 量级廉价），行控件仅在
    「可视区 ±WINDOW_PAD 行」窗口内经 _materialize_visible 构建（此时
    PreviewBar 才发起异步渲染）；窗口外的已物化行控件释放——tile 留在
    PreviewVM LRU，回滚重物化直接命中缓存（in-flight 防重复由
    PreviewBar 自守）。"""

    count_changed = Signal(int)
    entity_picked = Signal(FontEntity)

    def __init__(self, worker, pvm, preview_text_fn, parent=None):
        super().__init__(parent)
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn   # () -> str；返回空串则用字体名
        self._rows: list[FontEntity] = []
        self._materialized: set[int] = set()      # 已物化行号（控件所有权在 Qt）
        self._row_h = ROW_H                       # 物化首行后按内容校准
        self.setIconSize(self.iconSize())
        self.setUniformItemSizes(True)
        self.setSpacing(ROW_SPACING)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        # 裸 QListWidget 画调色板底（深色主题下白块）+ 原生 QScrollBar 深色
        # 不透明白条：底透明露卡面色，滚动条换 qfw SmoothScrollBar（随主题）
        self.setStyleSheet("QListWidget{border:none;background:transparent}")
        self._v_scroll = SmoothScrollBar(Qt.Vertical, self)
        self.itemClicked.connect(self._on_click)
        self.verticalScrollBar().valueChanged.connect(
            lambda _: self._materialize_visible())
        self.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._materialize_visible())

    def set_rows(self, entities: list[FontEntity]):
        self._rows = list(entities)
        self._materialized.clear()
        self._row_h = ROW_H          # 校准基准每周期重置：item 高度被重置回
        self.clear()                 # 初值后，跨周期比较旧基准会早退漏校准
        for _e in self._rows:
            item = QListWidgetItem()
            item.setSizeHint(QSize(10, ROW_H))
            self.addItem(item)
        self.count_changed.emit(len(self._rows))
        self._materialize_visible()

    def _on_click(self, item):
        row = self.row(item)
        if 0 <= row < len(self._rows):
            self.entity_picked.emit(self._rows[row])

    def _row_widget(self, i: int) -> _RowWidget:
        e = self._rows[i]
        return _RowWidget(self._worker, self._pvm, e,
                          self._preview_text_fn() or e.display_name,
                          f"plist:{e.fingerprint}:{id(self)}")

    def _visible_range(self) -> tuple[int, int]:
        """可视首末行（闭区间）。indexAt 探针取视口内侧、避开 spacing 空隙
        （角点落在行间空隙时 indexAt 返回 -1）；仍无效则回退滚动条行单位
        （ScrollPerItem 模式下 value=首行、pageStep=可视行数）。"""
        n = len(self._rows)
        rect = self.viewport().rect()
        first = self.indexAt(rect.topLeft() + QPoint(0, ROW_SPACING + 1)).row()
        last = self.indexAt(rect.bottomLeft() - QPoint(0, ROW_SPACING + 1)).row()
        if first < 0 or last < first:
            sb = self.verticalScrollBar()
            first = min(max(0, sb.value()), n - 1)
            last = min(n - 1, first + max(1, sb.pageStep()))
        return max(0, first), min(n - 1, last)

    def _materialize_visible(self):
        """可视区 ±WINDOW_PAD 行：缺控件则物化（行控件构造即派渲染），
        窗口外的已物化行释放。count 守卫跳过 set_rows 清空/增量中段
        的滚动条信号。"""
        if not self._rows or self.count() != len(self._rows):
            return
        first, last = self._visible_range()
        lo = max(0, first - WINDOW_PAD)
        hi = min(len(self._rows) - 1, last + WINDOW_PAD)
        for i in range(lo, hi + 1):
            if i not in self._materialized:
                w = self._row_widget(i)
                self.setItemWidget(self.item(i), w)
                self._materialized.add(i)
                self._calibrate_row_height(w)
        for i in [i for i in self._materialized if i < lo or i > hi]:
            self.removeItemWidget(self.item(i))
            self._materialized.discard(i)

    def _calibrate_row_height(self, w: QWidget):
        """行高按内容实测校准。常量初值是估的：三行内容（名字 + 元信息 +
        28px 预览条）真实高度随平台字体度量变化，估小了布局溢出、预览条
        下半被行边界裁掉（只能看到上半）。首行 sizeHint 为准，全列表套用。"""
        h = w.sizeHint().height()
        if h <= self._row_h or self.count() == 0:
            return
        self._row_h = h
        for i in range(self.count()):
            self.item(i).setSizeHint(QSize(10, h))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._materialize_visible()   # 首次显示/对话框尺寸变化时补物化


class _RowWidget(QWidget):
    def __init__(self, worker, pvm, e: FontEntity, text, tag):
        super().__init__()
        h = QVBoxLayout(self)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(4)               # 显式钉死：默认 spacing 随平台样式漂
        h.addWidget(StrongBodyLabel(e.display_name))
        meta = CaptionLabel(_META_FMT.format(script=e.script,
                                             weight=weight_tag(e.weight),
                                             category=e.category))
        h.addWidget(meta)
        self.bar = PreviewBar(worker, pvm, e, text, tag)
        h.addWidget(self.bar)
