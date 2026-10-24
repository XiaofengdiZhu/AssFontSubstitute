from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QSpinBox
from qfluentwidgets.common.font import setFont
from qfluentwidgets.common.style_sheet import FluentStyleSheet
from qfluentwidgets.components.widgets.spin_box import SpinButton, SpinIcon

_BTN_W, _BTN_H, _GAP, _RIGHT = 31, 23, 5, 4   # qfw InlineSpinBox 的按钮几何
_EDIT_RESERVE = _BTN_W * 2 + _GAP + _RIGHT + 14   # 按钮区 + 边框预留


class _FluentSpinMixin:
    """原生 QSpinBox 套 qfluentwidgets 样式 + qfw SpinButton。

    qfluentwidgets 1.11.3 的 SpinBox（InlineSpinBox）在 PySide6 6.12 下内部
    lineEdit 宽度会被 QSS subControlRect 算成 1px，数值不可见（与嵌套层级/
    创建顺序相关，非确定性）。此处按钮改 qfw SpinButton 以 move() overlay
    定位（Ghost Downloader 同款悬停高亮），并对内部 lineEdit 几何兜底强
    制——每次布局后按自身宽度重设，不再依赖 Qt 的计算。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        FluentStyleSheet.SPIN_BOX.apply(self)
        setFont(self)
        self.setFixedHeight(33)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        # 构造期建按钮：首个 resize 前若 setEnabled(False)，changeEvent 的
        # 可用态同步会因按钮未建而落空——懒创建窗口期按钮停在可用态，
        # stepUp 是公共槽不查 isEnabled，禁用的 SpinBox 仍会被点改
        self.upButton = self.downButton = None
        self._ensure_buttons()
        self.lineEdit().installEventFilter(self)

    def _ensure_buttons(self):
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
        右侧预留按钮区（_EDIT_RESERVE），窄框退回全宽：那时数值必然很短，
        不会滑进按钮底下。"""
        le = self.lineEdit()
        want = self.width() - _EDIT_RESERVE
        if want <= 20:
            want = self.width() - 4
        if want > 20 and le.width() != want:
            le.setGeometry(2, (self.height() - le.height()) // 2, want,
                           le.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        y = (self.height() - _BTN_H) // 2
        self.downButton.move(self.width() - _RIGHT - _BTN_W, y)
        self.upButton.move(self.width() - _RIGHT - _BTN_W - _GAP - _BTN_W, y)
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
            self.upButton.setEnabled(on)
            self.downButton.setEnabled(on)


class SpinBox(_FluentSpinMixin, QSpinBox):
    pass
