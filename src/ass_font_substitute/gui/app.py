from __future__ import annotations

import logging
import sys

import darkdetect
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QColor
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow
from qfluentwidgets import NavigationItemPosition
from qfluentwidgets import Theme
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig
from qfluentwidgets import setTheme
from qfluentwidgets.common.style_sheet import updateStyleSheet

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


# 设置页 theme 值 → qfw Theme
_THEME_OF = {"auto": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


class AFSWindow(FluentWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or Settings()
        # 先定主题再建页：页面里有按当前主题取色的自绘控件（统计分隔线/药丸）
        setTheme(_THEME_OF[self.settings.get("theme")])
        self._apply_bg_effect(self.settings.get("bg_effect"))
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
        # 库数据变化（删目录/索引更新完成）广播：预览页重拉清单、字幕页重分类
        self.library_page.libraryChanged.connect(
            self.browse_page.on_library_changed)
        self.library_page.libraryChanged.connect(
            self.subs_page.on_library_changed)
        # 保存方式/另存后缀双向同步：任一页改动回灌另一页（setter 不回写）
        self.subs_page.saveModeChanged.connect(
            self.settings_page.set_save_mode)
        self.settings_page.saveModeChanged.connect(
            self.subs_page.set_save_mode)
        self.subs_page.suffixChanged.connect(
            self.settings_page.set_save_suffix)
        self.settings_page.suffixChanged.connect(
            self.subs_page.set_save_suffix)
        self.settings_page.themeChanged.connect(self._apply_theme)
        self.settings_page.bgEffectChanged.connect(self._apply_bg_effect)
        # 主题切换（含跟随系统翻转）后重贴窗口材质（Mica 明暗/Acrylic 底色随
        # 主题变）；自绘控件（统计分隔线/药丸）paintEvent 现取主题色，免刷
        qconfig.themeChangedFinished.connect(self._apply_bg_effect)
        # 跟随系统明暗：GUI 线程 2s 轮询（darkdetect 读注册表，开销可忽略）。
        # 不用 qfw SystemThemeListener——win32 分支卡在 darkdetect 原生循环
        # 里收不了尾，进程退出会炸
        self._last_sys_theme = darkdetect.theme()
        self._sys_theme_timer = QTimer(self)
        self._sys_theme_timer.setInterval(2000)
        self._sys_theme_timer.timeout.connect(self._poll_system_theme)
        self._sys_theme_timer.start()
        self.setWindowTitle("Ass 字体替补")
        self.resize(1100, 750)

    # ---- 主题与窗口材质（设置页「常规」组驱动）----
    def _apply_theme(self, name: str):
        setTheme(_THEME_OF[name])

    def _apply_bg_effect(self, effect: str | None = None):
        """按设置贴窗口材质。FluentWindow 在 Win11 出厂即开 Mica；其余材质
        手贴 windowEffect 并把窗口底色置透明，None 恢复实色。offscreen
        （测试）下 winId 无真实 DWM 窗口，调用静默无效。"""
        effect = effect or self.settings.get("bg_effect")
        # 一律先清再贴：setMicaEffectEnabled 在 Win10 早退，复位不能靠它；
        # 非普通 Mica 的材质也不能让 _isMicaEnabled 残留 True（主题切换时
        # FluentWindow 会拿普通 Mica 覆盖）
        self._isMicaEnabled = False
        self.windowEffect.removeBackgroundEffect(self.winId())
        if effect == "mica":
            self.setMicaEffectEnabled(True)
        elif effect == "none":
            self.setBackgroundColor(self._normalBackgroundColor())
        else:
            if effect == "mica_alt":
                self.windowEffect.setMicaEffect(
                    self.winId(), isDarkTheme(), isAlt=True)
            elif effect == "acrylic":
                self.windowEffect.setAcrylicEffect(
                    self.winId(), "00000030" if isDarkTheme() else "FFFFFF30")
            elif effect == "aero":
                self.windowEffect.setAeroEffect(self.winId())
            self.setBackgroundColor(QColor(0, 0, 0, 0))   # 材质透出：底色透明

    def _poll_system_theme(self):
        if self.settings.get("theme") != "auto":
            return
        t = darkdetect.theme()
        if not t or t == self._last_sys_theme:
            return
        self._last_sys_theme = t
        qconfig.theme = Theme.AUTO          # 重解析 auto → 新的系统明暗
        updateStyleSheet()
        qconfig.themeChangedFinished.emit()

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
