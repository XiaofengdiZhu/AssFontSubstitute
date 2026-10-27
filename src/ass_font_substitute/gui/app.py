from __future__ import annotations

import logging
import sys

import darkdetect
from PySide6.QtCore import QEvent
from PySide6.QtCore import QRect
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtGui import QColor
from PySide6.QtGui import QFont
from PySide6.QtGui import QGuiApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import BodyLabel
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow
from qfluentwidgets import IndeterminateProgressRing
from qfluentwidgets import NavigationItemPosition
from qfluentwidgets import Theme
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig
from qfluentwidgets import setTheme
from qfluentwidgets import setThemeColor
from qfluentwidgets.common.style_sheet import updateStyleSheet
from qfluentwidgets.window import SplashScreen

from ass_font_substitute.gui.pages.browse_page import BrowsePage
from ass_font_substitute.gui.pages.library_page import LibraryPage
from ass_font_substitute.gui.pages.settings_page import SettingsPage
from ass_font_substitute.gui.pages.subs_page import SubsPage
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.widgets.dialog_ani import patch_mask_dialog_animation
from ass_font_substitute.gui.widgets.primary_text import patch_primary_icon
from ass_font_substitute.gui.widgets.primary_text import tune_all
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

# 出厂窗口尺寸（无存档/存档弃用时的启动大小，主屏居中）。居中须显式传
# 尺寸：ctor 里布局尚未稳定，窗口 width() 是中间值不是 resize 的目标值
_DEFAULT_SIZE = (1100, 750)

# 强调色「跟随系统」的兜底：qfw 出厂主题色（getSystemAccentColor 失败/非
# win/mac 平台时用）；save=False 一律不落 qconfig 自己的 config.json
_DEFAULT_THEME_COLOR = QColor("#009faa")


def _system_accent() -> QColor | None:
    """Windows/macOS 系统强调色；取不到（其他平台/DWM 查询失败）返回
    None。qframelesswindow 走 DWM DwmGetColorizationColor，失败时返回
    无效 QColor 并 warn，这里一并拦成 None。"""
    if sys.platform not in ("win32", "darwin"):
        return None
    try:
        from qframelesswindow.utils import getSystemAccentColor
        c = getSystemAccentColor()
        return c if (c is not None and c.isValid()) else None
    except Exception:
        return None


class _Splash(SplashScreen):
    """启动画面：空图标（图标待设计）+ 加载环 + 文案，盖住首帧到库就绪。
    qfw SplashScreen 无布局（icon 手动定位），环与文案在 resizeEvent 跟随。"""

    def __init__(self, parent):
        super().__init__(QIcon(), parent)
        # qfw TitleBar 只靠 window 的 WindowStateChange 事件切换最大化/
        # 还原图标，初值恒「最大化」——本画面创建晚于 ctor 期最大化，
        # 错过事件，须手动同步初值（最大化重启时按钮才不显错图标）
        self.titleBar.maxBtn.setMaxState(self.window().isMaximized())
        self.ring = IndeterminateProgressRing(self)
        self.ring.setFixedSize(28, 28)
        self.tip = BodyLabel("正在加载字体库…", self)
        # qfw 靠父对象事件过滤器跟随窗口尺寸，实测 show 期最大化那记
        # resize 不经 Qt 事件系统（独立过滤器同样收不到），轮询兜底；
        # 存续期仅启动数秒，50ms 开销可忽略，hide 即停
        self._poll = QTimer(self)
        self._poll.setInterval(50)
        self._poll.timeout.connect(self._follow_parent)
        self._poll.start()

    def _follow_parent(self):
        p = self.parentWidget()
        if p is not None:
            self.resize(p.size())

    def eventFilter(self, obj, e):
        # qfw 过滤器逐笔记父 Resize 同步自身尺寸：show 期 native 最大化的
        # 中间尺寸序列（1100→654×517→终值）会把预给满的 splash 缩回中间
        # 值，首帧只盖一角。尺寸跟随全权交轮询，这里只保留新兄弟置顶
        if obj is self.parent() and e.type() == QEvent.ChildAdded:
            self.raise_()
        return False

    def hideEvent(self, e):
        self._poll.stop()
        super().hideEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ring.move((self.width() - self.ring.width()) // 2,
                       self.height() - 110)
        self.tip.adjustSize()
        self.tip.move((self.width() - self.tip.width()) // 2,
                      self.ring.y() + 44)


class AFSWindow(FluentWindow):
    def __init__(self, settings: Settings | None = None):
        super().__init__()
        # 边缘 resize 命中带：qframelesswindow 出厂 BORDER_WIDTH=5 物理px
        # （WM_NCHITTEST 按物理坐标判定），高 DPI 下更窄难抓住；提到原生
        # 同级 8 逻辑px。原生窗口的 resize 边在客户区外由 DWM 接管，无边
        # 框窗只能换算后在客户区内画等宽带——官方 Gallery 等同栈应用均窄
        self.BORDER_WIDTH = max(8, round(8 * self.devicePixelRatioF()))
        self.settings = settings or Settings()
        # 先定主题再建页：页面里有按当前主题取色的自绘控件（统计分隔线/药丸）
        setTheme(_THEME_OF[self.settings.get("theme")])
        self._apply_theme_color(self.settings.get("theme_color"))
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
        # qfw 侧栏出厂展开宽 322 太宽，砍掉三分之一
        self.navigationInterface.setExpandWidth(215)
        self.settings_page.bind_search_activity(self.subs_page)
        # 启动强调色应用在建页前（主题先行约束），页面按钮当时还不存在——
        # 建页后补一轮 primary 文字色调优，否则重启后存量浅强调色下白字
        tune_all()
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
        # store 级变更通知驱动窗口应用：任何来源的 set（含 worker 线程
        # cache_move 同款通路）都能触发，页面不再当信号转发器
        self.settings.valueChanged.connect(self._on_setting_changed)
        # 主题切换（含跟随系统翻转）后重贴窗口材质（Mica 明暗/Acrylic 底色随
        # 主题变）；自绘控件（统计分隔线/药丸）paintEvent 现取主题色，免刷
        qconfig.themeChangedFinished.connect(self._apply_bg_effect)
        # 跟随系统明暗：GUI 线程 2s 轮询（darkdetect 读注册表，开销可忽略）。
        # 不用 qfw SystemThemeListener——win32 分支卡在 darkdetect 原生循环
        # 里收不了尾，进程退出会炸
        self._last_sys_theme = darkdetect.theme()
        # 强调色跟随系统：轮询同款定时器里捎带查系统强调色（注册表读，廉价）
        self._last_sys_accent = _system_accent()
        self._sys_theme_timer = QTimer(self)
        self._sys_theme_timer.setInterval(2000)
        self._sys_theme_timer.timeout.connect(self._poll_system_theme)
        self._sys_theme_timer.start()
        self.setWindowTitle("Ass 字体替补")
        self.resize(*_DEFAULT_SIZE)
        # 最小窗口按布局硬约束定（175% DPI 实测各页 layout 最小宽：库页
        # 823=统计卡横排+目录行左块定宽、字幕页 767、预览页 844=搜索下限
        # +三组带标签下拉+计数，均含页边距；四页纵向皆可滚，高度不算硬
        # 约束）——最宽页 + 左侧导航 48 + 窗框余量；高度给标题条 + 统计
        # 卡 + 一行目录的可用下限
        self.setMinimumSize(900, 620)
        self._pending_maximized = False
        self._restore_geometry()

    # ---- 主题与窗口材质（设置页「常规」组驱动）----
    def _on_setting_changed(self, key: str, value):
        if key == "theme":
            self._apply_theme(value)
        elif key == "bg_effect":
            self._apply_bg_effect(value)
        elif key == "theme_color":
            self._apply_theme_color(value)
        elif key == "language":
            # 各界面展示名按界面语言链重挑：浏览页行模型即建即显，重建
            # 列表即可；处理页卡片选项名随下轮分析重建，弹窗类打开时现取
            self.browse_page.reload_names()

    def _apply_theme(self, name: str):
        setTheme(_THEME_OF[name])

    def _apply_theme_color(self, stored: str):
        """强调色：非空 #rrggbb 直接用；空=跟随系统（取不到用 qfw 出厂色）。
        save=False：qconfig 自持的 config.json 与本项目设置无关，不落盘。"""
        c = QColor(stored) if stored else _system_accent()
        setThemeColor(c if c is not None else _DEFAULT_THEME_COLOR, save=False)
        # qfw 缺陷：primary 按钮文字色写死白，浅色强调色下不可读——按
        # 亮度换深色字（弹窗按钮懒创建，显示前由 dialog_ani 钩子补调）
        tune_all()

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
        self._poll_system_accent()          # 强调色跟随独立于主题模式
        if self.settings.get("theme") != "auto":
            return
        t = darkdetect.theme()
        if not t or t == self._last_sys_theme:
            return
        self._last_sys_theme = t
        qconfig.theme = Theme.AUTO          # 重解析 auto → 新的系统明暗
        updateStyleSheet()
        qconfig.themeChangedFinished.emit()

    def _poll_system_accent(self):
        """「跟随系统」强调色在系统换色后 2s 内跟上；自定义颜色不受影响。
        必须走 _apply_theme_color 统一入口：setThemeColor 重拼 qss 用的是
        tune 时算好的静态覆盖串，深强调色时存的是空串，换浅色后重拼即
        白字回归（审查 P0）——tune_all 必须跟着跑。"""
        if self.settings.get("theme_color"):
            return
        c = _system_accent()
        if c is not None and c != self._last_sys_accent:
            self._last_sys_accent = c
            self._apply_theme_color("")

    def closeEvent(self, e):
        self._save_geometry()
        self.library_page.shutdown()        # 先汇合在途索引线程（写库中不能退）
        self.worker.shutdown()
        self.worker.wait(10000)
        super().closeEvent(e)

    # ---- 窗口位置/大小记忆 ----

    def _save_geometry(self):
        """关闭时记位置/大小进设置。最大化时取 normalGeometry（还原态
        矩形）+ max 标记，恢复时先复位矩形再套最大化。未上屏不记
        （offscreen 测试里 geometry 是无意义初值）。"""
        if not self.isVisible():
            return
        r = self.normalGeometry()
        self.settings.set("win_geometry", {
            "x": r.x(), "y": r.y(), "w": r.width(), "h": r.height(),
            "max": self.isMaximized(),
        })

    def _center_on_screen(self, w: int, h: int):
        """出厂位：主屏可用区居中（Qt 默认落左上角）。窗口比屏大时偏移
        取 0（贴左上），超屏部分向右/下探出——小屏下物理上不可避免。"""
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(scr.x() + max(0, (scr.width() - w) // 2),
                         scr.y() + max(0, (scr.height() - h) // 2), w, h)

    def _restore_geometry(self):
        """启动恢复上次关闭的位置/大小；无存档或存档弃用时主屏居中。
        屏幕会变（拔显示器/改分辨率/换机）：包围盒内且至少与一块屏有
        标题条交集的存档照原样恢复（跨双屏的正常布局不动）；越界的整窗
        钳进交集最大的那块屏（按包围盒钳会落进拼屏间的死角）；与所有
        屏幕交集不足一块标题条（200x100）的视为屏外存档，弃用回默认。
        最大化标记独立于矩形有效性：最大化必落在某屏内，永远可恢复。"""
        g = self.settings.get("win_geometry")
        if not g:
            # 居中只在真实窗口系统做：offscreen（测试）下 ctor 期重定位
            # 会毒化后续页面刷新（深色页渲染竞态出白块），跳过
            if QGuiApplication.platformName() != "offscreen":
                self._center_on_screen(*_DEFAULT_SIZE)
            return
        screens = QGuiApplication.screens()
        avail = screens[0].availableGeometry()
        for sc in screens[1:]:
            avail = avail.united(sc.availableGeometry())
        rect = QRect(g["x"], g["y"], g["w"], g["h"])
        rect.setSize(rect.size().expandedTo(
            QSize(self.minimumWidth(), self.minimumHeight())))

        def titlebar_on(geo) -> bool:
            inter = rect.intersected(geo)
            return inter.width() >= 200 and inter.height() >= 100

        # 包围盒 contain 只是粗滤：混合分辨率/转向的拼屏存在包围盒内
        # 死角，还要与至少一块屏有标题条交集才可原样恢复，否则窗口会
        # 恢复成看不见也拖不着
        if avail.contains(rect) and any(
                titlebar_on(sc.availableGeometry()) for sc in screens):
            self.setGeometry(rect)
            if g["max"]:
                self._restore_maximized()
            return
        # 越界：找交集最大的屏幕整窗钳入；交集不足一块标题条（200x100）
        # = 屏外存档弃用
        best = None
        best_area = 0
        for sc in screens:
            inter = rect.intersected(sc.availableGeometry())
            area = inter.width() * inter.height()
            if area > best_area:
                best, best_area = sc.availableGeometry(), area
        if best is None or not titlebar_on(best):
            if g["max"]:
                self._restore_maximized()
            if QGuiApplication.platformName() != "offscreen":
                self._center_on_screen(*_DEFAULT_SIZE)
            return
        rect.setWidth(min(rect.width(), best.width()))
        rect.setHeight(min(rect.height(), best.height()))
        rect.moveLeft(min(max(rect.left(), best.left()),
                          best.right() - rect.width() + 1))
        rect.moveTop(min(max(rect.top(), best.top()),
                         best.bottom() - rect.height() + 1))
        self.setGeometry(rect)
        if g["max"]:
            self._restore_maximized()

    def _restore_maximized(self):
        """最大化恢复：真实平台 ctor 直接置状态——show 首帧即最大化，免
        「先还原矩形再最大化」的中间帧与启动画面尺寸竞态。offscreen 隐藏
        窗口 setWindowState 走 qframelesswindow 的 win32 假句柄通路会炸
        （GetWindowPlacement 无效句柄），改挂标记供测试断言。"""
        if QGuiApplication.platformName() == "offscreen":
            self._pending_maximized = True
        else:
            self.setWindowState(Qt.WindowState.WindowMaximized)


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
    patch_primary_icon()        # qfw primary 图标画死反白，浅强调色下换深色
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
    # 启动画面：库加载（开库 + 逐目录探测，大库数秒）期间盖住窗口，
    # loadCompleted（首个 lib 结果/失败）收幕；30s 兜底防卡死盖板。
    # 无目录没有可等的加载，直接进主界面。
    # splash 须在 w.show() 之前建好、给足首尺寸并 show：最大化存档下
    # 窗口曝光即伴随同步绘制，子控件此时仍隐藏就不参与首帧（裸 UI 先
    # 出、盖板迟到）；且最大化时窗口尺寸还是还原矩形，首尺寸按目标屏
    # 可用区给满（后续由 _Splash 轮询校正），首帧即全窗盖板
    if w.settings.get("font_dirs"):
        splash = _Splash(w)
        if w.isMaximized():
            scr = (QGuiApplication.screenAt(w.normalGeometry().center())
                   or QGuiApplication.primaryScreen())
            splash.resize(scr.availableGeometry().size())
        else:
            splash.resize(w.size())
        splash.show()
        w.show()
        splash.raise_()
        w.library_page.loadCompleted.connect(splash.finish)
        QTimer.singleShot(30_000, splash.finish)
    else:
        w.show()
    return app.exec()
