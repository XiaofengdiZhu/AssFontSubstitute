import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mouse(w, etype, x, button=None):
    from PySide6.QtCore import QPointF
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    ev = QMouseEvent(etype, QPointF(x, 11), QPointF(x, 11),
                     button or Qt.MouseButton.NoButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, ev)
    return ev


def _mk(qapp, lo=50, hi=200, lo_v=90, hi_v=110):
    from ass_font_substitute.gui.widgets.range_slider import RangeSlider
    w = RangeSlider(lo, hi, lo_v, hi_v)
    w.resize(300, 22)
    return w


def test_init_swaps_and_clamps(qapp):
    w = _mk(qapp, lo_v=180, hi_v=40)          # 传入反序 + 越界
    assert w.values() == (50, 180)            # 交换 + 钳到 [50,200]
    w2 = _mk(qapp, lo_v=25, hi_v=999)
    assert w2.values() == (50, 200)


def test_init_both_out_of_range(qapp):
    # 双双越界（损坏设置 JSON 可造出）：各自钳满域后排序，钮不出控件
    w = _mk(qapp, lo=50, hi=150, lo_v=250, hi_v=300)
    assert w.values() == (150, 150)
    w.resize(180, 22)
    w.grab()
    assert w.lo_handle.pos().x() >= 0
    assert w.hi_handle.pos().x() + 22 <= w.width()


def test_drag_crossing_keeps_order(qapp):
    # 拖低钮越过右钮：两值同步让位、恒有序（互换语义，固化现行为）
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import Qt

    w = _mk(qapp, lo_v=90, hi_v=110)
    w.grab()
    x = w.lo_handle.pos().x() + 11
    _mouse(w, QEvent.Type.MouseButtonPress, x, Qt.MouseButton.LeftButton)
    _mouse(w, QEvent.Type.MouseMove, int(w.width() * 0.9))
    _mouse(w, QEvent.Type.MouseButtonRelease, int(w.width() * 0.9),
           Qt.MouseButton.LeftButton)
    lo, hi = w.values()
    # 互换语义：被拖值越过成 high，旧 high 让位成 low
    assert lo == 110 and hi > 110


def test_set_values_clamp_signal(qapp):
    w = _mk(qapp)
    got = []
    w.valueChanged.connect(lambda lo, hi: got.append((lo, hi)))
    w.setValues(40, 80)
    assert w.values() == (50, 80) and got == [(50, 80)]
    w.setValues(150, 90)                      # 反序：低钮让位
    assert w.values() == (90, 150) and got[-1] == (90, 150)
    w.setValues(90, 150)                      # 同值不发信号
    assert got == [(50, 80), (90, 150)]


def test_drag_low_and_high_handles(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import Qt

    w = _mk(qapp, lo_v=90, hi_v=110)
    w.grab()                                   # 强制布局，钮位置就位
    got = []
    w.valueChanged.connect(lambda lo, hi: got.append((lo, hi)))
    # 按住低钮拖到最左：low → 50
    x = w.lo_handle.pos().x() + 11
    ev = _mouse(w, QEvent.Type.MouseButtonPress, x,
                Qt.MouseButton.LeftButton)
    assert ev.isAccepted() is True             # 不 accept 则 Qt 不送后续 move，真机拖不动
    _mouse(w, QEvent.Type.MouseMove, 0)
    _mouse(w, QEvent.Type.MouseButtonRelease, 0,
           Qt.MouseButton.LeftButton)
    assert w.values()[0] == 50 and w.values()[1] == 110
    # 按住高钮拖到最右：high → 200
    x = w.hi_handle.pos().x() + 11
    _mouse(w, QEvent.Type.MouseButtonPress, x,
           Qt.MouseButton.LeftButton)
    _mouse(w, QEvent.Type.MouseMove, w.width())
    _mouse(w, QEvent.Type.MouseButtonRelease, w.width(),
           Qt.MouseButton.LeftButton)
    assert w.values() == (50, 200)
    assert got[-1] == (50, 200)


def test_track_click_moves_nearest_handle(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import Qt

    w = _mk(qapp, lo_v=90, hi_v=110)
    w.grab()
    # 点轨道右端空白：高钮（近端）跳至点击值，低钮不动
    _mouse(w, QEvent.Type.MouseButtonPress, w.width() - 1,
           Qt.MouseButton.LeftButton)
    assert w.values() == (90, 200)


def test_drag_via_handle_event_filter(qapp):
    # 真机拖动路径：press 落在圆钮（子控件）上，SliderHandle 覆写不冒泡，
    # 事件须经过滤器在钮层截走——直投钮的事件也必须能拖
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QPointF
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = _mk(qapp, lo_v=90, hi_v=110)
    w.grab()

    def to_handle(etype, local_x):
        ev = QMouseEvent(etype, QPointF(local_x, 11), QPointF(0, 0),
                         Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(w.lo_handle, ev)
        return ev

    press = to_handle(QEvent.Type.MouseButtonPress, 11)
    # 抓钮只认领拖拽不落值（点击点含钮心偏移，落值=向右瞬移 20 多）
    assert press.isAccepted() is True and w._drag == "low"
    assert w.values() == (90, 110)
    # 原位微动（光标未移）值必须不动：move 按抓取偏移回算，不按光标点取值
    to_handle(QEvent.Type.MouseMove, 11)
    assert w.values() == (90, 110)
    # 钮内局部 x=-钮位x → 映射回父级 x=0（拖到最左）
    to_handle(QEvent.Type.MouseMove, -w.lo_handle.pos().x())
    to_handle(QEvent.Type.MouseButtonRelease, -w.lo_handle.pos().x())
    assert w.values() == (50, 110) and w._drag is None


def test_wheel_never_changes_value(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import QPointF
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWheelEvent

    w = _mk(qapp)
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 120),
                     QPoint(0, 120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.ScrollUpdate, False)
    w.wheelEvent(ev)
    assert ev.isAccepted() is False            # ignore：冒泡给设置页滚动
    assert w.values() == (90, 110)


def test_paint_smoke(qapp):
    w = _mk(qapp)
    assert not w.grab().isNull()               # 底槽+强调色区段渲染不崩


def test_non_left_button_never_releases(qapp):
    # 审查二轮 P2：右/中键 release 曾发 sliderReleased（settings_page
    # 把它当提交信号）——只认左键且 press 在先
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import Qt

    w = _mk(qapp, lo_v=90, hi_v=110)
    w.grab()
    fired = []
    w.sliderReleased.connect(lambda: fired.append(1))

    # 右键 press+release：press 被 ignore，release 不发
    _mouse(w, QEvent.Type.MouseButtonPress, 150, Qt.MouseButton.RightButton)
    _mouse(w, QEvent.Type.MouseButtonRelease, 150, Qt.MouseButton.RightButton)
    assert fired == []
    assert w.values() == (90, 110)              # 值也不动

    # 左键 press+release：发一次
    _mouse(w, QEvent.Type.MouseButtonPress, 150, Qt.MouseButton.LeftButton)
    _mouse(w, QEvent.Type.MouseButtonRelease, 150, Qt.MouseButton.LeftButton)
    assert fired == [1]
