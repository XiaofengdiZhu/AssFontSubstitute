from __future__ import annotations

import shiboken6
from PySide6.QtCore import QRectF
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPainterPath
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QFrame
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import CardWidget
from qfluentwidgets import CheckBox
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentStyleSheet
from qfluentwidgets import IconWidget
from qfluentwidgets import InfoBadge
from qfluentwidgets import StrongBodyLabel
from qfluentwidgets import TransparentPushButton
from qfluentwidgets import qconfig
from qfluentwidgets import setCustomStyleSheet
from qfluentwidgets import themeColor

from ass_font_substitute.gui.subs_vm import KEEP
from ass_font_substitute.gui.subs_vm import Card
from ass_font_substitute.gui.widgets.pill import PillLabel
from ass_font_substitute.gui.widgets.pill import pill
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar


def _pill(text: str) -> PillLabel:
    return pill(text)


_TAG_STYLE = {"原": ("#757575", "#9a9a9a"), "孪": ("#0f7b0f", "#6ccb5f")}


def _char_tag(ch: str) -> QWidget:
    """名前单字圆角框标签（无底色）：亮暗各一份描边/文字色，经 qfw
    setCustomStyleSheet 随主题切换。裸 QLabel 须先 apply 一个 qfw 样式表
    挂上 CustomStyleSheetWatcher——否则自定义 QSS 只写属性无人应用。
    壳层 margin 不对称（上 3 下 0）把框相对名行中心下压 1.5px：字体名
    字形带在其 label 矩形里偏下，框按行居中会整体偏高（实测上差 2px、
    下差 1px，取中对齐）。"""
    light, dark = _TAG_STYLE[ch]
    lab = QLabel(ch)
    lab.setAlignment(Qt.AlignCenter)
    lab.setFixedSize(15, 15)
    FluentStyleSheet.LABEL.apply(lab)
    setCustomStyleSheet(
        lab,
        f"QLabel{{color:{light}; border:1px solid {light};"
        f" border-radius:3px; font-size:9px;}}",
        f"QLabel{{color:{dark}; border:1px solid {dark};"
        f" border-radius:3px; font-size:9px;}}")
    wrap = QWidget()
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 3, 0, 0)
    v.addWidget(lab)
    return wrap


# 状态图标/徽章配色（亮/暗各一份，线框配色）
_KIND_STYLE = {
    "keep":    ("ACCEPT", "#0f7b0f", "#6ccb5f"),
    "twin":    ("ACCEPT", "#0f7b0f", "#6ccb5f"),
    "replace": ("INFO",   "#9d5d00", "#ffce5c"),
    "missing": ("CANCEL", "#c42b1c", "#ff99a4"),
    "zero":    (None,     "#757575", "#9a9a9a"),
}
_STATUS_TEXT = {"keep": "库中有 · 覆盖足够",
                "replace": "需替换", "missing": "库中没有",
                "zero": "保留（无对白用字）"}   # twin 随方向，_build_head 现拼
_BADGE_MAKER = {"keep": InfoBadge.success,
                "replace": InfoBadge.warning, "missing": InfoBadge.error}

_ROW_QSS = """
QFrame#optRow{ background: transparent; border: 1px solid rgba(128,128,128,0.35);
    border-radius: 6px; }
QFrame#optRow:hover{ background: rgba(128,128,128,0.08); }
"""


def _row_qss() -> str:
    """行样式（含选中描边/底色跟强调色）：QSS 无法引用运行时主题色，
    选中段动态拼。拼在 hover 之后——同特异性后者胜，选中行悬停无
    hover 反馈（改动前语义一致）。行自身 sheet 与卡级 sheet 同名规则
    内容一致，行自身距离更近必胜，卡级只铺 base/hover。"""
    c = themeColor()
    return _ROW_QSS + (
        f'QFrame#optRow[sel="true"]{{ border: 1px solid {c.name()};\n'
        f'    background: rgba({c.red()}, {c.green()}, {c.blue()}, 0.06); }}\n')


class _TofuFrame(QFrame):
    """缺字预览框：虚线边自绘。QSS dashed+border-radius 的圆角段 Qt 画成
    实线（QSS 边框引擎按边分段 dash，弧长不够一个 dash 周期直接实线），
    改 paintEvent 用 CustomDashLine 沿圆角路径 stroke——QPen dash 按弧长
    均匀铺，圆角处照样虚。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tofu")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 内缩 0.5px：1px pen 中心贴边界会被裁半，虚线显粗细不均
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            6, 6)
        pen = QPen(QColor(198, 43, 28, 140), 1)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawPath(path)


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else ""



class FontCard(CardWidget):
    """单张字体卡（线框重排）：状态图标+名称+用量+右侧状态徽章；缺字虚线框；
    选项逐行成带边框行（勾选框|名称+标签+指标|预览条），选中行主题色描边；
    底部单一动作列不随状态搬动——搜索钮自身变身承载全部搜索态（见
    _cont_btn）。纯展示：勾选与搜索动作经信号上抛页面，由页面驱动 vm
    （不触碰 vm）。"""

    option_checked = Signal(str, str)      # (norm_name, key)
    continue_clicked = Signal(str)
    pick_clicked = Signal(str)

    def __init__(self, card: Card, worker, pvm, preview_text_fn,
                 direction: str = "t2s", parent=None):
        super().__init__(parent)
        self.card = card
        self._worker, self._pvm = worker, pvm
        self._preview_text_fn = preview_text_fn
        self._direction = direction   # 孪生徽章文案随检测方向变（简/繁）
        self.setStyleSheet(_ROW_QSS)
        # 选中行描边/底色烤着 themeColor() 派生值，两路刷新：换强调色走
        # themeColorChanged；亮暗主题切换 qfw 只发 themeChanged（随后
        # themeChangedFinished），themeColor() 按 isDarkTheme 派生结果
        # 不同——都要重拼。receiver 是本卡，销毁时 Qt 自动断连
        qconfig.themeColorChanged.connect(self._retheme)
        qconfig.themeChangedFinished.connect(self._retheme)
        self._bars: list[PreviewBar] = []   # 选项行预览条：teardown 撤单用
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(self._build_head())

        if card.kind == "replace" and card.missing_preview:
            v.addWidget(self._build_missing_strip())

        v.addWidget(self._build_options())

        foot = self._build_foot()
        if foot is not None:
            v.addLayout(foot)

    def teardown(self):
        """整卡重建前由页面调用：撤销所有预览条的在途渲染任务。回执
        节流下重建频繁，不撤单则每轮重建都重复派任务，worker 串行队列
        被废任务塞死，预览永远轮不到上屏（真机实锤）。"""
        for b in self._bars:
            b.cancel()

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
            h.addWidget(_pill(_STATUS_TEXT["zero"]))
        elif kind == "twin":
            twin = "找到孪生简体" if self._direction == "t2s" else "找到孪生繁体"
            h.addWidget(InfoBadge.success(twin))   # 好结果给绿（同页头徽章）
        else:
            h.addWidget(_BADGE_MAKER[kind](_STATUS_TEXT[kind]))
        return head

    def _build_missing_strip(self) -> QFrame:
        box = _TofuFrame(self)
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 6, 10, 6)
        lab = CaptionLabel(
            f"未覆盖 {self.card.n_missing} 字："
            + "".join(self.card.missing_preview)
            + ("……" if self.card.n_missing > len(self.card.missing_preview)
               else ""))
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
        return wrap

    def _build_option_row(self, o) -> QFrame:
        row = QFrame(self)
        row.setObjectName("optRow")
        row.setProperty("sel", o.key == self.card.checked_key)
        row.setStyleSheet(_row_qss())        # property 变化后需重 Applying
        g = QHBoxLayout(row)
        g.setContentsMargins(10, 7, 10, 7)
        g.setSpacing(10)

        cb = CheckBox()
        # qfw QSS min-width:28px 把空文本框撑到 57px（indicator 右侧拖
        # 空白，选项名离勾选框过远）；polish 会把 min-width 写回
        # minimumWidth，setFixedWidth 前置必被顶回——走 qfw
        # setCustomStyleSheet 覆写再定宽（同 subs_page 文件行）
        setCustomStyleSheet(cb, "CheckBox{min-width:0px;}",
                            "CheckBox{min-width:0px;}")
        cb.setFixedWidth(24)
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
        # 原字体/孪生对应 → 名前单字框标签（原=灰、孪=绿，无底色）
        if "原字体" in o.badges:
            name_row.addWidget(_char_tag("原"))
        elif "孪生对应" in o.badges:
            name_row.addWidget(_char_tag("孪"))
        name_row.addWidget(BodyLabel(o.display_name))
        for b in o.badges:
            if b == "上次使用":                     # 记忆回放仍走主题徽章
                name_row.addWidget(InfoBadge.info(b))
        for m in o.meta:
            name_row.addWidget(_pill(m))            # 简繁/字重等：中性灰
        name_row.addStretch(1)
        text.addLayout(name_row)
        text.addWidget(CaptionLabel(self._metrics_text(o)))
        g.addLayout(text, 1)

        if o.entity is not None:
            bar = PreviewBar(self._worker, self._pvm, o.entity,
                             self._preview_text_fn() or o.display_name,
                             f"card:{self.card.norm_name}:{o.key}",
                             fit_width=False)   # 行内 hint 定宽：防呼吸边自反塌缩
            bar.setFixedHeight(28)
            self._bars.append(bar)
            g.addWidget(bar, 0, Qt.AlignVCenter)

        # 行点击=切换选择：实例级覆盖 mousePressEvent（QFrame 默认不消费），
        # 只认左键，右键/中键不应有选择行为
        def _row_press(e, k=o.key):
            if e.button() != Qt.MouseButton.LeftButton:
                return
            if k != self.card.checked_key:
                self.option_checked.emit(self.card.norm_name, k)

        row.mousePressEvent = _row_press
        return row

    def _metrics_text(self, o) -> str:
        if o.key == KEEP:
            return ("播放时由播放器自行回退处理" if self.card.kind == "missing"
                    else "无对白用字，无需处理")
        if "原字体" in o.badges:
            n_missing = round(self.card.n_chars * (1 - (o.coverage or 0)))
            if n_missing > 0:
                return f"覆盖 {_pct(o.coverage)} · 未覆盖 {n_missing} 字"
            return f"覆盖 {_pct(o.coverage)} · 无需替换"
        parts = [p for p in (
            f"相似 {_pct(o.similarity)}" if o.similarity is not None else None,
            f"覆盖 {_pct(o.coverage)}" if o.coverage is not None else None,
            f"相对大小 {_pct(o.size_ratio)}" if o.size_ratio is not None else None,
        ) if p]
        text = " · ".join(parts) if parts else "已选"
        # 覆盖不足的候选/手选行同原字体行补未覆盖字数；孪生注脚垫尾
        if o.coverage is not None:
            n_missing = round(self.card.n_chars * (1 - o.coverage))
            if n_missing > 0:
                text += f" · 未覆盖 {n_missing} 字"
        if "孪生对应" in o.badges:
            text += " · 与原字体同款设计"
        return text

    # ---- 底部随状态 ----
    def _pick_btn(self) -> TransparentPushButton:
        btn = TransparentPushButton(FIF.SEARCH_MIRROR, "从字体库手动选择")
        # 搜索中禁用：评分任务会排在搜索片后迟迟不回，选择结果也易被
        # 回填的候选行冲掉
        btn.setEnabled(not self.card.searching)
        btn.clicked.connect(
            lambda: self.pick_clicked.emit(self.card.norm_name))
        return btn

    def _cont_btn(self) -> TransparentPushButton:
        """搜索钮一钮三态（用户拍板 2026-10-28）：搜索中变「寻找中…
        已对比/总数」+Pause 图标（再点=暂停，删独立的环+文字行）；攒够
        暂停即还原；整池搜完变禁用「无更多符合要求的相似字体」+Info。
        位置恒由 foot 的动作列钉住，不随状态搬动。target_error 同禁用，
        文案说明缘由（替代原独立标签行）。"""
        card = self.card
        btn = TransparentPushButton()
        if card.searching:
            s = card.searcher
            text = (f"寻找中… {s.compared}/{s.pool_size}"
                    if s is not None and s.pool_size else "寻找中…")
            btn.setIcon(FIF.PAUSE.icon())
            btn.setText(text)
        elif card.search_state == "exhausted":
            btn.setIcon(FIF.INFO.icon())
            btn.setText("无更多符合要求的相似字体")
            btn.setEnabled(False)
        elif card.search_state == "target_error":
            btn.setIcon(FIF.INFO.icon())
            btn.setText("原字体无法渲染，无法比较相似度")
            btn.setEnabled(False)
        else:
            btn.setIcon(FIF.MORE.icon())
            btn.setText("继续寻找更多")
        btn.clicked.connect(
            lambda: self.continue_clicked.emit(self.card.norm_name))
        return btn

    def _action_col(self, *btns) -> QVBoxLayout:
        """foot 动作钮竖排：内容默认居中，长短不一的钮左缘参差——qfw 图标
        x=12+(width−minimumSizeHint)//2、文本走 text-align。各钮钉自身
        hint 宽（width==hint → 图标 x=12）+ 左对齐文本，左缘即齐。"""
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        for b in btns:
            b.setFixedWidth(b.minimumSizeHint().width())
            setCustomStyleSheet(b, "TransparentPushButton{text-align:left;}",
                                "TransparentPushButton{text-align:left;}")
            col.addWidget(b)
        return col

    def _build_foot(self) -> QHBoxLayout | None:
        """foot 恒为单一动作列：搜索态全部由搜索钮自身表达（_cont_btn），
        按钮位置各态一致。"""
        ekind = self._ekind()
        if ekind == "zero":
            return None            # 无对白用字：无需任何动作
        if ekind == "missing":
            # 库中没有：无相似度可搜（target=None），只有手动挑选入口
            btns = (self._pick_btn(),)
        elif self.card.n_chars > 0:
            btns = (self._cont_btn(), self._pick_btn())
        else:
            return None            # 0 字卡无可比字型：不给搜索入口
        foot = QHBoxLayout()
        foot.setSpacing(6)
        foot.addLayout(self._action_col(*btns))
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

    def _retheme(self):
        """强调色变化：重设卡与行样式（选中描边/底色动态段随新色）。
        qfw themeColorChanged 发出时 themeColor() 尚是旧值（实测慢一拍），
        排到事件循环下一拍重取。"""
        QTimer.singleShot(0, self._reapply_qss)

    def _reapply_qss(self):
        if not shiboken6.isValid(self):
            return
        # 卡级只铺 base/hover（选中段行自带，行自身规则距离更近必胜，
        # 卡级铺选中段是冗余——P2-3）
        self.setStyleSheet(_ROW_QSS)
        # 行包在 _build_options 的 wrap 容器里，非卡直子——全深度查
        for row in self.findChildren(QFrame, "optRow"):
            row.setStyleSheet(_row_qss())

    def _resel_rows(self):
        """整卡重建之外的轻量重选：仅同步行描边（勾选切换不重建整卡）。"""
        for o in self.card.options:
            row = self._boxes[o.key].parentWidget()
            if isinstance(row, QFrame) and row.objectName() == "optRow":
                row.setProperty("sel", o.key == self.card.checked_key)
                row.style().unpolish(row)
                row.style().polish(row)
