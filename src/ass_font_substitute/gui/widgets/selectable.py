"""文本划选通用 flags：字体名、未覆盖字符等标签可拖选只拷一段。
Keyboard 位顺带把 QLabel focusPolicy 变 ClickFocus——划选后 Ctrl+C
直达本标签（Flyout 浮层是激活窗，同样直达）。副作用备案：挂 flags 的
标签 press 被 QLabel 消费，不再向父级传播（行点击/双击在标签区失效）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

SELECT_FLAGS = (Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)


def apply_selectable(lab: QLabel) -> None:
    """挂划选 flags + IBeam 光标（文字可选的指针语汇，用户拍板
    2026-10-29：可选处指针须变 IBeam）。"""
    lab.setTextInteractionFlags(SELECT_FLAGS)
    lab.setCursor(Qt.CursorShape.IBeamCursor)
