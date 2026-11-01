"""双滑块范围滑动条（qfw 无 RangeSlider）。

复用 qfw SliderHandle（22×22 圆钮、内圆随主题强调色、按压半径动画），
轨道自绘：底槽半透明灰、两钮间区段用主题强调色（setThemeColor/跟随
系统强调色后 repaint 自动跟上——强调色是 paint 时现取不缓存）。
拖拽语义：按在近钮上拖该钮；按在轨道空白处，近钮跳至点击值再拖。
约束 low ≤ high（相等合法：区间退化为单点仍可用）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtCore import QRectF
from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget
from qfluentwidgets.common.style_sheet import isDarkTheme
from qfluentwidgets.common.style_sheet import themeColor
from qfluentwidgets.components.widgets.slider import SliderHandle


class RangeSlider(QWidget):
    """水平范围滑动条：整数值 [minimum, maximum]，值域 (low, high)。"""

    valueChanged = Signal(int, int)       # (low, high)
    sliderPressed = Signal()
    sliderReleased = Signal()
    # 键盘步进落定（鼠标拖拽的提交挂 sliderReleased，键盘无 release——
    # 设置页由此信号即时落盘，审查二轮 P1：键盘步进曾只刷数值框不落盘）
    stepped = Signal()

    def __init__(self, minimum: int, maximum: int, low: int, high: int,
                 parent=None):
        super().__init__(parent)
        self._min, self._max = minimum, maximum
        self._low, self._high = int(low), int(high)
        self._drag = None                  # None / "low" / "high"
        self._last_knob = "low"            # 键盘步进作用钮（最后拖动侧）
        self._grab_dx = 0                  # 抓取点相对钮心的 x 偏移
        self.lo_handle = SliderHandle(self)
        self.hi_handle = SliderHandle(self)
        for h in (self.lo_handle, self.hi_handle):
            # 钮的 press 被 SliderHandle 覆写吃掉（未 ignore=已接受），
            # 不冒泡到本控件——拖拽事件须经过滤器在钮层截走
            h.installEventFilter(self)
        self.setMinimumHeight(22)
        self.setMinimumWidth(120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # 键盘步进入口
        self.setValues(low, high)          # 钳界/排序统一走 setValues
        self._adjust_handles()

    def eventFilter(self, watched, event):
        if watched in (self.lo_handle, self.hi_handle):
            t = event.type()
            if (t == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                # 只认领拖拽，不按点击点落值；记光标相对钮心的偏移，
                # move 按偏移回算——否则首笔 move 从按点取值，一滑就
                # 向右瞬移（按点偏右半钮+内缩=22px）
                self._drag = "low" if watched is self.lo_handle else "high"
                self._last_knob = self._drag
                self._grab_dx = self._handle_x(watched, event) - self._value_x(
                    self._low if self._drag == "low" else self._high)
                self.sliderPressed.emit()
                return True
            if t == QEvent.Type.MouseMove and self._drag is not None:
                self._apply_drag(self._handle_x(watched, event)
                                 - self._grab_dx)
                return True
            if (t == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._drag is not None):
                # 按钮维度同 widget 路径对齐：左键按住期间叠加的右键
                # release 曾清 _drag 并误发提交、拖拽中断
                self._drag = None
                self.sliderReleased.emit()
                return True
        return super().eventFilter(watched, event)

    def _handle_x(self, watched, event) -> int:
        return watched.mapTo(self, event.position().toPoint()).x()

    def values(self) -> tuple[int, int]:
        return self._low, self._high

    def setValues(self, low: int, high: int) -> None:
        # 先各自钳到值域再排序：交叉赋值会撞上旧自值，一次拖两值会互相顶住
        low = max(self._min, min(int(low), self._max))
        high = max(self._min, min(int(high), self._max))
        low, high = min(low, high), max(low, high)
        if (low, high) == (self._low, self._high):
            return
        self._low, self._high = low, high
        self._adjust_handles()
        self.valueChanged.emit(low, high)

    # ---- 几何 ----

    @property
    def groove_length(self) -> int:
        """可用轨道长：总宽减一个钮宽（两端钮心内缩半钮，与 qfw 同款）。"""
        return max(self.width() - self.lo_handle.width(), 1)

    def _value_x(self, value: int) -> int:
        total = max(self._max - self._min, 1)
        return round((value - self._min) / total * self.groove_length)

    def _x_value(self, x: int) -> int:
        return round(x / self.groove_length * (self._max - self._min)
                     ) + self._min

    def _adjust_handles(self):
        # qfw Slider 同款：move(value_x) 即可，钮心天生在 value_x+半钮
        # （画时已按半钮内缩轨道）——再 +r 会把两端钮推出槽外
        self.lo_handle.move(self._value_x(self._low), 0)
        self.hi_handle.move(self._value_x(self._high), 0)

    def resizeEvent(self, e):
        self._adjust_handles()

    # ---- 绘制 ----

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = self.lo_handle.width() / 2
        # 底槽：qfw Slider 同色同几何（4px 圆角条，钮心内缩半钮）
        p.setBrush(QColor(255, 255, 255, 115) if isDarkTheme()
                   else QColor(0, 0, 0, 100))
        p.drawRoundedRect(QRectF(r, r - 2, self.width() - r * 2, 4), 2, 2)
        # 两钮心之间区段：主题强调色（paint 时现取，换肤/换强调色免刷）
        p.setBrush(themeColor())
        x0, x1 = self._value_x(self._low), self._value_x(self._high)
        p.drawRoundedRect(QRectF(r + x0, r - 2, max(x1 - x0, 4), 4), 2, 2)

    # ---- 交互 ----

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        x = e.position().toPoint().x()
        # 按在钮上：只认领拖拽（真机走过滤器，此处兜底直投合成事件）
        self._drag = None
        if self.lo_handle.geometry().contains(e.position().toPoint()):
            self._drag = "low"
        elif self.hi_handle.geometry().contains(e.position().toPoint()):
            self._drag = "high"
        if self._drag is not None:
            self._grab_dx = x - self._value_x(
                self._low if self._drag == "low" else self._high)
        else:
            # 点在轨道空白处：近钮跳至点击值（光标即钮心，无偏移）
            mid = (self.lo_handle.pos().x()
                   + self.hi_handle.pos().x()) // 2
            self._drag = "low" if x < mid else "high"
            self._last_knob = self._drag
            self._apply_drag(x)
            self._grab_dx = 0
        self.sliderPressed.emit()
        # 必须显式 accept：不接住 press，Qt 不把后续 move/release 送给
        # 本控件（钮是子控件，press 从它 ignore 冒泡上来，无抓取=拖不动）
        e.accept()

    def mouseMoveEvent(self, e):
        if self._drag:
            # 与过滤器路径同款：按抓取偏移回算，不按光标绝对位置取值
            self._apply_drag(e.position().toPoint().x() - self._grab_dx)
            e.accept()
        else:
            e.ignore()

    def mouseReleaseEvent(self, e):
        # 只认左键且有 press 在先：右/中键 release（press 已被 ignore，
        # _drag 必 None）不发 sliderReleased——settings_page 把它当提交
        # 信号，右键误触会把未改动的值重提交（二轮审查 P2）
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        was = self._drag is not None
        self._drag = None
        if was:
            self.sliderReleased.emit()
        e.accept()

    def _apply_drag(self, x: int):
        v = max(self._min, min(self._x_value(x), self._max))
        if self._drag == "low":
            self.setValues(v, self._high)
        else:
            self.setValues(self._low, v)

    def keyPressEvent(self, e):
        # 键盘步进：作用于最后拖动的钮（_last_knob 记忆；键盘首用默认
        # low）。←→ 步 1、PageUp/Down 步 10、Home/End 端点。
        # 拖拽中不落值（松手才提交，与鼠标口径一致）
        if self._drag is not None:
            e.ignore()
            return
        prev = (self._low, self._high)
        k = e.key()
        step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                Qt.Key.Key_PageDown: -10, Qt.Key.Key_PageUp: 10}.get(k)
        if step is not None:
            if self._last_knob == "high":
                self.setValues(self._low, self._high + step)
            else:
                self.setValues(self._low + step, self._high)
        elif k == Qt.Key.Key_Home:
            self.setValues(self._min, self._high)
        elif k == Qt.Key.Key_End:
            self.setValues(self._low, self._max)
        else:
            e.ignore()
            return
        e.accept()
        # 值未变（已在端点再按 Home 等）不发了：提交方是全量 JSON 落盘，
        # 空按键不值得付一次写
        if (self._low, self._high) != prev:
            self.stepped.emit()

    def wheelEvent(self, e):
        # 滚轮不改值（防滚动设置页误改）；ignore 让事件冒泡给设置页滚动
        e.ignore()
