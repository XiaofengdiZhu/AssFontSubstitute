from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent
from PySide6.QtCore import QObject
from PySide6.QtCore import QPoint
from PySide6.QtCore import QRectF
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtGui import QCursor
from PySide6.QtGui import QFontMetrics
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPainterPath
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QApplication
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
from qfluentwidgets import Flyout
from qfluentwidgets import FlyoutAnimationType
from qfluentwidgets import FlyoutViewBase
from qfluentwidgets import IconWidget
from qfluentwidgets import InfoBadge
from qfluentwidgets import InfoBar
from qfluentwidgets import StrongBodyLabel
from qfluentwidgets import TransparentPushButton
from qfluentwidgets import TransparentToolButton
from qfluentwidgets import qconfig
from qfluentwidgets import setCustomStyleSheet
from qfluentwidgets import themeColor

from ass_font_substitute.gui.subs_vm import KEEP
from ass_font_substitute.gui.subs_vm import Card
from ass_font_substitute.gui.tip import SHOW_DELAY
from ass_font_substitute.gui.tip import install_tip
from ass_font_substitute.gui.widgets.pill import PillLabel
from ass_font_substitute.gui.widgets.pill import padded_badge
from ass_font_substitute.gui.widgets.pill import pill
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar


def _pill(text: str) -> PillLabel:
    return pill(text)


_TAG_STYLE = {"原": ("#757575", "#9a9a9a"), "孪": ("#0f7b0f", "#6ccb5f")}

# 选项行左列定宽（用户拍板 2026-10-29：预览条起点一致、各行预览同宽）
_NAME_W = 200       # 名称（超宽省略号）
_TEXT_W = 380       # 文本列宽：真机雅黑 12px 容下常见指标行；最全指标串
                    # （相似+覆盖+未覆盖+相对大小，offscreen 实测 516）超出
                    # 部分省略号+tooltip 全文。左列每胖 1px 预览条瘦 1px
                    # （150% 缩放小窗左列 554 曾把预算压到 250 < 旧上限 360）
_LEFT_W = 24 + 10 + _TEXT_W   # 勾选框 24 + 列距 10 + 文本列


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


class _ElideMixin:
    """定宽省略标签：左列锁宽后名称/指标可能放不下，超宽折成 … 不撑列。
    宽度由子类 setFixedWidth 锁死，elide 按自身宽现算（resize 时重算），
    hint 与显示宽一致，布局零反馈。"""

    _full = ""

    def setFullText(self, t: str):
        if t == self._full:
            return
        self._full = t
        self._relayout()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self):
        fm = QFontMetrics(self.font())
        t = fm.elidedText(self._full, Qt.ElideRight, max(1, self.width()))
        if t != self.text():
            self.setText(t)
        # 截断时 tooltip 给全文（省略号丢的指标可悬停补读）
        self.setToolTip("" if t == self._full else self._full)


class _NameLabel(_ElideMixin, BodyLabel):
    pass


class _MetricsLabel(_ElideMixin, CaptionLabel):
    pass


class _MissingFlyoutView(FlyoutViewBase):
    """未覆盖字符浮层视图：单行 HBox——字列（截 30，超出尾加「…」）+
    复制钮。不走 FlyoutView（竖排，容纳不了行内钮）；圆角底/描边承
    基类 paintEvent 亮暗自适应。复制走卡级回执（同缺字条），拷贝载荷
    是**全部**未覆盖字符——展示截 30 只管显示（用户纠偏 2026-10-29）。"""

    closed = Signal()

    def __init__(self, shown: str, copy_text: str, more: bool, on_copy,
                 parent=None):
        super().__init__(parent)
        self._copy_text, self._on_copy = copy_text, on_copy
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 8, 8)
        h.setSpacing(4)
        self.label = CaptionLabel(shown + ("…" if more else ""))
        h.addWidget(self.label, 0, Qt.AlignVCenter)
        btn = TransparentToolButton(FIF.COPY.icon(), self)
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(22, 22)
        install_tip(btn, "复制未覆盖字符")
        btn.clicked.connect(self._copy)
        h.addWidget(btn, 0, Qt.AlignVCenter)

    def _copy(self):
        self._on_copy(self._copy_text)
        self.closed.emit()


class _MissingFlyoutFilter(QObject):
    """未覆盖指标悬停过滤器：Enter 起 SHOW_DELAY 延时弹 Flyout（贴指标
    行 DROP_DOWN）。浮层常驻可点（旧 ToolTip 窗悬停即收，复制钮够不着，
    真机实锤）；鼠标离开指标行与浮层两者即收——Qt.Popup 抓鼠标，行/浮层
    的 enter/leave 在抓取下不可靠，轮询光标判定（点外点击仍可立收）。
    原生 tooltip 事件吃掉不双弹；行被移除（Hide）时浮层立收不留孤窗。"""

    CURSOR_TICK_MS = 120

    def __init__(self, label, shown: str, copy_text: str, more: bool,
                 on_copy):
        super().__init__(label)
        self._lab, self._enter = label, False
        self._shown, self._copy_text = shown, copy_text
        self._more, self._on_copy = more, on_copy
        self._flyout: Flyout | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show)
        self._tick = QTimer(self)
        self._tick.setInterval(self.CURSOR_TICK_MS)
        self._tick.timeout.connect(self._on_tick)
        self._pos_fn = QCursor.pos      # 测试注替（offscreen 光标不可驱）

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.ToolTip:
            return True
        if t == QEvent.Type.Enter:
            self._enter = True
            self._timer.start(SHOW_DELAY)
        elif t in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._enter = False
            self._timer.stop()
            if t == QEvent.Type.Hide and self._flyout is not None \
                    and self._flyout.isVisible():
                self._flyout.close()
        return False

    def _show(self):
        if not self._enter:
            return
        if self._flyout is not None:
            if self._flyout.isVisible():
                return
            # 弃旧引用前先撤 destroyed 连接：旧浮层的 deleteLater 在途，
            # 迟到的 destroyed 会把新浮层引用清掉、停掉其轮询（审查 P2-1）
            self._flyout.destroyed.disconnect(self._forget)
            self._flyout = None
        view = _MissingFlyoutView(self._shown, self._copy_text, self._more,
                                  self._on_copy)
        fly = Flyout(view, self._lab.window())
        view.closed.connect(fly.close)
        fly.destroyed.connect(self._forget)
        self._flyout = fly
        # x=光标为浮层中轴（用户拍板：跟光标曾偏右展开，收为居中）、
        # y 贴指标行下缘：DROP_DOWN 的 widget 目标按 380px 定宽列居中，
        # 短指标串时浮层飘离悬停点（真机截图实锤）；QPoint 目标直喂
        # exec 绕开居中，屏缘钳制仍由 _adjustPosition 管
        gp = self._pos_fn()
        w = fly.sizeHint().width()
        pos = QPoint(gp.x() - w // 2,
                     self._lab.mapToGlobal(QPoint(0, self._lab.height())).y())
        fly.exec(pos, FlyoutAnimationType.DROP_DOWN)
        self._tick.start()

    def _on_tick(self):
        if self._flyout is None or not self._flyout.isVisible():
            self._tick.stop()
            return
        self._close_if_out(self._pos_fn())

    def _close_if_out(self, pos):
        """光标同时在指标行与浮层之外 → 收。浮层贴行下缘零缝，途经无死区。"""
        if self._flyout is None or not self._flyout.isVisible():
            return
        in_lab = self._lab.rect().contains(self._lab.mapFromGlobal(pos))
        in_fly = self._flyout.geometry().contains(pos)
        if not in_lab and not in_fly:
            self._flyout.close()

    def _forget(self):
        self._flyout = None
        self._tick.stop()


class FontCard(CardWidget):
    """单张字体卡（线框重排）：状态图标+名称+用量+右侧状态徽章；缺字虚线框；
    选项逐行成带边框行（勾选框|名称+标签+指标|预览条），选中行主题色描边；
    底部单一动作列不随状态搬动——搜索钮自身变身承载全部搜索态（见
    _apply_cont_state）。有状态视图：构建一次，此后 vm 卡数据变化经
    apply() 就地刷新（新候选行插入、按钮/勾选/预览文字原地刷），整卡
    不拆不闪；只有 _reclassify 整页重算才 teardown 重建。纯展示：
    勾选与搜索动作经信号上抛页面，由页面驱动 vm（不触碰 vm）。"""

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
        self._cont = None                   # foot 动作钮（apply 原地刷用）
        self._pick = None
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.addWidget(self._build_head())

        # 缺字条：replace=原字体未覆盖；twin=孪生体未覆盖（换过去会缺的
        # 字，勾选前可见）
        if card.kind in ("replace", "twin") and card.missing:
            v.addWidget(self._build_missing_strip())

        v.addWidget(self._build_options())

        foot = self._build_foot()
        if foot is not None:
            v.addLayout(foot)

    def apply(self):
        """vm 卡数据就地刷新（搜索回执/手选回填/状态翻转后由页面调用）：
        新增选项行按综合分位次插入，搜索钮/手选钮/勾选态/预览文字原地刷。
        既有行相对次序恒稳——add_candidates/attach_pick 稳定排序，新项
        只插队不换位，已建行无需搬移。零整卡重建：300ms 节流把回执洪收
        束成一次本调用，不闪不跳不丢焦点。"""
        self._sync_rows()
        self._sync_foot()
        self.set_checked(self.card.checked_key)
        self._sync_previews()

    def _sync_rows(self):
        """行集 reconcile：新 key 建行插到 card.options 位次（其后第一个
        已建行之前，其后全为新行则尾插），指标文案变了就地改。"""
        old_rows = dict(self._rows)
        for i, o in enumerate(self.card.options):
            if o.key in old_rows:
                lab = self._metrics.get(o.key)
                t = self._metrics_text(o)
                if lab is not None and lab.fullText() != t:
                    lab.setFullText(t)
                continue
            row = self._build_option_row(o)
            anchor = next((self._rows[x.key] for x in self.card.options[i + 1:]
                           if x.key in old_rows), None)
            idx = self._opt_layout.indexOf(anchor) if anchor is not None else -1
            if idx < 0:
                self._opt_layout.addWidget(row)
            else:
                self._opt_layout.insertWidget(idx, row)

    def _sync_foot(self):
        # pick 先启停：cont 禁用交焦点的落点条件依赖 pick 已可用
        if self._pick is not None:
            self._pick.setEnabled(not self.card.searching)
        if self._cont is not None:
            self._apply_cont_state(self._cont)

    def _sync_previews(self):
        """预览文字变化（预览框输入防抖后整页 apply）：各条原地换文字
        重渲（tile LRU 命中零渲染）。文字没变时 set_text 内部直接返回。"""
        pt = self._preview_text_fn()
        for key, bar in self._bar_by_key.items():
            o = self.card.option(key)
            if o is not None:
                bar.set_text(pt or o.display_name)

    def rerender_previews(self):
        """强制等宽开关翻转后由页面调用（settings.valueChanged 广播）：
        各条按新口径清贴图重渲（缓存键含 mono）。文字未变 set_text 不动，
        走 bar 专用 rerender。"""
        for bar in self._bar_by_key.values():
            bar.rerender()

    def teardown(self):
        """整卡销毁前由页面调用（仅 _reclassify 整页重算/页面关闭）：
        撤销所有预览条的在途渲染任务。不撤单则废任务塞死 worker 串行
        队列，预览永远轮不到上屏（真机实锤）。apply 增量刷新不撤单——
        行都还活着，各自的在途任务仍有效。"""
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
            h.addWidget(padded_badge(InfoBadge.success, twin))   # 好结果给绿（同页头徽章）
        else:
            h.addWidget(padded_badge(_BADGE_MAKER[kind], _STATUS_TEXT[kind]))
        return head

    def _build_missing_strip(self) -> QFrame:
        box = _TofuFrame(self)
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(4)
        shown = "".join(self.card.missing[:30])
        lab = CaptionLabel(
            f"未覆盖 {self.card.n_missing} 字："
            + shown
            + ("…" if len(self.card.missing) > 30 else ""))
        lab.setWordWrap(False)               # 单行条：30 字封顶后宽有界
        h.addWidget(lab, 0, Qt.AlignVCenter)
        # 复制钮紧跟最后一个字/省略号右边（不钉行尾，用户拍板
        # 2026-10-29）：拷贝**全部**未覆盖字符（展示截 30 只管显示，
        # 用户纠偏 2026-10-29：截断的是预览不是载荷）。小号 22×22/icon
        # 14，行内不抢高
        btn = TransparentToolButton(FIF.COPY.icon(), box)
        btn.setIconSize(QSize(14, 14))
        btn.setFixedSize(22, 22)
        install_tip(btn, "复制未覆盖字符")
        btn.clicked.connect(
            lambda: self._copy_missing_chars("".join(self.card.missing)))
        h.addWidget(btn, 0, Qt.AlignVCenter)
        h.addStretch(1)
        return box

    def _copy_missing_chars(self, chars: str):
        QApplication.clipboard().setText(chars)
        InfoBar.success("已复制", f"未覆盖 {len(chars)} 字",
                        duration=1500, parent=self.window())

    # ---- 选项行 ----
    def _build_options(self) -> QWidget:
        wrap = QWidget(self)
        self._opt_layout = QVBoxLayout(wrap)
        self._opt_layout.setContentsMargins(0, 0, 0, 0)
        self._opt_layout.setSpacing(6)
        self._rows: dict[str, QFrame] = {}        # key → 行（reconcile 用）
        self._boxes: dict[str, CheckBox] = {}     # key → 勾选框
        self._metrics: dict[str, CaptionLabel] = {}   # key → 指标文案
        self._bar_by_key: dict[str, PreviewBar] = {}
        for o in self.card.options:
            self._opt_layout.addWidget(self._build_option_row(o))
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
        # objectName 带选项键（测试定位用）
        cb.setObjectName(f"opt:{o.key}")
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
        # 不走带文字构造：qfw FluentLabelBase singledispatch 对子类会重派发
        # 回本构造（同 PreviewBar 坑），且 mixin _full 需经 setFullText 立账
        name_lab = _NameLabel()
        # 封顶不锁死：短名贴身、药丸紧跟（锁死 200 曾在短名后留死空白），
        # 长名 200 截断省略
        name_lab.setMaximumWidth(_NAME_W)
        name_lab.setFullText(o.display_name)
        name_row.addWidget(name_lab)
        for b in o.badges:
            if b == "上次使用":                     # 记忆回放仍走主题徽章
                name_row.addWidget(padded_badge(InfoBadge.info, b))
        for m in o.meta:
            name_row.addWidget(_pill(m))            # 简繁/字重等：中性灰
        name_row.addStretch(1)
        text.addLayout(name_row)
        m_lab = _MetricsLabel()
        m_lab.setFixedWidth(_TEXT_W)
        m_lab.setFullText(self._metrics_text(o))
        self._metrics[o.key] = m_lab
        if o.missing and o.entity is not None:
            # 未覆盖指标悬停弹字符 Flyout（展示截 30+复制钮；拷贝载荷
            # 全列）。带过滤器的行原生 tooltip 事件被吃，省略号全文
            # tooltip 让位
            m_lab.installEventFilter(_MissingFlyoutFilter(
                m_lab, "".join(o.missing[:30]), "".join(o.missing),
                len(o.missing) > 30, self._copy_missing_chars))
        text.addWidget(m_lab)
        # 左列（勾选框+名称+指标）定宽：各行预览条起点一致、预览同宽
        # （用户拍板 2026-10-29，防预览大大小小）
        left = QWidget(row)
        lh = QHBoxLayout(left)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(10)
        lh.addWidget(cb, 0, Qt.AlignVCenter)
        lh.addLayout(text, 1)
        left.setFixedWidth(_LEFT_W)
        # AlignLeft 必须显式：QLayout alignedRect 对无水平对齐位的 item
        # 默认水平居中——KEEP 行（无预览条、独苗定宽左列）曾被居中缩进
        # 行中部（真机 missing 卡实锤）；有 bar 的行 bar 填满剩余不受影响
        g.addWidget(left, 0, Qt.AlignLeft | Qt.AlignVCenter)

        if o.entity is not None:
            bar = PreviewBar(self._worker, self._pvm, o.entity,
                             self._preview_text_fn() or o.display_name,
                             f"card:{self.card.norm_name}:{o.key}",
                             fit_width=False,
                             width_fn=self._bar_budget(row, left))
            bar.setFixedHeight(28)
            self._bars.append(bar)
            self._bar_by_key[o.key] = bar
            g.addWidget(bar, 0, Qt.AlignVCenter)

        # 行点击=切换选择：实例级覆盖 mousePressEvent（QFrame 默认不消费），
        # 只认左键，右键/中键不应有选择行为
        def _row_press(e, k=o.key):
            if e.button() != Qt.MouseButton.LeftButton:
                return
            if k != self.card.checked_key:
                self.option_checked.emit(self.card.norm_name, k)

        row.mousePressEvent = _row_press
        if o.entity is not None:
            base_resize = QFrame.resizeEvent

            def _row_resize(e, _r=row, _b=bar):
                base_resize(_r, e)
                _b.budget_changed()   # 行宽变化：按新预算重贴预览

            row.resizeEvent = _row_resize
        self._rows[o.key] = row
        return row

    @staticmethod
    def _bar_budget(row: QFrame, left: QWidget):
        """预览条宽度预算：行内容宽（contentsRect 已扣 QSS 1px 边框，否则
        满宽贴图比 bar 宽 2px 右缘被裁）− 列间距 − 定宽左列。bar 不自述
        宽度（自反塌缩真机实锤），只读行几何。"""
        def fn() -> int:
            g = row.layout()
            return (row.contentsRect().width()
                    - g.spacing() - left.width())
        return fn

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
        ) if p]
        # 孪生对应：行首注明来由（用户拍板 2026-10-29），后接覆盖/未覆盖；
        # 孪生行无相似率/相对大小评分
        if "孪生对应" in o.badges:
            text = "原字体名称对应的简体名称"
            if o.coverage is not None:
                text += f" · 覆盖 {_pct(o.coverage)}"
                n_missing = round(self.card.n_chars * (1 - o.coverage))
                if n_missing > 0:
                    text += f" · 未覆盖 {n_missing} 字"
            return text
        # 次序（用户拍板 2026-10-29）：相似 → 覆盖 → 未覆盖 → 相对大小
        text = " · ".join(parts) if parts else "已选"
        if o.coverage is not None:
            n_missing = round(self.card.n_chars * (1 - o.coverage))
            if n_missing > 0:
                text += f" · 未覆盖 {n_missing} 字"
        if o.size_ratio is not None:
            text += f" · 相对大小 {_pct(o.size_ratio)}"
        return text

    # ---- 底部随状态 ----
    def _pick_btn(self) -> TransparentPushButton:
        btn = TransparentPushButton(FIF.SEARCH_MIRROR, "从字体库手动选择")
        btn.setObjectName("pickBtn")
        # 搜索中禁用：评分任务会排在搜索片后迟迟不回，选择结果也易被
        # 回填的候选行冲掉
        btn.setEnabled(not self.card.searching)
        btn.clicked.connect(
            lambda: self.pick_clicked.emit(self.card.norm_name))
        return btn

    def _cont_btn(self) -> TransparentPushButton:
        """搜索钮（用户拍板 2026-10-28 一钮三态，态由 _apply_cont_state
        承载：构建与 apply 增量刷新共用同一套逻辑）。"""
        btn = TransparentPushButton()
        btn.setObjectName("contBtn")
        btn.clicked.connect(
            lambda: self.continue_clicked.emit(self.card.norm_name))
        self._apply_cont_state(btn)
        return btn

    def _apply_cont_state(self, btn: TransparentPushButton):
        """搜索钮四态原地刷：idle=开始/继续寻找（按寻没寻过分文案，态键
        同为 idle）/ searching=寻找中…已对比/总数+Pause / exhausted=禁用
        无更多 / target_error=禁用说明缘由。文字变才 setText 并重钉 hint
        宽（_action_col 按 hint 定宽对齐左缘）；图标/启停随态键变（计数
        频繁变不触发）。"""
        card = self.card
        if card.searching:
            s = card.searcher
            text = (f"寻找中… {s.compared}/{s.pool_size}"
                    if s is not None and s.pool_size else "寻找中…")
            state = "searching"
        elif card.search_state == "exhausted":
            text, state = "无更多符合要求的相似字体", "exhausted"
        elif card.search_state == "target_error":
            text, state = "原字体无法渲染，无法比较相似度", "target_error"
        elif card.search_state == "paused":
            text, state = "继续寻找更多相似字体", "idle"
        else:
            text, state = "开始寻找相似字体", "idle"
        if btn.property("state") != state:
            enabled = state in ("searching", "idle")
            # 禁用前交焦点：钮带焦被禁时 Qt 把焦点塞给任意下一焦点控件，
            # QScrollArea 见 FocusIn 滚过去（exhausted 滚动乱跳真机实锤，
            # 落点不可控）。交给同 foot 的手选钮——贴邻且必可见，零滚动。
            if not enabled and btn.hasFocus() and self._pick is not None \
                    and self._pick.isEnabled():
                self._pick.setFocus(Qt.FocusReason.OtherFocusReason)
            btn.setProperty("state", state)
            btn.setIcon(FIF.PAUSE.icon() if state == "searching"
                        else FIF.INFO.icon()
                        if state in ("exhausted", "target_error")
                        else FIF.MORE.icon())
            btn.setEnabled(enabled)
        if btn.text() != text:
            btn.setText(text)
            btn.setFixedWidth(btn.minimumSizeHint().width())

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
        """foot 恒为单一动作列：搜索态全部由搜索钮自身表达（_apply_cont_state），
        按钮位置各态一致。foot 组成只由 kind/字数定（分类后不变），apply
        只刷钮态不重排。"""
        ekind = self._ekind()
        if ekind == "zero":
            return None            # 无对白用字：无需任何动作
        if ekind == "missing":
            # 库中没有：无相似度可搜（target=None），只有手动挑选入口
            self._pick = self._pick_btn()
            btns = (self._pick,)
        elif self.card.n_chars > 0:
            self._cont = self._cont_btn()
            self._pick = self._pick_btn()
            btns = (self._cont, self._pick)
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
        """轻量重选：仅同步行描边（勾选切换/apply 不重建任何行）。"""
        for key, row in self._rows.items():
            row.setProperty("sel", key == self.card.checked_key)
            row.style().unpolish(row)
            row.style().polish(row)
