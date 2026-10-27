import pytest

from ass_font_substitute.gui.settings_store import DEFAULTS
from ass_font_substitute.gui.settings_store import MAX_PREVIEW_HISTORY
from ass_font_substitute.gui.settings_store import Settings


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mk(qapp, tmp_path, hist=None):
    from ass_font_substitute.gui.widgets.preview_input import PreviewInput

    s = Settings(tmp_path / "settings.json")
    if hist is not None:
        s.set("preview_history", hist)
    return PreviewInput(s), s


def _focus_out(combo):
    # 失焦记录路径：直投 FocusOut 事件走覆写的 focusOutEvent
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtCore import QEvent
    QCoreApplication.sendEvent(combo, QEvent(QEvent.Type.FocusOut))


def test_defaults_have_preview_history():
    assert DEFAULTS["preview_history"] == []


def test_corrupt_history_filtered(tmp_path):
    # 非 list / 含非字符串 / 含空白串 → 按损坏弃用回默认
    import json

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"preview_history": "abc"}), "utf-8")
    s = Settings(f)
    assert s.get("preview_history") == []
    f.write_text(json.dumps({"preview_history": ["a", 1, "b"]}), "utf-8")
    s = Settings(f)
    assert s.get("preview_history") == []
    f.write_text(json.dumps({"preview_history": ["", "a"]}), "utf-8")
    s = Settings(f)
    assert s.get("preview_history") == []


def test_init_loads_history(qapp, tmp_path):
    combo, _s = _mk(qapp, tmp_path, ["甲", "乙", "丙"])
    assert combo.history() == ["甲", "乙", "丙"]
    assert combo.text() == ""
    combo.deleteLater()


def test_return_pressed_records_history(qapp, tmp_path):
    combo, s = _mk(qapp, tmp_path, [])
    combo.setText("测试预览")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["测试预览"]
    assert combo.history() == ["测试预览"]
    assert combo.text() == "测试预览"               # 记录不清编辑中文本
    combo.deleteLater()


def test_focus_out_records_history(qapp, tmp_path):
    combo, s = _mk(qapp, tmp_path, [])
    combo.setText("失焦记录")
    _focus_out(combo)
    assert s.get("preview_history") == ["失焦记录"]
    combo.deleteLater()


def test_menu_visible_swallows_half_input(qapp, tmp_path):
    # 补全菜单可见（弹出抢焦 focusOut / 键盘接受先发的 returnPressed）
    # 时半截输入不入史
    combo, s = _mk(qapp, tmp_path, ["Star Platinum"])
    menu = type("M", (), {"isVisible": staticmethod(lambda: True)})()
    combo._completerMenu = menu
    combo.setText("sta")
    combo.returnPressed.emit()
    _focus_out(combo)
    assert s.get("preview_history") == ["Star Platinum"]
    # 菜单关了：真失焦才记
    combo._completerMenu = None
    _focus_out(combo)
    assert s.get("preview_history") == ["sta", "Star Platinum"]
    combo.deleteLater()


def test_return_pressed_dedup_moves_to_top(qapp, tmp_path):
    combo, s = _mk(qapp, tmp_path, ["甲", "乙", "丙"])
    combo.setText("乙")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["乙", "甲", "丙"]
    # 栈顶重复记一条是 no-op（不重灌不落盘）
    combo.setText("乙")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["乙", "甲", "丙"]
    combo.deleteLater()


def test_history_capped_at_ten(qapp, tmp_path):
    combo, s = _mk(qapp, tmp_path, [f"t{i}" for i in range(10)])
    combo.setText("新的")
    combo.returnPressed.emit()
    assert s.get("preview_history")[0] == "新的"
    assert len(s.get("preview_history")) == MAX_PREVIEW_HISTORY
    assert "t9" not in s.get("preview_history")     # 最旧（末位）被挤出
    combo.deleteLater()


def test_blank_text_not_recorded(qapp, tmp_path):
    combo, s = _mk(qapp, tmp_path, ["甲"])
    combo.setText("   ")
    combo.returnPressed.emit()
    combo.setText("")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["甲"]
    combo.deleteLater()


def test_history_shared_between_live_instances(qapp, tmp_path):
    # 两页同时存活（同一 Settings 实例）：一处记录，另一处补全列表实时跟上
    from ass_font_substitute.gui.widgets.preview_input import PreviewInput

    s = Settings(tmp_path / "settings.json")
    combo1 = PreviewInput(s)
    combo2 = PreviewInput(s)
    combo1.setText("共享文本")
    combo1.returnPressed.emit()
    assert combo2.history() == ["共享文本"]
    combo1.deleteLater()
    combo2.deleteLater()


def test_prefix_merges_into_history(qapp, tmp_path):
    # 某条历史是输入前缀 → 该条原地升级为输入并挪顶，不积半截记录
    combo, s = _mk(qapp, tmp_path, ["Star Platinum", "King Crimson"])
    combo.setText("Star Platinum Or")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["Star Platinum Or", "King Crimson"]
    assert combo.history() == ["Star Platinum Or", "King Crimson"]
    combo.deleteLater()


def test_prefix_merge_picks_longest_hit(qapp, tmp_path):
    # 多条历史都是前缀 → 取最长（最接近输入）那条升级，短的保留
    combo, s = _mk(qapp, tmp_path, ["Star", "Star Platinum"])
    combo.setText("Star Platinum Or")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["Star Platinum Or", "Star"]
    combo.deleteLater()


def test_non_prefix_still_recorded(qapp, tmp_path):
    # 无前缀命中 → 正常头插
    combo, s = _mk(qapp, tmp_path, ["Star Platinum"])
    combo.setText("完全不同")
    combo.returnPressed.emit()
    assert s.get("preview_history") == ["完全不同", "Star Platinum"]
    combo.deleteLater()
