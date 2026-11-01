import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mk(qapp):
    from ass_font_substitute.gui.widgets.spin import CompactSpinBox
    w = CompactSpinBox()
    w.setRange(0, 100)
    w.setValue(50)
    return w


def test_compact_spin_steps(qapp):
    w = _mk(qapp)
    b = w.spinButton
    b._up = True
    b._step()
    assert w.value() == 51
    b._up = False
    b._step()
    assert w.value() == 50
    w.deleteLater()


def test_disabled_spin_no_step(qapp):
    # _step 守卫既有：禁用期步进空转
    w = _mk(qapp)
    w.setEnabled(False)
    w.spinButton._up = True
    w.spinButton._step()
    assert w.value() == 50
    w.deleteLater()


def test_disable_during_hold_stops_repeat(qapp):
    # 审查二轮 P2：按住期间被禁用——禁用期 Qt 不再投递 release，isPressed
    # 残留 True 会让 _repeat 无人停、重新启用后失控步进；EnabledChange
    # 须即刻停连发+复位按压态
    w = _mk(qapp)
    b = w.spinButton
    w.setEnabled(True)
    b.isPressed = True
    b._start_repeat()
    assert b._repeat.isActive()
    w.setEnabled(False)
    assert not b._repeat.isActive() and b.isPressed is False
    # 重新启用后恢复可步进
    w.setEnabled(True)
    b._up = True
    b._step()
    assert w.value() == 51
    w.deleteLater()
