from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel
from qfluentwidgets import ComboBox
from qfluentwidgets import MessageBoxBase
from qfluentwidgets import SearchLineEdit
from qfluentwidgets import SubtitleLabel

from ass_font_substitute.gui.jobs import make_searcher
from ass_font_substitute.gui.jobs import pull_chunk
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

        self.rec_title = SubtitleLabel("推荐（覆盖达标）")
        self.rec_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.rec_list.entity_picked.connect(self._pick)
        self.rec_list.setFixedHeight(120)
        self.rec_title.hide()
        self.rec_list.hide()

        all_title = SubtitleLabel("全部字体")
        self.name_edit = SearchLineEdit()
        self.name_edit.setPlaceholderText("搜索字体名")
        self.cb_cat, self.cb_script, self.cb_weight = ComboBox(), ComboBox(), ComboBox()
        for cb, ph in ((self.cb_cat, "分类"), (self.cb_script, "简繁"),
                       (self.cb_weight, "字重")):
            cb.setPlaceholderText(ph)
            cb.currentIndexChanged.connect(self._apply_filters)
        filters = QHBoxLayout()
        for w in (self.name_edit, self.cb_cat, self.cb_script, self.cb_weight):
            filters.addWidget(w)
        self.filter_bar = QWidget()
        self.filter_bar.setLayout(filters)

        self.all_list = FontListWidget(worker, self.pvm, lambda: self._preview())
        self.all_list.entity_picked.connect(self._pick)
        self.all_list.setMinimumHeight(120)

        self.hint = CaptionLabel("未选择")
        self.empty_label = CaptionLabel(
            "字体库为空：先到「字体库」页添加目录并建立索引，再回到这里选择。")
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        if not self.pvm.entities:
            all_title.hide()
            self.filter_bar.hide()
            self.all_list.hide()
            self.hint.hide()
            self.empty_label.show()

        body = QWidget()
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(12)
        body_v.addWidget(self.titleLabel)
        body_v.addWidget(self.rec_title)
        body_v.addWidget(self.rec_list)
        body_v.addWidget(all_title)
        body_v.addWidget(self.filter_bar)
        body_v.addWidget(self.all_list, 1)
        body_v.addWidget(self.empty_label)
        body_v.addWidget(self.hint)
        if not self.pvm.entities:
            # 空库态列表全隐：不给 stretch 的话剩余高度会摊到各标签上
            body_v.addStretch(1)
        # 不套外层滚动区：all_list 自滚（细条随主题），外层再滚会出右缘双条
        self.viewLayout.addWidget(body, 1)

        # 按钮区钉在 widget 底部：高度封在窗口内，列表超出交给 all_list 自滚；
        # 空库态只有标题+提示，收到 280 免得撑满整窗。dialog 尺寸 = 所在页面
        if self.pvm.entities:
            h = max(min(self.height() - 48, 620), 320)
        else:
            h = max(min(self.height() - 48, 280), 220)
        self.widget.setFixedHeight(h)
        self.widget.setFixedWidth(min(720, max(self.width() - 48, 320)))
        self.yesButton.setText("选定")
        self.yesButton.setEnabled(False)
        self.cancelButton.setText("取消")

        self._fill_filters()
        self._reload_all()
        self.name_edit.textChanged.connect(lambda _: self._reload_all())

        if chars:
            worker.result.connect(self._on_reco)
            self._reco_cancel = {"cancelled": False}
            worker.submit("pickreco", _reco_job, chars,
                          float(settings.get("cov_threshold")), direction,
                          self._reco_cancel)
            # 关弹窗（取消/选定/ESC/宿主窗口关闭，均经 done→finished）即
            # 置取消标志：任务在 pull_chunk 每候选处检查，提前退出不白算
            self.finished.connect(lambda _: self._reco_cancel.__setitem__(
                "cancelled", True))
        # 复用字体库预览的文字；对话框自己不发 blib（实体由调用方传入）

    def _preview(self):
        return ""

    def _fill_filters(self):
        for cb, items in ((self.cb_cat, ["全部", *self.pvm.categories()]),
                          (self.cb_script, ["全部", *self.pvm.scripts()]),
                          (self.cb_weight, ["全部", *self.pvm.weight_buckets()])):
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
        rows = {e.fingerprint for e in self.pvm.filtered()}
        self.all_list.set_rows(self.pvm.filtered())
        self.rec_list.set_rows([e for e in self._reco or []
                                if e.fingerprint in rows])

    def _on_reco(self, tag, payload):
        if tag != "pickreco":
            return
        self.worker.result.disconnect(self._on_reco)
        self._reco = payload
        if not payload:                 # 无达标候选或已取消：不出「推荐」空框
            return
        self.rec_title.show()
        self.rec_list.show()
        self._reload_all()

    def _pick(self, e):
        self.selected = e
        self.hint.setText(f"已选：{e.display_name}")
        self.yesButton.setEnabled(True)

    _reco = None


def _reco_job(ctx, chars, cov_threshold, direction, cancel=None):
    """target=None：无暂停语义一次搜穿整池（元数据 + 覆盖率，~15µs/候选）。
    cancel 与 pull 任务同款标志字典：弹窗已关则提前退出，不白算。"""
    s = make_searcher(ctx, target=None, chars=set(chars),
                      threshold=cov_threshold, n_pause=10 ** 9,
                      min_sim=0.0, size_range=(0.0, 10.0), direction=direction)
    cands, state = pull_chunk(s, cancel=cancel, chunk=10 ** 9)
    if state == "cancelled":
        return None                    # 跳过 fp2e 全池倒排的构建
    fp2e = {e.fingerprint: e for e in ctx.lib.entities()}
    return [fp2e[c.fingerprint] for c in cands]
