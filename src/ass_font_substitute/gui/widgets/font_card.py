from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            IndeterminateProgressRing, InfoBadge, PushButton,
                            StrongBodyLabel, TransparentPushButton)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.subs_vm import Card
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

_KIND_COLOR = {"keep": "#2e7d32", "twin": "#1565c0", "replace": "#c62828",
               "missing": "#6d4c41", "zero": "#757575"}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


class FontCard(CardWidget):
    """单张字体卡：状态徽章 + 三列选项网格（勾选 | 名字·徽章·指标 | 预览条）
    + 入口区（继续搜 / 放宽重算 / 从字体库选择 / 搜索中转圈 / 终态文字）。
    纯展示：勾选与搜索动作经信号上抛页面，由页面驱动 vm（本组件不触碰 vm）。"""

    option_checked = Signal(str, str)      # (norm_name, key)
    continue_clicked = Signal(str)
    relax_clicked = Signal(str)
    pick_clicked = Signal(str)

    def __init__(self, card: Card, worker, pvm, preview_text_fn, parent=None):
        super().__init__(parent)
        self.card = card
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn
        v = QVBoxLayout(self)
        head = QHBoxLayout()
        dot = BodyLabel("●")
        dot.setStyleSheet(f"color: {_KIND_COLOR.get(card.kind, '#333')};")
        head.addWidget(dot)
        head.addWidget(StrongBodyLabel(card.raw_name))
        head.addWidget(CaptionLabel(card.header))
        head.addWidget(InfoBadge.info(card.status_text) if card.kind != "zero"
                       else InfoBadge.custom("保留（无对白用字）", "#757575", "#fff"))
        head.addStretch(1)
        v.addLayout(head)

        if card.kind == "replace" and card.missing_preview:
            miss = CaptionLabel(
                "缺字：" + " ".join(card.missing_preview)
                + (f" 等 {card.n_chars} 字中 {len(card.missing_preview)}+ 字"
                   if len(card.missing_preview) >= 12 else ""))
            miss.setWordWrap(True)
            v.addWidget(miss)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self._boxes: dict[str, CheckBox] = {}
        for row, o in enumerate(card.options):
            r = row * 2
            cb = CheckBox()
            cb.setChecked(o.key == card.checked_key)
            cb.setEnabled(o.key != card.checked_key)   # 至少勾一项：点已选项无效
            cb.toggled.connect(lambda on, k=o.key:
                               self.option_checked.emit(self.card.norm_name, k)
                               if on else None)
            self._boxes[o.key] = cb
            grid.addWidget(cb, r, 0, 2, 1, Qt.AlignTop)

            name_row = QHBoxLayout()
            name_row.addWidget(BodyLabel(o.display_name))
            for b in o.badges:
                if b in ("孪生对应", "上次使用"):       # 强调标签用主题徽章
                    name_row.addWidget(InfoBadge.info(b))
                else:                                   # 原字体/不改动：中性灰
                    name_row.addWidget(InfoBadge.custom(b, "#eceff1", "#37474f"))
            name_row.addStretch(1)
            grid.addLayout(name_row, r, 1)

            metrics = (f"相似 {_pct(o.similarity)} · 覆盖 {_pct(o.coverage)}"
                       f" · 相对大小 {_pct(o.size_ratio)}")
            if o.meta:
                metrics += "  ·  " + "  ".join(o.meta)
            mlabel = CaptionLabel(metrics)
            if o.size_ratio:
                diff = round((o.size_ratio - 1) * 100)
                mlabel.setToolTip(f"比原字体{'大' if diff >= 0 else '小'} {abs(diff)}%")
            grid.addWidget(mlabel, r + 1, 1)

            if o.entity is not None:
                bar = PreviewBar(self._worker, self._pvm, o.entity,
                                 self._preview_text_fn() or o.display_name,
                                 f"card:{self.card.norm_name}:{o.key}")
                bar.setFixedHeight(28)
                grid.addWidget(bar, r, 2, 2, 1)
        v.addLayout(grid)

        foot = QHBoxLayout()
        if card.searching:
            self.ring = IndeterminateProgressRing()
            self.ring.setFixedSize(18, 18)
            foot.addWidget(self.ring)
            foot.addWidget(CaptionLabel("正在搜索相似字体…"))
        elif card.search_state == "exhausted" and card.kind not in ("zero", "missing"):
            foot.addWidget(CaptionLabel(
                "无更多相似字体"
                + (f"（当前最低相似率 {_pct(card.min_sim)}）" if card.min_sim else "")))
            relax = TransparentPushButton("放宽门槛重算")
            relax.setEnabled(card.min_sim is None or card.min_sim > 0.50)
            relax.clicked.connect(lambda: self.relax_clicked.emit(self.card.norm_name))
            foot.addWidget(relax)
        elif card.search_state == "target_error":
            foot.addWidget(CaptionLabel("原字体无法渲染，无法比较相似度"))
        if card.kind in ("replace", "twin", "keep"):
            cont = TransparentPushButton("继续找其他相似字体")
            cont.clicked.connect(lambda: self.continue_clicked.emit(self.card.norm_name))
            foot.addWidget(cont)
        if card.kind in ("missing",):
            pick = PushButton(FIF.SEARCH, "从字体库选择")
            pick.clicked.connect(lambda: self.pick_clicked.emit(self.card.norm_name))
            foot.addWidget(pick)
        foot.addStretch(1)
        v.addLayout(foot)

    def set_checked(self, key: str):
        """vm 勾选变化后回写 UI（含默认勾选链/对话框回填）。"""
        self.card.checked_key = key
        for k, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(k == key)
            cb.setEnabled(k != key)      # 已选项禁点，保证至少勾一项
            cb.blockSignals(False)
