from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, PrimaryPushButton,
                            PushButton, SearchLineEdit, SubtitleLabel)

from ass_font_substitute.gui.jobs import make_searcher, pull_chunk
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.widgets.font_list import FontListWidget


class FontPickerDialog(QDialog):
    """「从字体库选择」：与字体库预览页同款筛选/列表；
    chars 给定时顶部加「推荐」区（target=None 全池覆盖率门槛 + 探针粗排，
    纯元数据一次搜穿，无渲染）。"""

    def __init__(self, settings, worker, entities, chars=None,
                 direction="t2s", parent=None):
        super().__init__(parent)
        self.setWindowTitle("从字体库选择")
        self.resize(720, 640)
        self.worker = worker            # _on_reco 取消订阅需要（brief 漏存）
        self.selected = None
        self.pvm = PreviewVM()
        self.pvm.set_entities(entities)

        v = QVBoxLayout(self)
        self.rec_title = SubtitleLabel("推荐（覆盖达标）")
        self.rec_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.rec_list.entity_picked.connect(self._pick)
        self.rec_title.hide()
        self.rec_list.hide()
        v.addWidget(self.rec_title)
        v.addWidget(self.rec_list)

        v.addWidget(SubtitleLabel("全部字体"))
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
        v.addLayout(filters)

        self.all_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.all_list.entity_picked.connect(self._pick)
        v.addWidget(self.all_list, 1)

        bottom = QHBoxLayout()
        self.hint = CaptionLabel("未选择")
        self.btn_ok = PrimaryPushButton("选定")
        self.btn_ok.setEnabled(False)
        self.btn_cancel = PushButton("取消")
        bottom.addWidget(self.hint, 1)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_ok)
        v.addLayout(bottom)

        self._fill_filters()
        self._reload_all()
        self.name_edit.textChanged.connect(lambda _: self._reload_all())
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

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
        self.btn_ok.setEnabled(True)

    _reco = None


def _reco_job(ctx, chars, cov_threshold, direction):
    """target=None：无暂停语义一次搜穿整池（元数据 + 覆盖率，~15µs/候选）。"""
    s = make_searcher(ctx, target=None, chars=set(chars),
                      threshold=cov_threshold, n_pause=10 ** 9,
                      min_sim=0.0, size_range=(0.0, 10.0), direction=direction)
    cands, _ = pull_chunk(s, chunk=10 ** 9)
    fp2e = {e.fingerprint: e for e in ctx.lib.entities()}
    return [fp2e[c.fingerprint] for c in cands]
