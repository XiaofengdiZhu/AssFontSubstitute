from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import ScrollArea
from qfluentwidgets import SearchLineEdit
from qfluentwidgets import TransparentToggleToolButton
from qfluentwidgets import setFont

from ass_font_substitute.gui import script_filter
from ass_font_substitute.gui.jobs import ensure_library
from ass_font_substitute.gui.jobs import lib_key
from ass_font_substitute.gui.pages.subs_page import _fld
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.tip import install_tip
from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog
from ass_font_substitute.gui.widgets.font_list import FontListWidget
from ass_font_substitute.gui.widgets.preview_input import PreviewInput
from ass_font_substitute.gui.widgets.wheel_block import WheelBlock


class BrowsePage(ScrollArea):
    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("browsePage")
        self.pvm = PreviewVM(settings)

        self.view = _inner_view(self)
        v = self.view.vbox
        # 间距节奏对齐处理字幕页：页边距 (36,24,36,24)，区块间 12
        v.setContentsMargins(36, 24, 36, 24)
        v.setSpacing(12)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 页头：标题 + 预览文字输入（页级工具，宽度封顶；与下方筛选行
        # 分开——预览只改渲染不改清单，搜索/筛选才收窄列表）
        head = QHBoxLayout()
        head.setSpacing(8)
        self.page_title = BodyLabel("字体库预览")
        setFont(self.page_title, 20)   # qfw setFont：20px Normal 字重不变
        head.addWidget(self.page_title)
        head.addStretch(1)
        # 可键入 + 下拉最近 10 条历史（与字幕编辑页共享，回车记一条）
        self.preview_edit = PreviewInput(settings)
        self.preview_edit.setFixedWidth(340)
        head.addWidget(self.preview_edit, 0, Qt.AlignVCenter)
        # 框外右侧覆盖筛选开关：亮起（qfw toggle 亮起态自动强调色）=
        # 只留 cmap 覆盖全部预览文字的字体；空文字无可筛字符=不筛
        self.btn_cov = TransparentToggleToolButton(FIF.FILTER, self)
        install_tip(self.btn_cov, "只显示包含全部预览文字的字体")
        self.btn_cov.toggled.connect(self._on_cov_toggled)
        head.addWidget(self.btn_cov, 0, Qt.AlignVCenter)
        v.addLayout(head)

        # 筛选行：搜索撑满剩余（封顶），下拉带标签（裸「全部」看不出维度），
        # 计数贴行尾——筛选结果的规模随筛选变化，语义归筛选行
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.name_edit = SearchLineEdit()
        self.name_edit.setPlaceholderText("搜索字体名")
        self.name_edit.setMinimumWidth(200)
        self.name_edit.setMaximumWidth(400)
        self.cb_cat, self.cb_weight = ComboBox(), ComboBox()
        self.cb_script = script_filter.StrikeTailComboBox()
        # 滚动页面扫过下拉会误改筛选——吞滚轮
        self._wheel_block = WheelBlock(self)
        for cb in (self.cb_cat, self.cb_script, self.cb_weight):
            cb.setFixedWidth(104)
            cb.currentIndexChanged.connect(self._apply_filters)
            cb.installEventFilter(self._wheel_block)
        self.count_label = CaptionLabel("0 款字体")
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(_fld("分类", self.cb_cat))
        bar.addWidget(_fld("简繁", self.cb_script))
        bar.addWidget(_fld("字重", self.cb_weight))
        bar.addStretch(1)
        bar.addWidget(self.count_label, 0, Qt.AlignVCenter)
        v.addLayout(bar)

        self.hint = BodyLabel("先到「字体库管理」添加字体目录")
        v.addWidget(self.hint)
        self.list = FontListWidget(worker, self.pvm,
                                   lambda: self.preview_edit.text())
        self.list.count_changed.connect(
            lambda n: self.count_label.setText(f"{n} 款字体"))
        self.list.detail_requested.connect(self._open_detail)
        v.addWidget(self.list)
        # 空库态（_fill_filters 未跑）下拉不空白：预填「全部」（=不筛，
        # 与装载后首项一致）；blockSignals 免 ctor 期误触 _apply_filters
        for cb in (self.cb_cat, self.cb_script, self.cb_weight):
            cb.blockSignals(True)
            cb.addItems(["全部"])
            cb.blockSignals(False)

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
        script_items = ["全部", *script_filter.options(self.pvm)]
        for cb, items in ((self.cb_cat, ["全部", *self.pvm.categories()]),
                          (self.cb_script, script_items),
                          (self.cb_weight, ["全部", *self.pvm.weight_buckets()])):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems([str(x) for x in items])
            cb.blockSignals(False)

    def _apply_filters(self):
        self.pvm.category = _pick(self.cb_cat)
        self.pvm.script = script_filter.pick(self.cb_script)
        wt = _pick(self.cb_weight)
        self.pvm.weight = int(wt) if wt else None
        self._reload_list()

    def _on_preview_text(self, _):
        # 预览文字变化：整页 tile 缓存键含文字，无需失效，行内异步重渲
        if self.btn_cov.isChecked():
            self.pvm.require_text = self.preview_edit.text()
        self._debounce.start()

    def _on_cov_toggled(self, on: bool):
        self.pvm.require_text = self.preview_edit.text() if on else None
        self._reload_list()

    def reload_names(self):
        """界面语言变更：行展示名按语言链重挑（行模型即建即显，重建
        列表即可）。设置页 valueChanged("language") → app 转发。"""
        self._reload_list()

    def _reload_list(self):
        self.pvm.name_query = self.name_edit.text()
        rows = self.pvm.filtered()
        self.hint.setVisible(not rows and not self.pvm.entities)
        self.list.set_rows(rows)

    def _open_detail(self, e):
        # 实体全集现场取 pvm（弹窗开着时后台重拉会整体换列表——弹窗是
        # 快照视图，可接受；基嵌套弹窗共用同一份 entities 引用）
        FontDetailDialog(e, self.pvm.entities, self.worker, self.pvm,
                         settings=self.settings, parent=self).exec()


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
