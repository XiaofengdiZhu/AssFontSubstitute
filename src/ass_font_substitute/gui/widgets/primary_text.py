"""primary 系按钮文字色随强调色亮度自适应。

根因（qfw 上游缺陷）：BUTTON.qss 把 PrimaryPushButton/PrimaryToolButton
文字色写死 white，背景色却随 setThemeColor 变——用户选浅色强调色后
白字浅底不可读。

修法：按当前主题色 luma 阈值换深色文字，经 setCustomStyleSheet 追加到
控件 qss（同特异性后到胜出；:hover/:pressed/:disabled 是伪类更高特异性，
须逐个覆盖）。深色文字配浅色主题的 hover/pressed 背景仍是浅色系
（ThemeColor.LIGHT_1/3 只是明度微调），无需分支。

tune_all 在每次强调色应用后调用（app._apply_theme_color）；弹窗按钮
（MessageBox/ColorDialog 的 yesButton 等懒创建）由 dialog_ani 的
showEvent 钩子在显示前补调 tune。
"""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QWidget
from qfluentwidgets import PrimaryPushButton
from qfluentwidgets import PrimaryToolButton
from qfluentwidgets import Theme
from qfluentwidgets.common.icon import FluentIconBase
from qfluentwidgets.common.icon import drawIcon
from qfluentwidgets.common.style_sheet import isDarkTheme
from qfluentwidgets.common.style_sheet import setCustomStyleSheet
from qfluentwidgets.common.style_sheet import themeColor

# 强调色 luma（Rec.601）高于此值换深色文字/图标；qfw 出厂青 #009faa
# luma≈0.44。与斜体钮 _on_ink 的 150/255≈0.588 对齐——阈值曾分叉
# （0.62 vs 150），强调色 luma 落在两值之间时斜体钮已反相而 primary
# 系白字/白图标仍隐形
_LUMA_DARK_TEXT = 150 / 255


def _qss(sel: str, text: str, pressed: str, disabled: str | None = None) -> str:
    # 伪类必须逐选择器展开（f"{sel}:pressed" 对逗号列表会生成裸
    # "PrimaryPushButton"，它匹配一切状态且规则靠后，把正常态文字也
    # 覆盖成 pressed 色——真机暗主题深紫下文字变 36% 白实锤）
    parts = sel.split(", ")
    def _all(suffix: str = "") -> str:
        return ", ".join(s + suffix for s in parts)
    return (f"{_all()}, {_all(':hover')} {{color: {text};}}\n"
            f"{_all(':pressed')} {{color: {pressed};}}\n"
            f"{_all(':disabled')} {{color: {disabled or text};}}\n")


def _selector(widget: QWidget) -> str | None:
    """该按钮在 qfw qss 里的选择器；非 primary 系返回 None。
    PrimaryPushSettingCard 内是普通 QPushButton + objectName=primaryButton
    （setting_card.py:271），qss 同样写死白字，一并覆盖。"""
    if isinstance(widget, (PrimaryPushButton, PrimaryToolButton)):
        return "PrimaryPushButton, PrimaryToolButton"
    if isinstance(widget, QPushButton) and widget.objectName() == "primaryButton":
        return "#primaryButton"
    return None


def tune(widget: QWidget) -> bool:
    """按当前主题色为 primary 按钮追加文字色规则；非 primary 按钮忽略。
    浅色强调色 → 深色文字；正常深度：亮主题 qfw 出厂即白字无需覆盖，
    暗主题 qfw 写死黑字（dark/button.qss `color: black`，赌主题色提亮
    到 v=1 够亮——#2e1773 提亮成 #7e54ff luma 仍 0.455，饱和紫黑字不可
    读），须覆盖回白。setThemeColor 变浅→深来回切换时必须能撤销旧覆盖。"""
    sel = _selector(widget)
    if sel is None:
        return False
    luma = accent_luma()
    if luma >= _LUMA_DARK_TEXT:
        light = _qss(sel, "rgba(0, 0, 0, 0.87)", "rgba(0, 0, 0, 0.55)")
        dark = _qss(sel, "rgba(0, 0, 0, 0.9)", "rgba(0, 0, 0, 0.6)")
    else:
        # 暗主题覆盖 qfw 黑字（pressed/disabled 对齐 qfw 白系透明度）；
        # 亮主题出厂白字，留空。custom light/dark 变体随主题切换自动
        # 各取所需（换肤重应用不丢）
        light = ""
        # disabled 0.43 对齐 qfw dark QSS 出厂禁用文字透明度
        dark = _qss(sel, "white", "rgba(255, 255, 255, 0.786)",
                    "rgba(255, 255, 255, 0.43)")
    if sel == "#primaryButton":
        # 卡内按钮是普通 QPushButton，从没注册过 qfw 样式源：
        # setCustomStyleSheet 只写 property 无人转成样式表，白字规则
        # 从卡片级联下来——按钮自身样式表才能赢过父级级联，直接 set
        # （正常深度也显式给白，浅↔深切换同一通道撤销）
        widget.setStyleSheet(light if light
                             else _qss(sel, "white",
                                       "rgba(255, 255, 255, 0.63)"))
    else:
        setCustomStyleSheet(widget, light, dark)
    return True


def tune_all() -> int:
    """全量已存在控件调一遍（强调色应用后调用）；返回处理数。"""
    n = 0
    for w in QApplication.allWidgets():
        n += tune(w)
    return n


# ---- 图标色 ----
# qfw 把 primary 按钮图标画死反色（浅色主题 → Theme.DARK 白图标，不走
# qss），浅强调色下与文字同样不可读。类级 monkeypatch：浅强调色时改用
# 深色画（FluentIconBase.icon(color=...) 官方出口），正常深度走原逻辑。
_DARK_ICON_COLOR = QColor(0, 0, 0, 222)      # 与浅强调色文字同深度
_patched = False


def accent_luma() -> float:
    c = themeColor()
    return 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()


# patch 前的原生 PrimaryToolButton._drawIcon：checked 反色（icon.icon(反
# 色 Theme)）与禁用态分支都在这层——借道调用的 toggle 钮靠它兜底
_native_primary_draw_icon = None


def _draw_icon(self, icon, painter, rect, state=QIcon.Off):
    if not isinstance(icon, FluentIconBase):
        return _native_draw(self, icon, painter, rect, state)
    if accent_luma() >= _LUMA_DARK_TEXT:
        # 浅强调色：图标换深色（禁用态沿用 qfw 的降不透明度处理）
        if not self.isEnabled():
            painter.setOpacity(0.786 if isDarkTheme() else 0.9)
        return drawIcon(icon.icon(color=_DARK_ICON_COLOR), painter, rect,
                        state)
    if isDarkTheme() and self.isEnabled():
        # 暗主题深饱和强调色：qfw 原生画黑图标（Theme.LIGHT 路径，赌
        # 提亮到 v=1 的底够亮——#7e54ff 上黑图标不可读），显式换白；
        # 亮主题出厂即白图标，走原生
        return drawIcon(icon.icon(Theme.DARK), painter, rect, state)
    return _native_draw(self, icon, painter, rect, state)


def _native_draw(self, icon, painter, rect, state=QIcon.Off):
    # 优先类属性上补丁前的原实现：FluentIconBase 的 checked 反色等
    # qfw 原生语义全保留（self 是 primary 系时走绑定；ToggleToolButton
    # checked 时未绑定调用 PrimaryToolButton._drawIcon(self, ...)，
    # self 不是 primary 系，类属性显式传入 self 仍正确）。
    orig = getattr(type(self), "_draw_icon_original", None)
    if orig is not None:
        return orig(self, icon, painter, rect, state)
    # 借道调用（透明 toggle checked：type(self) 类上无 original）——
    # 退回 patch 前的原生反色实现。不能直接 drawIcon(icon, ...,
    # state)：qfw drawIcon 的 state 只作用于裸 QIcon，FluentIconBase
    # 走 render() 根本不看 state，直传会丢反色画成正色（checked 深
    # 色强调底上黑图标，真机实锤）
    if _native_primary_draw_icon is not None:
        return _native_primary_draw_icon(self, icon, painter, rect,
                                         state)
    return drawIcon(icon, painter, rect, state)


def patch_primary_icon() -> None:
    """全局替换 PrimaryPushButton/PrimaryToolButton 的图标绘制。
    QApplication 创建后调用一次；幂等。"""
    global _patched, _native_primary_draw_icon
    if _patched:
        return
    _patched = True
    for cls in (PrimaryPushButton, PrimaryToolButton):
        # 手改 _patched 重打时 original 若被覆写成 patched 版会无限递归
        if not hasattr(cls, "_draw_icon_original"):
            cls._draw_icon_original = cls._drawIcon
        cls._drawIcon = _draw_icon
    _native_primary_draw_icon = PrimaryToolButton._draw_icon_original
