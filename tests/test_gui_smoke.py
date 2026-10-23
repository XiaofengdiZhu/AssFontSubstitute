import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_boots(qapp, tmp_path, monkeypatch):
    # 隔离 APPDATA：Cache/设置不落真实用户目录
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    assert w.library_page.objectName() == "libraryPage"
    assert w.browse_page.objectName() == "browsePage"
    assert w.subs_page.objectName() == "subsPage"
    assert w.settings_page.objectName() == "settingsPage"
    w.close()
