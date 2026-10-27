from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter
from qfluentwidgets import LineEdit

from ass_font_substitute.gui.settings_store import MAX_PREVIEW_HISTORY


class PreviewInput(LineEdit):
    """预览文字输入框：LineEdit + QCompleter 补全最近 10 条历史（fluent
    补全菜单，大小写不敏感、含中段匹配）。历史落 settings["preview_history"]，
    字体库预览页与字幕编辑页共享同一份（valueChanged 实时互通）；回车或
    失焦记一条（去重挪顶、裁 10）。若某条历史是输入的前缀，原地升级该条
    为输入（最长的命中），不积半截记录。"""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setPlaceholderText("预览文字（留空显示字体名）")
        self.setMaxLength(60)   # 条带缓存按条数计，长文内存线性涨
        self.setMinimumWidth(260)
        self.setClearButtonEnabled(True)
        self._completer = QCompleter(settings.get("preview_history"), self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setMaxVisibleItems(MAX_PREVIEW_HISTORY)
        self.setCompleter(self._completer)
        # 记录走 returnPressed + focusOutEvent 两条路径，不用
        # editingFinished——qfw 补全菜单是 Qt.Popup，弹出抢焦/键盘接受
        # 重定向都会以半截输入误发 editingFinished
        self.returnPressed.connect(self._remember)
        # 跨页实时：另一页记了历史，本页补全列表立刻跟上
        settings.valueChanged.connect(self._on_setting_changed)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._remember()

    def _on_setting_changed(self, key, value):
        if key == "preview_history":
            self._completer.model().setStringList(value)

    def _remember(self):
        # 补全菜单可见=正被菜单交互（弹出抢焦的 focusOut、键盘接受时
        # Enter 先于写回的 returnPressed）——半截不入史，等真失焦/写回
        menu = getattr(self, "_completerMenu", None)
        if menu is not None and menu.isVisible():
            return
        text = self.text().strip()
        if not text:
            return
        hist = self._settings.get("preview_history")
        if hist and hist[0] == text:
            return
        # 某条历史是输入的前缀 → 该条升级为输入并挪顶；多条命中取最长
        # （最接近输入），其余不动
        hits = [h for h in hist if text.startswith(h)]
        if hits:
            best = max(hits, key=len)
            hist = [text] + [h for h in hist if h != best]
        else:
            hist = [text] + [h for h in hist if h != text]
        hist = hist[:MAX_PREVIEW_HISTORY]
        self._settings.set("preview_history", hist)
        self._completer.model().setStringList(hist)

    def history(self) -> list[str]:
        m = self._completer.model()
        return [m.data(m.index(i, 0)) for i in range(m.rowCount())]
