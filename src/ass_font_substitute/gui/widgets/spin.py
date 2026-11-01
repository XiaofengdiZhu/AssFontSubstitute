from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent
from PySide6.QtCore import QRect
from PySide6.QtCore import QRectF
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtGui import QCursor
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QSpinBox
from PySide6.QtWidgets import QToolButton
from qfluentwidgets.common.font import setFont
from qfluentwidgets.common.style_sheet import FluentStyleSheet
from qfluentwidgets.common.style_sheet import isDarkTheme
from qfluentwidgets.components.widgets.spin_box import SpinButton
from qfluentwidgets.components.widgets.spin_box import SpinIcon

_BTN_W, _BTN_H, _GAP, _RIGHT = 31, 23, 5, 4   # qfw InlineSpinBox 的按钮几何
_EDIT_RESERVE = _BTN_W * 2 + _GAP + _RIGHT + 14   # 按钮区 + 边框预留
_CBTN_W, _CBTN_H = 26, 33                     # qfw CompactSpinButton 几何


class _CompactSpinButton(QToolButton):
    """照 qfw CompactSpinButton：26×33 单钮上下双箭头，贴框右缘常显。
    点击按上下半区分流 stepUp/stepDown，长按连发——qfw 原版点击弹
    Qt.Popup 浮层抢焦点，弃。"""

    def __init__(self, spin: QSpinBox, parent=None):
        super().__init__(parent)
        self._spin = spin
        self._up = True
        self.isPressed = False
        self.setFixedSize(_CBTN_W, _CBTN_H)
        self.setMouseTracking(True)      # 跨半区 hover 移动时刷新底色
        self._repeat = QTimer(self)
        self._repeat.setInterval(70)
        self._repeat.timeout.connect(self._step)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._up = e.position().y() * 2 < self.height()
            self.isPressed = True
            self._step()
            QTimer.singleShot(350, self._start_repeat)
            self.update()

    def mouseReleaseEvent(self, e):
        self.isPressed = False
        self._repeat.stop()
        self.update()

    def mouseMoveEvent(self, e):
        self.update()
        super().mouseMoveEvent(e)

    def _start_repeat(self):
        # singleShot 回调不与控件生命周期绑定，同 eventFilter 路径守卫
        if not shiboken6.isValid(self):
            return
        if self.isPressed:               # 快速点按已松手，不再连发
            self._repeat.start()

    def _step(self):
        if self._spin.isEnabled():
            (self._spin.stepUp if self._up else self._spin.stepDown)()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        # hover/按压底色复刻 SpinButton 的 QSS（light 黑 9/255、dark 白
        # 9/255，按压再淡；圆角 4），上下半区各自高亮——qfw QSS 类选择
        # 器按类名匹配，套不上自定义钮，只能自画。箭头画法照 qfw 原版
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            top = self.mapFromGlobal(QCursor.pos()).y() * 2 < self.height()
            h2 = self.height() // 2
            rects = ((QRect(1, 1, self.width() - 2, h2 - 1), top),
                     (QRect(1, h2, self.width() - 2, self.height() - h2 - 1),
                      not top))
            painter.setPen(Qt.PenStyle.NoPen)
            for rect, hot in rects:
                if not hot:
                    continue
                alpha = 6 if self.isPressed else 9
                painter.setBrush(QColor(255, 255, 255, alpha)
                                 if isDarkTheme() else QColor(0, 0, 0, alpha))
                painter.drawRoundedRect(rect, 4, 4)
        x = (self.width() - 10) / 2
        s = 9
        # 两箭头各离中线 2px：qfw 原版偏移 (+1/0) 两箭重叠，观感挤
        SpinIcon.UP.render(painter, QRectF(x, self.height() / 2 - s - 2, s, s))
        SpinIcon.DOWN.render(painter, QRectF(x, self.height() / 2 + 2, s, s))


class _FluentSpinMixin:
    """原生 QSpinBox 套 qfluentwidgets 样式 + qfw SpinButton。

    qfluentwidgets 1.11.3 的 SpinBox/CompactSpinBox 给 QAbstractSpinBox 套
    QSS（padding 撑出按钮区），而 Qt 6.12 的 QStyleSheetStyle 会把带样式
    的 QAbstractSpinBox 内部 lineEdit 宽度算成 1px（任何有效 QSS 都触发，
    与 padding 值无关；6.9 正常，官方 Gallery 同款 demo 也中招）——真机
    数值不可见。此处按钮改 overlay 定位（Ghost Downloader 同款悬停高
    亮），并对内部 lineEdit 几何兜底强制——每次布局后按自身宽度重设，
    不依赖 Qt 的 QSS 计算。

    compact 形态（_compact=True）：qfw CompactSpinButton 同款 26×33 双箭
    头单钮，贴框右缘常显，点击直接步进。"""

    _compact = False

    def __init__(self, parent=None):
        super().__init__(parent)
        FluentStyleSheet.SPIN_BOX.apply(self)
        setFont(self)
        self.setFixedHeight(33)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        # 构造期建按钮：首个 resize 前若 setEnabled(False)，changeEvent 的
        # 可用态同步会因按钮未建而落空——懒创建窗口期按钮停在可用态，
        # stepUp 是公共槽不查 isEnabled，禁用的 SpinBox 仍会被点改
        self.upButton = self.downButton = self.spinButton = None
        self._ensure_buttons()
        self.lineEdit().installEventFilter(self)

    def _ensure_buttons(self):
        if self._compact:
            if self.spinButton is not None:
                return
            self.spinButton = _CompactSpinButton(self, self)
            return
        if self.upButton is not None:
            return
        self.upButton = SpinButton(SpinIcon.UP, self)
        self.downButton = SpinButton(SpinIcon.DOWN, self)
        self.upButton.clicked.connect(self.stepUp)
        self.downButton.clicked.connect(self.stepDown)
        for b in (self.upButton, self.downButton):
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(70)

    def _fix_edit(self):
        """QSS 下 Qt 可能把内部 lineEdit 算成 1px——按自身宽度兜底重设。
        右侧预留按钮区，窄框退回全宽：那时数值必然很短，不会滑进钮底下。"""
        le = self.lineEdit()
        if self._compact:
            # 框宽按最宽值定制：文字左起、够短，滑不进右缘钮底下；
            # lineEdit 全宽——预留区会在 % 后面凭空切一刀（lineEdit 边界）
            want = self.width() - 4
        else:
            want = self.width() - _EDIT_RESERVE
            if want <= 20:
                want = self.width() - 4
        if want > 20 and le.width() != want:
            le.setGeometry(2, (self.height() - le.height()) // 2, want,
                           le.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._compact:
            self.spinButton.move(self.width() - _CBTN_W, 0)
        else:
            y = (self.height() - _BTN_H) // 2
            self.downButton.move(self.width() - _RIGHT - _BTN_W, y)
            self.upButton.move(self.width() - _RIGHT - _BTN_W - _GAP - _BTN_W,
                               y)
        self._fix_edit()

    def eventFilter(self, obj, e):
        # Qt 在焦点/样式事件后仍会重排 lineEdit——等它落子后纠偏一次。
        # singleShot 回调不与控件生命周期绑定，销毁后触发会 RuntimeError
        if obj is self.lineEdit() and e.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fix_edit_if_alive)
        return super().eventFilter(obj, e)

    def _fix_edit_if_alive(self):
        if shiboken6.isValid(self):
            self._fix_edit()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.EnabledChange:
            on = self.isEnabled()
            if self._compact:
                if self.spinButton is not None:
                    self.spinButton.setEnabled(on)
                    if not on:
                        # 按住期间被禁用：连发必须即刻停——禁用期 Qt 不再
                        # 投递 release，isPressed 残留 True 会让 _repeat
                        # 无人停；重新启用后恢复可步进（二轮审查 P2）
                        self.spinButton._repeat.stop()
                        self.spinButton.isPressed = False
            elif self.upButton is not None:
                self.upButton.setEnabled(on)
                self.downButton.setEnabled(on)


class SpinBox(_FluentSpinMixin, QSpinBox):
    pass


class CompactSpinBox(_FluentSpinMixin, QSpinBox):
    _compact = True
