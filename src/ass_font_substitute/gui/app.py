from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF, FluentWindow, NavigationItemPosition

from ass_font_substitute.gui.pages.browse_page import BrowsePage
from ass_font_substitute.gui.pages.library_page import LibraryPage
from ass_font_substitute.gui.pages.settings_page import SettingsPage
from ass_font_substitute.gui.pages.subs_page import SubsPage
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.workers import CoreWorker


class AFSWindow(FluentWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()
        self.worker = CoreWorker(self.settings)
        self.worker.start()
        self.library_page = LibraryPage(self.settings, self.worker)
        self.browse_page = BrowsePage(self.settings, self.worker)
        self.subs_page = SubsPage(self.settings, self.worker)
        self.settings_page = SettingsPage(self.settings, self.worker)
        self.addSubInterface(self.library_page, FIF.LIBRARY, "字体库管理")
        self.addSubInterface(self.browse_page, FIF.VIEW, "字体库预览")
        self.addSubInterface(self.subs_page, FIF.DOCUMENT, "处理字幕")
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置",
                             NavigationItemPosition.BOTTOM)
        self.setWindowTitle("Ass 字体替补")
        self.resize(1100, 750)

    def closeEvent(self, e):
        self.library_page.shutdown()        # 先汇合在途索引线程（写库中不能退）
        self.worker.shutdown()
        self.worker.wait(10000)
        super().closeEvent(e)


def main() -> int:
    # 分数缩放（如 175%）下按系统比例原生渲染；取整会让 DWM 拉伸整窗导致文字发虚。
    # 必须在 QApplication 创建前设置。
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if sys.platform == "win32":
        # 任务栏归属自身 AUMID，而不是显示/分组在 python.exe 下
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AssFontSubstitute.App")
    # fontTools 解析怪字体喷大量 warning（post.stringData 等），界面程序静默
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    app = QApplication(sys.argv)
    w = AFSWindow()
    w.show()
    return app.exec()
