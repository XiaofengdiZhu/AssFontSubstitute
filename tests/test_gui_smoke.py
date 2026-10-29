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
    # 隔离数据目录：Cache/设置不落真实目录（含仓库根 Data/）
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    assert w.library_page.objectName() == "libraryPage"
    assert w.browse_page.objectName() == "browsePage"
    assert w.subs_page.objectName() == "subsPage"
    assert w.settings_page.objectName() == "settingsPage"
    # 最小窗口盖住最宽页硬约束（库页 823 + 导航 48 + 余量）
    assert w.minimumWidth() == 900
    assert w.minimumHeight() == 620
    # 应用图标：assets/icon.ico 随包分发，真实解码非空帧
    assert not w.windowIcon().isNull()
    assert not w.windowIcon().pixmap(32, 32).isNull()
    w.close()


def test_resize_border_band_widened(qapp, tmp_path, monkeypatch):
    # qframelesswindow WM_NCHITTEST 命中带出厂 5 物理px，高 DPI 难抓住；
    # 主窗口实例覆写成原生同级 ≥8 逻辑px（offscreen DPR=1）
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    assert w.BORDER_WIDTH >= 8
    # 库属性未被污染：只覆写实例
    from qframelesswindow.windows import WindowsFramelessWindowBase
    assert WindowsFramelessWindowBase.BORDER_WIDTH == 5
    w.close()


def test_load_completed_signal_fires_once(qapp, tmp_path):
    # 启动画面收幕信号：无目录同步就绪；有目录首个 lib 结果/失败收幕；
    # 后续刷新不再发（_lib_reported 一次性）
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal

    from ass_font_substitute.gui.pages.library_page import LibraryPage
    from ass_font_substitute.gui.settings_store import Settings

    class _FakeWorker(QObject):
        result = Signal(object, object)
        failed = Signal(object, object)

        def submit(self, *a):
            pass

    class _Lib:
        @staticmethod
        def stats():
            return {}

        @staticmethod
        def dir_overlaps():
            return []

    class _Snap:
        lib = _Lib
        offline_dirs = ()

    # 无目录：构造即就绪（singleShot(0) 挂事件循环，drain 后可见——
    # 生产里 main() 先 connect 再 exec，无竞态）
    lp = LibraryPage(Settings(tmp_path / "s1.json"), _FakeWorker())
    got = []
    lp.loadCompleted.connect(lambda: got.append(1))
    qapp.processEvents()
    assert got == [1]

    # 有目录：lib 结果到达收幕一次，再刷不发
    s2 = Settings(tmp_path / "s2.json")
    s2.set("font_dirs", [r"D:\fonts"])
    w2 = _FakeWorker()
    lp2 = LibraryPage(s2, w2)
    got2 = []
    lp2.loadCompleted.connect(lambda: got2.append(1))
    assert got2 == []                        # 在途未就绪
    w2.result.emit("lib", _Snap)
    qapp.processEvents()
    assert got2 == [1]
    w2.result.emit("lib", _Snap)
    qapp.processEvents()
    assert got2 == [1]                       # 只发一次

    # 失败路径也要收幕（不能卡死在启动画面后）
    s3 = Settings(tmp_path / "s3.json")
    s3.set("font_dirs", [r"D:\fonts2"])
    w3 = _FakeWorker()
    lp3 = LibraryPage(s3, w3)
    got3 = []
    lp3.loadCompleted.connect(lambda: got3.append(1))
    w3.failed.emit("lib", "boom")
    qapp.processEvents()
    assert got3 == [1]


def test_worker_init_failure_is_observable(qapp, tmp_path, monkeypatch):
    # 回归：Cache 初始化失败必须发 failed("worker:init") 且线程存活——
    # 后续任务逐个 failed，而不是静默死线程 + submit 永久悬挂
    import time

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker

    blocker = tmp_path / "blocker"
    blocker.write_text("", "utf-8")
    # 缓存位置不可选后造败改走数据目录：Data 父级是文件 → Cache 建目录必败
    monkeypatch.setenv("AFS_DATA_DIR", str(blocker / "inner" / "data"))
    s = Settings(tmp_path / "s.json")
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


def test_worker_skips_cancelled_jobs(qapp, tmp_path, monkeypatch):
    # 取消句柄：已置 cancelled 的任务弹出即跳过，不执行不回传——
    # 预览列表快速滚动靠这个把过期渲染从串行队列里除名
    import threading
    import time

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker

    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    s = Settings(tmp_path / "s.json")
    w = CoreWorker(s)
    results = []
    w.result.connect(lambda tag, p: results.append(tag))
    w.start()
    started, release = threading.Event(), threading.Event()

    def blocker(ctx):
        started.set()
        release.wait(5)
        return "b"

    try:
        w.submit("b", blocker)
        assert started.wait(5)              # 阻塞住工作者，钉死取消时机
        j = w.submit("c", lambda ctx: "c")
        j.cancelled = True                  # 此刻 b 未完，c 必然未出队
        release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and "b" not in results:
            qapp.processEvents()
            time.sleep(0.01)
        assert results == ["b"]             # c 已取消：不执行不回传
    finally:
        w.shutdown()
        assert w.wait(2000)


def test_shutdown_drains_queued_jobs(qapp, tmp_path, monkeypatch):
    # 退出排空（终审 P0）：shutdown 先把积压任务全置 cancelled 再放
    # 哨兵——否则哨兵排队尾，退出要等积压全跑完（大库下 wait 必超时
    # → 进程带活线程退出硬崩）
    import threading
    import time

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.workers import CoreWorker

    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    s = Settings(tmp_path / "s.json")
    w = CoreWorker(s)
    results = []
    w.result.connect(lambda tag, p: results.append(tag))
    w.start()
    started, release = threading.Event(), threading.Event()

    def blocker(ctx):
        started.set()
        release.wait(5)
        return "b"

    try:
        w.submit("b", blocker)
        assert started.wait(5)              # 阻塞住工作者，积压停在队列里
        handles = [w.submit(f"j{i}", lambda ctx: "x") for i in range(3)]
        w.shutdown()                        # 排空：三个积压全被取消
        release.set()
        assert w.wait(5000)                 # 哨兵直达，不等积压跑完
        deadline = time.monotonic() + 5.0   # result 跨线程排队投递，主线程要转事件循环
        while time.monotonic() < deadline and "b" not in results:
            qapp.processEvents()
            time.sleep(0.01)
        assert results == ["b"] and all(h.cancelled for h in handles)
    finally:
        release.set()


def test_index_worker_cancel_dir(qapp, tmp_path, monkeypatch):
    # 单停：排队目录到队即跳（回发取消结果让行按钮脱「更新中」），
    # 在途目录 progress 回调返 False 中断剩余文件，其余目录照常
    import time

    import ass_font_substitute.gui.jobs as jobs
    from ass_font_substitute.core.fontindex.store import UpdateResult
    from ass_font_substitute.gui.workers import IndexWorker

    calls, seen = [], {}

    def fake_update_dir(d, progress=None, max_threads=1, force=False):
        calls.append(d)
        assert max_threads >= 1               # 设置页 max_threads 透传通路
        if progress is not None:
            seen["stop_requested"] = not progress(1, 10, UpdateResult())
        return UpdateResult(cancelled=seen.get("stop_requested", False))

    monkeypatch.setattr(jobs, "update_dir", fake_update_dir)
    w = IndexWorker(["J:/a", "J:/b", "J:/c"])
    done = []
    w.finished_dir.connect(lambda d, r: done.append((d, r.cancelled)))
    w.cancel_dir("J:/b")                    # 尚未开跑：b 排队即跳
    w.start()
    # finished_dir 是跨线程队列连接，主线程须转事件循环才有回执
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(done) < 3:
        qapp.processEvents()
        time.sleep(0.01)
    assert w.wait(2000)                     # 三条回执收齐≠线程簿记已完成
    assert calls == ["J:/a", "J:/c"]        # b 未执行
    assert ("J:/b", True) in done           # b 回发取消结果
    assert ("J:/a", False) in done          # a 照常完成


def test_window_geometry_restore(qapp, tmp_path, monkeypatch):
    # 位置/大小记忆：屏内存档按档恢复；屏外存档弃用回默认；max 标记
    # 独立恢复；关闭时把当前几何落盘
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QGuiApplication

    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    avail = QGuiApplication.primaryScreen().availableGeometry()
    gw = min(1000, avail.width() - 40)
    gh = min(700, avail.height() - 40)

    s = Settings(tmp_path / "s1.json")
    s.set("win_geometry", {"x": avail.x() + 8, "y": avail.y() + 8,
                           "w": gw, "h": gh, "max": False})
    w = AFSWindow(s)
    # 位置契约：屏内存档原位；放不下右/下缘时钳回。实现先钳尺寸再定位
    # （Qt 最小宽强制发生在 setGeometry），期望按钳后宽算（offscreen 屏
    # 800 宽小于窗口 900 最小宽，必钳到 0——真机屏宽足够则原位）
    cw = min(w.width(), avail.width())
    ch = min(w.height(), avail.height())
    ex = min(avail.x() + 8, avail.right() - cw + 1)
    ey = min(avail.y() + 8, avail.bottom() - ch + 1)
    assert (w.geometry().x(), w.geometry().y()) == (ex, ey)
    # 尺寸钳制链：不小于窗口最小值、不大于可用区；屏比最小值还小时
    # setGeometry 由 Qt 强制回最小值（offscreen 屏 800 宽 < 900 即此况）
    want = (QSize(gw, gh).expandedTo(QSize(900, 620))
            .boundedTo(avail.size()).expandedTo(QSize(900, 620)))
    assert (w.width(), w.height()) == (want.width(), want.height())
    w.close()

    s2 = Settings(tmp_path / "s2.json")
    s2.set("win_geometry", {"x": avail.x() + 99999, "y": avail.y(),
                            "w": gw, "h": gh, "max": False})
    w2 = AFSWindow(s2)
    # 屏外弃用→默认尺寸+主屏居中（窗口比屏大时偏移取 0 不出屏）；
    # 居中只在真实窗口系统生效（offscreen 下 ctor 重定位毒化渲染），
    # pos() 是含框位置（geometry() 是客户区，差帧边距）
    assert w2.width() == 1100
    if QGuiApplication.platformName() != "offscreen":
        assert (w2.pos().x(), w2.pos().y()) == (
            avail.x() + max(0, (avail.width() - 1100) // 2),
            avail.y() + max(0, (avail.height() - 750) // 2))
    w2.close()

    # 部分越界（右缘拖出一半，交集仍够一块标题条）：整窗钳回交集最大的屏
    s2b = Settings(tmp_path / "s2b.json")
    s2b.set("win_geometry", {"x": avail.right() - 300, "y": avail.y() + 8,
                             "w": gw, "h": gh, "max": False})
    w2b = AFSWindow(s2b)
    assert w2b.geometry().x() == avail.left()
    w2b.close()

    s3 = Settings(tmp_path / "s3.json")
    s3.set("win_geometry", {"x": avail.x() + 8, "y": avail.y() + 8,
                            "w": gw, "h": gh, "max": True})
    w3 = AFSWindow(s3)
    # 真实平台 ctor 期直接置最大化态；offscreen 隐藏窗口 setWindowState
    # 走 qframelesswindow 的 win32 假句柄通路会炸，只挂标记供此处断言
    assert w3.isMaximized() or w3._pending_maximized
    w3.close()

    s4 = Settings(tmp_path / "s4.json")
    w4 = AFSWindow(s4)
    w4.show()
    qapp.processEvents()
    g4 = w4.geometry()
    w4.close()
    saved = Settings(tmp_path / "s4.json").get("win_geometry")
    assert (saved["x"], saved["y"], saved["w"], saved["h"]) == (
        g4.x(), g4.y(), g4.width(), g4.height())
    assert saved["max"] is False


def test_dir_row_no_top_level_flash(qapp, tmp_path, monkeypatch):
    # 回归：重排行时「新增/修改」提示曾先 setVisible(True) 后入父——
    # 无父 setVisible 即顶层窗口上屏（取消更新后行回 stale 重排时
    # 闪现带标题栏小窗），修复后必须先 addWidget 再 setVisible
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    page = w.library_page
    page.vm.add_dir(str(tmp_path / "fonts"))
    page.vm.dirs[0].state = "stale"        # 触发 hint.setVisible(True) 的行

    class Spy(QObject):
        def __init__(self):
            super().__init__()
            self.flashed = None

        def eventFilter(self, obj, ev):
            # 顶层 Show 且非主窗 = 有控件以独立窗口上屏
            if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and obj.isWindow() and obj is not w):
                self.flashed = obj
            return False

    spy = Spy()
    qapp.installEventFilter(spy)
    try:
        page._rebuild_dir_rows()
    finally:
        qapp.removeEventFilter(spy)
    assert spy.flashed is None
    w.close()


def test_dir_row_progress_strip(qapp, tmp_path, monkeypatch):
    # 行内进度：indexing 行挂行底细条+行中文本；逐文件回执只原地推进
    # （不重排行），中途回排行从缓存恢复；脱 indexing 后进度区移除
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    from PySide6.QtWidgets import QProgressBar
    from qfluentwidgets import IndeterminateProgressBar

    from ass_font_substitute.core.fontindex.store import UpdateResult
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.pages.library_page import _STATS_TMPL
    from ass_font_substitute.gui.pages.library_page import _progress_text
    from ass_font_substitute.gui.settings_store import Settings

    assert _progress_text(12308, 17583) == "70% · 12308/17583"
    assert _progress_text(0, 0) == "准备中…"   # 首笔回执前（扫描中）

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    page = w.library_page
    assert page.vm.add_dir(str(d)) is True
    page.vm.mark_indexing(str(d))
    page._index_worker = object()          # 模拟在途（不真起线程）
    page._rebuild_dir_rows()
    ind, det, lbl = page._progress_widgets[str(d)]
    assert isinstance(ind, IndeterminateProgressBar)
    # offscreen 窗口未 show，isVisible 恒 False，用 isHidden 断显隐
    assert det.isHidden() and not ind.isHidden()
    assert lbl.text() == "准备中…"

    page._on_index_progress(str(d), 12308, 17583, None)
    assert not det.isHidden() and ind.isHidden()
    assert det.value() == 12308 and det.maximum() == 17583
    assert lbl.text() == "70% · 12308/17583"

    page._rebuild_dir_rows()               # 他目录收尾触发重排行：进度恢复
    ind2, det2, lbl2 = page._progress_widgets[str(d)]
    assert (ind2, det2, lbl2) != (ind, det, lbl)
    assert det2.value() == 12308 and det2.maximum() == 17583
    assert lbl2.text() == "70% · 12308/17583"

    page._index_worker = None
    page._on_index_done(str(d), UpdateResult())
    assert str(d) not in page._progress_widgets
    row = page.dirs_box.itemAt(0).widget()
    # QProgressBar 基类连不定态条一起断（其非 ProgressBar 子类，防泄漏漏报）
    assert not row.findChildren(QProgressBar)

    # 左块两行（路径+统计）锁同一定宽=统计模板宽：行中进度文案位置逐行一致
    grp = row.groupWidgets[0]
    want = grp.contentLabel.fontMetrics().horizontalAdvance(_STATS_TMPL)
    assert grp.contentLabel.minimumWidth() == want
    assert grp.contentLabel.maximumWidth() == want
    path_lbl = grp.textLayout.itemAt(0).widget()
    assert path_lbl.minimumWidth() == path_lbl.maximumWidth() == want
    w.close()


def _update_dirs_job(ctx, dirs):
    from ass_font_substitute.gui.jobs import update_dir
    for d in dirs:
        update_dir(d)


def _ensure_lib_job(ctx, dirs):
    # 复用页面真实任务：_lib_job 现只做聚合库快照，载荷为 snapshot
    # （payload 形状与生产保持一致；目录行明细走收幕后补的 libdetail 任务）
    from ass_font_substitute.gui.pages.library_page import _lib_job
    return _lib_job(ctx, dirs)


def test_library_page_add_and_stats(qapp, tmp_path, monkeypatch):
    # load_library 只建空库骨架（不自动索引），先经 worker 跑一次索引
    # 再拉 lib 快照，统计卡才有字体数
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    (d / "b_bad.ttf").write_bytes(b"not a font")    # 坏文件：入 bad 表不计实体
    from PySide6.QtTest import QTest  # 本版 PySide6 无模块级 qWait

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
    assert page.btn_update.text() == "更新全部"
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
    # 目录行统计：文件=盘上全部支持后缀（含坏文件）；失败/重复=bad 表。
    # 行明细在收幕后的 libdetail 任务里落定，再等它
    for _ in range(150):
        QTest.qWait(20)
        if row.state == "ok":
            break
    assert (row.files, row.fonts, row.fail) == (2, 1, 1)
    # 失败清单卡动态计数（PushSettingCard 无 content() 读口，走 contentLabel）
    assert "坏文件 1" in page.failures_card.contentLabel.text()
    w.close()


def test_library_changed_propagates(qapp, tmp_path, monkeypatch):
    # 删目录广播：browse 页真清数据（不是只挂 hint），subs 页换代重分类；
    # 更新在途时目录行按钮（更新/删除）禁用
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永宇", family="甲")
    from PySide6.QtTest import QTest
    from qfluentwidgets import CaptionLabel
    from qfluentwidgets import PushButton

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

    # 更新在途：需更新行按钮+删除钮禁用（_index_worker 先于重排行赋值）；
    # stale 计数小字仅 stale 态可见
    page.vm.dirs[0].state = "stale"
    page._index_worker = object()
    page._rebuild_dir_rows()
    card = page.dirs_box.itemAt(0).widget()
    btns = {b.text(): b for b in card.findChildren(PushButton)}
    assert not btns["需更新"].isEnabled() and not btns["删除"].isEnabled()
    hint = card.findChildren(CaptionLabel)[0]
    assert not hint.isHidden()
    page._index_worker = None
    page._rebuild_dir_rows()
    card = page.dirs_box.itemAt(0).widget()
    btns = {b.text(): b for b in card.findChildren(PushButton)}
    assert btns["需更新"].isEnabled() and btns["删除"].isEnabled()

    # 已最新行保留更新入口（强制重建）：在途禁用、空闲可点
    page.vm.dirs[0].state = "ok"
    page._index_worker = object()
    page._rebuild_dir_rows()
    card = page.dirs_box.itemAt(0).widget()
    btns = {b.text(): b for b in card.findChildren(PushButton)}
    assert not btns["已最新"].isEnabled()
    page._index_worker = None
    page._rebuild_dir_rows()
    card = page.dirs_box.itemAt(0).widget()
    btns = {b.text(): b for b in card.findChildren(PushButton)}
    assert btns["已最新"].isEnabled()

    gen0 = w.subs_page._gen
    page._remove_dir_confirmed(str(d))          # → libraryChanged 广播（确认弹窗路径 offscreen 不测，直接走确认后段）
    assert w.browse_page.pvm.entities == []
    assert not w.browse_page.hint.isHidden()
    assert w.browse_page.count_label.text() == "0 款字体"
    assert w.subs_page._gen == gen0 + 1
    w.close()


def test_index_worker_force_rebuilds(qapp, tmp_path):
    # force 通路：IndexWorker(force=…) → update_dir(force=True) → 清库
    # 全量重解析（行内「已最新」强制更新的后台半边）
    from tests.fonts import build_font
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="甲")
    from ass_font_substitute.gui.jobs import update_dir
    assert update_dir(str(d)).fonts == 1
    assert update_dir(str(d)).added == 0          # 增量门放行

    from ass_font_substitute.gui.workers import IndexWorker
    assert IndexWorker([str(d)])._force == set()  # 默认不强制
    w = IndexWorker([str(d)], force={str(d)})
    assert w._force == {str(d)}
    results = []
    w.finished_dir.connect(lambda dd, r: results.append((dd, r)))
    w.start()
    # finished_dir 是跨线程队列连接，主线程须转事件循环才有回执
    import time
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline and not results:
        qapp.processEvents()
        time.sleep(0.01)
    assert w.wait(2000)
    assert results and results[0][0] == str(d)
    assert results[0][1].added == 1 and results[0][1].fonts == 1


def test_box_content_wraps_long_path(qapp):
    # 确认框文案预折行封宽：qfw 按父窗口宽折行，长路径单行不折会把
    # 对话框撑满整窗宽；_box_content 按 32 汉字（64 宽单位）预折
    from qfluentwidgets import TextWrap

    from ass_font_substitute.gui.pages.library_page import _box_content
    out = _box_content(
        '是否删除 "Q:/Others/超级字体整合包 XZ 的很长很长的路径目录名" '
        '的已有索引数据库，然后强制更新索引？这将花费一些时间。')
    assert "\n" in out
    assert max(TextWrap.get_text_width(ln)
               for ln in out.splitlines()) <= 64

    # 删除目录框的分段空行保留（TextWrap 逐行处理不吞空行）
    out2 = _box_content("确定从列表移除以下目录吗？\n\nX:\\字体\n\n仅移出列表。")
    assert out2.splitlines()[1] == ""


def test_dir_library_stats_maps_normalized_paths(qapp, tmp_path, monkeypatch):
    # 实体 dir_path 是 str(Path()) 规范形（反斜杠），目录表原样（可正斜杠）
    from types import SimpleNamespace

    from ass_font_substitute.gui.pages.library_page import _dir_library_stats
    ent = SimpleNamespace(dir_path=str(tmp_path), rel_path="a.ttf", script="简")
    fwd = str(tmp_path).replace("\\", "/")
    snap = SimpleNamespace(lib=SimpleNamespace(
        entities=lambda: [ent],
        skipped=lambda: [f"{tmp_path!s}\\a.ttf#1 重复版本"]))
    out = _dir_library_stats(snap, [fwd])
    assert out[fwd] == {"fonts": 1, "sc": 1, "tc": 0, "skipped": 1}
    # 未知目录（非本库目录）不进结果
    assert _dir_library_stats(snap, ["X:/nowhere"])["X:/nowhere"]["fonts"] == 0


def test_window_applies_stored_theme_and_effect(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from qfluentwidgets import Theme
    from qfluentwidgets import qconfig
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    s.set("theme", "dark")
    s.set("bg_effect", "none")
    try:
        w = AFSWindow(s)
        assert qconfig.theme == Theme.DARK        # 建页前已定主题
        assert not w.isMicaEffectEnabled()        # none：FluentWindow 出厂 Mica 已关
        w.settings.set("bg_effect", "mica")       # store 信号驱动窗口应用
        assert w.isMicaEffectEnabled()
    finally:
        setTheme(Theme.AUTO)                      # 还原全局主题，防污染后续测试
        w.close()


def test_theme_color_custom_follow_and_fallback(qapp, tmp_path, monkeypatch):
    # 强调色三态：自定义 #rrggbb 直接生效；跟随系统取 monkeypatch 的系统色；
    # 系统色取不到回落 qfw 出厂色 #009faa。setThemeColor 全局，finally 复位
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from PySide6.QtGui import QColor
    from qfluentwidgets import Theme
    from qfluentwidgets import qconfig
    from qfluentwidgets import setTheme
    from qfluentwidgets import setThemeColor

    import ass_font_substitute.gui.app as app_mod
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    def reset():
        setThemeColor("#009faa", save=False)      # qfw 出厂色复位防污染

    try:
        reset()
        s = Settings(tmp_path / "s.json")
        # 回归：强调色应用在建页前（主题先行约束），页面 primary 按钮当时
        # 不存在——建页后必须补一轮 tune，否则浅强调色下重启白字不可读
        s.set("theme_color", "#f5c1cb")
        w = AFSWindow(s)
        assert QColor(qconfig.themeColor.value) == QColor("#f5c1cb")
        # 主色按钮现是「添加目录」（更新全部已换普通灰底）
        assert "rgba(0, 0, 0" in w.library_page.btn_add.property(
            "lightCustomQss")

        # 切自定义深色：覆盖撤销 + 色值生效
        s.set("theme_color", "#0065d5")
        assert QColor(qconfig.themeColor.value) == QColor("#0065d5")
        assert not w.library_page.btn_add.property("lightCustomQss")

        # 切跟随系统：取 monkeypatch 的系统强调色（经 store 信号驱动）
        monkeypatch.setattr(app_mod, "_system_accent",
                            lambda: QColor("#112233"))
        s.set("theme_color", "")
        assert QColor(qconfig.themeColor.value) == QColor("#112233")

        # 系统强调色取不到：回落 qfw 出厂色
        monkeypatch.setattr(app_mod, "_system_accent", lambda: None)
        w._apply_theme_color("")
        assert QColor(qconfig.themeColor.value) == QColor("#009faa")
    finally:
        reset()
        setTheme(Theme.AUTO)
        w.close()


def test_dark_theme_pages_are_dark(qapp, tmp_path, monkeypatch):
    # 回归：subs/library/browse 三页 ScrollArea 默认画调色板底（浅色），
    # 深色主题下整页亮块；enableTransparentBackground 后页面底透出窗口
    # 实色（none 材质下 = 深色）
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from PySide6.QtGui import QImage
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    s.set("theme", "dark")
    s.set("bg_effect", "none")
    try:
        w = AFSWindow(s)
        # switchTo 的滑入动画 250ms，processEvents 等不走完，render 会
        # 抓到中间帧的未画区白块（挂哪个页随机）——测试直切
        w.stackedWidget.setAnimationEnabled(False)
        w.resize(1100, 750)
        w.show()
        qapp.processEvents()
        img = QImage(w.size(), QImage.Format_ARGB32_Premultiplied)
        for name in ("subs_page", "library_page", "browse_page",
                     "settings_page"):
            w.switchTo(getattr(w, name))
            qapp.processEvents()
            img.fill(0)
            w.render(img)
            r, g, b, _a = img.pixelColor(600, 500).getRgb()
            from PySide6.QtCore import QPoint as _QP
            _pg = getattr(w, name)
            _wd = _pg.childAt(_pg.mapFrom(w, _QP(600, 500)))
            _ch = []
            while _wd is not None and len(_ch) < 8:
                _ch.append(f"{type(_wd).__name__}"
                           f"({_wd.x()},{_wd.y()} {_wd.width()}x{_wd.height()})")
                _wd = _wd.parentWidget()
            assert (r + g + b) / 3 < 80, \
                f"{name} 底色未随深色主题变暗 pixel={(r, g, b)} pos={w.pos()} chain={' <- '.join(_ch)}"
    finally:
        setTheme(Theme.AUTO)                      # 还原全局主题
        w.close()


def test_page_and_group_titles_follow_theme(qapp, tmp_path, monkeypatch):
    # 回归：页标题/设置组标题原为裸 QLabel（黑字死调色板），深色下不可读；
    # 换 BodyLabel 后 LABEL qss 黑/白随主题
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    try:
        w = AFSWindow(s)
        setTheme(Theme.DARK)
        qapp.processEvents()
        assert "color: white" in w.subs_page.page_title.styleSheet()
        assert "color: white" in w.library_page.page_title.styleSheet()
        cls = type(w.subs_page.page_title)
        titles = [lab for lab in w.settings_page.view.findChildren(cls)
                  if lab.text() in ("常规", "相似候选", "输出", "相似度缓存", "关于")]
        assert len(titles) == 5
        assert all("color: white" in lab.styleSheet() for lab in titles)
    finally:
        setTheme(Theme.AUTO)
        w.close()


def test_failures_dialog_constructs(qapp, tmp_path, monkeypatch):
    # 回归：library_page 收编 import 时误删 SubtitleLabel，失败清单一点就崩。
    # 挂宿主窗构造两种载荷的对话框（不 exec，exec 走模态循环会挂测试）；
    # MaskDialogBase 要有窗才有几何，裸 parent=None 会 AttributeError
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.pages.library_page import _FailuresDialog

    host = QWidget()
    host.resize(1100, 750)
    for data in ({"bad": {}, "skipped": []},
                 {"bad": {"J:/fonts": [("a.ttf", "PostScript 名解析失败")],
                          "K:/x": [("b.otf", "坏表")]},
                  "skipped": ["J:/fonts/old.ttf#旧版 1.0"]}):
        dlg = _FailuresDialog(data, retry=lambda dr, rel: None, parent=host)
        assert dlg.titleLabel.text() == "失败清单"
        dlg.deleteLater()
    host.deleteLater()


def test_retry_gate_and_settled_debounce(qapp, tmp_path, monkeypatch):
    # 终审 P1 两条：①索引更新在途时重试拒绝（与 IndexWorker 并发写同库）；
    # ②重试收尾去抖——N 笔回执只触发一轮 refresh_status（原逐笔各刷一
    # 轮全目录探测，N 坏文件=N 轮灌满串行队列）
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal

    from ass_font_substitute.gui.pages.library_page import LibraryPage
    from ass_font_substitute.gui.settings_store import Settings

    class _FakeWorker(QObject):
        result = Signal(object, object)
        failed = Signal(object, object)

        def __init__(self):
            super().__init__()
            self.submits = []

        def submit(self, *a):
            self.submits.append(a)

    lp = LibraryPage(Settings(tmp_path / "s.json"), _FakeWorker())
    refreshes = []
    monkeypatch.setattr(lp, "refresh_status", lambda: refreshes.append(1))
    # ① 门：索引在途拒绝
    lp._index_worker = object()      # 非None 即在途（只用 isNone 判定）
    lp._submit_retry("J:/f", "a.ttf")
    assert lp.worker.submits == []
    lp._index_worker = None
    # ② 风暴：3 笔重试只刷一轮
    for _ in range(3):
        lp._submit_retry("J:/f", "a.ttf")
    assert len([s for s in lp.worker.submits if s[0].startswith("retry:")]) == 3
    for i in range(3):
        lp._on_result(f"retry:J:/f:a{i}", None)
    lp.deleteLater()
    assert refreshes == [1]          # 尾触发一轮，非 3 轮


def test_language_change_triggers_browse_name_reload(qapp, tmp_path, monkeypatch):
    # 展示名随界面语言链重挑的接线：valueChanged("language") →
    # browse_page.reload_names（browse 列表重建）；断线即名称滞留旧语言
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.app import AFSWindow
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "settings.json")
    w = AFSWindow(s)
    calls = []
    w.browse_page.reload_names = lambda: calls.append(1)
    s.set("language", "zh-Hant")
    assert calls == [1]
    w.close()
