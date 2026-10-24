"""吞掉 watched 对象的滚轮事件：防止滚动页面时误改 ComboBox 等控件。

设置页 _WheelFreeCard 的独立过滤器版——供页面级控件（如处理字幕页
保存方式下拉）直接 installEventFilter 使用。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject


class WheelBlock(QObject):
    def eventFilter(self, watched, event):
        return event.type() == QEvent.Type.Wheel
