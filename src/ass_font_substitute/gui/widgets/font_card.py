from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox,
                            IconWidget, IndeterminateProgressRing, InfoBadge,
                            StrongBodyLabel, TransparentPushButton)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.subs_vm import KEEP, Card
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

# 状态图标/徽章配色（亮/暗各一份，线框配色）
_KIND_STYLE = {
    "keep":    ("ACCEPT", "#0f7b0f", "#6ccb5f"),
    "twin":    ("ACCEPT", "#0f7b0f", "#6ccb5f"),
    "replace": ("INFO",   "#9d5d00", "#ffce5c"),
    "missing": ("CANCEL", "#c42b1c", "#ff99a4"),
    "zero":    (None,     "#757575", "#9a9a9a"),
}
_STATUS_TEXT = {"keep": "库中有 · 覆盖足够", "twin": "找到孪生对应的简体字体",
                "replace": "需替换", "missing": "库中没有",
                "zero": "保留（无对白用字）"}
_BADGE_MAKER = {"keep": InfoBadge.success, "twin": InfoBadge.info,
                "replace": InfoBadge.warning, "missing": InfoBadge.error}

_ROW_QSS = """
QFrame#optRow{ background: transparent; border: 1px solid rgba(128,128,128,0.35);
    border-radius: 6px; }
QFrame#optRow:hover{ background: rgba(128,128,128,0.08); }
QFrame#optRow[sel="true"]{ border: 1px solid #005fb8;
    background: rgba(0,95,184,0.06); }
QFrame#tofu{ border: 1px dashed rgba(198,43,28,0.55); border-radius: 6px; }
"""


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else ""


class FontCard(CardWidget):
    """单张字体卡（线框重排）：状态图标+名称+用量+右侧状态徽章；缺字虚线框；
    选项逐行成带边框行（勾选框|名称+标签+指标|预览条），选中行主题色描边；
    库中没有卡的「从字体库选择」为行式入口；底部随搜索状态变化。
    纯展示：勾选与搜索动作经信号上抛页面，由页面驱动 vm（不触碰 vm）。"""

    option_checked = Signal(str, str)      # (norm_name, key)
    continue_clicked = Signal(str)
    relax_clicked = Signal(str)
    pick_clicked = Signal(str)

    def __init__(self, card: Card, worker, pvm, preview_text_fn, parent=None):
        super().__init__(parent)
        self.card = card
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn
        self.setStyleSheet(_ROW_QSS)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(self._build_head())

        if card.kind == "replace" and card.missing_preview:
            v.addWidget(self._build_missing_strip())

        v.addWidget(self._build_options())

        foot = self._build_foot()
        if foot is not None:
            v.addLayout(foot)

    # ---- 头部 ----
    def _ekind(self) -> str:
        """展示用 kind：0 字的 keep（空库/无对白用字）按零用字态展示，
        不冒充「库中有 · 覆盖足够」。"""
        return ("zero" if self.card.kind == "keep" and not self.card.n_chars
                else self.card.kind)

    def _build_head(self) -> QWidget:
        head = QWidget(self)
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        kind = self._ekind()
        name, light, dark = _KIND_STYLE.get(kind, _KIND_STYLE["zero"])
        if name is None:
            dot = BodyLabel("●")
            dot.setStyleSheet(f"color: {light};")
            h.addWidget(dot)
        else:
            h.addWidget(IconWidget(getattr(FIF, name).colored(
                QColor(light), QColor(dark))))
        h.addWidget(StrongBodyLabel(self.card.raw_name))
        h.addWidget(CaptionLabel(self.card.header))
        h.addStretch(1)
        if kind == "zero":
            h.addWidget(InfoBadge.custom(_STATUS_TEXT["zero"],
                                         QColor("#f0f0f0"), QColor("#3f3f3f")))
        else:
            h.addWidget(_BADGE_MAKER[kind](_STATUS_TEXT[kind]))
        return head

    def _build_missing_strip(self) -> QFrame:
        box = QFrame(self)
        box.setObjectName("tofu")
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 6, 10, 6)
        lab = CaptionLabel(
            "缺字：" + " ".join(self.card.missing_preview)
            + (f" 等 {self.card.n_chars} 字中 {len(self.card.missing_preview)}+ 字"
               if len(self.card.missing_preview) >= 12 else ""))
        lab.setWordWrap(True)
        h.addWidget(lab, 1)
        return box

    # ---- 选项行 ----
    def _build_options(self) -> QWidget:
        wrap = QWidget(self)
        ol = QVBoxLayout(wrap)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(6)
        self._boxes: dict[str, CheckBox] = {}
        for o in self.card.options:
            ol.addWidget(self._build_option_row(o))
        if self.card.kind == "missing":
            ol.addWidget(self._build_picker_row())
        return wrap

    def _build_option_row(self, o) -> QFrame:
        row = QFrame(self)
        row.setObjectName("optRow")
        row.setProperty("sel", o.key == self.card.checked_key)
        row.setStyleSheet(_ROW_QSS)          # property 变化后需重 Applying
        g = QHBoxLayout(row)
        g.setContentsMargins(10, 7, 10, 7)
        g.setSpacing(10)

        cb = CheckBox()
        cb.setChecked(o.key == self.card.checked_key)
        cb.setEnabled(o.key != self.card.checked_key)   # 至少勾一项：点已选项无效
        cb.toggled.connect(lambda on, k=o.key:
                           self.option_checked.emit(self.card.norm_name, k)
                           if on else None)
        self._boxes[o.key] = cb
        g.addWidget(cb, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(BodyLabel(o.display_name))
        for b in [*o.badges, *o.meta]:
            if b in ("孪生对应", "上次使用"):        # 强调标签用主题徽章
                name_row.addWidget(InfoBadge.info(b))
            else:                                   # 原字体/不改动/简繁/字重等：中性灰
                name_row.addWidget(InfoBadge.custom(b, QColor("#eceff1"),
                                                    QColor("#3f3f3f")))
        name_row.addStretch(1)
        text.addLayout(name_row)
        text.addWidget(CaptionLabel(self._metrics_text(o)))
        g.addLayout(text, 1)

        if o.entity is not None:
            bar = PreviewBar(self._worker, self._pvm, o.entity,
                             self._preview_text_fn() or o.display_name,
                             f"card:{self.card.norm_name}:{o.key}")
            bar.setFixedHeight(28)
            g.addWidget(bar, 0, Qt.AlignVCenter)

        row.mousePressEvent = lambda e, k=o.key: (
            self.option_checked.emit(self.card.norm_name, k)
            if k != self.card.checked_key else None)
        return row

    def _build_picker_row(self) -> QFrame:
        row = QFrame(self)
        row.setObjectName("optRow")
        row.setStyleSheet(_ROW_QSS)
        g = QHBoxLayout(row)
        g.setContentsMargins(10, 7, 10, 7)
        g.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(BodyLabel("从字体库选择"))
        text.addWidget(CaptionLabel("点击从字体库挑选任意字体"))
        g.addLayout(text, 1)
        chev = IconWidget(FIF.CHEVRON_RIGHT.colored(QColor("#8a8a8a"),
                                                    QColor("#9a9a9a")))
        g.addWidget(chev, 0, Qt.AlignVCenter)
        row.mousePressEvent = lambda e: self.pick_clicked.emit(self.card.norm_name)
        row.setCursor(Qt.PointingHandCursor)
        return row

    def _metrics_text(self, o) -> str:
        if o.key == KEEP:
            return ("播放时由播放器自行回退处理" if self.card.kind == "missing"
                    else "无对白用字，无需处理")
        if "原字体" in o.badges:
            n_missing = round(self.card.n_chars * (1 - (o.coverage or 0)))
            if n_missing > 0:
                return f"覆盖 {_pct(o.coverage)} · 有 {n_missing} 字未覆盖"
            return f"覆盖 {_pct(o.coverage)} · 无需替换"
        parts = [p for p in (
            f"相似 {_pct(o.similarity)}" if o.similarity is not None else None,
            f"覆盖 {_pct(o.coverage)}" if o.coverage is not None else None,
            f"相对大小 {_pct(o.size_ratio)}" if o.size_ratio is not None else None,
        ) if p]
        if "孪生对应" in o.badges:
            parts.append("与原字体同款设计")
        return " · ".join(parts) if parts else "已选"

    # ---- 底部随状态 ----
    def _build_foot(self) -> QHBoxLayout | None:
        card = self.card
        ekind = self._ekind()
        foot = QHBoxLayout()
        foot.setSpacing(6)
        if card.searching:
            ring = IndeterminateProgressRing()
            ring.setFixedSize(18, 18)
            foot.addWidget(ring)
            foot.addWidget(CaptionLabel("正在搜索相似字体…"))
        elif card.search_state == "paused":
            n = sum(1 for o in card.options if o.source == "search")
            foot.addWidget(CaptionLabel(f"已找到 {n} 个相似字体，暂停搜索。"))
            more = TransparentPushButton("继续找更多")
            more.clicked.connect(
                lambda: self.continue_clicked.emit(self.card.norm_name))
            foot.addWidget(more)
        elif card.search_state == "exhausted" and ekind not in ("zero",
                                                               "missing"):
            foot.addWidget(CaptionLabel(
                "无更多相似字体"
                + (f"（当前最低相似率 {_pct(card.min_sim)}）" if card.min_sim else "")))
            relax = TransparentPushButton("放宽门槛重算")
            relax.setEnabled(card.min_sim is None or card.min_sim > 0.50)
            relax.clicked.connect(
                lambda: self.relax_clicked.emit(self.card.norm_name))
            foot.addWidget(relax)
        elif card.search_state == "target_error":
            foot.addWidget(CaptionLabel("原字体无法渲染，无法比较相似度"))
        elif (ekind in ("replace", "twin", "keep") and card.n_chars > 0):
            # 0 字卡无可比字型：不给搜索入口（空池/全池放行都无意义）
            cont = TransparentPushButton("继续找其他相似字体")
            cont.clicked.connect(
                lambda: self.continue_clicked.emit(self.card.norm_name))
            foot.addWidget(cont)
        else:
            return None
        foot.addStretch(1)
        return foot

    def set_checked(self, key: str):
        """vm 勾选变化后回写 UI（含默认勾选链/对话框回填）。"""
        self.card.checked_key = key
        for k, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(k == key)
            cb.setEnabled(k != key)      # 已选项禁点，保证至少勾一项
            cb.blockSignals(False)
        self._resel_rows()

    def _resel_rows(self):
        """整卡重建之外的轻量重选：仅同步行描边（勾选切换不重建整卡）。"""
        for o in self.card.options:
            row = self._boxes[o.key].parentWidget()
            if isinstance(row, QFrame) and row.objectName() == "optRow":
                row.setProperty("sel", o.key == self.card.checked_key)
                row.style().unpolish(row)
                row.style().polish(row)
