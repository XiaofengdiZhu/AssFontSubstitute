"""三页拖拽入：库管理/预览页拖入文件夹（确认框后加库）、字幕页拖入
文件与文件夹（静默加载 ass）。视图薄壳只测归集逻辑 + 事件胶水。"""
import os
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PySide6.QtCore import QObject
from PySide6.QtCore import QPointF
from PySide6.QtCore import Qt
from PySide6.QtCore import QUrl
from PySide6.QtCore import Signal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWorker(QObject):
    result = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        self.submits.append((tag, fn, args, kw))
        return SimpleNamespace(cancelled=False)


class _YesBox:
    """qfw MessageBox 替身：记录构造参数，exec 恒确认。"""

    calls: ClassVar[list] = []

    def __init__(self, title, content, parent=None):
        type(self).calls.append((title, content))
        self.yesButton = SimpleNamespace(setText=lambda *a: None)
        self.cancelButton = SimpleNamespace(setText=lambda *a: None)

    def exec(self):
        return 1


class _NoBox(_YesBox):
    def exec(self):
        return 0


def _make_event(cls, paths):
    from PySide6.QtCore import QMimeData
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = cls(QPointF(1, 1), Qt.CopyAction, mime, Qt.LeftButton,
             Qt.NoModifier)
    ev._mime_keepalive = mime   # 事件只存裸指针：mime 被 GC 即 access violation
    return ev


# ---- 纯函数：路径归集 ----

def test_split_dropped(tmp_path):
    from ass_font_substitute.gui.drop import split_dropped
    d = tmp_path / "dd"
    d.mkdir()
    f = tmp_path / "ff.ass"
    f.write_text("", encoding="utf-8")
    dirs, files = split_dropped([str(d), str(f), str(tmp_path / "nope")])
    assert dirs == [str(d)] and files == [str(f)]


def test_accepts_gate(tmp_path):
    from ass_font_substitute.gui.drop import accepts
    d = tmp_path / "dd"
    d.mkdir()
    txt = tmp_path / "t.txt"
    txt.write_text("", encoding="utf-8")
    ass = tmp_path / "t.ass"
    ass.write_text("", encoding="utf-8")
    assert accepts([str(d)], dirs_only=True)
    assert not accepts([str(txt)], dirs_only=True)
    assert accepts([str(ass)], dirs_only=False)
    assert accepts([str(d)], dirs_only=False)
    assert not accepts([str(txt)], dirs_only=False)


# ---- 字幕处理页：拖入即加载，不确认 ----

def test_subs_drop_loads_without_confirm(qapp, tmp_path, monkeypatch):

    d = tmp_path / "subs"
    (d / "nested").mkdir(parents=True)
    (d / "a.ass").write_text("", encoding="utf-8")
    (d / "b.txt").write_text("", encoding="utf-8")
    f = tmp_path / "solo.ass"
    f.write_text("", encoding="utf-8")

    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.pages.subs_page import SubsPage
    from ass_font_substitute.gui.settings_store import Settings
    page = SubsPage(Settings(tmp_path / "s.json"), _FakeWorker())
    assert page.acceptDrops()

    page._apply_dropped([str(f), str(d), str(tmp_path / "b.txt")])
    tags = [t for t, *_ in page.worker.submits]
    assert f"parse:{f}" in tags                       # 直拖文件
    assert any(t.startswith("scandir:") for t in tags)  # 目录走递归扫描
    assert not any("b.txt" in t for t in tags)        # 非 ass 忽略
    page.deleteLater()


def test_subs_drop_event_glue(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QDragEnterEvent
    from PySide6.QtGui import QDropEvent
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.pages.subs_page import SubsPage
    from ass_font_substitute.gui.settings_store import Settings
    page = SubsPage(Settings(tmp_path / "s.json"), _FakeWorker())
    ass = tmp_path / "e.ass"
    ass.write_text("", encoding="utf-8")
    txt = tmp_path / "e.txt"
    txt.write_text("", encoding="utf-8")

    ev = _make_event(QDragEnterEvent, [str(ass)])
    page.dragEnterEvent(ev)
    assert ev.isAccepted()
    ev2 = _make_event(QDragEnterEvent, [str(txt)])
    page.dragEnterEvent(ev2)
    assert not ev2.isAccepted()

    dropped = _make_event(QDropEvent, [str(ass)])
    page.dropEvent(dropped)
    # QUrl 归一化成斜杠形式：按 Path 比对，勿比原始串
    parses = [Path(t[6:]) for t, *_ in page.worker.submits
              if t.startswith("parse:")]
    assert ass in parses
    page.deleteLater()


# ---- 字体库管理页：拖入文件夹，确认框后加库 ----

def _make_lib_page(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    import ass_font_substitute.gui.pages.library_page as m
    from ass_font_substitute.gui.pages.library_page import LibraryPage
    from ass_font_substitute.gui.settings_store import Settings
    page = LibraryPage(Settings(tmp_path / "s.json"), _FakeWorker())
    return page, m


def test_library_drop_confirm_then_add(qapp, tmp_path, monkeypatch):
    page, m = _make_lib_page(qapp, tmp_path, monkeypatch)
    d = tmp_path / "fonts"
    d.mkdir()
    _YesBox.calls = []
    monkeypatch.setattr(m, "MessageBox", _YesBox)
    assert page.acceptDrops()

    page._apply_dropped([str(d)])
    assert _YesBox.calls == [("加入字体库", f"确认将 {d} 加入字体库？")]
    assert [r.path for r in page.vm.dirs] == [str(d)]
    assert str(d) in page.settings.get("font_dirs")   # 已持久化
    page.deleteLater()


def test_library_drop_decline_keeps(qapp, tmp_path, monkeypatch):
    page, m = _make_lib_page(qapp, tmp_path, monkeypatch)
    d = tmp_path / "fonts2"
    d.mkdir()
    monkeypatch.setattr(m, "MessageBox", _NoBox)
    page._apply_dropped([str(d)])
    assert not page.vm.dirs
    page.deleteLater()


def test_library_drop_duplicate_warns(qapp, tmp_path, monkeypatch):
    page, m = _make_lib_page(qapp, tmp_path, monkeypatch)
    d = tmp_path / "fonts3"
    d.mkdir()
    page.vm.add_dir(str(d))
    warned = []
    _YesBox.calls = []               # 清掉前序用例累积的弹窗记录
    monkeypatch.setattr(m, "MessageBox", _YesBox)
    monkeypatch.setattr(m.InfoBar, "warning",
                        lambda title, content, **k: warned.append(content))
    page._apply_dropped([str(d)])
    assert warned and not _YesBox.calls
    assert [r.path for r in page.vm.dirs] == [str(d)]   # 不重复入列
    page.deleteLater()


def test_library_drop_ignores_files(qapp, tmp_path, monkeypatch):
    page, m = _make_lib_page(qapp, tmp_path, monkeypatch)
    f = tmp_path / "x.ttf"
    f.write_text("", encoding="utf-8")
    _YesBox.calls = []
    monkeypatch.setattr(m, "MessageBox", _YesBox)
    page._apply_dropped([str(f)])
    assert not _YesBox.calls and not page.vm.dirs
    page.deleteLater()


# ---- 字体库预览页：拖入文件夹，确认后发信号交库管理页加 ----

def test_browse_drop_emits_dirs_added(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    import ass_font_substitute.gui.pages.browse_page as m
    from ass_font_substitute.gui.pages.browse_page import BrowsePage
    from ass_font_substitute.gui.settings_store import Settings
    page = BrowsePage(Settings(tmp_path / "s.json"), _FakeWorker())
    assert page.acceptDrops()

    d = tmp_path / "bfonts"
    d.mkdir()
    _YesBox.calls = []
    monkeypatch.setattr(m, "MessageBox", _YesBox)
    fired = []
    page.dirsAdded.connect(fired.append)
    page._apply_dropped([str(d)])
    assert fired == [[str(d)]]
    assert _YesBox.calls == [("加入字体库", f"确认将 {d} 加入字体库？")]
    page.deleteLater()


def test_browse_drop_duplicate_no_signal(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    import ass_font_substitute.gui.pages.browse_page as m
    from ass_font_substitute.gui.pages.browse_page import BrowsePage
    from ass_font_substitute.gui.settings_store import Settings
    s = Settings(tmp_path / "s.json")
    (tmp_path / "dup").mkdir()       # 必须真实存在，否则进不了 split 门
    s.set("font_dirs", [str(tmp_path / "dup")])
    page = BrowsePage(s, _FakeWorker())
    fired = []
    page.dirsAdded.connect(fired.append)
    monkeypatch.setattr(m.InfoBar, "warning",
                        lambda title, content, **k: fired.append("warn"))
    page._apply_dropped([str(tmp_path / "dup")])
    assert fired == ["warn"]                          # 只警告，无确认框无信号
    page.deleteLater()
