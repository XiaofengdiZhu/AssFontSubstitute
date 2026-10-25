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
    """只实现 PreviewBar 用到的接口；submit 记账并返回可取消句柄，
    result 手动发射。"""

    result = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        from types import SimpleNamespace
        handle = SimpleNamespace(cancelled=False)
        self.submits.append((tag, fn, args, handle))
        return handle


def _row_of(handle_or_submit):
    """submit 记录 → 行号（tag 形如 plist:{fingerprint}:{list_id}）。"""
    return int(handle_or_submit[0].split(":")[1])


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

    fl.verticalScrollBar().setValue(150 * (ROW_H + ROW_SPACING))   # 像素模式
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


def test_scroll_out_cancels_pending_renders(qapp):
    # 滚出物化窗口的行：未执行的渲染任务必须取消——串行工作者下不取消
    # 则快速滚动积压几百个过期任务，新视口的预览排在队尾迟迟不出来
    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import ROW_H
    from ass_font_substitute.gui.widgets.font_list import ROW_SPACING
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])

    old = list(fl._worker.submits)
    assert old and all(not h.cancelled for _t, _f, _a, h in old)
    fl.verticalScrollBar().setValue(150 * (ROW_H + ROW_SPACING))
    qapp.processEvents()
    assert all(h.cancelled for _t, _f, _a, h in old)   # 旧窗口全部出窗取消
    new = fl._worker.submits[len(old):]
    assert new and all(not h.cancelled for _t, _f, _a, h in new)
    assert 150 in {_row_of(s) for s in new}


def test_set_rows_cancels_inflight_renders(qapp):
    # 筛选/重拉重建列表：旧窗口在途任务全部取消（行控件即将销毁，结果
    # 无处可贴；不取消同样白占工作者串行时段）
    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])
    old = list(fl._worker.submits)
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])
    assert old and all(h.cancelled for _t, _f, _a, h in old)


def test_materialization_submits_nearest_visible_first(qapp):
    # 连续滚动时新进入视野的行先入渲染队列：物化提交按到可视中心距离
    # 排序（旧实现固定自上而下，向下滚动时最想看的行反而最后派发）
    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])

    first, last = fl._visible_range()
    center = (first + last) / 2
    rows = {_row_of(s) for s in fl._worker.submits}
    expected = sorted(rows, key=lambda i: abs(i - center))
    assert [_row_of(s) for s in fl._worker.submits] == expected


def test_smooth_scroll_delegated(qapp):
    # 平滑滚动接线：ScrollPerPixel + qfw SmoothScrollDelegate useAni 路径。
    # 行为钉：滚轮 1 格净位移恒 120px——默认引擎路径把每帧增量以合成
    # 滚轮事件发给原生条、原生条按 angleDelta/120×singleStep 换算，而
    # QListView singleStep=行高（QScrollArea 页面是 20px），每格滚出
    # 4.4 倍距离与其他页面手感脱节（实测 422px vs 96px）；动画路径
    # scrollValue() 直驱原生条 setValue，无此二次换算。
    from PySide6.QtCore import QEventLoop
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QAbstractItemView
    from qfluentwidgets import SmoothScrollDelegate

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(100)])
    assert fl.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    assert isinstance(fl._scroll, SmoothScrollDelegate)
    assert fl._scroll.useAni
    assert fl.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    e = QWheelEvent(QPoint(150, 300), QPoint(150, 300), QPoint(0, 0),
                    QPoint(0, -120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.ScrollBegin, False)
    qapp.sendEvent(fl.viewport(), e)
    loop = QEventLoop()                     # QPropertyAnimation 需真事件循环
    QTimer.singleShot(800, loop.quit)
    loop.exec()
    assert fl.verticalScrollBar().value() == 120


def test_visible_range_converts_pixels(qapp):
    # 可视区判定 = 滚动条 value 按实测行距换算（ScrollPerPixel 下
    # value/pageStep 单位是像素；QListView indexAt 因布局 x 内缩恒 -1、
    # 行高校准后深处又错位，不可作依据，已整体弃用）
    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(200)])
    fl.verticalScrollBar().setValue(10 * fl._row_pitch)
    first, last = fl._visible_range()
    assert first == 10
    assert 10 < last < 200


def test_deep_jump_materializes_viewport_rows(qapp, monkeypatch):
    # 回归（实机：拖滚动条直达深处大片空白）：可视区判定弃用 indexAt、
    # 直接按 value//实测行距 换算——物化窗口必须落在真视口行上。真值
    # 以 visualRect 与视口相交的行是否有控件为准（indexAt 自身不可信）。
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    class _TallRow(QWidget):
        def sizeHint(self):
            return QSize(300, 96)       # 模拟实机：行高实测 > 初值

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    monkeypatch.setattr(fl, "_row_widget", lambda i: _TallRow())
    fl.resize(1200, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(2000)])

    def viewport_rows():
        vp = fl.viewport().rect()
        return [i for i in range(fl.count())
                if fl.visualRect(fl.model().index(i, 0)).intersects(vp)]

    sb = fl.verticalScrollBar()
    sb.setValue(int(sb.maximum() * 0.7))
    qapp.processEvents()
    fl._materialize_visible()
    qapp.processEvents()
    assert all(fl.itemWidget(fl.item(i)) is not None
               for i in viewport_rows())
    first, _last = fl._visible_range()
    assert abs(first - sb.value() // fl._row_pitch) <= 2


def test_wheel_after_scrollbar_jump_no_rewind(qapp):
    # 回归（实机：点滚动条后滚轮从点击前位置开始滚）：点 groove 跳位走
    # setValue(useAni=True) 动画，动画结束时滚动条 value 已到位，但
    # SmoothScrollBar 的滚动目标 __value 仍停在点击前——下次滚轮
    # scrollValue 从旧目标出发即回跳。动画 finished 必须把目标重置到
    # 当前 value。
    from PySide6.QtCore import QEventLoop
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QWheelEvent

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(300)])

    def drain(ms=900):
        loop = QEventLoop()                 # 动画推进需真事件循环
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    smooth = fl._scroll.vScrollBar
    target = int(smooth.maximum() * 0.8)
    smooth.setValue(target)                 # 与点击 groove 同路径：动画跳位
    drain()
    assert fl.verticalScrollBar().value() >= target - 2

    e = QWheelEvent(QPoint(150, 300), QPoint(150, 300), QPoint(0, 0),
                    QPoint(0, -120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.ScrollBegin, False)
    qapp.sendEvent(fl.viewport(), e)
    drain()
    assert fl.verticalScrollBar().value() >= target     # 不许回跳


def test_wheel_mid_jump_animation_continues_from_reality(qapp):
    # 回归（审查实证）：跳位动画「中途」滚一格，scrollValue 先从过期
    # 滚动目标 __value（停在点击前）算好目标再 stop 旧动画——finished
    # 补救晚于传参，整段跳位被丢弃回跳到点击前。滚轮入口必须先从原生
    # 条真实现值重置目标，动画中断即从当前帧续走。
    from PySide6.QtCore import QEventLoop
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QWheelEvent

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_list import FontListWidget

    fl = FontListWidget(_FakeWorker(), PreviewVM(), lambda: "永")
    fl.resize(300, 600)
    fl.show()
    qapp.processEvents()
    fl.set_rows([_ent(str(i), f"字体{i}") for i in range(300)])

    def drain(ms=900):
        loop = QEventLoop()                 # 动画推进需真事件循环
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    smooth = fl._scroll.vScrollBar
    target = int(smooth.maximum() * 0.8)
    smooth.setValue(target)                 # 跳位动画开跑
    drain(150)                              # 停在动画中段
    v_mid = fl.verticalScrollBar().value()

    e = QWheelEvent(QPoint(150, 300), QPoint(150, 300), QPoint(0, 0),
                    QPoint(0, -120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.ScrollBegin, False)
    qapp.sendEvent(fl.viewport(), e)
    drain()
    # 从中断帧 +120 续走（修复前回跳到点击前附近：终值 ≈120）
    assert abs(fl.verticalScrollBar().value() - (v_mid + 120)) <= 2
    assert v_mid > 0                        # 护栏：确已停在动画中段
