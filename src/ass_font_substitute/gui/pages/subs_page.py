from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import CheckBox
from qfluentwidgets import ComboBox
from qfluentwidgets import FlowLayout
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import GroupHeaderCardWidget
from qfluentwidgets import InfoBadge
from qfluentwidgets import InfoBar
from qfluentwidgets import LineEdit
from qfluentwidgets import PrimaryPushButton
from qfluentwidgets import PushButton
from qfluentwidgets import ScrollArea
from qfluentwidgets import SegmentedWidget
from qfluentwidgets import SwitchButton
from qfluentwidgets import TransparentToolButton
from qfluentwidgets import setCustomStyleSheet
from qfluentwidgets import setFont

from ass_font_substitute.gui.jobs import apply_to_file
from ass_font_substitute.gui.jobs import classify_one
from ass_font_substitute.gui.jobs import ensure_library
from ass_font_substitute.gui.jobs import make_searcher
from ass_font_substitute.gui.jobs import parse_subtitle
from ass_font_substitute.gui.jobs import pull_chunk
from ass_font_substitute.gui.jobs import score_pick
from ass_font_substitute.gui.lastdir import pick_open_files
from ass_font_substitute.gui.paths import decisions_path
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.subs_vm import CARD_RANK
from ass_font_substitute.gui.subs_vm import SubsVM
from ass_font_substitute.gui.tip import install_tip
from ass_font_substitute.gui.widgets.font_card import FontCard
from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog
from ass_font_substitute.gui.widgets.pill import padded_badge
from ass_font_substitute.gui.widgets.preview_input import PreviewInput
from ass_font_substitute.gui.widgets.wheel_block import WheelBlock

_REMOVE_RED = QColor("#e04343")   # 移除钮图标：亮暗主题下都醒目的危险红


class SubsPage(QWidget):
    """处理字幕页：文件卡列表 → 参数行 → 字体卡渐进填充 → 底部应用条。
    页面骨架 = 外层竖排（内层 ScrollArea 吃余量 + 底栏钉死视口下缘，
    不随内容滚动）。跨线程约定：worker 任务收纯数据、回纯数据；vm 的
    全部变更都发生在 UI 线程的 _on_result / 信号槽里。"""

    searchActiveChanged = Signal(bool)   # 有无进行中的相似搜索（设置页提示用）
    saveModeChanged = Signal(bool)       # 覆盖原文件？（设置页同步）
    suffixChanged = Signal(str)          # 另存后缀变更（设置页同步）

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("subsPage")
        db = decisions_path()
        db.parent.mkdir(parents=True, exist_ok=True)   # 首启无 afs 目录：决策库随用随建
        self.vm = SubsVM(settings, db)
        self._pvm = PreviewVM(settings)            # 字体卡预览条共享 tile LRU
        self._cards_ui: dict[str, FontCard] = {}
        self._cancels: dict[str, dict] = {}        # norm → 取消标志（pull 任务读）
        self._parsing: set[str] = set()            # 批次解析中（收齐才重算）
        self._direction = "t2s"
        self._gen = 0                              # 分类代次：弃置旧代结果
        self._apply_total = 0
        self._apply_results: list = []
        self._apply_dispatching = False     # 派工在途标志（防重入）
        self._confirmed_once = False
        self._search_active = False
        self._removed: set[str] = set()    # 已移除文件：迟到解析回执弃置

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = ScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, 1)

        self.view = QWidget(self._scroll)
        v = QVBoxLayout(self.view)
        v.setContentsMargins(36, 24, 36, 12)   # 页边距对齐其他页（底边距归底栏）
        v.setSpacing(12)
        self.view.setMinimumWidth(420)
        self._scroll.setWidget(self.view)
        # 先 setWidget 再透明化：qfw enableTransparentBackground 只对已挂
        # 的 widget() 补 transparent QSS，次序颠倒视口留浅色底（真机实锤）
        self._scroll.enableTransparentBackground()

        head = QHBoxLayout()
        # BodyLabel 而非裸 QLabel：LABEL qss 黑/白随主题（裸 QLabel 黑字死调色板）
        self.page_title = BodyLabel("处理字幕")
        setFont(self.page_title, 20)   # qfw setFont：20px Normal 字重不变
        head.addWidget(self.page_title)
        head.addStretch(1)
        self.summary = _BadgeRow()
        head.addWidget(self.summary)
        v.addLayout(head)

        # 字幕文件卡在 _rebuild_file_rows 里整卡重建（GroupHeaderCardWidget）：
        # 汇总/两钮是常驻控件，重建时重挂进新卡标题栏
        self.files_summary = CaptionLabel("0 / 0 个文件 · 0 种字体")
        self.btn_reload = PushButton(FIF.SYNC, "重新载入")
        self.btn_add = PrimaryPushButton(FIF.ADD, "添加文件")
        self.files_box = QVBoxLayout()
        self.files_box.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.files_box)

        # 参数行：字段组在左（FlowLayout 窄窗换行），预览框钉右端
        params = QWidget(self.view)
        ph = QHBoxLayout(params)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(18)
        flow_wrap = QWidget(params)
        pl = FlowLayout(flow_wrap, needAni=False, isTight=False)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setHorizontalSpacing(18)
        pl.setVerticalSpacing(8)
        self.seg_dir = SegmentedWidget(self)
        self.seg_dir.addItem("t2s", "繁 → 简", lambda: self._set_direction("t2s"))
        self.seg_dir.addItem("s2t", "简 → 繁", lambda: self._set_direction("s2t"))
        self.seg_dir.setCurrentItem("t2s")
        pl.addWidget(_fld("孪生检测方向", self.seg_dir))
        ph.addWidget(flow_wrap, 1)
        # 强制等宽（settings 键 preview_mono，两页同键互通）：预览统一格宽
        # 渲染，多字体行逐字对位；默认关（自然字距）。qfw SwitchButton 的
        # 自带文本是 On/Off 态字（toggle 即覆写 setText），说明文字用 _fld
        # 标签对，开关只留指示器
        self.sw_mono = SwitchButton(self)
        self.sw_mono.setOnText("")
        self.sw_mono.setOffText("")
        self.sw_mono.setChecked(self.settings.get("preview_mono"))
        install_tip(self.sw_mono, "统一格宽渲染预览（全角 1em/半角 0.5em），多字体行逐字对位")
        self.sw_mono.checkedChanged.connect(self._on_mono_toggled)
        ph.addWidget(_fld("强制等宽", self.sw_mono), 0, Qt.AlignVCenter)
        # 可键入 + 下拉最近 10 条历史（与字体库预览页共享，回车记一条）
        self.preview_edit = PreviewInput(self.settings)
        ph.addWidget(self.preview_edit, 0, Qt.AlignVCenter)
        v.addWidget(params)

        self.cards_box = QVBoxLayout()
        v.addLayout(self.cards_box)
        v.addStretch(1)

        # 底栏钉死视口下缘（用户拍板 2026-10-29：保存方式/替换保存常驻
        # 可见，不随内容滚走）——在外层布局、滚动区之外
        foot = QWidget(self)
        bottom = QHBoxLayout(foot)
        # 上下对称且收紧：内容垂直居中、栏体矮（控件 hint ~33px + 20 边距）
        bottom.setContentsMargins(36, 15, 36, 15)
        bottom.setSpacing(12)
        bottom.addWidget(CaptionLabel("保存方式:"))
        self.save_mode = ComboBox()
        self.save_mode.addItems(["另存为新文件", "覆盖原文件"])
        self.save_mode.setFixedWidth(150)
        self.save_mode.setCurrentIndex(
            1 if self.settings.get("overwrite_default") else 0)
        self.save_mode.currentIndexChanged.connect(self._on_save_mode)
        # 滚动页面扫过下拉会误改保存方式并落盘——吞滚轮
        self._wheel_block = WheelBlock(self)
        self.save_mode.installEventFilter(self._wheel_block)
        bottom.addWidget(self.save_mode)
        # 另存模式才需要后缀；编辑与设置页双向同步（suffixChanged 信号）
        self.suffix_label = CaptionLabel("另存后缀:")
        self.suffix_edit = LineEdit()
        self.suffix_edit.setText(self.settings.get("save_suffix"))
        self.suffix_edit.setFixedWidth(160)
        self.suffix_edit.setClearButtonEnabled(True)
        self.suffix_edit.editingFinished.connect(self._on_suffix_edit)
        bottom.addWidget(self.suffix_label)
        bottom.addWidget(self.suffix_edit)
        bottom.addStretch(1)
        self.replace_label = CaptionLabel("将替换 0 / 0 种字体")
        bottom.addWidget(self.replace_label)
        self.btn_apply = PrimaryPushButton(FIF.PLAY, "替换并保存")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        bottom.addWidget(self.btn_apply)
        outer.addWidget(foot)
        self._set_suffix_row_visible(
            not self.settings.get("overwrite_default"))

        # ---- connect 段（控件就绪后统一接线）----
        self.btn_add.clicked.connect(self._add_files)
        self.btn_reload.clicked.connect(self._reload_files)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.preview_edit.textChanged.connect(self._on_preview_text)
        self.settings.valueChanged.connect(self._on_mono_setting)
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(300)
        self._preview_debounce.timeout.connect(self._apply_all_cards)
        # 渐进回执刷新节流：回执只记账，突发合并成一次增量刷新（见
        # _schedule_refresh）
        self._refresh_pending: set[str] = set()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._flush_refresh)
        self._rebuild_file_rows()   # 启动空态也建卡：添加文件钮在卡标题栏
        self._reclassify()

    def verticalScrollBar(self):
        """兼容旧 ScrollArea 接口（测试/调用方按页取滚动条）。"""
        return self._scroll.verticalScrollBar()

    # ---- 文件层 ----
    def _add_files(self):
        paths = pick_open_files(self, self.settings, "添加字幕文件",
                                "ASS 字幕 (*.ass *.ssa)", "subs")
        if not paths:
            return
        self._removed.difference_update(paths)   # 重加同一文件：解除弃置
        self._parsing.update(paths)
        for p in paths:
            self.worker.submit(f"parse:{p}", _parse_job, p)

    def _reload_files(self):
        self._parsing.update(f.path for f in self.vm.files)
        for f in self.vm.files:
            self.worker.submit(f"parse:{f.path}", _parse_job, f.path)

    def _parse_arrived(self, path: str):
        """批内一份结果到达：先记账，整批收齐才重算——逐份重算会把
        N 个文件放大成 1+2+…+N 次分类（全卡拆除重建 N-1 次，白算）。"""
        self._parsing.discard(path)
        if not self._parsing:
            self._reclassify()

    def _rebuild_file_rows(self):
        while self.files_box.count():
            item = self.files_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 一张 GroupHeaderCardWidget 框住全部文件行（行间分隔线由组件管）；
        # 标题栏当工具条：左=标题+汇总，右=重新载入+添加文件
        card = GroupHeaderCardWidget(self)
        card.setTitle("字幕文件")
        # 标题栏左块两行：标题下挂汇总（标题栏 48px 容得下）
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.setAlignment(Qt.AlignVCenter)
        lv.addWidget(card.headerLabel)
        lv.addWidget(self.files_summary)
        card.headerLayout.addWidget(left)
        card.headerLayout.addStretch(1)
        card.headerLayout.addWidget(self.btn_reload)
        card.headerLayout.addWidget(self.btn_add)
        for f in self.vm.files:
            # qfw QSS min-width:28px 把空文本框撑到 57px（indicator 右侧
            # 拖 ~36px 空白），文件名离勾选框过远；且 polish 会把 QSS
            # min-width 写回 minimumWidth（setFixedWidth 前置必被顶回）——
            # 走 qfw setCustomStyleSheet 覆写 min-width 再定宽
            cb = CheckBox()
            setCustomStyleSheet(cb, "CheckBox{min-width:0px;}",
                                "CheckBox{min-width:0px;}")
            cb.setFixedWidth(24)
            cb.setChecked(f.checked)
            cb.toggled.connect(lambda on, p=f.path: self._on_file_checked(p, on))
            name = _ElidedLabel(Path(f.path).name)
            install_tip(name, f.path)

            right = QWidget()
            rh = QHBoxLayout(right)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(8)
            content = ""
            if f.error:
                badge = padded_badge(InfoBadge.error, "解析失败")
                rh.addWidget(badge, 0, Qt.AlignVCenter)
                # tip 挂徽章本体：容器只吃自身窄缝 Enter，悬徽章不冒泡
                install_tip(badge, f.error)
            else:
                content = f"{f.font_count} 种字体"
            rm = TransparentToolButton(FIF.REMOVE_FROM)
            # 危险动作：红色示警（icon() 第二参才是颜色，须关键字传）
            rm.setIcon(FIF.REMOVE_FROM.icon(color=_REMOVE_RED))
            install_tip(rm, "从列表移除")
            rm.clicked.connect(lambda _=False, p=f.path: self._remove_file(p))
            rh.addWidget(rm, 0, Qt.AlignVCenter)

            group = card.addGroup(FIF.DOCUMENT, "", content, right)
            # 勾选框占原文件图标位：行首、跨两行垂直居中（出布局并隐藏
            # 原图标，免拖 15px 间距）
            group.hBoxLayout.removeWidget(group.iconWidget)
            group.iconWidget.hide()
            group.hBoxLayout.insertWidget(0, cb, 0, Qt.AlignVCenter)
            # 清组件给 textLayout 预置的 AlignCenter：否则两行 items 被
            # 关进居中窄条（各按 hint 宽居中，短行不贴左）。清掉后 items
            # 填满列宽——文件名/统计同列左对齐，窄窗时文件名省略得动；
            # 文本列吃满余宽需压掉组件预置 stretch（itemAt(2)）
            group.textLayout.setAlignment(Qt.Alignment())
            group.hBoxLayout.setStretch(1, 1)
            group.hBoxLayout.setStretch(2, 0)
            group.titleLabel.hide()
            group.textLayout.insertWidget(0, name)
        if not self.vm.files:
            ph = CaptionLabel("（尚未添加字幕文件）")
            ph.setAlignment(Qt.AlignCenter)
            wrap = QWidget()
            wh = QHBoxLayout(wrap)
            wh.setContentsMargins(24, 10, 24, 14)
            wh.addWidget(ph)
            card.viewLayout.addWidget(wrap)
        self.files_box.addWidget(card)
        self._update_files_summary()

    def _remove_file(self, path: str):
        """从列表移除文件：vm 记账 + 整卡重建 + 重分类（同取消勾选路径）；
        在途解析回执按 _removed 弃置，免得删掉的文件又冒回来。"""
        self._removed.add(path)
        self._parsing.discard(path)     # 移除即视为该份已到达：不卡批次
        self.vm.remove_file(path)
        self._rebuild_file_rows()
        if not self._parsing:
            self._reclassify()

    def _update_files_summary(self):
        checked = len(self.vm.checked_parses())
        total = len(self.vm.files)
        n_fonts = len(self.vm.merged_usages())
        self.files_summary.setText(f"{checked} / {total} 个文件 · {n_fonts} 种字体")

    def _on_file_checked(self, path, on):
        self.vm.set_checked(path, on)
        self._update_files_summary()
        # 批次解析中不立即重算：勾选变化会在批末统一重算时生效，
        # 免得半批数据先拆一次卡
        if not self._parsing:
            self._reclassify()

    # ---- 结果分发（UI 线程；vm 一切变更都在此）----
    def _on_result(self, tag, payload):
        if isinstance(tag, str) and tag.startswith("parse:"):
            if payload.path in self._removed:   # 移除后迟到的回执：弃置
                return
            self.vm.add_files([payload])
            self._rebuild_file_rows()
            self._parse_arrived(payload.path)
        elif isinstance(tag, str) and tag.startswith("classify:"):
            if not tag.startswith(f"classify:{self._gen}:"):
                return                     # 旧代结果：勾选/方向/参数已变，弃置
            usage, cls = payload
            card = self.vm.make_card(usage, cls)
            self.vm.apply_default_check(card)
            # 卡序按 CARD_RANK 插入（孪生→需替换→库中没有→保留）
            idx = len(self.vm.cards)
            for j, c in enumerate(self.vm.cards):
                if CARD_RANK.get(c.kind, 9) > CARD_RANK.get(card.kind, 9):
                    idx = j
                    break
            self.vm.cards.insert(idx, card)
            self._append_card(card, idx)
        elif tag == "slib":
            self.vm.set_entities(payload.lib.entities())
        elif isinstance(tag, str) and tag.startswith("newsearcher:"):
            # worker 回发提交时的原 tag：建新搜索器回执，挂接后立即拉第一批
            norm = tag.split(":", 1)[1]
            card = self._card(norm)
            if card is not None:
                self.vm.attach_searcher(card, payload)
                self._apply_card(card)
                self.worker.submit(f"pull:{norm}", _pull_job, payload,
                                   self._cancel_for(norm))
        elif isinstance(tag, str) and tag.startswith("pull:"):
            norm = tag.split(":", 1)[1]
            card = self._card(norm)
            # 卡片重算后重建（searcher 未见挂前）：旧拉取结果一律弃置
            if card is None or card.searcher is None:
                return
            cands, state = payload
            self.vm.add_candidates(card, cands)
            if state == "running":
                self._schedule_refresh(card)           # 记账即回，重建走节流
                self.worker.submit(f"pull:{norm}", _pull_job, card.searcher,
                                   self._cancel_for(norm))
            else:
                self._refresh_pending.discard(norm)
                self.vm.finish_search(card, card.searcher,
                                      cancelled=(state == "cancelled"))
                self._apply_card(card)
                self._update_summary()
        elif isinstance(tag, str) and tag.startswith("pickscore:"):
            norm = tag.split(":", 1)[1]
            card = self._card(norm)
            if card is None:               # 评分期间整页重算：弃置
                return
            entity, sim, size = payload
            self.vm.attach_pick(card, entity, sim, size)
            self._apply_card(card)
            self._update_summary()
        elif tag == "apply":
            self._on_apply_done(payload)

    def _on_failed(self, tag, err):
        if isinstance(tag, str) and tag.startswith("parse:"):
            path = tag.split(":", 1)[1]
            if path in self._removed:   # 已移除文件的迟到失败：不重算不弹泡
                return
            self._parse_arrived(path)   # 批次对齐：失败也算到达
        # 搜索链任务失败：解卡片的 searching 状态，避免转圈挂死
        if isinstance(tag, str) and (tag.startswith("newsearcher:")
                                     or tag.startswith("pull:")):
            norm = tag.split(":", 1)[1]
            card = self._card(norm)
            if card is not None and card.searching:
                card.searching = False
                self._refresh_pending.discard(norm)
                self._apply_card(card)
        if tag == "apply":                 # 派工链中断：复位防重入状态
            self._apply_results = []
            self._apply_total = 0
            self._apply_dispatching = False
            self._update_summary()         # 按当前状态恢复
        # 弹泡白名单：只报本页任务。其余 tag（cachestat/cachemove/预览渲染
        # 失败等）别页自会提示或已自行降级——无差别弹泡只会刷屏且不可读
        if isinstance(tag, str) and (
                tag in ("slib", "apply")
                or tag.startswith(("parse:", "classify:", "pull:",
                                   "newsearcher:", "pickscore:"))):
            InfoBar.error("任务失败", f"{tag}: {err}", parent=self)

    # ---- 分类 ----
    def _reclassify(self):
        self._gen += 1
        for d in self._cancels.values():
            d["cancelled"] = True
        self._cancels.clear()
        usages = self.vm.merged_usages()
        self.vm.cards.clear()
        for w in self._cards_ui.values():
            w.teardown()    # 撤预览渲染单
            w.hide()        # deleteLater 迟到：不 hide 旧卡照旧叠画
            w.deleteLater()
        self._cards_ui.clear()
        self._update_summary()
        if not usages:
            return
        self.worker.submit("slib", _lib_job, list(self.settings.get("font_dirs")))
        cov = float(self.settings.get("cov_threshold"))
        for i, u in enumerate(usages):
            self.worker.submit(f"classify:{self._gen}:{i}", _classify_job, u,
                               cov, self._direction)

    def _set_direction(self, d):
        if d == self._direction:
            return
        self._direction = d
        self.vm.direction = d
        self._reclassify()

    def on_library_changed(self):
        """字体库管理页删目录/索引更新后的广播：全量重分类。

        分类结论（库中有/没有、候选池）基于建卡时的库快照，库变了即失效；
        _reclassify 换代自带取消在跑 pull、清旧 searcher。解析批次中不打断
        ——批末 _parse_arrived 的重算读当时 font_dirs，天然收敛。"""
        if not self._parsing:
            self._reclassify()

    # ---- 卡片 UI ----
    def _make_card_ui(self, card) -> FontCard:
        fc = FontCard(card, self.worker, self._pvm,
                      lambda: self.preview_edit.text(), self._direction)
        fc.option_checked.connect(self._on_option_checked)
        fc.continue_clicked.connect(self._continue_search)
        fc.pick_clicked.connect(self._open_picker)
        return fc

    def _append_card(self, card, idx=None):
        fc = self._make_card_ui(card)
        self._cards_ui[card.norm_name] = fc
        if idx is None or idx >= self.cards_box.count():
            self.cards_box.addWidget(fc)
        else:
            self.cards_box.insertWidget(idx, fc)
        self._update_summary()

    def _schedule_refresh(self, card):
        """渐进回执合并刷新：大片池按时长切片后回执最密 ~2/s，缓存全
        命中时一片 24 对比瞬完、回执成洪——每回执即时 apply 仍会把
        refresh 洪水直接灌进 reconcile。回执只记账 vm，300ms 单发定时
        器把突发合并成一次增量刷新；终态回执仍即时刷。"""
        self._refresh_pending.add(card.norm_name)
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _flush_refresh(self):
        pending, self._refresh_pending = self._refresh_pending, set()
        for norm in pending:
            card = self._card(norm)
            if card is not None:
                self._apply_card(card)

    def _apply_card(self, card):
        """卡数据变化（搜索批次回填/勾选回填/状态翻转）后就地刷新：FontCard
        按 vm 卡数据 reconcile——新候选行位次插入、按钮/勾选/预览文字
        原地刷。整卡不拆不重建（旧「拆除重建」路线每 300ms 闪一次、点击
        搜索钮滚动瞬移、焦点丢失，2026-10-29 重做废弃）。
        尾随 _update_summary：searching 翻转（起搜/终态）在此发生，设置页
        的「搜索中改门槛待生效」提示依赖该信号。"""
        fc = self._cards_ui.get(card.norm_name)
        if fc is None:                     # 无 UI 的卡（理论不可达）：兜底建
            self._append_card(card)        # （内含 _update_summary）
        else:
            fc.apply()
            self._update_summary()

    def _apply_all_cards(self):
        for card in list(self.vm.cards):
            if card.norm_name in self._cards_ui:
                self._apply_card(card)

    def _on_option_checked(self, norm, key):
        card = self._card(norm)
        if card is not None:
            card.checked_key = key
            for c in self.vm.cards:
                ui = self._cards_ui.get(c.norm_name)
                if ui is not None:
                    ui.set_checked(c.checked_key)
            self._update_summary()

    def _on_preview_text(self, _):
        # 预览文字变化：各卡预览条就地换文字重渲（tile 缓存键含文字，
        # 重渲走 LRU 命中）
        self._preview_debounce.start()

    def _on_mono_toggled(self, on: bool):
        # 只落设置；本页与他页的重渲统一走 valueChanged 广播
        # （_on_mono_setting），两页口径一个来源
        self.settings.set("preview_mono", bool(on))

    def _on_mono_setting(self, key, value):
        if key != "preview_mono":
            return
        self.sw_mono.setChecked(value)      # 他页翻转时本页开关跟上
        for fc in self._cards_ui.values():
            fc.rerender_previews()

    def _card(self, norm):
        return next((c for c in self.vm.cards if c.norm_name == norm), None)

    # ---- 搜索 ----
    def _cancel_for(self, norm) -> dict:
        return self._cancels.setdefault(norm, {"cancelled": False})

    def _continue_search(self, norm):
        card = self._card(norm)
        if card is None:
            return
        if card.searching:            # 搜索中再点（钮已变 Pause 图标）= 暂停
            self._cancel_for(norm)["cancelled"] = True
            return
        if not card.n_chars:          # 0 字卡无可比字型：不给搜索（UI 已藏入口）
            return
        if card.searcher is not None and card.search_state == "paused":
            card.searching = True
            card.searcher.resume()    # 清暂停+重置批计数：否则空片回执原地打转
            cancel = {"cancelled": False}   # 上轮手动暂停标志不带入本轮
            self._cancels[norm] = cancel
            self._apply_card(card)
            self.worker.submit(f"pull:{norm}", _pull_job, card.searcher,
                               cancel)     # 续用：累计 hit_count 语义
            return
        # 新搜索器：相似度基准由 UI 线程算好传入（worker 线程无 vm 引用）；
        # 门槛取 card.min_sim（vm 与在用搜索器保持同步），非设置页原值；
        # 覆盖率/大小区间此刻从设置页现读（改设置不影响在跑的搜索器）
        card.searching = True
        cancel = {"cancelled": False}       # 先立取消标志：newsearcher 在途
        self._cancels[norm] = cancel        # 时点 Pause 也能生效（回执沿用）
        self._apply_card(card)
        self.worker.submit(f"newsearcher:{norm}", _new_searcher_job,
                           vm_target(self.vm, card),
                           self.vm.chars_by_norm.get(norm, set()),
                           float(self.settings.get("cov_threshold")),
                           int(self.settings.get("n_pause")),
                           float(card.min_sim or self.settings.get("min_sim")),
                           (float(self.settings.get("size_min")),
                            float(self.settings.get("size_max"))),
                           self._direction)

    def _open_picker(self, norm):
        card = self._card(norm)
        if card is None:
            return
        dlg = FontPickerDialog(self.settings, self.worker,
                               list(self.vm.ent_by_fp.values()),
                               chars=self.vm.chars_by_norm.get(norm),
                               direction=self._direction, parent=self)
        if dlg.exec() and dlg.selected is not None:
            # 模态期间 worker 结果照常派发：parse/classify 可能已整页重算，
            # 闭包里的旧 card 成孤儿——回填前按 norm 重取
            card = self._card(norm)
            if card is None:
                InfoBar.warning("字体已重算",
                                "该字体卡片已被重新分类，请重新选择", parent=self)
                return
            # 相似率/相对大小经 worker 评分（渲染+比较 ~57ms，UI 线程不做）；
            # 行先不入列，评分回执一并 attach（免两帧重建）
            self.worker.submit(f"pickscore:{norm}", _pick_score_job,
                               vm_target(self.vm, card), dlg.selected)

    # ---- 应用 ----
    def _apply(self):
        if self._apply_dispatching:
            return                        # 派工在途：忽略重复点击（防重入）
        conflicts = self.vm.pending_out_conflicts()
        if conflicts and not self._confirmed_once:
            InfoBar.warning("另存文件已存在",
                            "将覆盖：" + "；".join(conflicts) + "（再次点击确认）",
                            parent=self)
            self._confirmed_once = True
            return                        # 二次点击才真正执行（简单确认机制）
        self._confirmed_once = False
        mapping = self.vm.build_mapping()
        overwrite = bool(self.settings.get("overwrite_default"))
        self._apply_total = len(self.vm.checked_parses())
        self._apply_results = []
        if not self._apply_total:
            return                        # 无勾选文件：不派工、不禁用
        self._apply_dispatching = True
        self.btn_apply.setEnabled(False)   # 派工期禁用：防连点重复写文件
        for f in self.vm.checked_parses():
            self.worker.submit("apply", _apply_job, f.path, mapping,
                               self.settings.get("save_suffix"), overwrite)

    def _on_apply_done(self, result):
        # 派工中断（_on_failed 复位）后迟到的结果不再触发收尾，
        # 否则剩余回执会连环重复 InfoBar/record_all
        if not self._apply_dispatching or len(self._apply_results) \
                >= self._apply_total:
            return
        self._apply_results.append(result)
        if len(self._apply_results) < self._apply_total:
            return
        ok = [r for r in self._apply_results if r.error is None]
        bad = [r for r in self._apply_results if r.error is not None]
        if bad:
            InfoBar.error("部分文件失败", "；".join(
                f"{Path(r.path).name}: {r.error}" for r in bad), parent=self)
        if ok:
            InfoBar.success("替换完成", f"{len(ok)} 个文件已写入", parent=self)
        self.vm.record_all()
        self._apply_dispatching = False
        self._apply_results = []
        self._apply_total = 0
        self._update_summary()             # 按当前状态恢复按钮可用性

    def _update_summary(self):
        kinds = {"keep": 0, "twin": 0, "replace": 0, "missing": 0}
        for c in self.vm.cards:
            if c.kind in kinds:
                kinds[c.kind] += 1
        self.summary.set_counts(kinds)
        n, total = self.vm.replacement_counts()
        self.replace_label.setText(f"将替换 {n} / {total} 种字体")
        # 派工期保持禁用：此时写盘用的是派工快照，改勾选不会生效，
        # 放开按钮只会让「替换并保存」与决策记忆（record_all）分叉
        self.btn_apply.setEnabled(not self._apply_dispatching
                                  and bool(self.vm.checked_parses())
                                  and bool(self.vm.cards))
        active = any(c.searching for c in self.vm.cards)
        if active != self._search_active:
            self._search_active = active
            self.searchActiveChanged.emit(active)

    # ---- 保存方式/后缀（与设置页双向同步）----
    def _on_save_mode(self, index):
        overwrite = index == 1
        self.settings.set("overwrite_default", overwrite)
        self._set_suffix_row_visible(not overwrite)
        self.saveModeChanged.emit(overwrite)
        self._update_summary()

    def set_save_mode(self, overwrite: bool):
        """设置页改了保存方式：只搬 UI（信号源头已写设置，不再回写）。"""
        self.save_mode.blockSignals(True)
        self.save_mode.setCurrentIndex(1 if overwrite else 0)
        self.save_mode.blockSignals(False)
        self._set_suffix_row_visible(not overwrite)
        self._update_summary()

    def _on_suffix_edit(self):
        text = self.suffix_edit.text().strip()
        if text == self.settings.get("save_suffix"):
            return                        # 失焦也触发 editingFinished：未变不落盘
        try:
            self.settings.set("save_suffix", text)
        except ValueError:
            InfoBar.error("后缀无效", "必须以点开头且非空，如 .substituted.ass",
                          parent=self)
            self.suffix_edit.setText(self.settings.get("save_suffix"))
            return
        self.suffixChanged.emit(self.settings.get("save_suffix"))

    def set_save_suffix(self, text: str):
        """设置页改了后缀：只搬 UI（editingFinished 不因 setText 触发）。"""
        self.suffix_edit.setText(text)

    def _set_suffix_row_visible(self, visible: bool):
        self.suffix_label.setVisible(visible)
        self.suffix_edit.setVisible(visible)


def _fld(label: str, w: QWidget) -> QWidget:
    """参数工具条的字段对：标签+控件，交 FlowLayout 换行排布。"""
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    h.addWidget(CaptionLabel(label))
    h.addWidget(w)
    return box


class _ElidedLabel(BodyLabel):
    """中间省略号文件名：随宽度重算，长名不撑横向。"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full = text
        self._update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update()

    def _update(self):
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.ElideMiddle,
                                   max(120, self.width())))


class _BadgeRow(QWidget):
    """页头分类计数徽章行（线框配色）；text() 供测试回读。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = QHBoxLayout(self)
        self._h.setContentsMargins(0, 0, 0, 0)
        self._h.setSpacing(8)
        self._counts = {"keep": 0, "twin": 0, "replace": 0, "missing": 0}
        self._rebuild()

    def set_counts(self, kinds: dict):
        self._counts = {k: kinds.get(k, 0) for k in self._counts}
        self._rebuild()

    def text(self) -> str:
        c = self._counts
        return (f"孪生对应 {c['twin']} / 需替换 {c['replace']} / "
                f"库中没有 {c['missing']} / 保留 {c['keep']}")

    def _rebuild(self):
        while self._h.count():
            it = self._h.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        c = self._counts
        # 顺序=用户关注度（孪生最好 → 保留垫底）；孪生/保留互换底色：
        # 绿色给「孪生对应」（同样是无需处理的好结果），保留退中性蓝
        for text, mk in ((f"孪生对应 {c['twin']}", InfoBadge.success),
                         (f"需替换 {c['replace']}", InfoBadge.warning),
                         (f"库中没有 {c['missing']}", InfoBadge.error),
                         (f"保留 {c['keep']}", InfoBadge.info)):
            self._h.addWidget(padded_badge(mk, text))
        self._h.addStretch(1)


# ---- worker 任务（纯数据进出；ctx 由 CoreWorker 提供）----

def _parse_job(ctx, p):
    return parse_subtitle(p)


def _lib_job(ctx, dirs):
    return ensure_library(ctx, dirs)


def _classify_job(ctx, usage, cov, direction):
    ensure_library(ctx, ctx.settings.get("font_dirs"))
    return usage, classify_one(ctx.lib, usage, cov, direction)


def vm_target(vm, card):
    """相似度基准在 UI 线程算好（vm._search_target），作纯数据传给任务。"""
    return vm._search_target(card)


def _new_searcher_job(ctx, target, chars, cov, n_pause, min_sim, size_range,
                      direction):
    return make_searcher(ctx, target=target, chars=chars, threshold=cov,
                         n_pause=n_pause, min_sim=min_sim,
                         size_range=size_range, direction=direction)


def _pull_job(ctx, searcher, cancel):
    return pull_chunk(searcher, cancel)


def _pick_score_job(ctx, target, entity):
    return score_pick(ctx, target, entity)


def _apply_job(ctx, path, mapping, suffix, overwrite):
    return apply_to_file(path, mapping, suffix, overwrite)
