"""文本划选通用 flags：字体名、未覆盖字符等标签可拖选只拷一段。
Keyboard 位顺带把 QLabel focusPolicy 变 ClickFocus——划选后 Ctrl+C
直达本标签（Flyout 浮层是激活窗，同样直达）。副作用备案：挂 flags 的
标签 press 被 QLabel 消费，不再向父级传播（行点击/双击在标签区失效）。
keyboard=False 退 Mouse-only：弹窗内多语言名/路径等复制型标签用——
不夺焦点（ClickFocus 会抢走弹窗 Tab 焦点序），鼠标划选照常。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

SELECT_FLAGS = (Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)
MOUSE_FLAGS = Qt.TextInteractionFlag.TextSelectableByMouse


def apply_selectable(lab: QLabel, keyboard: bool = True) -> None:
    """挂划选 flags + IBeam 光标（文字可选的指针语汇，用户拍板
    2026-10-29：可选处指针须变 IBeam）。keyboard=False 仅鼠标划选。"""
    lab.setTextInteractionFlags(SELECT_FLAGS if keyboard else MOUSE_FLAGS)
    lab.setCursor(Qt.CursorShape.IBeamCursor)
