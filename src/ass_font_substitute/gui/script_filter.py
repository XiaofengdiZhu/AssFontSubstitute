"""简繁筛选项共享逻辑（字体库预览页与「从字体库选择」对话框）。

script 值 简/繁/简繁 之外还有「未标注」（探针简繁双不中，见
classify_script）；行 meta 不上屏该值，但筛选要能圈出这批字体——
选项文案「简/繁」以删除线渲染（两个脚本都划掉 = 都不是）。"""
from qfluentwidgets import ComboBox as _ComboBox

from ass_font_substitute.gui import vm_text

NO_SCRIPT = "简/繁"

# 选项源值 → pvm.script 筛选值；全部/空不在表内，统一 None（不筛）。
# 下拉显示译文、userData 存源值（显示串随界面语言变，不能当键）
_SCRIPT_VALUES = {"简": "简", "繁": "繁", "简繁": "简繁", NO_SCRIPT: "未标注"}


class StrikeTailComboBox(_ComboBox):
    """末项（NO_SCRIPT）删除线的简繁下拉。

    qfw ComboBox 弹层菜单每次打开现建（_showComboMenu 从 items 重建
    QAction/QListWidgetItem 后原地 exec），模型侧 FontRole 无处常驻，
    exec 又是模态内联——只能在菜单构建期挂钩：覆写 _createComboMenu，
    视图 rowsInserted 时给末项补删除线字体（QStyledItemDelegate 吃
    FontRole）。选定后落在按钮上的文本不带删除线，可接受。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # 按钮上的选定文本不走弹层 delegate（QPushButton.setText），
        # 删除线只能切按钮自身字体
        self.currentTextChanged.connect(self._sync_button_strike)

    def _sync_button_strike(self, text: str) -> None:
        on = text == vm_text.text(NO_SCRIPT)
        f = self.font()
        if f.strikeOut() != on:
            f.setStrikeOut(on)
            self.setFont(f)

    def _createComboMenu(self):
        menu = super()._createComboMenu()
        view = menu.view

        def _strike_tail(*_args):
            item = view.item(view.count() - 1)
            if item is not None \
                    and item.text().lstrip() == vm_text.text(NO_SCRIPT):
                f = item.font()
                f.setStrikeOut(True)
                item.setFont(f)

        view.model().rowsInserted.connect(_strike_tail)
        return menu


def options(pvm) -> list[str]:
    opts = pvm.scripts()
    if any(e.script == "未标注" for e in pvm.entities):
        opts.append(NO_SCRIPT)
    return opts


def pick(cb) -> str | None:
    """当前选中项 → pvm.script 筛选值；全部/空 = None（不筛）。
    键取 userData（源值）；无 userData 的项（全部/裸 addItems）按文本兜底。"""
    data = cb.currentData()
    return _SCRIPT_VALUES.get(data if data is not None else cb.currentText())
