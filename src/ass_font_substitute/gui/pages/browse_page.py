from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from qfluentwidgets import BodyLabel
from qfluentwidgets import ScrollArea
from qfluentwidgets import setFont

from ass_font_substitute.gui.jobs import ensure_library
from ass_font_substitute.gui.jobs import lib_key
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog
from ass_font_substitute.gui.widgets.font_filter import FontFilterBar
from ass_font_substitute.gui.widgets.font_filter import PreviewTools
from ass_font_substitute.gui.widgets.font_list import FontListWidget


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

        # 页头：标题 + 预览工具组（与「从字体库选择」对话框共享控件，
        # font_filter 一处口径）——预览只改渲染不改清单，搜索/筛选才收
        # 列表。tools 属性别名留给 smoke/测试直引
        head = QHBoxLayout()
        head.setSpacing(8)
        self.page_title = BodyLabel("字体库预览")
        setFont(self.page_title, 20)   # qfw setFont：20px Normal 字重不变
        head.addWidget(self.page_title)
        head.addStretch(1)
        self.tools = PreviewTools(settings, self.pvm)
        self.sw_mono = self.tools.sw_mono
        self.preview_edit = self.tools.preview_edit
        self.btn_cov = self.tools.btn_cov
        head.addWidget(self.tools, 0, Qt.AlignVCenter)
        v.addLayout(head)

        # 筛选行（共享控件）：搜索撑满剩余（封顶），下拉带标签（裸「全部」
        # 看不出维度），计数贴行尾——筛选结果的规模随筛选变化，语义归筛选行
        self.bar = FontFilterBar(self.pvm)
        self.name_edit = self.bar.name_edit
        self.count_label = self.bar.count_label
        v.addWidget(self.bar)

        self.hint = BodyLabel("先到「字体库管理」添加字体目录")
        v.addWidget(self.hint)
        self.list = FontListWidget(worker, self.pvm,
                                   lambda: self.preview_edit.text())
        self.list.count_changed.connect(self.bar.set_count)
        self.list.detail_requested.connect(self._open_detail)
        v.addWidget(self.list)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._reload_list)
        # 搜索/预览文字防抖重载；下拉与两个开关即时（共享控件发信号，
        # require_text/preview_mono 已由其同步）
        self.bar.name_edit.textChanged.connect(
            lambda _: self._debounce.start())
        self.bar.filtersChanged.connect(self._reload_list)
        self.tools.previewChanged.connect(lambda _: self._debounce.start())
        self.tools.covChanged.connect(lambda _on: self._reload_list())
        self.tools.monoChanged.connect(lambda _on: self._reload_list())
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
        self.bar.fill()
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
