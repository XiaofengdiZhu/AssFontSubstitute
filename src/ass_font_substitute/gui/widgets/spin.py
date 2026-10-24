from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox
from qfluentwidgets.common.font import setFont
from qfluentwidgets.common.style_sheet import FluentStyleSheet


class _FluentSpinMixin:
    """原生 QSpinBox/QDoubleSpinBox 套 qfluentwidgets 的 SPIN_BOX 样式表。

    qfluentwidgets 1.11.3 的 SpinBox（InlineSpinBox，自带按钮+布局）在
    PySide6 6.12 下内部 lineEdit 宽度塌成 1px，数值不可见；原生类 + 同一
    样式表外观一致且数值正常（对照实验 temp/spin_probe*.png）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        FluentStyleSheet.SPIN_BOX.apply(self)
        setFont(self)
        self.setFixedHeight(33)


class SpinBox(_FluentSpinMixin, QSpinBox):
    pass


class DoubleSpinBox(_FluentSpinMixin, QDoubleSpinBox):
    pass
