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
        self.mono = False

    def mono_cells(self):
        return self.mono

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


def _make_bar(qapp, width_fn=None, fit_width=False):
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
    w = _FakeWorker()
    pvm = _FakePVM()
    bar = PreviewBar(w, pvm, _ent("fp1", "甲字体"), "永", "t:fp1",
                     fit_width=fit_width, width_fn=width_fn)
    return w, pvm, bar


def test_render_failure_shows_fullwidth_text_and_tooltip(qapp):
    w, _pvm, bar = _make_bar(qapp)
    assert bar.text() == "…"                       # 初始占位
    w.result.emit("t:fp1", ("永", False, None, "FontParseError: boom"))
    assert bar.text() == "（渲染失败）"
    assert bar.toolTip() == "FontParseError: boom"
    assert not bar._inflight                       # 收尾复位，可再派发


def test_stale_failure_is_discarded(qapp):
    w, _pvm, bar = _make_bar(qapp)
    w.result.emit("t:fp1", ("旧文字", False, None, "boom"))  # 文字不匹配：丢弃
    assert bar.text() == "…"
    assert bar._inflight                           # 未收尾

    w.result.emit("t:fp1", ("永", False, None, None))  # 正常空 bundle 路径不受影响
    assert bar.text() == "（无可渲染字形）"


def test_bar_refits_pixmap_to_layout_width(qapp):
    # QLabel 只裁不缩：贴图按实际可用宽重贴（上限不再写死 360，用户拍板
    # 2026-10-29 预览吃满行宽），扣呼吸边；变宽随之放宽
    import numpy as np

    _w, _pvm, bar = _make_bar(qapp)
    strip = np.full((48, 900), 255, dtype=np.uint8)
    bar.show()
    qapp.processEvents()        # 未显示的 widget resize 不发事件：先 show
    bar.resize(200, 28)
    bar._apply(strip)
    assert bar.pixmap().width() == 184              # 可用宽 200-16
    bar.resize(500, 28)
    bar._apply(strip)
    assert bar.pixmap().width() == 484              # 放宽到新可用宽
    bar.resize(1000, 28)
    bar._apply(np.full((48, 2400), 255, dtype=np.uint8))
    assert bar.pixmap().width() == 984              # 无 360 上限：吃满可用宽


def test_width_fn_budget_refit_and_fallback(qapp):
    # width_fn 行（字幕卡选项行）：预算≤0（布局未跑）退兜底 360；预算
    # 就位后行 resize 回调 budget_changed 按真预算重贴
    import numpy as np

    budget = {"w": 0}
    _w, _pvm, bar = _make_bar(qapp, width_fn=lambda: budget["w"])
    strip = np.full((48, 900), 255, dtype=np.uint8)
    bar.show()
    qapp.processEvents()
    bar.resize(100, 28)
    bar._apply(strip)
    assert bar.pixmap().width() == 360              # 预算 0：兜底上限
    budget["w"] = 250
    bar.budget_changed()
    assert bar.pixmap().width() == 250              # 真预算重贴
    budget["w"] = 400
    bar.budget_changed()
    assert bar.pixmap().width() == 400


def test_fit_width_auto_refits_on_resize(qapp):
    # fit_width=True（浏览页/选择器行）：resizeEvent 自动按新可用宽重贴
    # （不手动 _apply），360 上限已删——长条带吃满行宽扣呼吸边
    import numpy as np

    _w, _pvm, bar = _make_bar(qapp, fit_width=True)
    bar._apply(np.full((48, 2400), 255, dtype=np.uint8))
    bar.show()
    qapp.processEvents()        # 未显示的 widget resize 不发事件：先 show
    bar.resize(200, 28)
    assert bar.pixmap().width() == 184              # resize 即自动重贴
    bar.resize(500, 28)
    assert bar.pixmap().width() == 484              # 无 360 上限


def test_long_strip_truncated_not_shrunk(qapp):
    # 过长截断不缩放（用户拍板 2026-10-29：字号恒定）：超预算裁右缘，
    # 显示高保持满 28（旧 scaledToWidth 会把高也压到 ~13）
    import numpy as np

    budget = {"w": 184}
    _w, _pvm, bar = _make_bar(qapp, width_fn=lambda: budget["w"])
    bar.show()
    qapp.processEvents()
    bar._apply(np.full((48, 900), 255, dtype=np.uint8))
    assert bar.pixmap().width() == 184              # 预算宽截断
    assert bar.pixmap().height() == 28              # 高度不缩：字号恒定


def test_mono_flag_keys_cache_and_dispatch(qapp):
    # 强制等宽口径进缓存键与派发参数；在途翻转后旧口径回传丢弃、重派
    # 走新口径（rerender 由页面广播触发）
    import numpy as np

    w, pvm, bar = _make_bar(qapp)
    assert w.submits[-1][2] == (bar._entity, "永", False)   # 派发带口径
    strip = np.full((4, 40), 255, dtype=np.uint8)
    w.result.emit("t:fp1", ("永", False, strip, None))
    assert pvm.cache[(bar._entity.fingerprint, "永", False)] is strip

    pvm.mono = True
    bar.rerender()
    assert w.submits[-1][2] == (bar._entity, "永", True)    # 新口径重派
    w.result.emit("t:fp1", ("永", False, strip, None))      # 旧口径回传：丢弃
    assert bar.text() == "…"                                # 保持占位不贴错
    new_strip = np.full((4, 60), 255, dtype=np.uint8)
    w.result.emit("t:fp1", ("永", True, new_strip, None))
    assert pvm.cache[(bar._entity.fingerprint, "永", True)] is new_strip
