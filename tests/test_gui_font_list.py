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
    app = QApplication.instance() or QApplication([])
    yield app


def _ent(fp, name):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=400, width=5,
                      italic=False, script="简", category="黑",
                      cmap=CmapRanges.from_chars([0x6c38]), dir_path="d",
                      rel_path="r.ttf", face_index=0)


class _FakeWorker(QObject):
    """只实现 PreviewBar 用到的接口；submit 记账、result 手动发射。"""

    result = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        self.submits.append((tag, fn, args))


def test_font_list_lazy_materialization(qapp):
    # 回归：17k 库 set_rows 不得急切构建行控件（spec §10 虚拟滚动）——
    # 只有可视区 ±WINDOW_PAD 行物化并派渲染，滚动窗口外行控件释放
    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import ROW_H
    from ass_font_substitute.gui.widgets.font_list import ROW_SPACING
    from ass_font_substitute.gui.widgets.font_list import WINDOW_PAD
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)                     # ~9 行可视
    fl.show()
    qapp.processEvents()

    def materialized():
        return [i for i in range(fl.count())
                if fl.itemWidget(fl.item(i)) is not None]

    emitted = []
    fl.count_changed.connect(emitted.append)
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])
    assert emitted[-1] == 200 and fl.count() == 200

    vis = materialized()
    assert vis and min(vis) == 0            # 首屏窗口已物化
    assert len(vis) <= 600 // (ROW_H + ROW_SPACING) + 2 * WINDOW_PAD + 1
    assert fl.itemWidget(fl.item(199)) is None          # 远端行未构建
    assert len(fl._worker.submits) == len(vis)          # 仅物化行派渲染任务

    fl.verticalScrollBar().setValue(150)    # ScrollPerItem：单位即行
    qapp.processEvents()
    far = materialized()
    assert far and 0 not in far             # 首屏行控件已释放
    assert fl.itemWidget(fl.item(150)) is not None
    assert all(abs(i - 150) <= WINDOW_PAD + 600 // (ROW_H + ROW_SPACING) + 1
               for i in far)

    picked = []
    fl.entity_picked.connect(picked.append)
    fl._on_click(fl.item(150))
    assert picked and picked[0].fingerprint == "150"    # _on_click 用 _rows


def test_pill_follows_theme(qapp):
    # 药丸 paintEvent 现取主题色（qfw VerticalSeparator 同法）：渲染像素验证
    # 暗浅两套底色 alpha 不同，setTheme 后旧实例自动换装
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.widgets.font_card import _pill

    def bg_alpha(w):
        img = QImage(w.size(), QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        w.render(img)
        return img.pixelColor(QPoint(2, w.height() // 2)).alpha()

    try:
        setTheme(Theme.DARK)
        p = _pill("W400")
        # render 会先铺不透明调色板底，挂半透明属性让药丸自身 alpha 露出
        p.setAttribute(Qt.WA_TranslucentBackground)
        p.resize(p.sizeHint())
        a_dark = bg_alpha(p)
        setTheme(Theme.LIGHT)
        a_light = bg_alpha(p)
        assert a_dark > a_light >= 0     # 暗 26α 白底 / 亮 15α 黑底
    finally:
        setTheme(Theme.AUTO)
        p.deleteLater()
