"""字体库预览页接线：强制等宽开关两页同键互通（valueChanged 广播）。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")

from PySide6.QtCore import QObject
from PySide6.QtCore import Signal


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWorker(QObject):
    result = Signal(object, object)
    failed = Signal(object, str)

    def submit(self, tag, fn, *args, **kw):
        return type("H", (), {"cancelled": False})()


def _make_page(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.pages.browse_page import BrowsePage
    from ass_font_substitute.gui.settings_store import Settings
    return BrowsePage(Settings(tmp_path / "settings.json"), _FakeWorker())


def test_mono_switch_two_way_sync(qapp, tmp_path, monkeypatch):
    # 开关翻转落设置（本页→设置）；他页翻转经 valueChanged 广播回开关联动
    # （设置→本页），两页同键 preview_mono 一个口径来源
    page = _make_page(qapp, tmp_path, monkeypatch)
    assert not page.sw_mono.isChecked()             # 默认关（自然字距）
    page.settings.set("preview_mono", True)
    assert page.sw_mono.isChecked()                 # 广播联动，无死循环
    page.sw_mono.setChecked(False)
    assert page.settings.get("preview_mono") is False
    page.deleteLater()


def test_blib_failure_reports(qapp, tmp_path, monkeypatch):
    # 终审 P1：blib 失败曾全层无人处理——页面保持旧列表+误导性 hint
    from qfluentwidgets import InfoBar
    page = _make_page(qapp, tmp_path, monkeypatch)
    fired = []
    monkeypatch.setattr(InfoBar, "error",
                        lambda *a, **kw: fired.append(a))
    page.worker.failed.emit("blib", "RuntimeError: boom")
    assert fired and "boom" in fired[0][1]
    page.worker.failed.emit("unrelated", "x")       # 白名单外不弹
    assert len(fired) == 1
    page.deleteLater()
