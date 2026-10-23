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


def _update_dirs_job(ctx, dirs):
    from ass_font_substitute.gui.jobs import update_dir
    for d in dirs:
        update_dir(d)


def _ensure_lib_job(ctx, dirs):
    from ass_font_substitute.gui.jobs import ensure_library
    return ensure_library(ctx, dirs)


def test_library_page_add_and_stats(qapp, tmp_path, monkeypatch):
    # load_library 只建空库骨架（不自动索引），先经 worker 跑一次索引
    # 再拉 lib 快照，统计卡才有字体数
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    from PySide6.QtTest import QTest          # 本版 PySide6 无模块级 qWait
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    page = w.library_page
    assert page.vm.add_dir(str(d)) is True
    page.worker.submit("update", _update_dirs_job, [str(d)])
    page.worker.submit("lib", _ensure_lib_job, [str(d)])
    # 等待串行工作者处理完（上限 2s，超时即断言失败，属预期行为）
    for _ in range(100):
        QTest.qWait(20)
        if page.vm.stats.get("fonts") == 1:
            break
    assert page.vm.stats.get("fonts") == 1
    w.close()
