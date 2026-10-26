"""工具提示过滤器（gui/tip.py）：安装后 ToolTip 事件被拦截（原生灰底
气泡不再出，qfw Fluent 气泡经 Enter 延时出）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_install_tip_sets_text_and_blocks_native(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QLabel

    from ass_font_substitute.gui.tip import install_tip

    w = QLabel()
    f = install_tip(w, "aiko - キラキラ ✨")
    assert w.toolTip() == "aiko - キラキラ ✨"
    # ToolTip 事件被过滤器吃掉（返回 True）→ 原生 tooltip 不再弹；
    # Fluent 气泡走 Enter 事件延时路径（qfw 官方实现）
    assert f.eventFilter(w, QEvent(QEvent.Type.ToolTip)) is True
    # 二次安装（动态更新场景）不覆盖已有文本
    install_tip(w)
    assert w.toolTip() == "aiko - キラキラ ✨"
