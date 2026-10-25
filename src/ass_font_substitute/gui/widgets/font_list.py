from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation
from PySide6.QtCore import QSize
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel
from qfluentwidgets import SmoothScrollDelegate
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
    PreviewBar 才发起异步渲染，按可视中心距离排序提交）；行滚出窗口
    时控件释放并 cancel 未执行的渲染任务——不取消则快速滚动会积压
    几百个过期任务，新视口的预览排在队尾迟迟不出来。条带留在
    PreviewVM LRU，回滚重物化直接命中缓存（in-flight 防重复由
    PreviewBar 自守）。滚动为 qfw SmoothScrollDelegate：ScrollPerPixel
    + 滚轮插值动画（裸 SmoothScrollBar 只镜像显示，滚轮仍逐行跳）。"""

    count_changed = Signal(int)
    entity_picked = Signal(FontEntity)

    def __init__(self, worker, pvm, preview_text_fn, parent=None):
        super().__init__(parent)
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn   # () -> str；返回空串则用字体名
        self._rows: list[FontEntity] = []
        self._materialized: set[int] = set()      # 已物化行号（控件所有权在 Qt）
        self._row_h = ROW_H                       # 物化首行后按内容校准
        self._row_pitch = ROW_H + ROW_SPACING     # 实测行距，见 _measure_pitch
        self.setIconSize(self.iconSize())
        self.setUniformItemSizes(True)
        self.setSpacing(ROW_SPACING)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 裸 QListWidget 画调色板底（深色主题下白块）+ 原生 QScrollBar 深色
        # 不透明白条：底透明露卡面色；滚动条换 qfw SmoothScrollDelegate
        # （随主题 + 滚轮平滑），原生条被其置为隐藏。useAni=True 必须开：
        # 默认引擎路径把每帧增量以合成滚轮事件发给原生条，原生条按
        # angleDelta/120×singleStep 换算，而 QListView 的 singleStep=行高
        # （88px，QScrollArea 页面是 20px）——列表每格滚出 4.4 倍距离。
        # 动画路径 scrollValue() 直接驱动原生条 setValue，恒定 120px/格
        self.setStyleSheet("QListWidget{border:none;background:transparent}")
        self._scroll = SmoothScrollDelegate(self, useAni=True)
        bar = self._scroll.vScrollBar
        # qfw SmoothScrollBar 的滚动目标 __value 只在自身交互更新，外部
        # 直设原生条（键盘翻页/程序定位）后脱钩，下次滚轮从旧目标跳变——
        # 动画不在跑时把目标重置到原生条现值（动画期间的变化来自动画
        # 自身，重置会打断它）
        def _sync_scroll_target(v):
            if bar.ani.state() != QPropertyAnimation.Running:
                bar.resetValue(v)
        self.verticalScrollBar().valueChanged.connect(_sync_scroll_target)
        # 滚轮接手动画的基准问题。qfw scrollValue 从内部目标 __value 累加：
        # ①点 groove 跳位（setValue(useAni=True) 动画，__value 停在点击前）
        #   中途滚一格 → 目标按过期 __value 算好，整段跳位被丢弃回跳；
        # ②反之若每格都从当前帧重算，快速连滚在动画帧间隙内互相覆盖，
        #   六格只滚一格的距离。判别「动画是不是滚轮建立的」：滚轮会话
        #   标志——滚轮续算（__value 累计，连滚不丢格）；外来 setValue
        #   （groove 跳位/程序定位）建立动画即作废会话，下一格滚轮从
        #   真实值重基准（不回跳）。动画链真结束（含被外来动画打断后的
        #   下一拍）把目标对齐真实值，防陈旧累计
        wheel_state = {"from_scroll": False, "in_scroll": False}
        _orig_set_value = bar.setValue
        _orig_scroll_value = bar.scrollValue

        def _set_value_marked(value, useAni=True):
            if not wheel_state["in_scroll"]:
                wheel_state["from_scroll"] = False
            _orig_set_value(value, useAni)
        bar.setValue = _set_value_marked

        def _scroll_value_seated(value, useAni=True):
            if not wheel_state["from_scroll"]:
                bar.resetValue(bar.value())
            wheel_state["in_scroll"] = True
            try:
                _orig_scroll_value(value, useAni)
            finally:
                wheel_state["in_scroll"] = False
            wheel_state["from_scroll"] = True
        bar.scrollValue = _scroll_value_seated

        def _on_ani_done():
            # 滚轮连发时 stop→restart 也发 finished：延迟到事件循环下一
            # 拍，动画仍在跑则不动（保住连滚累计）；真结束才对齐
            def _finalize():
                if bar.ani.state() != QPropertyAnimation.Running:
                    bar.resetValue(bar.value())
            QTimer.singleShot(0, _finalize)
        bar.ani.finished.connect(_on_ani_done)
        self.itemClicked.connect(self._on_click)
        self.verticalScrollBar().valueChanged.connect(
            self._on_scroll_value_changed)
        self.verticalScrollBar().rangeChanged.connect(
            self._on_scroll_range_changed)

    def _on_scroll_value_changed(self, _value):
        self._materialize_visible()

    def _on_scroll_range_changed(self, *_args):
        self._materialize_visible()

    def set_rows(self, entities: list[FontEntity]):
        for i in self._materialized:            # 重建前取消在途渲染，见下
            bar = getattr(self.itemWidget(self.item(i)), "bar", None)
            if bar is not None:
                bar.cancel()
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
        """可视首末行（闭区间）：滚动条 value 按实测行距换算。

        不用 QListView 的 indexAt：静态布局把行控件 x 内缩 spacing，
        视口左缘探针点恒落沟槽返回 -1；行高校准改 item hint 后深处
        又会失效或与滚动条位置错开数百行——物化窗口整体贴到视口外
        （直达深处大片空白的病根）。像素换算 + 实测行距端到端可靠
        （test_deep_jump_materializes_viewport_rows 端到端钉住）。"""
        n = len(self._rows)
        pitch = self._measure_pitch()
        sb = self.verticalScrollBar()
        first = min(max(0, sb.value() // pitch), n - 1)
        last = min(n - 1, first + max(1, sb.pageStep() // pitch))
        return max(0, first), min(n - 1, last)

    def _measure_pitch(self) -> int:
        """实测行距（相邻行 visualRect y 差，权威值）。行距 ≠ item hint +
        spacing：Qt 布局实测 104（96+4+4，spacing 参与两次），手拼公式
        在深处累计偏差数百行。visualRect 对任意行按当前布局即时可算，
        每次物化周期重测（校准改行高后自愈）。"""
        m = self.model()
        r0 = self.visualRect(m.index(0, 0))
        r1 = self.visualRect(m.index(1, 0))
        if r1.isValid() and r1.y() > r0.y():
            self._row_pitch = r1.y() - r0.y()
        return self._row_pitch

    def _materialize_visible(self):
        """可视区 ±WINDOW_PAD 行：缺控件则物化（行控件构造即派渲染），
        按到可视中心距离排序提交——连续滚动时新进入视野的行先入渲染
        队列；窗口外的已物化行释放并 cancel 在途任务。count 守卫跳过
        set_rows 清空/增量中段的滚动条信号。"""
        if not self._rows or self.count() != len(self._rows):
            return
        first, last = self._visible_range()
        lo = max(0, first - WINDOW_PAD)
        hi = min(len(self._rows) - 1, last + WINDOW_PAD)
        center = (first + last) / 2
        for i in sorted(range(lo, hi + 1), key=lambda i: abs(i - center)):
            if i not in self._materialized:
                w = self._row_widget(i)
                self.setItemWidget(self.item(i), w)
                self._materialized.add(i)
                self._calibrate_row_height(w)
        for i in [i for i in self._materialized if i < lo or i > hi]:
            w = self.itemWidget(self.item(i))
            bar = getattr(w, "bar", None)
            if bar is not None:
                bar.cancel()            # 过期任务出队，新视口预览不排队尾
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
