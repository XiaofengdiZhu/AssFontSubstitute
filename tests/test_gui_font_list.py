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


def _solid_tile():
    import numpy as np
    return np.full((48, 48), 255, dtype=np.uint8)


def _solid_strip():
    import numpy as np
    return np.full((40, 160), 255, dtype=np.uint8)


def test_preview_tile_colors_follow_theme(qapp):
    # 灰度条带 → RGBA：字形色暗白亮黑、底全透明（露卡面底色）
    import numpy as np
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.widgets.preview_bar import strip_to_pixmap

    strip = np.zeros((48, 48), dtype=np.uint8)
    strip[10:38, 10:38] = 255
    try:
        setTheme(Theme.DARK)
        img = strip_to_pixmap(strip).toImage()
        ink, bg = img.pixelColor(24, 24), img.pixelColor(2, 2)
        assert (ink.red(), ink.green(), ink.blue()) == (255, 255, 255)
        assert ink.alpha() == 255 and bg.alpha() == 0
        setTheme(Theme.LIGHT)
        img = strip_to_pixmap(strip).toImage()
        ink = img.pixelColor(24, 24)
        assert (ink.red(), ink.green(), ink.blue()) == (0, 0, 0)
    finally:
        setTheme(Theme.AUTO)


def test_preview_bar_reapplies_tiles_on_theme_flip(qapp):
    # 已上屏的贴图在 setTheme 后用缓存条带原地重贴（不派渲染任务）
    from PySide6.QtCore import QPoint
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

    pvm = PreviewVM()
    pvm.put_tiles(("fp1", "永"), _solid_strip())
    bar = None
    try:
        setTheme(Theme.DARK)
        bar = PreviewBar(_FakeWorker(), pvm, _ent("fp1", "甲"), "永", "t")
        dark_ink = bar.pixmap().toImage().pixelColor(QPoint(10, 10))
        assert (dark_ink.red(), dark_ink.green(), dark_ink.blue()) == (255, 255, 255)
        setTheme(Theme.LIGHT)
        qapp.processEvents()
        light_ink = bar.pixmap().toImage().pixelColor(QPoint(10, 10))
        assert (light_ink.red(), light_ink.green(), light_ink.blue()) == (0, 0, 0)
        assert not bar._worker.submits                     # 全程零新渲染任务
    finally:
        setTheme(Theme.AUTO)
        if bar is not None:
            bar.deleteLater()


def test_preview_bar_theme_flip_at_zero_height_defers_to_show(qapp):
    # 高度 0（布局未跑/被压塌）时换肤跳过重贴（缩到 0 得 null 贴图），
    # 旧贴图保留；首次 show 时 showEvent 用缓存条带补贴到新主题
    from PySide6.QtCore import QPoint
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

    pvm = PreviewVM()
    pvm.put_tiles(("fp1", "永"), _solid_strip())
    bar = None
    try:
        setTheme(Theme.DARK)
        bar = PreviewBar(_FakeWorker(), pvm, _ent("fp1", "甲"), "永", "t")
        bar.setFixedHeight(0)                  # 模拟未布局/被压塌
        setTheme(Theme.LIGHT)
        qapp.processEvents()
        ink = bar.pixmap().toImage().pixelColor(QPoint(10, 10))
        assert (ink.red(), ink.green(), ink.blue()) == (255, 255, 255)   # 跳过：旧贴图在
        bar.setFixedHeight(28)
        bar.show()
        qapp.processEvents()
        ink = bar.pixmap().toImage().pixelColor(QPoint(10, 10))
        assert (ink.red(), ink.green(), ink.blue()) == (0, 0, 0)         # show 补贴
    finally:
        setTheme(Theme.AUTO)
        if bar is not None:
            bar.deleteLater()


def test_row_height_recibrates_each_set_rows(qapp, monkeypatch):
    # P1 回归：校准基准若跨 set_rows 周期残留，第二次筛选后行高回退
    # 初值、预览条下半重新被裁。行高必须每个 set_rows 周期重校准。
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    class _TallRow(QWidget):
        def sizeHint(self):
            return QSize(300, 100)          # 模拟实机字体度量高于初值

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    monkeypatch.setattr(fl, "_row_widget", lambda i: _TallRow())
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()

    ents = [_ent(str(i), f"字体{i}") for i in range(50)]
    fl.set_rows(ents)
    assert fl.item(0).sizeHint().height() == 100    # 首周期校准生效

    fl.set_rows(ents)                       # 第二周期（筛选/重拉路径）
    assert fl.item(0).sizeHint().height() == 100    # 不得回退 ROW_H
    assert fl._row_h == 100


def test_scaled_pixmap_keeps_device_ratio(qapp):
    # DPR 路径护栏：Qt 6.12 scaled 家族按原始像素缩放且保留 DPR 标签
    # （PreviewBar 几何数学依赖此行为，版本升级漂移时在此炸）
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    pm = QPixmap(200, 74)
    pm.setDevicePixelRatio(1.75)
    out = pm.scaledToHeight(49, Qt.SmoothTransformation)
    assert out.height() == 49 and out.devicePixelRatio() == 1.75
    out2 = out.scaledToWidth(100, Qt.SmoothTransformation)
    assert out2.width() == 100 and out2.devicePixelRatio() == 1.75
