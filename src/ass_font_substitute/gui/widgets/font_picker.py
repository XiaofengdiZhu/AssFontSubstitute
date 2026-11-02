from __future__ import annotations

import itertools

from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel
from qfluentwidgets import MessageBoxBase
from qfluentwidgets import SubtitleLabel

from ass_font_substitute.gui.jobs import make_searcher
from ass_font_substitute.gui.jobs import pull_chunk
from ass_font_substitute.gui.name_lang import pick_display_name
from ass_font_substitute.gui.name_lang import ui_lang
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.widgets.font_filter import FontFilterBar
from ass_font_substitute.gui.widgets.font_filter import PreviewTools
from ass_font_substitute.gui.widgets.font_list import FontListWidget

# pickreco 回执 tag 自增序号：worker.result 是单通道广播，tag 固定
# "pickreco" 时前一弹窗已取消任务的迟到回执会冒充新弹窗的回执，
# 吃掉其单次订阅 →「推荐」区静默永不出现（审查三轮 P1 offscreen 实锤）。
# 不用 id(self)：WA_DeleteOnClose 关即删后 CPython 可能复用地址，新弹窗
# 同 id 则旧回执照样冒充——自增序号全局唯一无此窗口
_PICKRECO_SEQ = itertools.count()


class FontPickerDialog(MessageBoxBase):
    """「从字体库选择」：照字体库预览页重做（2026-10-29）——页头预览
    工具组（强制等宽/预览文字/覆盖筛选）与筛选行用 font_filter 同款共享
    控件，行预览随预览文字渲染（留空退字体名）；chars 给定时顶部加
    「推荐」区（target=None 全池覆盖率门槛 + 探针粗排，纯元数据一次搜穿，
    无渲染）。"""

    def __init__(self, settings, worker, entities, chars=None,
                 direction="t2s", parent=None):
        super().__init__(parent)
        self.worker = worker            # _on_reco 取消订阅需要（brief 漏存）
        self.selected = None
        # 重做后 tools/PreviewInput 各挂一条 settings.valueChanged 广播：
        # exec 后不销毁则死弹窗随打开次数累积（subs_page._open_picker 复用
        # 实例），关即删（font_detail 同款；淡出动画完成后才 deleteLater
        # 不截断动画，exec 返回后读 selected 等 Python 属性不受影响）
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._settings = settings
        self.pvm = PreviewVM(settings)
        self.pvm.set_entities(entities)

        self.titleLabel = SubtitleLabel(self.tr("从字体库选择"))

        self.rec_title = SubtitleLabel(self.tr("推荐（覆盖达标）"))
        # 选择对话框行名不可划选（用户拍板「除外」2026-10-29）：名是
        # picker 行宽最大的点击目标，可选化会吃掉单击选中/双击接受
        self.rec_list = FontListWidget(worker, self.pvm, self._preview,
                                       name_selectable=False,
                                       face_controls=False)
        self.rec_list.entity_picked.connect(self._pick)
        self.rec_list.detail_requested.connect(self._accept_pick)
        self.rec_list.setFixedHeight(120)
        self.rec_title.hide()
        self.rec_list.hide()

        # 预览工具组 + 筛选行：字体库预览页同款共享控件（font_filter）；
        # 文字/搜索变化经 300ms 防抖重载（照预览页口径），开关与下拉即时
        self.tools = PreviewTools(settings, self.pvm)
        # 字重筛选/行内字重斜体切换都不建（拍板 2026-10-30：工具不改
        # 字重斜体，行面由 sel_key 定——非斜体+400 最近）
        self.bar = FontFilterBar(self.pvm, weight_filter=False)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._reload_all)
        self.tools.previewChanged.connect(lambda _: self._debounce.start())
        self.tools.covChanged.connect(lambda _on: self._reload_all())
        self.tools.monoChanged.connect(lambda _on: self._reload_all())
        self.bar.name_edit.textChanged.connect(
            lambda _: self._debounce.start())
        self.bar.filtersChanged.connect(self._reload_all)

        self.all_list = FontListWidget(worker, self.pvm, self._preview,
                                       name_selectable=False,
                                       face_controls=False)
        self.all_list.entity_picked.connect(self._pick)
        self.all_list.detail_requested.connect(self._accept_pick)
        self.all_list.setMinimumHeight(120)
        self.all_list.count_changed.connect(self.bar.set_count)

        self.hint = CaptionLabel(self.tr("未选择"))
        self.empty_label = CaptionLabel(self.tr(
            "字体库为空：先到「字体库」页添加目录并建立索引，再回到这里选择。"))
        self.empty_label.setWordWrap(True)
        if not self.pvm.entities:
            # 空库态列表全隐；空库提示保持默认可见即可——勿在挂父前 show()，
            # 无父 show 即顶层窗口（AGENTS.md UI 约定 / 14f3e50 同类教训，
            # 审查二轮 P1 事件 Spy 实锤顶层 Show）
            self.tools.hide()
            self.bar.hide()
            self.all_list.hide()
            self.hint.hide()
        else:
            self.empty_label.hide()

        body = QWidget()
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(12)
        body_v.addWidget(self.titleLabel)
        body_v.addWidget(self.rec_title)
        body_v.addWidget(self.rec_list)
        body_v.addWidget(self.tools)
        body_v.addWidget(self.bar)
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
        self.yesButton.setText(self.tr("选定"))
        self.yesButton.setEnabled(False)
        self.cancelButton.setText(self.tr("取消"))

        self._reload_all()

        # 关弹窗（取消/选定/ESC/宿主窗口关闭，均经 done→finished）即撤
        # 已物化行在途渲染单 + 置推荐任务取消标志：关闭后回传没人接，
        # 废任务塞死工作者串行队列，后续手选评分排其后秒级延迟（审查
        # 三轮 P2；font_detail._on_finished 同款口径）
        self.finished.connect(self._on_finished)

        if chars:
            self._reco_tag = f"pickreco:{next(_PICKRECO_SEQ)}"
            worker.result.connect(self._on_reco)
            self._reco_cancel = {"cancelled": False}
            worker.submit(self._reco_tag, _reco_job, chars,
                          float(settings.get("cov_threshold")), direction,
                          self._reco_cancel)
        # 复用字体库预览的文字；对话框自己不发 blib（实体由调用方传入）

    def _on_finished(self, _result):
        self._closed = True
        self.all_list.cancel_pending()
        self.rec_list.cancel_pending()
        if self._reco_cancel is not None:
            self._reco_cancel["cancelled"] = True

    def _preview(self):
        return self.tools.text()

    def _reload_all(self):
        self.pvm.name_query = self.bar.name_edit.text()
        rows = self.pvm.filtered()
        shown = {f.fingerprint for r in rows for f in r.matching}
        self.all_list.set_rows(rows)
        # 推荐区不合并：逐 face 单行（as_rows），但只显示行通过筛选的 face
        self.rec_list.set_rows(self.pvm.as_rows(
            [e for e in self._reco or [] if e.fingerprint in shown]))

    def _on_reco(self, tag, payload):
        if self._closed:
            return
        if tag != self._reco_tag:
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
        self.hint.setText(self.tr("已选：{0}").format(
            pick_display_name(e, ui_lang(self._settings))))
        self.yesButton.setEnabled(True)

    def _accept_pick(self, e):
        # 双击=快捷接受：选定并关窗（详情入口仅浏览页提供，此处不发）。
        # 绕过 validate()——MessageBoxBase 默认恒 True 未覆写；将来覆写
        # validate 时须让双击路径同走
        self._pick(e)
        self.accept()

    _reco = None
    _reco_tag = None   # ctor chars 分支赋值；未提交时 _on_reco 不可达，
                       # None 兜底比对恒 False 防 AttributeError
    _reco_cancel = None  # 同上：未提交推荐任务时 _on_finished 判空跳过
    _closed = False     # finished 置位：qfw 淡出期回执仍可能派发，
                        # _on_reco 早退防补物化/补派渲染单（gui 审查 P2）


def _reco_job(ctx, chars, cov_threshold, direction, cancel=None):
    """target=None：无暂停语义一次搜穿整池（元数据 + 覆盖率，~15µs/候选）。
    cancel 与 pull 任务同款标志字典：弹窗已关则提前退出，不白算。"""
    s = make_searcher(ctx, target=None, chars=set(chars),
                      threshold=cov_threshold, n_pause=10 ** 9,
                      min_sim=0.0, size_range=(0.0, 10.0), direction=direction)
    # 分片推进到终态（60cd763 起 pull_chunk 无 chunk 参数，一片至多
    # EVAL_SLICE 次对比即回执）；每片间检查取消标志，弹窗已关则提前退
    cands = []
    state = "running"
    while state == "running":
        batch, state = pull_chunk(s, cancel=cancel)
        cands += batch
    if state == "cancelled":
        return None                    # 跳过 fp2e 全池倒排的构建
    fp2e = {e.fingerprint: e for e in ctx.lib.entities()}
    return [fp2e[c.fingerprint] for c in cands]
