"""字体库预览页 / 「从字体库选择」对话框共享的页头工具组与筛选行。

对话框照预览页重做（用户需求 2026-10-29）：两处控件同源，筛选/预览
口径一处维护。控件只写共享状态（settings["preview_mono"]、
pvm.require_text、pvm 各筛选字段），行重载由页面接信号自驱——文字
防抖 / 开关即时的节流口径各页自定。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import CaptionLabel
from qfluentwidgets import ComboBox
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import SearchLineEdit
from qfluentwidgets import SwitchButton
from qfluentwidgets import TransparentToggleToolButton

from ass_font_substitute.gui import script_filter
from ass_font_substitute.gui import vm_text
from ass_font_substitute.gui.tip import install_tip
from ass_font_substitute.gui.widgets.preview_input import PreviewInput
from ass_font_substitute.gui.widgets.wheel_block import WheelBlock


def _fld(label: str, w: QWidget) -> QWidget:
    """字段对：标签+控件。与 subs_page._fld 同形但独立——widgets 反向
    import pages 会成环（subs_page → font_picker → 本模块 → subs_page）。"""
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    h.addWidget(CaptionLabel(label))
    h.addWidget(w)
    return box


class PreviewTools(QWidget):
    """预览工具组：强制等宽开关 + 预览文字输入 + 覆盖筛选开关。

    previewChanged(str)  预览文字变化（覆盖筛选开着时 require_text 已同步）
    covChanged(bool)     覆盖筛选开关翻转（require_text 已同步）
    monoChanged(bool)    等宽口径变化（本页翻转经 settings 回环，他页翻转
                         经 valueChanged 广播——两页同键互通一个来源）
    """

    previewChanged = Signal(str)
    covChanged = Signal(bool)
    monoChanged = Signal(bool)

    def __init__(self, settings, pvm, parent=None):
        super().__init__(parent)
        self._settings, self.pvm = settings, pvm
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        # qfw SwitchButton 自带文本是 On/Off 态字（toggle 即覆写 setText），
        # 说明文字用 _fld 标签对，开关只留指示器
        self.sw_mono = SwitchButton(self)
        self.sw_mono.setOnText("")
        self.sw_mono.setOffText("")
        self.sw_mono.setChecked(bool(settings.get("preview_mono")))
        install_tip(self.sw_mono,
                    self.tr("统一格宽渲染预览（全角 1em/半角 0.5em），多字体行逐字对位"))
        self.sw_mono.checkedChanged.connect(self._on_mono_toggled)
        h.addWidget(_fld(self.tr("强制等宽"), self.sw_mono), 0, Qt.AlignVCenter)
        self.preview_edit = PreviewInput(settings)
        self.preview_edit.setFixedWidth(340)
        self.preview_edit.textChanged.connect(self._on_text)
        h.addWidget(self.preview_edit, 0, Qt.AlignVCenter)
        # 框外右侧覆盖筛选开关：亮起（qfw toggle 亮起态自动强调色）=
        # 只留 cmap 覆盖全部预览文字的字体；空文字无可筛字符=不筛
        # （require_text 空串视同不筛，见 PreviewVM）
        self.btn_cov = TransparentToggleToolButton(FIF.FILTER, self)
        install_tip(self.btn_cov, self.tr("只显示包含全部预览文字的字体"))
        self.btn_cov.toggled.connect(self._on_cov_toggled)
        h.addWidget(self.btn_cov, 0, Qt.AlignVCenter)
        # 行尾收拢：SwitchButton 扩张策略会把多余宽度摊进控件之间
        # （对话框整行铺宽时现形），给 stretch 兜住；预览页 tools 本就
        # 紧凑右置，不可见
        h.addStretch(1)
        settings.valueChanged.connect(self._on_setting)

    def text(self) -> str:
        return self.preview_edit.text()

    def _on_mono_toggled(self, on: bool):
        # 只落设置；重载统一走 valueChanged 广播（_on_setting），两页
        # 口径一个来源
        self._settings.set("preview_mono", bool(on))

    def _on_setting(self, key, value):
        if key != "preview_mono":
            return
        if self.sw_mono.isChecked() != bool(value):
            # setChecked 会触发 checkedChanged → 再写一次设置，等值后
            # valueChanged 不再发出，回环自然终止
            self.sw_mono.setChecked(bool(value))
        self.monoChanged.emit(bool(value))

    def _on_text(self, text: str):
        if self.btn_cov.isChecked():
            self.pvm.require_text = text
        self.previewChanged.emit(text)

    def _on_cov_toggled(self, on: bool):
        self.pvm.require_text = self.preview_edit.text() if on else None
        self.covChanged.emit(on)


class FontFilterBar(QWidget):
    """筛选行：搜索框撑满（封顶）+ 分类/简繁/字重下拉（带标签，裸
    「全部」看不出维度）+ 计数贴行尾——筛选结果的规模随筛选变化，语义
    归筛选行。下拉变化即写 pvm 并发 filtersChanged（页面即时重载）；
    搜索文字由页面接 name_edit.textChanged 自定节流。构造即按 pvm 现有
    实体填下拉：空库得「全部」单预选项，与装载后首项一致。"""

    filtersChanged = Signal()

    def __init__(self, pvm, parent=None, weight_filter: bool = True):
        super().__init__(parent)
        self.pvm = pvm
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.name_edit = SearchLineEdit()
        self.name_edit.setPlaceholderText(self.tr("搜索字体名"))
        # 下限 160：对话框（720 定宽）里留给带标签下拉与计数的空间比整页
        # 紧；预览页宽到 400 封顶才生效，160 不可见
        self.name_edit.setMinimumWidth(160)
        self.name_edit.setMaximumWidth(400)
        self.cb_cat, self.cb_weight = ComboBox(), ComboBox()
        self.cb_script = script_filter.StrikeTailComboBox()
        # 滚动页面扫过下拉会误改筛选——吞滚轮
        self._wheel_block = WheelBlock(self)
        for cb in (self.cb_cat, self.cb_script, self.cb_weight):
            cb.setFixedWidth(104)
            cb.currentIndexChanged.connect(self._apply)
            cb.installEventFilter(self._wheel_block)
        self.count_label = CaptionLabel(self.tr("0 款字体"))
        h.addWidget(self.name_edit, 1)
        h.addWidget(_fld(self.tr("分类"), self.cb_cat))
        h.addWidget(_fld(self.tr("简繁"), self.cb_script))
        # 字重下拉可选（拍板 2026-10-30：选择对话框不建——工具不改字重
        # 斜体，库页照旧）；cb 仍创建并参与 fill，隐藏档恒「全部」
        if weight_filter:
            h.addWidget(_fld(self.tr("字重"), self.cb_weight))
        h.addStretch(1)
        h.addWidget(self.count_label, 0, Qt.AlignVCenter)
        self.fill()

    def fill(self):
        """按 pvm 现有实体重填下拉选项（实体集装载/变化后由页面调用）。

        重填后按 pvm 现值回选（审查二轮 P1）：blockSignals 清空重填会把
        显示复位到「全部」而 pvm 筛选保留——列表按旧值筛、下拉显示失步，
        用户再动任意下拉时 _apply 全量回写把保留的筛选静默清掉。pvm 值
        已不在新选项集（实体变化后选项收窄）时回「全部」并复位对应 pvm
        字段（调用方 fill 后自行重载列表，无需发 filtersChanged）。"""
        # 显示译文、userData 存源值（显示串随界面语言变，筛选按源值）；
        # 首项「全部」无 userData = 不筛
        for cb, values, cur in (
                (self.cb_cat, self.pvm.categories(), self.pvm.category),
                (self.cb_script, script_filter.options(self.pvm),
                 script_filter.option_of(self.pvm.script)),
                (self.cb_weight, self.pvm.weight_buckets(),
                 None if self.pvm.weight is None else int(self.pvm.weight))):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(self.tr("全部"))
            for v in values:
                cb.addItem(vm_text.text(str(v)), userData=v)
            idx = cb.findData(cur) if cur is not None else 0
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.blockSignals(False)
            if cur is not None and idx < 0:     # 失效筛选值随显示复位
                if cb is self.cb_cat:
                    self.pvm.category = None
                elif cb is self.cb_script:
                    self.pvm.script = None
                else:
                    self.pvm.weight = None
        # 回选/复位期间 currentTextChanged 被 block——简繁按钮删除线字体
        # 显式补同步（复位到「全部」曾残留删除线）
        self.cb_script.sync_strike()

    def _apply(self):
        self.pvm.category = self.cb_cat.currentData()
        self.pvm.script = script_filter.pick(self.cb_script)
        wt = self.cb_weight.currentData()
        self.pvm.weight = int(wt) if wt is not None else None
        self.filtersChanged.emit()

    def set_count(self, n: int):
        self.count_label.setText(self.tr("{0} 款字体").format(n))
