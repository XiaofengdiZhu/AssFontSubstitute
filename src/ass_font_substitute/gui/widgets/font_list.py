from __future__ import annotations

import weakref

import shiboken6
from PySide6.QtCore import QCoreApplication
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtCore import QRect
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QFont
from PySide6.QtGui import QIcon
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import SegmentedWidget
from qfluentwidgets import SmoothScrollDelegate
from qfluentwidgets import StrongBodyLabel
from qfluentwidgets import TransparentToggleToolButton
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.gui import vm_text
from ass_font_substitute.gui.preview_vm import FamilyRow
from ass_font_substitute.gui.preview_vm import weight_tag
from ass_font_substitute.gui.widgets.pill import PillRow
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
from ass_font_substitute.gui.widgets.primary_text import _LUMA_DARK_TEXT
from ass_font_substitute.gui.widgets.primary_text import accent_luma
from ass_font_substitute.gui.widgets.selectable import apply_selectable

ROW_H = 82                 # 行高初值：控制行 + 32px 预览条；物化首行后
ROW_SPACING = 0            # 按内容实测校准（见 _calibrate_row_height）。
                           # 列表 spacing 砍零：行间留白由行内 margins 提
                           # 供，hover 背景铺满 item 时文字离边不局促。
                           # 初值必须 ≤ 实测：校准只增不减，偏大会把余量
                           # 固化成行间空白
WINDOW_PAD = 20            # 可视区窗口向外各扩的行数（懒物化缓冲）
BAR_H = 32                 # 预览条逻辑高


def meta_items(e: FontEntity) -> list[str]:
    """行 meta 药丸项：script（未标注不上屏）· 字重 · 斜体 · 分类。"""
    items = [] if e.script == "未标注" else [vm_text.text(e.script)]
    items.append(weight_tag(e.weight))
    if e.italic:
        items.append(QCoreApplication.translate("FontListWidget", "斜体"))
    items.append(vm_text.text(e.category))
    return items


def _draw_italic_pixmap(color: QColor) -> QPixmap:
    # 衬线体：无衬线 I 缩到 16px 只剩一根斜竖线，衬线上下横笔才是「斜体」
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont("Georgia", 20)
    f.setBold(True)
    f.setItalic(True)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QRect(0, 0, 32, 32), Qt.AlignCenter, "I")
    p.end()
    return pm


def _on_ink() -> QColor:
    """checked 底为强调色块：浅强调色上白 I 隐形，按亮度反相。
    阈值统一走 primary_text（历史值 150 与 0.62 曾分叉）。"""
    return (QColor(0, 0, 0) if accent_luma() >= _LUMA_DARK_TEXT
            else QColor(255, 255, 255))


_ITALIC_ICONS: dict[tuple[bool, int], QIcon] = {}   # (主题, On态墨色) → 图标
_ITALIC_BTNS: weakref.WeakSet[TransparentToggleToolButton] = weakref.WeakSet()
_ITALIC_HOOKED = False


def _refresh_italic_icons():
    # 缓存 pixmap 是冻结位图：强调色/主题 live 切换后 QSS 系控件自动跟
    # 色，已物化行的斜体钮却攥着旧墨色 pixmap（浅强调色下白 I 隐形）。
    # 清缓存后给活钮换新图标；死钮 WeakSet 自动剔除。isValid 守卫：
    # 行滚出 deleteLater 已删 C++ 对象、wrapper 未 GC 时抛 RuntimeError
    # 会中断后续活钮刷新（冻结白 I 残留正是历史坑本体）
    _ITALIC_ICONS.clear()
    for btn in list(_ITALIC_BTNS):
        if not shiboken6.isValid(btn):
            continue
        btn.setIcon(_italic_icon())


def _hook_italic_retheme():
    global _ITALIC_HOOKED
    if _ITALIC_HOOKED:
        return
    _ITALIC_HOOKED = True
    # singleShot(0) 顺延到 setThemeColor 的 updateStyleSheet 之后落地
    # （纯时序卫生，themeColor() 在信号发出时已是新值）；亮暗切换
    # qfw 只发 themeChangedFinished
    for sig in (qconfig.themeColorChanged, qconfig.themeChangedFinished):
        sig.connect(lambda: QTimer.singleShot(0, _refresh_italic_icons))


def _italic_icon() -> QIcon:
    """斜体图标：FluentIcon 无此形，自绘衬线斜体「I」，双态缓存复用。

    unchecked = 透明底主题墨色；checked 底为强调色块（浅强调色需深 I）。
    ToggleToolButton checked 走 PrimaryToolButton._drawIcon 只对
    FluentIconBase 反色，裸 QIcon 不会，必须预置 On 态 pixmap。"""
    _hook_italic_retheme()
    dark = isDarkTheme()
    on_ink = _on_ink()
    key = (dark, on_ink.rgb())
    icon = _ITALIC_ICONS.get(key)
    if icon is None:
        icon = QIcon()
        icon.addPixmap(_draw_italic_pixmap(QColor(255, 255, 255) if dark
                                           else QColor(0, 0, 0)),
                       QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(_draw_italic_pixmap(on_ink),
                       QIcon.Mode.Normal, QIcon.State.On)
        _ITALIC_ICONS[key] = icon
    return icon


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
    detail_requested = Signal(FontEntity)   # 双击行：浏览页开详情；选择
    #                                         对话框转作快捷接受（选定+关窗）

    def __init__(self, worker, pvm, preview_text_fn, parent=None,
                 name_selectable: bool = True, face_controls: bool = True):
        super().__init__(parent)
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn   # () -> str；返回空串则用字体名
        # 行名可划选（用户需求 2026-10-29）；选择对话框除外——名是
        # picker 行宽最大的点击目标，可选化会吃掉单击选中/双击接受
        self._name_selectable = name_selectable
        # 字重分段/斜体钮（拍板 2026-10-30 选择对话框关闭：工具不改
        # 字重斜体，行面由 sel_key 定——非斜体+400 最近）
        self._face_controls = face_controls
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
        self.itemDoubleClicked.connect(self._on_double_click)
        self.verticalScrollBar().valueChanged.connect(
            self._on_scroll_value_changed)
        self.verticalScrollBar().rangeChanged.connect(
            self._on_scroll_range_changed)

    def _on_scroll_value_changed(self, _value):
        self._materialize_visible()

    def _on_scroll_range_changed(self, *_args):
        self._materialize_visible()

    def set_rows(self, rows: list[FamilyRow]):
        for i in self._materialized:            # 重建前取消在途渲染，见下
            bar = getattr(self.itemWidget(self.item(i)), "bar", None)
            if bar is not None:
                bar.cancel()
        self._rows = list(rows)
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
            self.entity_picked.emit(self._rows[row].selected)

    def _on_double_click(self, item):
        # 双击先发一次 itemClicked（选择语义先走），再发详情；发行当前
        # selected——chip 切过哪个 face 就看哪个
        row = self.row(item)
        if 0 <= row < len(self._rows):
            self.detail_requested.emit(self._rows[row].selected)

    def _row_widget(self, i: int) -> _RowWidget:
        row = self._rows[i]
        return _RowWidget(self._worker, self._pvm, row,
                          self._preview_text_fn() or row.display_name,
                          lambda e: f"plist:{e.fingerprint}:{id(self)}",
                          name_selectable=self._name_selectable,
                          face_controls=self._face_controls)

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
    """行 = 控制行（展示名 + 元信息 + 字重分段 + 斜体钮）+ 预览条。

    容器纯 QWidget 不画任何底：常态、悬停、按下的反馈全部交给列表
    自带样式。多 face 行的字重用 SegmentedWidget（qfw 分段控件），斜体
    用 TransparentToggleToolButton（自绘斜体 I 图标）；点击就地切
    row.selected 并重渲预览。单 face 行无控件、只读展示。行点击选择
    走列表 itemClicked（QLabel 不消费鼠标，事件自然传播到视口）；分段
    与斜体钮自行消费鼠标，不会触发列表点击。"""

    def __init__(self, worker, pvm, row: FamilyRow, text,
                 tag_for, name_selectable: bool = True,
                 face_controls: bool = True):
        super().__init__()
        self._worker, self._pvm, self._row = worker, pvm, row
        self._text, self._tag_for = text, tag_for
        self._face_controls = face_controls
        self._seg: SegmentedWidget | None = None
        self._it_btn: TransparentToggleToolButton | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 8, 16, 8)
        v.setSpacing(4)               # 显式钉死：默认 spacing 随平台样式漂
        top = QHBoxLayout()
        top.setSpacing(8)
        # 家族名可划选（用户需求 2026-10-29），选择对话框除外（picker
        # 传 name_selectable=False）：名区 press 被 QLabel 消费——名区
        # 双击变选词，嵌套/detail 入口走行的其余区域
        name_lab = StrongBodyLabel(row.display_name)
        if name_selectable:
            apply_selectable(name_lab)
        top.addWidget(name_lab)
        self._meta = PillRow()
        self._meta.set_items(meta_items(row.selected))
        top.addWidget(self._meta)
        top.addStretch(1)
        self._build_controls(top)
        v.addLayout(top)
        self.bar = PreviewBar(worker, pvm, row.selected, text,
                              tag_for(row.selected), height=BAR_H)
        v.addWidget(self.bar)

    def _build_controls(self, top: QHBoxLayout):
        row = self._row
        # face_controls=False（选择对话框）：不建字重分段/斜体钮，多 face
        # 行只看 sel_key 选出的面（非斜体+400 最近）
        if not self._face_controls or len(row.faces) < 2:
            return
        # 分段键 = 精确字重（不取整）：Semilight 350 折进 W300 曾把
        # Light/Semilight 两 face 折成一段（真库微软雅黑实测）。
        # onClick 走 itemClicked；setCurrentItem 只发 currentItemChanged
        # （未连接）——程序同步不会回环
        seg = SegmentedWidget(self)
        for f in row.faces:
            seg.addItem(str(f.weight), weight_tag(f.weight),
                        onClick=lambda _c, f=f: self._pick_weight(f.weight))
        # 段宽高都不固定：46px 宽曾把 W 左缘裁成 V 形；固定高 28 曾被 qss
        # 撑出的段高裁顶、且与斜体钮错高。按段 sizeHint 定高，钮取同高正方
        seg_h = max(it.sizeHint().height() for it in seg.items.values())
        seg.setFixedHeight(seg_h)
        seg.setCurrentItem(str(row.selected.weight))
        top.addWidget(seg)
        self._seg = seg
        if any(f.italic for f in row.faces):
            btn = TransparentToggleToolButton(_italic_icon(), self)
            btn.setCheckable(True)
            btn.setChecked(row.selected.italic)
            btn.setFixedSize(seg_h, seg_h)
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(
                lambda _c: self._pick_italic(self._it_btn.isChecked()))
            top.addWidget(btn)
            self._it_btn = btn
            _ITALIC_BTNS.add(btn)

    def _pick_weight(self, weight: int):
        cur = self._row.selected
        faces = self._row.faces
        cands = [f for f in faces
                 if f.weight == weight and f.italic == cur.italic]
        if not cands:                 # 该字重无当前斜态 face → 退任意斜态
            cands = [f for f in faces if f.weight == weight]
        if cands:
            self._select(cands[0])

    def _pick_italic(self, on: bool):
        cur = self._row.selected
        faces = self._row.faces
        cands = [f for f in faces
                 if f.italic == on and f.weight == cur.weight]
        if not cands:                 # 当前字重无该斜态 → 任意字重的该斜态
            cands = [f for f in faces if f.italic == on]
        if cands:
            self._select(cands[0])
        elif self._it_btn is not None:  # 无目标 face（不应达）：钮回弹
            self._it_btn.blockSignals(True)
            self._it_btn.setChecked(cur.italic)
            self._it_btn.blockSignals(False)

    def _select(self, e: FontEntity):
        if e is self._row.selected:
            return
        self._row.selected = e
        self._meta.set_items(meta_items(e))
        self.bar.set_entity(e, self._tag_for(e))
        self._sync_controls()

    def _sync_controls(self):
        cur = self._row.selected
        if self._seg is not None:
            self._seg.setCurrentItem(str(cur.weight))
        if self._it_btn is not None:
            self._it_btn.blockSignals(True)
            self._it_btn.setChecked(cur.italic)
            self._it_btn.blockSignals(False)
