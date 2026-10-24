from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            FlowLayout, InfoBadge, InfoBar, LineEdit,
                            PrimaryPushButton, PushButton, ScrollArea,
                            SegmentedWidget, StrongBodyLabel, SwitchButton)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.jobs import (apply_to_file, classify_one,
                                          ensure_library, make_searcher,
                                          parse_subtitle, pull_chunk)
from ass_font_substitute.gui.paths import decisions_path
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.subs_vm import SubsVM
from ass_font_substitute.gui.widgets.font_card import FontCard
from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog


class SubsPage(ScrollArea):
    """处理字幕页：文件卡列表 → 参数行 → 字体卡渐进填充 → 底部应用条。
    跨线程约定：worker 任务收纯数据、回纯数据；vm 的全部变更都发生在
    UI 线程的 _on_result / 信号槽里。"""

    searchActiveChanged = Signal(bool)   # 有无进行中的相似搜索（设置页提示用）

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("subsPage")
        db = decisions_path()
        db.parent.mkdir(parents=True, exist_ok=True)   # 首启无 afs 目录：决策库随用随建
        self.vm = SubsVM(settings, db)
        self._pvm = PreviewVM()                    # 字体卡预览条共享 tile LRU
        self._cards_ui: dict[str, FontCard] = {}
        self._cancels: dict[str, dict] = {}        # norm → 取消标志（pull 任务读）
        self._direction = "t2s"
        self._gen = 0                              # 分类代次：弃置旧代结果
        self._apply_total = 0
        self._apply_results: list = []
        self._apply_dispatching = False     # 派工在途标志（防重入）
        self._confirmed_once = False
        self._search_active = False

        self.view = QWidget(self)
        v = QVBoxLayout(self.view)
        v.setSpacing(12)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setMinimumWidth(420)

        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("处理字幕"))
        head.addStretch(1)
        self.summary = _BadgeRow()
        head.addWidget(self.summary)
        v.addLayout(head)

        files_card = CardWidget()
        fl = QVBoxLayout(files_card)
        fl.setSpacing(6)
        fhead = QHBoxLayout()
        fhead.setSpacing(8)
        fhead.addWidget(StrongBodyLabel("字幕文件"))
        self.files_summary = CaptionLabel("0 / 0 个文件 · 0 种字体")
        fhead.addWidget(self.files_summary)
        fhead.addStretch(1)
        self.btn_add = PushButton(FIF.ADD, "添加文件")
        self.btn_reload = PushButton(FIF.SYNC, "重新载入")
        fhead.addWidget(self.btn_add)
        fhead.addWidget(self.btn_reload)
        fl.addLayout(fhead)
        self.files_box = QVBoxLayout()
        self.files_box.setSpacing(6)
        fl.addLayout(self.files_box)
        v.addWidget(files_card)

        params = QWidget(self.view)
        pl = FlowLayout(params, needAni=False, isTight=False)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setHorizontalSpacing(18)
        pl.setVerticalSpacing(8)
        self.seg_dir = SegmentedWidget(self)
        self.seg_dir.addItem("t2s", "繁 → 简", lambda: self._set_direction("t2s"))
        self.seg_dir.addItem("s2t", "简 → 繁", lambda: self._set_direction("s2t"))
        self.seg_dir.setCurrentItem("t2s")
        pl.addWidget(_fld("孪生检测方向", self.seg_dir))
        self.preview_edit = LineEdit()
        self.preview_edit.setPlaceholderText("预览字体（留空显示字体名）")
        self.preview_edit.setClearButtonEnabled(True)
        self.preview_edit.setFixedWidth(260)
        pl.addWidget(self.preview_edit)
        v.addWidget(params)

        self.cards_box = QVBoxLayout()
        v.addLayout(self.cards_box)
        v.addStretch(1)

        bottom = QHBoxLayout()
        # SwitchButton 构造参数是 Off 态文案、勾上显示 onText（默认 "On"）——
        # 双态同文案才能当常驻标签用
        self.sw_save = SwitchButton("另存替换后文件")
        self.sw_save.setOnText("另存替换后文件")
        self.sw_save.setChecked(True)
        self.sw_overwrite = SwitchButton("覆盖原文件")
        self.sw_overwrite.setOnText("覆盖原文件")
        self.sw_save.checkedChanged.connect(self._on_save_toggle)
        self.sw_overwrite.checkedChanged.connect(self._on_overwrite_toggle)
        bottom.addWidget(self.sw_save)
        bottom.addWidget(self.sw_overwrite)
        bottom.addWidget(CaptionLabel(
            f"另存后缀 {self.settings.get('save_suffix')}（设置页可改）"))
        bottom.addStretch(1)
        self.replace_label = CaptionLabel("将替换 0 / 0 种字体")
        bottom.addWidget(self.replace_label)
        self.btn_apply = PrimaryPushButton(FIF.PLAY, "应用替换")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        bottom.addWidget(self.btn_apply)
        v.addLayout(bottom)

        # ---- connect 段（控件就绪后统一接线）----
        self.btn_add.clicked.connect(self._add_files)
        self.btn_reload.clicked.connect(self._reload_files)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.preview_edit.textChanged.connect(self._on_preview_text)
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(300)
        self._preview_debounce.timeout.connect(self._refresh_all_cards)
        self._reclassify()

    # ---- 文件层 ----
    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加字幕文件", "",
                                                "ASS 字幕 (*.ass *.ssa)")
        if not paths:
            return
        for p in paths:
            self.worker.submit(f"parse:{p}", _parse_job, p)

    def _reload_files(self):
        for f in self.vm.files:
            self.worker.submit(f"parse:{f.path}", _parse_job, f.path)

    def _rebuild_file_rows(self):
        while self.files_box.count():
            item = self.files_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for f in self.vm.files:
            row = CardWidget()
            h = QHBoxLayout(row)
            cb = CheckBox()
            cb.setChecked(f.checked)
            cb.toggled.connect(lambda on, p=f.path: self._on_file_checked(p, on))
            h.addWidget(cb)
            name = _ElidedLabel(Path(f.path).name)
            name.setToolTip(f.path)
            h.addWidget(name, 1)
            if f.error:
                h.addWidget(InfoBadge.error("解析失败"))
                row.setToolTip(f.error)
            else:
                h.addWidget(CaptionLabel(f"{f.font_count} 种字体"))
                if f.unknown_styles:
                    h.addWidget(InfoBadge.warning("未知样式"))
            self.files_box.addWidget(row)
        self._update_files_summary()

    def _update_files_summary(self):
        checked = len(self.vm.checked_parses())
        total = len(self.vm.files)
        n_fonts = len(self.vm.merged_usages())
        self.files_summary.setText(f"{checked} / {total} 个文件 · {n_fonts} 种字体")

    def _on_file_checked(self, path, on):
        self.vm.set_checked(path, on)
        self._update_files_summary()
        self._reclassify()

    # ---- 结果分发（UI 线程；vm 一切变更都在此）----
    def _on_result(self, tag, payload):
        if isinstance(tag, str) and tag.startswith("parse:"):
            self.vm.add_files([payload])
            self._rebuild_file_rows()
            self._reclassify()
        elif isinstance(tag, str) and tag.startswith("classify:"):
            if not tag.startswith(f"classify:{self._gen}:"):
                return                     # 旧代结果：勾选/方向/参数已变，弃置
            usage, cls = payload
            card = self.vm.make_card(usage, cls)
            self.vm.apply_default_check(card)
            self.vm.cards.append(card)
            self._append_card(card)
        elif tag == "slib":
            self.vm.set_entities(payload.lib.entities())
        elif isinstance(tag, str) and (tag.startswith("searcher:")
                                       or tag.startswith("newsearcher:")):
            # worker 回发提交时的原 tag：建新搜索器是 newsearcher:，续搜无回执
            norm = tag.split(":", 1)[1]
            card = self._card(norm)
            if card is not None:
                self.vm.attach_searcher(card, payload)
                self._refresh_card(card)
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
                self._refresh_card(card)               # 渐进填充
                self.worker.submit(f"pull:{norm}", _pull_job, card.searcher,
                                   self._cancel_for(norm))
            else:
                self.vm.finish_search(card, card.searcher)
                self._refresh_card(card)
                self._update_summary()
        elif tag == "apply":
            self._on_apply_done(payload)

    def _on_failed(self, tag, err):
        # 搜索链任务失败：解卡片的 searching 状态，避免转圈挂死
        if isinstance(tag, str) and (tag.startswith("newsearcher:")
                                     or tag.startswith("pull:")):
            card = self._card(tag.split(":", 1)[1])
            if card is not None and card.searching:
                card.searching = False
                self._refresh_card(card)
        if tag == "apply":                 # 派工链中断：复位防重入状态
            self._apply_results = []
            self._apply_total = 0
            self._apply_dispatching = False
            self._update_summary()         # 按当前状态恢复（全关时保持禁用）
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

    # ---- 卡片 UI ----
    def _make_card_ui(self, card) -> FontCard:
        fc = FontCard(card, self.worker, self._pvm,
                      lambda: self.preview_edit.text())
        fc.option_checked.connect(self._on_option_checked)
        fc.continue_clicked.connect(self._continue_search)
        fc.relax_clicked.connect(self._relax_search)
        fc.pick_clicked.connect(self._open_picker)
        return fc

    def _append_card(self, card):
        fc = self._make_card_ui(card)
        self._cards_ui[card.norm_name] = fc
        self.cards_box.addWidget(fc)
        self._update_summary()

    def _refresh_card(self, card):
        """卡片数据变化（搜索批次回填/勾选回填/状态翻转）后整卡重建，保持位置。"""
        old = self._cards_ui.get(card.norm_name)
        idx = self.cards_box.indexOf(old) if old is not None else -1
        if old is not None:
            self.cards_box.removeWidget(old)
            old.deleteLater()
        fc = self._make_card_ui(card)
        self._cards_ui[card.norm_name] = fc
        if idx >= 0:
            self.cards_box.insertWidget(idx, fc)
        else:
            self.cards_box.addWidget(fc)
        self._update_summary()

    def _refresh_all_cards(self):
        for card in list(self.vm.cards):
            if card.norm_name in self._cards_ui:
                self._refresh_card(card)

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
        # 预览文字变化：整卡重建（tile 缓存键含文字，重渲走 LRU 命中）
        self._preview_debounce.start()

    def _card(self, norm):
        return next((c for c in self.vm.cards if c.norm_name == norm), None)

    # ---- 搜索 ----
    def _cancel_for(self, norm) -> dict:
        return self._cancels.setdefault(norm, {"cancelled": False})

    def _continue_search(self, norm):
        card = self._card(norm)
        if card is None or card.searching:
            return
        if not card.n_chars:          # 0 字卡无可比字型：不给搜索（UI 已藏入口）
            return
        if card.searcher is not None and card.search_state == "paused":
            card.searching = True
            self._refresh_card(card)
            self.worker.submit(f"pull:{norm}", _pull_job, card.searcher,
                               self._cancel_for(norm))   # 续用：累计 hit_count 语义
            return
        # 新搜索器：相似度基准由 UI 线程算好传入（worker 线程无 vm 引用）；
        # 门槛取 card.min_sim（vm 与在用搜索器保持同步），非设置页原值；
        # 覆盖率/大小区间此刻从设置页现读（改设置不影响在跑的搜索器）
        card.searching = True
        self._refresh_card(card)
        self.worker.submit(f"newsearcher:{norm}", _new_searcher_job,
                           vm_target(self.vm, card),
                           self.vm.chars_by_norm.get(norm, set()),
                           float(self.settings.get("cov_threshold")),
                           int(self.settings.get("n_pause")),
                           float(card.min_sim or self.settings.get("min_sim")),
                           (float(self.settings.get("size_min")),
                            float(self.settings.get("size_max"))),
                           self._direction)

    def _relax_search(self, norm):
        card = self._card(norm)
        if card is None or self.vm.relax_min_sim(card) is None:
            return
        self._continue_search(norm)      # 新门槛建新搜索器（hit 计数清零）

    def _open_picker(self, norm):
        card = self._card(norm)
        if card is None:
            return
        dlg = FontPickerDialog(self.settings, self.worker,
                               list(self.vm.ent_by_fp.values()),
                               chars=self.vm.chars_by_norm.get(norm),
                               direction=self._direction, parent=self)
        if dlg.exec() and dlg.selected is not None:
            self.vm.attach_pick(card, dlg.selected)
            self._refresh_card(card)     # pick 可能新增选项行，整卡重建回填
            self._update_summary()

    # ---- 应用 ----
    def _apply(self):
        if getattr(self, "_apply_dispatching", False):
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
        self._update_summary()             # 按当前状态恢复按钮可用性

    def _update_summary(self):
        kinds = {"keep": 0, "twin": 0, "replace": 0, "missing": 0}
        for c in self.vm.cards:
            if c.kind in kinds:
                kinds[c.kind] += 1
        self.summary.set_counts(kinds)
        n, total = self.vm.replacement_counts()
        self.replace_label.setText(f"将替换 {n} / {total} 种字体")
        self.btn_apply.setEnabled(bool(self.vm.checked_parses())
                                  and bool(self.vm.cards)
                                  and (self.sw_save.isChecked()
                                       or self.sw_overwrite.isChecked()))
        active = any(c.searching for c in self.vm.cards)
        if active != self._search_active:
            self._search_active = active
            self.searchActiveChanged.emit(active)

    # ---- 开关/联动 ----
    def _on_save_toggle(self, checked):
        if checked:                       # 另存 / 覆盖互斥：开一个关另一个
            self.sw_overwrite.blockSignals(True)
            self.sw_overwrite.setChecked(False)
            self.sw_overwrite.blockSignals(False)
        self.settings.set("overwrite_default", False)
        self._update_summary()            # 全关时禁用应用条（spec §7）

    def _on_overwrite_toggle(self, checked):
        if checked:
            self.sw_save.blockSignals(True)
            self.sw_save.setChecked(False)
            self.sw_save.blockSignals(False)
        self.settings.set("overwrite_default", checked)
        self._update_summary()


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
        return (f"保留 {c['keep']} / 孪生对应 {c['twin']} / "
                f"需替换 {c['replace']} / 库中没有 {c['missing']}")

    def _rebuild(self):
        while self._h.count():
            it = self._h.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        c = self._counts
        for text, mk in ((f"保留 {c['keep']}", InfoBadge.success),
                         (f"孪生对应 {c['twin']}", InfoBadge.info),
                         (f"需替换 {c['replace']}", InfoBadge.warning),
                         (f"库中没有 {c['missing']}", InfoBadge.error)):
            self._h.addWidget(mk(text))
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


def _apply_job(ctx, path, mapping, suffix, overwrite):
    return apply_to_file(path, mapping, suffix, overwrite)
