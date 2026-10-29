"""中性灰药丸标签（处理字幕卡 / 预览页行 meta / 详情属性卡共用）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QSizePolicy
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBadge
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig
from qfluentwidgets import setCustomStyleSheet


class PillLabel(QLabel):
    """中性灰药丸：InfoBadge.custom 底色自定义但文字色死在样式表白字上
    ——浅底白字直接隐形。qfw VerticalSeparator 同法 paintEvent 现取主题
    配色，换肤自动跟随，无需外部重刷。"""

    def __init__(self, text: str):
        super().__init__(text)
        font = self.font()
        font.setPixelSize(11)
        self.setFont(font)
        self.setContentsMargins(8, 1, 8, 1)
        # 锁 sizeHint：QFormLayout 值列 / QHBoxLayout 默认会让子控件撑满
        # 剩余格（属性卡药丸曾横向铺满整行、预览行被拉到与行内最高控件
        # 同高），Fixed 后布局按内容给位、竖向自动居中
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # 绑定方法连接：随接收者析构自动断开；lambda 挂永生 qconfig 上，
        # 卡片 deleteLater 后死槽每次换肤喷 RuntimeError 噪声且连接泄漏
        qconfig.themeChanged.connect(self.update)

    def paintEvent(self, e):
        painter = QPainter(self)
        dark = isDarkTheme()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 26) if dark
                         else QColor(0, 0, 0, 15))
        painter.drawRoundedRect(self.rect(), 4, 4)
        painter.setPen(QColor("#cccccc") if dark else QColor("#5c5c5c"))
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())


def pill(text: str) -> PillLabel:
    return PillLabel(text)


def padded_badge(mk, text: str) -> InfoBadge:
    """彩色徽章（InfoBadge.success/warning/error/info）：出厂 QSS padding
    1px 3px 偏紧，项目内统一放宽 2px（3px 5px，用户拍板 2026-10-29）。
    setCustomStyleSheet 追加在基表后，同特异性后者胜。"""
    b = mk(text)
    qss = "InfoBadge{padding: 3px 5px;}"
    setCustomStyleSheet(b, qss, qss)
    return b


class PillRow(QWidget):
    """一行药丸（meta 项逐个并排）：内容随实体切换重建，texts() 供测试。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = QHBoxLayout(self)
        self._h.setContentsMargins(0, 0, 0, 0)
        self._h.setSpacing(4)

    def set_items(self, items: list[str]) -> None:
        while self._h.count():
            it = self._h.takeAt(0)
            w = it.widget()
            if w is not None:
                # hide 先于 deleteLater：DeferredDelete 要等事件循环一拍，
                # 同步 repaint/嵌套循环下旧药丸仍可见可命中，先藏消除叠影
                w.hide()
                w.deleteLater()
        for t in items:
            self._h.addWidget(pill(t), 0, Qt.AlignmentFlag.AlignVCenter)

    def texts(self) -> list[str]:
        return [self._h.itemAt(i).widget().text()
                for i in range(self._h.count())]
