"""PreviewBar 失败态：渲染异常折进 result 通路，占位换「（渲染失败）」。"""
import os

import pytest
from PySide6.QtCore import QObject
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

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        self.submits.append((tag, fn, args, kw))


class _FakePVM:
    def __init__(self):
        self.cache = {}

    def get_tiles(self, key):
        return self.cache.get(key)

    def put_tiles(self, key, tiles):
        self.cache[key] = tiles


def _ent(fp, name):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=400, width=5,
                      italic=False, script="简", category="黑",
                      cmap=CmapRanges.from_chars([ord(c) for c in "永宇"]),
                      dir_path="d", rel_path="r.ttf", face_index=0)


def _make_bar(qapp):
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
    w = _FakeWorker()
    pvm = _FakePVM()
    bar = PreviewBar(w, pvm, _ent("fp1", "甲字体"), "永", "t:fp1")
    return w, pvm, bar


def test_render_failure_shows_fullwidth_text_and_tooltip(qapp):
    w, _pvm, bar = _make_bar(qapp)
    assert bar.text() == "…"                       # 初始占位
    w.result.emit("t:fp1", ("永", None, "FontParseError: boom"))
    assert bar.text() == "（渲染失败）"
    assert bar.toolTip() == "FontParseError: boom"
    assert not bar._inflight                       # 收尾复位，可再派发


def test_stale_failure_is_discarded(qapp):
    w, _pvm, bar = _make_bar(qapp)
    w.result.emit("t:fp1", ("旧文字", None, "boom"))   # 文字不匹配：丢弃
    assert bar.text() == "…"
    assert bar._inflight                           # 未收尾

    w.result.emit("t:fp1", ("永", None, None))     # 正常空 bundle 路径不受影响
    assert bar.text() == "（无可渲染字形）"
