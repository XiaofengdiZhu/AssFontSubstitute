from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, ScrollArea,
                            SearchLineEdit)

from ass_font_substitute.gui.jobs import ensure_library, lib_key
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.widgets.font_list import FontListWidget


class BrowsePage(ScrollArea):
    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("browsePage")
        self.pvm = PreviewVM()

        self.view = _inner_view(self)
        v = self.view.vbox
        self.preview_edit = SearchLineEdit()
        self.preview_edit.setPlaceholderText("预览文字（留空显示字体名）")
        v.addWidget(self.preview_edit)

        filters = QHBoxLayout()
        self.name_edit = SearchLineEdit()
        self.name_edit.setPlaceholderText("搜索字体名")
        self.cb_cat, self.cb_script, self.cb_weight = ComboBox(), ComboBox(), ComboBox()
        for cb, ph in ((self.cb_cat, "分类"), (self.cb_script, "简繁"),
                       (self.cb_weight, "字重")):
            cb.setPlaceholderText(ph)
            cb.currentIndexChanged.connect(self._apply_filters)
        self.count_label = CaptionLabel("0")
        for w in (self.name_edit, self.cb_cat, self.cb_script, self.cb_weight,
                  self.count_label):
            filters.addWidget(w)
        v.addLayout(filters)

        self.hint = BodyLabel("先到「字体库管理」添加字体目录")
        v.addWidget(self.hint)
        self.list = FontListWidget(worker, self.pvm,
                                   lambda: self.preview_edit.text())
        self.list.count_changed.connect(
            lambda n: self.count_label.setText(f"{n} 款字体"))
        v.addWidget(self.list)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._reload_list)
        self.name_edit.textChanged.connect(lambda _: self._debounce.start())
        self.preview_edit.textChanged.connect(self._on_preview_text)
        self.worker.result.connect(self._on_result)
        self._lib_key: tuple | None = None  # 上次装载对应 快照失效键（jobs.lib_key）
        self.reload_library()

    def showEvent(self, e):
        # 页面缓存不永久化：再次显示时键变（目录增删 / 索引更新）即重拉
        super().showEvent(e)
        if lib_key(list(self.settings.get("font_dirs"))) != self._lib_key:
            self.reload_library()

    def reload_library(self):
        dirs = tuple(self.settings.get("font_dirs"))
        if not dirs:
            # 目录清空必须真清数据：只显 hint 会让旧字体列表留在提示下面
            self.pvm.set_entities([])
            self._reload_list()
            return
        self.worker.submit("blib", _lib_job, list(dirs))

    def on_library_changed(self):
        """字体库管理页删目录/索引更新后的广播：立即重拉，不等切页。"""
        self.reload_library()

    def _on_result(self, tag, payload):
        if tag != "blib":
            return
        self.hint.hide()
        self._lib_key = lib_key(list(self.settings.get("font_dirs")))
        snap = payload
        self.pvm.set_entities(snap.lib.entities())
        self._fill_filters()
        self._reload_list()

    def _fill_filters(self):
        for cb, items in ((self.cb_cat, ["全部"] + self.pvm.categories()),
                          (self.cb_script, ["全部"] + self.pvm.scripts()),
                          (self.cb_weight, ["全部"] + self.pvm.weight_buckets())):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems([str(x) for x in items])
            cb.blockSignals(False)

    def _apply_filters(self):
        self.pvm.category = _pick(self.cb_cat)
        self.pvm.script = _pick(self.cb_script)
        wt = _pick(self.cb_weight)
        self.pvm.weight = int(wt) if wt else None
        self._reload_list()

    def _on_preview_text(self, _):
        # 预览文字变化：整页 tile 缓存键含文字，无需失效，行内异步重渲
        self._debounce.start()

    def _reload_list(self):
        self.pvm.name_query = self.name_edit.text()
        rows = self.pvm.filtered()
        self.hint.setVisible(not rows and not self.pvm.entities)
        self.list.set_rows(rows)


def _pick(cb: ComboBox) -> str | None:
    t = cb.currentText()
    return None if (not t or t == "全部") else t


def _lib_job(ctx, dirs):
    return ensure_library(ctx, dirs)


def _inner_view(page):
    from PySide6.QtWidgets import QWidget
    w = QWidget(page)
    w.vbox = QVBoxLayout(w)
    page.setWidget(w)
    page.setWidgetResizable(True)
    page.enableTransparentBackground()   # 默认画调色板底，深色主题下亮块
    return w
