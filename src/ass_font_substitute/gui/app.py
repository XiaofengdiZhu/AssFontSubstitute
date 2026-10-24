from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF, FluentWindow, NavigationItemPosition

from ass_font_substitute.gui.pages.browse_page import BrowsePage
from ass_font_substitute.gui.pages.library_page import LibraryPage
from ass_font_substitute.gui.pages.settings_page import SettingsPage
from ass_font_substitute.gui.pages.subs_page import SubsPage
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.widgets.dialog_ani import patch_mask_dialog_animation
from ass_font_substitute.gui.workers import CoreWorker

# qfw 的 QSS 用 font 简写（如 `font: 14px 'Segoe UI',...`）造 pixel 字体
# （pointSize == -1）。首次悬停触发重绘时 Qt 内部对该字体走 setPointSize(-1)
# 喷一条无害警告（temp/probe_pointsize_fix.py 每进程仅一条，其余消息不受
# 影响）。无 C++ 栈帧可修（qInstallMessageHandler 栈里只有 qWait），过滤之，
# 其余 Qt 消息按默认格式原样转发。
_POINT_SIZE_WARN = "QFont::setPointSize: Point size <= 0"


def _forward_qt_messages(_type, ctx, message: str) -> None:
    if _POINT_SIZE_WARN in message:
        return
    category = ctx.category
    if category and category != "default":
        sys.stderr.write(f"{category}: {message}\n")
    else:
        sys.stderr.write(message + "\n")
    sys.stderr.flush()


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
        self.settings_page.bind_search_activity(self.subs_page)
        # 保存方式/另存后缀双向同步：任一页改动回灌另一页（setter 不回写）
        self.subs_page.saveModeChanged.connect(
            self.settings_page.set_save_mode)
        self.settings_page.saveModeChanged.connect(
            self.subs_page.set_save_mode)
        self.subs_page.suffixChanged.connect(
            self.settings_page.set_save_suffix)
        self.settings_page.suffixChanged.connect(
            self.subs_page.set_save_suffix)
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
    qInstallMessageHandler(_forward_qt_messages)
    app = QApplication(sys.argv)
    # 弹窗开/关动画换自绘层（Ghost Downloader 方案）：顶层窗口零特效，
    # 杜绝顶层特效引发的 native 窗口重建闪现带标题栏小窗
    patch_mask_dialog_animation()
    if sys.platform == "win32":
        # 默认 GDI 风格 hinting 在分数缩放（如 175%）下按像素网格对齐笔画，
        # 小字号汉字笔画粘连发虚；PreferNoHinting 线性缩放笔画均匀且启用
        # 亚像素定位（Ghost Downloader 同配方；A/B 证据 temp/hint_ab.py）
        font = app.font()
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        app.setFont(font)
    w = AFSWindow()
    w.show()
    return app.exec()
