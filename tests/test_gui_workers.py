import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mk(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))   # Cache/决策库隔离
    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker
    w = CoreWorker(Settings(tmp_path / "settings.json"))
    return w


def _wait(pred, timeout=10.0):
    """轮询等条件（offscreen 无事件循环依赖，processEvents 驱动跨线程
    信号投递；超时静默返回，由调用方断言抓空）。"""
    from PySide6.QtWidgets import QApplication
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if pred():
            return
        time.sleep(0.01)


def test_core_worker_roundtrip_and_cancelled(qapp, tmp_path, monkeypatch):
    w = _mk(qapp, tmp_path, monkeypatch)
    got: list = []
    errs: list = []
    w.result.connect(lambda tag, payload: got.append((tag, payload)))
    w.failed.connect(lambda tag, err: errs.append((tag, err)))
    w.start()
    try:
        # 正常任务回执
        w.submit("t1", lambda ctx: 42)
        _wait(lambda: got)
        assert got == [("t1", 42)]
        assert errs == []

        # 异常任务折 failed（ExcType: msg）
        def boom(ctx):
            raise RuntimeError("炸")
        w.submit("t2", boom)
        _wait(lambda: errs)
        assert errs == [("t2", "RuntimeError: 炸")]

        # shutdown 后提交即弃（防静默忙态）
        w.shutdown()
        h = w.submit("t3", lambda ctx: 1)
        assert h.cancelled
        assert all(t != "t3" for t, _ in got)
    finally:
        w.wait(15000)
    assert not w.isRunning()


def test_core_worker_shutdown_cancels_queued(qapp, tmp_path, monkeypatch):
    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker
    w = CoreWorker(Settings(tmp_path / "settings.json"))
    w.start()
    # 排队任务后立即 shutdown：句柄全置 cancelled，不再执行
    handles = [w.submit(f"q{i}", lambda ctx: 1) for i in range(5)]
    w.shutdown()
    assert any(h.cancelled for h in handles)
    w.wait(15000)


def test_core_worker_shutdown_idempotent(qapp, tmp_path, monkeypatch):
    # closeEvent「继续等待」路径二次调用：残留哨兵 None 不得炸 item[0]
    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker
    w = CoreWorker(Settings(tmp_path / "settings.json"))
    w.start()
    w.shutdown()
    w.shutdown()                                # 二次幂等
    w.wait(15000)
