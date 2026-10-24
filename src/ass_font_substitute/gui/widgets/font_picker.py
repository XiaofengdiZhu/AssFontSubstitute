from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import (CaptionLabel, ComboBox, MessageBoxBase,
                            SearchLineEdit, SubtitleLabel)

from ass_font_substitute.gui.jobs import make_searcher, pull_chunk
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.widgets.font_list import FontListWidget


class FontPickerDialog(MessageBoxBase):
    """「从字体库选择」：与字体库预览页同款筛选/列表；
    chars 给定时顶部加「推荐」区（target=None 全池覆盖率门槛 + 探针粗排，
    纯元数据一次搜穿，无渲染）。"""

    def __init__(self, settings, worker, entities, chars=None,
                 direction="t2s", parent=None):
        super().__init__(parent)
        self.worker = worker            # _on_reco 取消订阅需要（brief 漏存）
        self.selected = None
        self.pvm = PreviewVM()
        self.pvm.set_entities(entities)

        self.titleLabel = SubtitleLabel("从字体库选择")
        self.viewLayout.addWidget(self.titleLabel)

        self.rec_title = SubtitleLabel("推荐（覆盖达标）")
        self.rec_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.rec_list.entity_picked.connect(self._pick)
        self.rec_list.setFixedHeight(150)
        self.rec_title.hide()
        self.rec_list.hide()
        self.viewLayout.addWidget(self.rec_title)
        self.viewLayout.addWidget(self.rec_list)

        self.viewLayout.addWidget(SubtitleLabel("全部字体"))
        filters = QHBoxLayout()
        self.name_edit = SearchLineEdit()
        self.name_edit.setPlaceholderText("搜索字体名")
        self.cb_cat, self.cb_script, self.cb_weight = ComboBox(), ComboBox(), ComboBox()
        for cb, ph in ((self.cb_cat, "分类"), (self.cb_script, "简繁"),
                       (self.cb_weight, "字重")):
            cb.setPlaceholderText(ph)
            cb.currentIndexChanged.connect(self._apply_filters)
        for w in (self.name_edit, self.cb_cat, self.cb_script, self.cb_weight):
            filters.addWidget(w)
        self.viewLayout.addLayout(filters)

        self.all_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.all_list.entity_picked.connect(self._pick)
        self.all_list.setMinimumHeight(220)
        self.viewLayout.addWidget(self.all_list, 1)

        self.hint = CaptionLabel("未选择")
        self.viewLayout.addWidget(self.hint)

        self.widget.setMinimumWidth(720)
        # 窗口高度有限（内容按布局自适应），超出会把按钮区顶出窗口外
        win_h = self.window().height() if self.window() is not None else 0
        if win_h > 300:
            self.widget.setMaximumHeight(win_h - 24)
        self.yesButton.setText("选定")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("取消")

        self._fill_filters()
        self._reload_all()
        self.name_edit.textChanged.connect(lambda _: self._reload_all())

        if chars:
            worker.result.connect(self._on_reco)
            worker.submit("pickreco", _reco_job, chars,
                          float(settings.get("cov_threshold")), direction)
        # 复用字体库预览的文字；对话框自己不发 blib（实体由调用方传入）

    def _preview(self):
        return ""

    def _fill_filters(self):
        for cb, items in ((self.cb_cat, ["全部"] + self.pvm.categories()),
                          (self.cb_script, ["全部"] + self.pvm.scripts()),
                          (self.cb_weight, ["全部"] + self.pvm.weight_buckets())):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems([str(x) for x in items])
            cb.blockSignals(False)

    def _apply_filters(self):
        def pick(cb):
            t = cb.currentText()
            return None if (not t or t == "全部") else t
        self.pvm.category, self.pvm.script = pick(self.cb_cat), pick(self.cb_script)
        wt = pick(self.cb_weight)
        self.pvm.weight = int(wt) if wt else None
        self._reload_all()

    def _reload_all(self):
        self.pvm.name_query = self.name_edit.text()
        rows = set(e.fingerprint for e in self.pvm.filtered())
        self.all_list.set_rows(self.pvm.filtered())
        self.rec_list.set_rows([e for e in self._reco or []
                                if e.fingerprint in rows])

    def _on_reco(self, tag, payload):
        if tag != "pickreco":
            return
        self.worker.result.disconnect(self._on_reco)
        self._reco = payload
        self.rec_title.show()
        self.rec_list.show()
        self._reload_all()

    def _pick(self, e):
        self.selected = e
        self.hint.setText(f"已选：{e.display_name}")
        self.yesButton.setEnabled(True)

    _reco = None


def _reco_job(ctx, chars, cov_threshold, direction):
    """target=None：无暂停语义一次搜穿整池（元数据 + 覆盖率，~15µs/候选）。"""
    s = make_searcher(ctx, target=None, chars=set(chars),
                      threshold=cov_threshold, n_pause=10 ** 9,
                      min_sim=0.0, size_range=(0.0, 10.0), direction=direction)
    cands, _ = pull_chunk(s, chunk=10 ** 9)
    fp2e = {e.fingerprint: e for e in ctx.lib.entities()}
    return [fp2e[c.fingerprint] for c in cands]
