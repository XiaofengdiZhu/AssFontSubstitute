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
    # 复用页面真实任务：probe 已随 lib 在 worker 线程完成，载荷为
    # (snapshot, [DirStatus...])（payload 形状与生产 _lib_job 保持一致）
    from ass_font_substitute.gui.pages.library_page import _lib_job
    return _lib_job(ctx, dirs)


def test_library_page_add_and_stats(qapp, tmp_path, monkeypatch):
    # load_library 只建空库骨架（不自动索引），先经 worker 跑一次索引
    # 再拉 lib 快照，统计卡才有字体数
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    (d / "b_bad.ttf").write_bytes(b"not a font")    # 坏文件：入 bad 表不计实体
    from PySide6.QtTest import QTest          # 本版 PySide6 无模块级 qWait
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    page = w.library_page
    # 回归：更新态按钮原地变「[旋转环]取消更新」且宽度锁定不塌缩，
    # 收尾还原文字/图标/宽度（旧独立取消按钮+外挂巨环方案已废）
    page._set_updating(True)
    w0 = page.btn_update.minimumWidth()
    assert page.btn_update.text() == "取消更新"
    assert page.btn_update.minimumWidth() == page.btn_update.maximumWidth()
    assert w0 >= page.btn_update.sizeHint().width()
    assert page._busy_timer.isActive()
    assert not page.btn_update.icon().isNull()
    page._set_updating(False)
    assert page.btn_update.text() == "更新全部索引"
    assert not page._busy_timer.isActive()
    assert page.btn_update.maximumWidth() == 16777215  # QWIDGETSIZE_MAX
    assert page.vm.add_dir(str(d)) is True
    page.worker.submit("update", _update_dirs_job, [str(d)])
    page.worker.submit("lib", _ensure_lib_job, [str(d)])
    # 等待串行工作者处理完：update 任务含 FreeType 冷导入实测 ~2.3s、
    # lib 再 ~0.6s，预算给足 6s（旧 2s 预算在本机偶发/必挂=假 flaky）
    for _ in range(300):
        QTest.qWait(20)
        if page.vm.stats.get("fonts") == 1:
            break
    assert page.vm.stats.get("fonts") == 1
    row = page.vm.dirs[0]
    # 目录行统计：文件=盘上全部支持后缀（含坏文件）；失败/旧版=bad 表
    assert (row.files, row.fonts, row.fail) == (2, 1, 1)
    # 失败清单卡动态计数（PushSettingCard 无 content() 读口，走 contentLabel）
    assert "坏文件 1" in page.failures_card.contentLabel.text()
    w.close()


def test_library_changed_propagates(qapp, tmp_path, monkeypatch):
    # 删目录广播：browse 页真清数据（不是只挂 hint），subs 页换代重分类；
    # 更新在途时目录行按钮（更新/删除）禁用
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    from PySide6.QtTest import QTest
    from qfluentwidgets import ToolButton
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    page = w.library_page
    assert page.vm.add_dir(str(d)) is True
    page.worker.submit("update", _update_dirs_job, [str(d)])
    page.worker.submit("lib", _ensure_lib_job, [str(d)])
    for _ in range(300):
        QTest.qWait(20)
        if page.vm.stats.get("fonts") == 1:
            break
    assert page.vm.stats.get("fonts") == 1
    w.browse_page.reload_library()
    for _ in range(300):
        QTest.qWait(20)
        if w.browse_page.pvm.entities:
            break
    assert len(w.browse_page.pvm.entities) == 1

    # 更新在途：行内按钮禁用（_index_worker 先于重排行赋值）；
    # ToolButton 含标题栏刷新钮（常 enabled），禁用的是行内更新+删除两枚
    page._index_worker = object()
    page._rebuild_dir_rows()
    row = page.dirs_box.itemAt(0).widget()
    assert len([b for b in row.findChildren(ToolButton)
                if not b.isEnabled()]) == 2
    page._index_worker = None
    page._rebuild_dir_rows()
    row = page.dirs_box.itemAt(0).widget()
    assert all(b.isEnabled() for b in row.findChildren(ToolButton))

    gen0 = w.subs_page._gen
    page._remove_dir_confirmed(str(d))          # → libraryChanged 广播（确认弹窗路径 offscreen 不测，直接走确认后段）
    assert w.browse_page.pvm.entities == []
    assert not w.browse_page.hint.isHidden()
    assert w.browse_page.count_label.text() == "0 款字体"
    assert w.subs_page._gen == gen0 + 1
    w.close()


def test_dir_library_stats_maps_normalized_paths(qapp, tmp_path, monkeypatch):
    # 实体 dir_path 是 str(Path()) 规范形（反斜杠），目录表原样（可正斜杠）
    from types import SimpleNamespace
    from ass_font_substitute.gui.pages.library_page import _dir_library_stats
    ent = SimpleNamespace(dir_path=str(tmp_path), rel_path="a.ttf", script="简")
    fwd = str(tmp_path).replace("\\", "/")
    snap = SimpleNamespace(lib=SimpleNamespace(
        entities=lambda: [ent],
        skipped=lambda: [f"{str(tmp_path)}\\a.ttf#1 旧版本"]))
    out = _dir_library_stats(snap, [fwd])
    assert out[fwd] == {"fonts": 1, "sc": 1, "tc": 0, "skipped": 1}
    # 未知目录（非本库目录）不进结果
    assert _dir_library_stats(snap, ["X:/nowhere"])["X:/nowhere"]["fonts"] == 0
