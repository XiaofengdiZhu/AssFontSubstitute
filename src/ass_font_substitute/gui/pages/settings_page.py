from __future__ import annotations

import math
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtCore import Qt
from PySide6.QtCore import QUrl
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import ColorDialog
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import HyperlinkCard
from qfluentwidgets import InfoBar
from qfluentwidgets import LineEdit
from qfluentwidgets import MessageBox
from qfluentwidgets import MessageBoxBase
from qfluentwidgets import PrimaryPushSettingCard
from qfluentwidgets import PushButton
from qfluentwidgets import PushSettingCard
from qfluentwidgets import ScrollArea
from qfluentwidgets import SettingCard
from qfluentwidgets import SettingCardGroup
from qfluentwidgets import Slider
from qfluentwidgets import SubtitleLabel
from qfluentwidgets import TextEdit
from qfluentwidgets import qconfig
from qfluentwidgets import setFont

from ass_font_substitute.gui.paths import default_cache_path
from ass_font_substitute.gui.settings_store import COV_BOUNDS
from ass_font_substitute.gui.settings_store import SIM_BOUNDS
from ass_font_substitute.gui.settings_store import SIZE_BOUNDS
from ass_font_substitute.gui.widgets.range_slider import RangeSlider
from ass_font_substitute.gui.widgets.spin import CompactSpinBox

REPO_URL = "https://github.com/XiaofengdiZhu/AssFontSubstitute"

_SPACING = 24          # 控件与卡片右缘间距（Ghost Downloader 同款）

_LANG_LABELS = {
    "zh-Hans": ("简体中文", "繁体中文"),
    "zh-Hant": ("简体中文", "正體中文"),
}
_THEME_VALUES = ("auto", "light", "dark")
_BG_VALUES = ("mica", "mica_alt", "acrylic", "aero", "none")


def _app_version() -> str:
    try:
        return _pkg_version("ass-font-substitute")
    except PackageNotFoundError:
        return "dev"


def _pct(v: float) -> int:
    """0.805→81 半进位百分比。round() 是银行家舍入（90.5→90），存量
    阈值恰在 .5 边界时滑杆/数值框初值会差 1。"""
    return math.floor(v * 100 + 0.5)


def _sample_brief(text: str) -> str:
    """对比文字卡片只展示前段，全文进编辑对话框看。"""
    return text if len(text) <= 28 else text[:28] + "…"


class _WheelFreeCard(SettingCard):
    """滚轮事件拦截：卡片内控件不响应滚轮，避免滚动设置页时误改数值。
    只拦经 installEventFilter 挂进来的控件——每个交互控件都要显式挂。"""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and watched in (
                getattr(self, "slider", None), getattr(self, "spin", None),
                getattr(self, "spin_max", None)):
            return True
        return super().eventFilter(watched, event)


class _SliderSpinCard(_WheelFreeCard):
    """滑杆 + compact 数值框卡：双向联动。拖拽滑杆只刷显示、松手提交
    （每个 move 事件落盘=每秒百余次全量 JSON 写，浪费）；数值框直接改
    值即提交。"""

    def __init__(self, icon, title, content, lo, hi, step, value, on_commit,
                 suffix="", parent=None):
        super().__init__(icon, title, content, parent)
        self._on_commit = on_commit
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(lo, hi)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step)
        self.slider.setValue(value)
        self.slider.setFixedWidth(180)
        self.spin = CompactSpinBox(self)
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setValue(value)
        if suffix:
            self.spin.setSuffix(suffix)
        # 刚好容下最宽值：三位数模板 + 右缘钮 + 边距余量
        self.spin.setFixedWidth(
            self.spin.fontMetrics().horizontalAdvance(f"000{suffix}") + 38)
        self.hBoxLayout.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_SPACING)
        self.slider.installEventFilter(self)
        self.spin.installEventFilter(self)
        # 双向联动：数值框改值搬滑杆并即时提交；滑杆拖拽只回刷数值框
        # （blockSignals 防回环），松手统一提交。键盘/程序化改滑杆不发
        # released，按非拖拽态即时提交
        self._sliding = False
        self.spin.valueChanged.connect(self._on_spin_value)
        self.slider.valueChanged.connect(self._on_value)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)

    def _on_slider_pressed(self):
        self._sliding = True

    def _on_slider_released(self):
        self._sliding = False
        self._on_commit(self.slider.value())

    def _on_spin_value(self, v):
        # 框改值只搬滑杆：valueChanged → _on_value 按非拖拽态即时提交
        # （提交单点在 _on_value，这里再提会双写）
        self.slider.setValue(v)

    def _on_value(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        if not self._sliding:
            self._on_commit(v)           # 键盘/程序化改滑杆即时提交


class _SampleEditBox(MessageBoxBase):
    """编辑对比文字：fluent 风格弹窗（原生 QDialog 与页面风格割裂）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("编辑相似度对比文字")
        warn = CaptionLabel("保存后已有的相似度结果将全部失效，"
                            "下次使用时自动按新文字重新计算。")
        warn.setWordWrap(True)
        self.edit = TextEdit()
        self.edit.setPlainText(text)
        self.edit.setFixedHeight(220)
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(warn)
        self.viewLayout.addWidget(self.edit)
        self.yesButton.setText("保存")
        self.cancelButton.setText("取消")
        self.widget.setFixedWidth(min(720, max(self.width() - 48, 320)))
        # 窄窗（MaskDialogBase 覆盖页宽）下固定最小宽会横向溢出，
        # 与 FontPickerDialog/_FailuresDialog 同款钳制；高度同款封顶，
        # 免得标题+警告+编辑区把按钮顶出窗口
        win_h = self.window().height()
        if win_h > 420:
            self.widget.setMaximumHeight(win_h - 32)
        self.edit.textChanged.connect(
            lambda: self.yesButton.setEnabled(bool(self.edit.toPlainText().strip())))
        self.yesButton.setEnabled(bool(text.strip()))


def _theme_aware_title(group) -> None:
    """qfw SettingCardGroup 标题是裸 QLabel：黑字死调色板，深色主题不跟随。
    原位换成 BodyLabel（FluentLabelBase，LABEL qss 黑/白按主题）+同款 20px
    Normal（qfw setFont 只改字号字重不变）。"""
    old = group.titleLabel
    title = BodyLabel(old.text(), old.parentWidget())
    setFont(title, 20)
    group.titleLabel = title
    group.vBoxLayout.replaceWidget(old, title)
    old.deleteLater()


class SettingsPage(ScrollArea):
    saveModeChanged = Signal(bool)       # 覆盖原文件？（处理字幕页同步）
    suffixChanged = Signal(str)          # 另存后缀变更（处理字幕页同步）

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("settingsPage")

        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.enableTransparentBackground()
        v = QVBoxLayout(self.view)
        v.setContentsMargins(36, 24, 36, 36)
        v.setSpacing(32)                 # 组标题与上组卡片拉开距离
        v.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ── 常规 ─────────────────────────────────────────────
        g0 = SettingCardGroup("常规", self.view)
        # 繁体模式下「繁体中文」选项以繁方惯称「正體中文」显示
        lang = settings.get("language")
        self.lang_card = _WheelFreeCard(
            FIF.LANGUAGE, "语言", "设置界面语言（国际化尚未完成，暂只保存选择）", self)
        self.lang_combo = ComboBox(self.lang_card)
        self.lang_combo.addItems(list(_LANG_LABELS[lang]))
        self.lang_combo.setCurrentIndex(0 if lang == "zh-Hans" else 1)
        self.lang_combo.setFixedWidth(150)
        self.lang_combo.installEventFilter(self.lang_card)
        self.lang_combo.currentIndexChanged.connect(self._on_language)
        self.lang_card.hBoxLayout.addWidget(self.lang_combo, 0,
                                            Qt.AlignmentFlag.AlignRight)
        self.lang_card.hBoxLayout.addSpacing(_SPACING)
        self.theme_card = _WheelFreeCard(
            FIF.BRUSH, "主题", "更改应用程序的外观", self)
        self.theme_combo = ComboBox(self.theme_card)
        self.theme_combo.addItems(["跟随系统设置", "浅色", "深色"])
        self.theme_combo.setCurrentIndex(_THEME_VALUES.index(settings.get("theme")))
        self.theme_combo.setFixedWidth(150)
        self.theme_combo.installEventFilter(self.theme_card)
        self.theme_combo.currentIndexChanged.connect(self._on_theme)
        self.theme_card.hBoxLayout.addWidget(self.theme_combo, 0,
                                             Qt.AlignmentFlag.AlignRight)
        self.theme_card.hBoxLayout.addSpacing(_SPACING)
        self.bg_card = _WheelFreeCard(
            FIF.TRANSPARENT, "窗口背景", "设置窗口背景的透明材质"
            "（Win10 不支持 Mica 类材质，自动退化为实色）", self)
        self.bg_combo = ComboBox(self.bg_card)
        self.bg_combo.addItems(["Mica", "MicaAlt", "Acrylic", "Aero", "无"])
        self.bg_combo.setCurrentIndex(
            _BG_VALUES.index(settings.get("bg_effect")))
        self.bg_combo.setFixedWidth(150)
        self.bg_combo.installEventFilter(self.bg_card)
        self.bg_combo.currentIndexChanged.connect(self._on_bg_effect)
        self.bg_card.hBoxLayout.addWidget(self.bg_combo, 0,
                                          Qt.AlignmentFlag.AlignRight)
        self.bg_card.hBoxLayout.addSpacing(_SPACING)
        self.accent_card = _WheelFreeCard(
            FIF.PALETTE, "强调色",
            "界面控件的强调色；跟随系统取 Windows 主题色，或自定义", self)
        self.accent_combo = ComboBox(self.accent_card)
        self.accent_combo.addItems(["跟随系统", "自定义…"])
        self.accent_combo.setCurrentIndex(
            0 if not settings.get("theme_color") else 1)
        self._sync_accent_content()
        self.accent_combo.setFixedWidth(150)
        self.accent_combo.installEventFilter(self.accent_card)
        # activated 而非 currentIndexChanged：已停在「自定义…」再选一次
        # 也要弹取色器（同项重选 activated 照发，index 变化信号不发）；
        # 程序化 setCurrentIndex 不触发 activated，取消回退无回环
        self.accent_combo.activated.connect(self._on_accent)
        self.accent_card.hBoxLayout.addWidget(self.accent_combo, 0,
                                              Qt.AlignmentFlag.AlignRight)
        self.accent_card.hBoxLayout.addSpacing(_SPACING)
        g0.addSettingCard(self.lang_card)
        g0.addSettingCard(self.theme_card)
        g0.addSettingCard(self.accent_card)
        g0.addSettingCard(self.bg_card)
        v.addWidget(g0)

        # ── 相似候选（处理字幕页初始值）──────────────────────────
        g1 = SettingCardGroup("相似候选", self.view)
        self.cov_card = _SliderSpinCard(
            FIF.FILTER, "覆盖率门槛",
            "在库字形对字幕用字的覆盖达到该值即直接采用，否则进入相似度搜索",
            _pct(COV_BOUNDS[0]), _pct(COV_BOUNDS[1]), 1,
            _pct(settings.get("cov_threshold")),
            lambda x: self.settings.set("cov_threshold", x / 100),
            suffix="%")
        self.sim_card = _SliderSpinCard(
            FIF.SPEED_HIGH, "相似率门槛",
            "候选字体与原字体的相似率低于该值将不列出",
            _pct(SIM_BOUNDS[0]), _pct(SIM_BOUNDS[1]), 5,
            _pct(settings.get("min_sim")),
            lambda x: self.settings.set("min_sim", x / 100),
            suffix="%")
        self.size_card = self._make_size_card()
        # 2026-10 滑杆化时上限 50 收到 10：存量 11–50 显示钳 10，存储
        # 保留旧值（构造不提交约定）
        self.pause_card = _SliderSpinCard(
            FIF.PAUSE, "相似字体暂停数量",
            "每找到该数量的相似字体就暂停等待确认，之后可继续搜索",
            1, 10, 1, int(settings.get("n_pause")),
            lambda x: self.settings.set("n_pause", x))
        self.threads_card = _SliderSpinCard(
            FIF.IOT, "最大使用线程数",
            "建索引并行解析的上限（并行解析尚未启用，当前仅存储）",
            1, max(1, os.cpu_count() or 4), 1,
            int(settings.get("max_threads")),
            lambda x: self.settings.set("max_threads", x))
        g1.addSettingCard(self.cov_card)
        g1.addSettingCard(self.sim_card)
        g1.addSettingCard(self.size_card)
        g1.addSettingCard(self.pause_card)
        g1.addSettingCard(self.threads_card)
        self._add_sim_pending_hint(g1)
        v.addWidget(g1)

        # ── 输出 ─────────────────────────────────────────────
        g2 = SettingCardGroup("输出", self.view)
        self.save_mode_card = _WheelFreeCard(
            FIF.SAVE, "保存方式", "替换结果写回方式（与处理字幕页同步）", self)
        self.save_mode_combo = ComboBox(self.save_mode_card)
        self.save_mode_combo.addItems(["另存为新文件", "覆盖原文件"])
        self.save_mode_combo.setFixedWidth(150)
        self.save_mode_combo.setCurrentIndex(
            1 if settings.get("overwrite_default") else 0)
        self.save_mode_combo.installEventFilter(self.save_mode_card)
        self.save_mode_combo.currentIndexChanged.connect(self._on_save_mode)
        self.save_mode_card.hBoxLayout.addWidget(
            self.save_mode_combo, 0, Qt.AlignmentFlag.AlignRight)
        self.save_mode_card.hBoxLayout.addSpacing(_SPACING)
        self.suffix_card = _WheelFreeCard(
            FIF.SAVE, "另存后缀", "替换结果另存的文件名后缀，须以点开头",
            self)
        # qfluentwidgets 1.11.3：LineEdit 只接受 parent，文字经 setText
        self.suffix_edit = LineEdit(self.suffix_card)
        self.suffix_edit.setText(settings.get("save_suffix"))
        self.suffix_edit.setMinimumWidth(160)
        self.suffix_card.hBoxLayout.addWidget(self.suffix_edit, 0,
                                              Qt.AlignmentFlag.AlignRight)
        self.suffix_card.hBoxLayout.addSpacing(16)
        self.suffix_edit.editingFinished.connect(self._save_suffix)
        self.sample_card = PushSettingCard(
            "编辑…", FIF.FONT, "对比文字", _sample_brief(settings.get("sample_text")), self)
        self.sample_card.clicked.connect(self._edit_sample)
        g2.addSettingCard(self.save_mode_card)
        g2.addSettingCard(self.suffix_card)
        g2.addSettingCard(self.sample_card)
        v.addWidget(g2)

        # ── 相似度缓存 ────────────────────────────────────────
        g3 = SettingCardGroup("相似度缓存", self.view)
        self._cache_path = str(default_cache_path())   # cachestat 回执更新
        self.cache_card = SettingCard(FIF.ALBUM, "缓存状态", "…", self)
        # 缓存位置不可再选（绿色软件定死 <运行目录>/Data/cache.db，2026-10-29）：
        # 卡右只剩打开所在位置，看文件/手动备份用
        self.btn_open_cache = PushButton(FIF.FOLDER, "打开所在位置",
                                         self.cache_card)
        self.btn_open_cache.clicked.connect(self._open_cache_dir)
        self.cache_card.hBoxLayout.addWidget(self.btn_open_cache, 0,
                                             Qt.AlignmentFlag.AlignRight)
        self.cache_card.hBoxLayout.addSpacing(_SPACING)
        self.clear_card = PrimaryPushSettingCard(
            "清除", FIF.DELETE, "清除缓存",
            "删除全部字形预览与相似度记录，下次搜索重新计算", self)
        self.clear_card.clicked.connect(self._clear_cache)
        g3.addSettingCard(self.cache_card)
        g3.addSettingCard(self.clear_card)
        v.addWidget(g3)

        # ── 关于 ─────────────────────────────────────────────
        g4 = SettingCardGroup("关于", self.view)
        ver_card = SettingCard(FIF.INFO, "版本", _app_version(), self)
        repo_card = HyperlinkCard(REPO_URL, "打开", FIF.GITHUB, "项目仓库",
                                  "获取更新 / 反馈问题（GitHub）", self)
        g4.addSettingCard(ver_card)
        g4.addSettingCard(repo_card)
        v.addWidget(g4)

        # 组标题换主题跟随色（replaceWidget 递归进子布局，_add_sim_pending_hint
        # 先搬过 titleLabel 也照样找得到、换得动）
        for g in (g0, g1, g2, g3, g4):
            _theme_aware_title(g)

        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self._refresh_cache_stats()

    def _make_size_card(self):
        card = _WheelFreeCard(FIF.TILES, "相对大小区间",
                              "候选字形相对原字体的大小允许范围", self)
        self.range_slider = RangeSlider(
            _pct(SIZE_BOUNDS[0]), _pct(SIZE_BOUNDS[1]),
            _pct(self.settings.get("size_min")),
            _pct(self.settings.get("size_max")), card)
        self.range_slider.setFixedSize(180, 22)
        # 数值框取钳后值（存量越界时与滑块一致，构造不落盘）
        lo, hi = self.range_slider.values()
        lo_pct, hi_pct = _pct(SIZE_BOUNDS[0]), _pct(SIZE_BOUNDS[1])
        self.spin = CompactSpinBox(card)          # 下限
        self.spin_max = CompactSpinBox(card)      # 上限
        for s, v in ((self.spin, lo), (self.spin_max, hi)):
            s.setRange(lo_pct, hi_pct)
            s.setValue(v)
            s.setSuffix("%")
            s.setFixedWidth(
                s.fontMetrics().horizontalAdvance(f"{hi_pct}%") + 38)
            s.installEventFilter(card)            # _WheelFreeCard 滚轮拦截
        # _WheelFreeCard 名单按 card 属性匹配；框属性挂在 page（self.spin），
        # 补挂到 card 拦截才生效
        card.spin, card.spin_max = self.spin, self.spin_max

        def _commit():
            low_, hi_ = self.range_slider.values()
            self.settings.set("size_min", low_ / 100)
            self.settings.set("size_max", hi_ / 100)

        def _on_slider_change(low_, hi_):
            # 拖拽中只刷框：每个 move 事件落盘=每秒百余次全量 JSON 写，浪费
            for s, v in ((self.spin, low_), (self.spin_max, hi_)):
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)

        def _on_spin():
            # 框改值搬滑杆（setValues 统一钳界排序），即时落盘
            self.range_slider.setValues(self.spin.value(),
                                        self.spin_max.value())
            _commit()

        self.range_slider.valueChanged.connect(_on_slider_change)
        self.range_slider.sliderReleased.connect(_commit)
        self.spin.valueChanged.connect(_on_spin)
        self.spin_max.valueChanged.connect(_on_spin)
        card.hBoxLayout.addWidget(self.range_slider, 0,
                                  Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(5)
        card.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        dash = BodyLabel("–", card)
        card.hBoxLayout.addSpacing(4)
        card.hBoxLayout.addWidget(dash, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(4)
        card.hBoxLayout.addWidget(self.spin_max, 0,
                                  Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(_SPACING)
        return card

    def _add_sim_pending_hint(self, group):
        """相似候选标题行右端红字提示：在跑的搜索器持有建器时的门槛快照，
        此刻改动要等下次寻找相似才生效（提示由搜索活跃状态驱动显隐）。"""
        # 依赖 qfw 1.11.3 SettingCardGroup 内部布局：vBoxLayout 首元素是
        # titleLabel，随后 spacing + cardLayout——升级 qfw 改了顺序会静默错位
        group.vBoxLayout.removeWidget(group.titleLabel)
        head = QHBoxLayout()
        head.addWidget(group.titleLabel)
        head.addStretch(1)
        self.sim_pending_hint = CaptionLabel("下次寻找相似时生效")
        self.sim_pending_hint.setStyleSheet("color: #c42b1c; font-weight: 600;")
        self.sim_pending_hint.hide()
        head.addWidget(self.sim_pending_hint)
        group.vBoxLayout.insertLayout(0, head)
        self._search_active = False
        # 滑杆与数值框都挂：框是即时落盘路径，搜索进行中改了同样待生效
        for w in (self.cov_card.slider, self.cov_card.spin,
                  self.sim_card.slider, self.sim_card.spin,
                  self.range_slider, self.pause_card.slider,
                  self.pause_card.spin, self.spin, self.spin_max):
            w.valueChanged.connect(self._mark_sim_pending)

    def bind_search_activity(self, subs_page):
        """AFSWindow 注入处理字幕页：跟踪在跑相似搜索，驱动提示显隐。"""
        subs_page.searchActiveChanged.connect(self._on_search_active)

    def _on_search_active(self, active: bool):
        self._search_active = active
        # 新搜索开始=建器已带最新设置；搜索全部结束=无待生效项
        self.sim_pending_hint.hide()
        if not active:
            # 一轮寻找写完相似度记录：计数卡现刷（曾只 ctor+cacheclear
            # 触发，寻找一堆后计数不动要重启才更新，2026-10-30 报告）
            self._refresh_cache_stats()

    def showEvent(self, e):
        # 每次进设置页现读缓存计数：手选评分等其它写库路径不走
        # searchActiveChanged，靠翻页兜底
        super().showEvent(e)
        self._refresh_cache_stats()

    def _mark_sim_pending(self):
        if self._search_active:
            self.sim_pending_hint.show()

    # ---- 常规（语言/主题/窗口背景）----
    def _on_language(self, index):
        lang = "zh-Hant" if index == 1 else "zh-Hans"
        self.settings.set("language", lang)
        # 繁体模式下选项名换成简方/繁方惯称：重灌选项文案（索引不变）
        labels = list(_LANG_LABELS[lang])
        if labels != [self.lang_combo.itemText(i)
                      for i in range(self.lang_combo.count())]:
            self.lang_combo.blockSignals(True)
            self.lang_combo.clear()
            self.lang_combo.addItems(labels)
            self.lang_combo.setCurrentIndex(index)
            self.lang_combo.blockSignals(False)

    def _on_theme(self, index):
        self.settings.set("theme", _THEME_VALUES[index])   # 窗口经 valueChanged 应用

    def _on_bg_effect(self, index):
        self.settings.set("bg_effect", _BG_VALUES[index])

    # ---- 强调色 ----
    def _sync_accent_content(self):
        stored = self.settings.get("theme_color")
        if stored:
            self.accent_card.setContent(stored)
        else:
            # 跟随系统：展示当前生效色（窗口已解析的系统色/qfw 出厂色）
            self.accent_card.setContent(
                f"跟随系统（当前 {QColor(qconfig.themeColor.value).name()}）")

    def _on_accent(self, index):
        if index == 0:
            self.settings.set("theme_color", "")
            self._sync_accent_content()
            return
        # 自定义：fluent 取色器。accept 后直接从 dlg.color 落 store——
        # 不依赖 colorChanged（确认与当前色相同时上游不发信号，「钉住
        # 当前色」意图会丢）；取消（reject）不写
        dlg = ColorDialog(QColor(qconfig.themeColor.value), "选择强调色",
                          self.window())
        accepted = dlg.exec()
        chosen = dlg.color.name()
        dlg.deleteLater()                 # HuePanel 大 pixmap 别滞留到关窗
        if accepted:
            self.settings.set("theme_color", chosen)
        self._sync_accent_content()       # 卡面色值跟上（审查 P1）
        if not self.settings.get("theme_color"):
            # 取消而原本是跟随系统：选项回「跟随系统」
            self.accent_combo.blockSignals(True)
            self.accent_combo.setCurrentIndex(0)
            self.accent_combo.blockSignals(False)

    # ---- 保存方式/后缀（与处理字幕页双向同步）----
    def _on_save_mode(self, index):
        overwrite = index == 1
        self.settings.set("overwrite_default", overwrite)
        self.saveModeChanged.emit(overwrite)

    def set_save_mode(self, overwrite: bool):
        """处理字幕页改了保存方式：只搬 UI（源头已写设置，不再回写）。"""
        self.save_mode_combo.blockSignals(True)
        self.save_mode_combo.setCurrentIndex(1 if overwrite else 0)
        self.save_mode_combo.blockSignals(False)

    def set_save_suffix(self, text: str):
        """处理字幕页改了后缀：setText 不触发 editingFinished，无回环。"""
        self.suffix_edit.setText(text)

    def _save_suffix(self):
        text = self.suffix_edit.text().strip()
        if text == self.settings.get("save_suffix"):
            return   # 未变不落盘（对齐 subs_page：focus-out 不重写全量设置）
        try:
            self.settings.set("save_suffix", text)
        except ValueError:
            InfoBar.error("后缀无效", "必须以点开头且非空，如 .substituted.ass",
                          parent=self)
            self.suffix_edit.setText(self.settings.get("save_suffix"))
            return
        self.suffixChanged.emit(self.settings.get("save_suffix"))

    def _edit_sample(self):
        dlg = _SampleEditBox(self.settings.get("sample_text"), self)
        if dlg.exec():
            try:
                self.settings.set("sample_text", dlg.edit.toPlainText())
                self.sample_card.setContent(_sample_brief(dlg.edit.toPlainText()))
            except ValueError:
                InfoBar.error("对比文字不能为空", "", parent=self)

    def _refresh_cache_stats(self):
        self.worker.submit("cachestat", _stat_job)

    def _on_result(self, tag, payload):
        if tag == "cachestat":
            mb = payload["render_bytes"] / 1024 / 1024
            self.cache_card.setContent(
                f"字形预览 {mb:.1f} MB · 相似度记录 {payload['pair_count']} 条")
            self._cache_path = payload["path"]
        elif tag == "cacheclear":
            InfoBar.success("缓存已清除", "", parent=self)
            self._refresh_cache_stats()

    def _open_cache_dir(self):
        # 打开缓存所在目录（Data）：未建库时目录可能尚不存在，先补建再开
        d = Path(self._cache_path).parent
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def _on_failed(self, tag, err):
        # 缓存类任务失败浮泡（初始化/统计/清除）；其它页签的任务由
        # 各自页面的 failed 处理器负责，这里不重复弹
        if tag in ("worker:init", "cachestat", "cacheclear"):
            InfoBar.error("缓存操作失败", f"{tag}: {err}", parent=self)

    def _clear_cache(self):
        w = MessageBox("清除相似度缓存",
                       "确认清除全部字体的相似度记录和对比文字渲染缓存？"
                       "下次寻找相似字体时会再次产生", self)
        w.yesButton.setText("确认")
        w.cancelButton.setText("取消")
        if w.exec():
            self.worker.submit("cacheclear", _clear_job)


def _stat_job(ctx):
    from ass_font_substitute.gui.jobs import cache_stats
    return cache_stats(ctx)


def _clear_job(ctx):
    from ass_font_substitute.gui.jobs import cache_clear
    cache_clear(ctx)
