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


def test_worker_init_failure_is_observable(qapp, tmp_path, monkeypatch):
    # 回归：Cache 初始化失败必须发 failed("worker:init") 且线程存活——
    # 后续任务逐个 failed，而不是静默死线程 + submit 永久悬挂
    import time
    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker

    monkeypatch.setenv("APPDATA", str(tmp_path))
    blocker = tmp_path / "blocker"
    blocker.write_text("", "utf-8")
    s = Settings(tmp_path / "s.json")
    s.set("cache_path", str(blocker / "inner" / "cache.db"))   # 父级是文件 → Cache 必败
    errors = []
    w = CoreWorker(s)
    w.failed.connect(lambda tag, msg: errors.append((tag, msg)))
    w.start()
    try:
        w.submit("probe", lambda ctx: ctx.cache.nothing)       # cache=None → 单任务 failed
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(errors) < 2:
            qapp.processEvents()
            time.sleep(0.01)
        assert errors[0][0] == "worker:init" and errors[0][1]
        assert errors[1][0] == "probe" and errors[1][1].startswith("AttributeError")
    finally:
        w.shutdown()
        assert w.wait(2000)                                    # 线程正常退出
