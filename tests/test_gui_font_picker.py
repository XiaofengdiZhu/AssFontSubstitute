"""字体选择弹窗：pickreco 取消通路（弹窗关闭即置标志，任务提前退出）。"""
import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

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


def _ent(fp, name):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=400, width=5,
                      italic=False, script="简", category="黑",
                      cmap=CmapRanges.from_chars([ord(c) for c in "永宇"]),
                      dir_path="d", rel_path="r.ttf", face_index=0)


def test_picker_close_cancels_reco_and_none_payload_hides_rec(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog
    from ass_font_substitute.gui.settings_store import Settings

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    ents = [_ent("fp1", "甲字体"), _ent("fp2", "乙字体")]
    host = QWidget()
    host.resize(1100, 750)
    dlg = FontPickerDialog(s, w, ents, chars=set("永宇"), parent=host)

    # 提交带了取消标志字典（列表里混有预览渲染的 plist 任务，按 tag 找）
    reco = next(t for t in w.submits if t[0] == "pickreco")
    cancel = reco[2][3]
    assert cancel == {"cancelled": False}

    # 关闭（done→finished）置标志
    dlg.finished.emit(0)
    assert cancel["cancelled"] is True

    # 取消/空载荷的回执：解订阅、不显示推荐区
    dlg._on_reco("pickreco", None)
    assert dlg._reco is None
    assert not dlg.rec_title.isVisible()
