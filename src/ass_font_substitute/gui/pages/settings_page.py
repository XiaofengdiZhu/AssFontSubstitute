from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, Dialog,
                            HyperlinkCard, InfoBar, LineEdit,
                            MessageBoxBase, PrimaryPushSettingCard,
                            PushSettingCard, ScrollArea, SettingCard,
                            SettingCardGroup, Slider, SubtitleLabel, TextEdit,
                            ToolButton)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.settings_store import (COV_BOUNDS, SIM_BOUNDS,
                                                    SIZE_BOUNDS)
from ass_font_substitute.gui.widgets.spin import SpinBox

REPO_URL = "https://github.com/XiaofengdiZhu/AssFontSubstitute"

_SPACING = 24          # 控件与卡片右缘间距（Ghost Downloader 同款）


def _app_version() -> str:
    try:
        return _pkg_version("ass-font-substitute")
    except PackageNotFoundError:
        return "dev"


def _sample_brief(text: str) -> str:
    """对比文字卡片只展示前段，全文进编辑对话框看。"""
    return text if len(text) <= 28 else text[:28] + "…"


class _WheelFreeCard(SettingCard):
    """滚轮事件拦截：卡片内控件不响应滚轮，避免滚动设置页时误改数值。"""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and watched in (
                getattr(self, "slider", None), getattr(self, "spin", None),
                getattr(self, "spin_max", None)):
            return True
        return super().eventFilter(watched, event)


class _PercentCard(_WheelFreeCard):
    """百分比门槛卡：滑杆 + 数值标签，值以百分数提交（/100 存回设置）。"""

    def __init__(self, icon, title, content, lo_pct, hi_pct, step, value,
                 on_commit, parent=None):
        super().__init__(icon, title, content, parent)
        self._on_commit = on_commit
        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(lo_pct, hi_pct)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step)
        self.slider.setValue(value)
        self.slider.setFixedWidth(180)
        self.valueLabel = BodyLabel(self)
        self.hBoxLayout.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(10)
        self.hBoxLayout.addWidget(self.valueLabel, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_SPACING)
        self.slider.installEventFilter(self)
        self.slider.valueChanged.connect(self._on_value)
        self._on_value(value)

    def _on_value(self, v):
        self.valueLabel.setText(f"{v}%")
        self._on_commit(v)


class _SpinBoxCard(_WheelFreeCard):
    """整数 SpinBox 卡。"""

    def __init__(self, icon, title, content, lo, hi, value, on_commit,
                 suffix="", step=1, parent=None):
        super().__init__(icon, title, content, parent)
        self._on_commit = on_commit
        self.spin = SpinBox(self)
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setValue(value)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(140)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(_SPACING)
        self.spin.installEventFilter(self)
        self.spin.valueChanged.connect(on_commit)


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
        self.widget.setMinimumWidth(560)
        self.edit.textChanged.connect(
            lambda: self.yesButton.setEnabled(bool(self.edit.toPlainText().strip())))
        self.yesButton.setEnabled(bool(text.strip()))


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

        # ── 相似候选（处理字幕页初始值）──────────────────────────
        g1 = SettingCardGroup("相似候选", self.view)
        self.cov_card = _PercentCard(
            FIF.FILTER, "覆盖率门槛",
            "在库字形对字幕用字的覆盖达到该值即直接采用，否则进入相似度搜索",
            round(COV_BOUNDS[0] * 100), round(COV_BOUNDS[1] * 100), 1,
            round(settings.get("cov_threshold") * 100),
            lambda x: self.settings.set("cov_threshold", x / 100))
        self.sim_card = _PercentCard(
            FIF.SPEED_HIGH, "相似率门槛",
            "候选字体与原字体的相似率低于该值将不列出",
            round(SIM_BOUNDS[0] * 100), round(SIM_BOUNDS[1] * 100), 5,
            round(settings.get("min_sim") * 100),
            lambda x: self.settings.set("min_sim", x / 100))
        self.size_card = self._make_size_card()
        self.pause_card = _SpinBoxCard(
            FIF.PAUSE, "相似字体暂停数量",
            "每找到该数量的相似字体就暂停等待确认，之后可继续搜索",
            1, 50, int(settings.get("n_pause")),
            lambda x: self.settings.set("n_pause", x))
        self.threads_card = _SpinBoxCard(
            FIF.IOT, "最大使用线程数",
            "建索引并行解析的上限（并行解析尚未启用，当前仅存储）",
            1, max(1, os.cpu_count() or 4), int(settings.get("max_threads")),
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
        self.save_mode_card = SettingCard(
            FIF.SAVE, "保存方式", "替换结果写回方式（与处理字幕页同步）", self)
        self.save_mode_combo = ComboBox(self.save_mode_card)
        self.save_mode_combo.addItems(["另存为新文件", "覆盖原文件"])
        self.save_mode_combo.setFixedWidth(150)
        self.save_mode_combo.setCurrentIndex(
            1 if settings.get("overwrite_default") else 0)
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
        self.cache_card = SettingCard(FIF.ALBUM, "缓存状态", "…", self)
        self.path_card = SettingCard(FIF.FOLDER, "缓存位置",
                                     "字形预览与相似度记录存放位置", self)
        self.path_combo = ComboBox(self.path_card)
        self.path_combo.addItem("…")
        self.path_combo.setMinimumWidth(240)
        self.path_pick = ToolButton(FIF.FOLDER, self.path_card)
        self.path_pick.clicked.connect(self._move_cache)
        self.path_card.hBoxLayout.addWidget(self.path_combo, 0,
                                            Qt.AlignmentFlag.AlignRight)
        self.path_card.hBoxLayout.addSpacing(8)
        self.path_card.hBoxLayout.addWidget(self.path_pick, 0,
                                            Qt.AlignmentFlag.AlignRight)
        self.path_card.hBoxLayout.addSpacing(_SPACING)
        self.clear_card = PrimaryPushSettingCard(
            "清除", FIF.DELETE, "清除缓存",
            "删除全部字形预览与相似度记录，下次搜索重新计算", self)
        self.clear_card.clicked.connect(self._clear_cache)
        g3.addSettingCard(self.cache_card)
        g3.addSettingCard(self.path_card)
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

        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self._refresh_cache_stats()

    def _make_size_card(self):
        card = _WheelFreeCard(FIF.TILES, "相对大小区间",
                              "候选字形相对原字体的大小允许范围", self)
        lo = round(self.settings.get("size_min") * 100)
        hi = round(self.settings.get("size_max") * 100)
        self.spin_smin = SpinBox(card)
        self.spin_smax = SpinBox(card)
        for s in (self.spin_smin, self.spin_smax):
            s.setRange(round(SIZE_BOUNDS[0] * 100), round(SIZE_BOUNDS[1] * 100))
            s.setSingleStep(1)
            s.setSuffix("%")
            s.setFixedWidth(132)
            s.installEventFilter(card)
        self.spin_smin.setValue(lo)
        self.spin_smax.setValue(hi)
        self.spin_smin.setMaximum(hi)
        self.spin_smax.setMinimum(lo)
        self.spin_smin.valueChanged.connect(
            lambda x: (self.settings.set("size_min", x / 100),
                       self.spin_smax.setMinimum(x)))
        self.spin_smax.valueChanged.connect(
            lambda x: (self.settings.set("size_max", x / 100),
                       self.spin_smin.setMaximum(x)))
        card.hBoxLayout.addWidget(self.spin_smin, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(6)
        card.hBoxLayout.addWidget(BodyLabel("–", card), 0,
                                  Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(6)
        card.hBoxLayout.addWidget(self.spin_smax, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(_SPACING)
        return card

    def _add_sim_pending_hint(self, group):
        """相似候选标题行右端红字提示：在跑的搜索器持有建器时的门槛快照，
        此刻改动要等下次寻找相似才生效（提示由搜索活跃状态驱动显隐）。"""
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
        for w in (self.cov_card.slider, self.sim_card.slider,
                  self.spin_smin, self.spin_smax, self.pause_card.spin):
            w.valueChanged.connect(self._mark_sim_pending)

    def bind_search_activity(self, subs_page):
        """AFSWindow 注入处理字幕页：跟踪在跑相似搜索，驱动提示显隐。"""
        subs_page.searchActiveChanged.connect(self._on_search_active)

    def _on_search_active(self, active: bool):
        self._search_active = active
        # 新搜索开始=建器已带最新设置；搜索全部结束=无待生效项
        self.sim_pending_hint.hide()

    def _mark_sim_pending(self):
        if self._search_active:
            self.sim_pending_hint.show()

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
        try:
            self.settings.set("save_suffix", self.suffix_edit.text().strip())
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
            self._sync_path(payload["path"])
        elif tag == "cacheclear":
            InfoBar.success("缓存已清除", "", parent=self)
            self._refresh_cache_stats()
        elif tag == "cachemove":
            InfoBar.success("缓存位置已更改", payload, parent=self)
            self._refresh_cache_stats()

    def _sync_path(self, path: str):
        self.path_combo.blockSignals(True)
        if self.path_combo.count():
            self.path_combo.clear()
        self.path_combo.addItem(path)
        self.path_combo.blockSignals(False)

    def _on_failed(self, tag, err):
        # 缓存类任务失败浮泡（初始化/统计/清除/迁移）；其它页签的任务由
        # 各自页面的 failed 处理器负责，这里不重复弹
        if tag in ("worker:init", "cachestat", "cacheclear", "cachemove"):
            InfoBar.error("缓存操作失败", f"{tag}: {err}", parent=self)

    def _clear_cache(self):
        w = Dialog("清除相似度缓存", "将删除全部字形预览与相似度记录，下次搜索重新计算。确定？",
                   self)
        if w.exec():
            self.worker.submit("cacheclear", _clear_job)

    def _move_cache(self):
        d = QFileDialog.getExistingDirectory(self, "选择新缓存位置")
        if not d:
            return
        new = Path(d) / "cache.db"
        migrate = True
        if new.exists():
            w = Dialog("目标已有缓存文件",
                       "迁移将覆盖它；选「否」则丢弃现有缓存从空开始。", self)
            migrate = bool(w.exec())
        self.worker.submit("cachemove", _move_job, str(new), migrate)


def _stat_job(ctx):
    from ass_font_substitute.gui.jobs import cache_stats
    return cache_stats(ctx)


def _clear_job(ctx):
    from ass_font_substitute.gui.jobs import cache_clear
    cache_clear(ctx)


def _move_job(ctx, new_path, migrate):
    from ass_font_substitute.gui.jobs import cache_move
    return cache_move(ctx, new_path, migrate)
