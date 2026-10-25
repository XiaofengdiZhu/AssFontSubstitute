import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _custom_qss(widget) -> str:
    from qfluentwidgets.common.style_sheet import CustomStyleSheet
    cs = CustomStyleSheet(widget)
    return (cs.lightStyleSheet() or "") + (cs.darkStyleSheet() or "")


def test_primary_text_adapts_to_accent_luma(qapp):
    # qfw 缺陷回归：BUTTON.qss 写死 primary 按钮白字，浅色强调色下不可读。
    # tune 按主题色 luma 换深色字；正常深度清覆盖恢复白字；来回切换可撤销。
    # 文字像素哨兵（升级 canary）：qfw 若改选择器体系，custom-qss 字符串
    # 断言会照常绿而真实渲染翻车——渲染层断言不依赖 qss 串细节
    from qfluentwidgets import PrimaryPushButton
    from qfluentwidgets import setThemeColor

    from ass_font_substitute.gui.widgets.primary_text import tune

    b = PrimaryPushButton("替换并保存")
    b.resize(150, 34)

    def glyph_pixels(light_bg: bool) -> int:
        """文字笔画像素数：浅底数暗笔画，深底数亮笔画（背景色已知）"""
        b.show()
        qapp.processEvents()
        img = b.grab().toImage()
        if light_bg:
            return sum(1 for y in range(8, 26) for x in range(30, 140)
                       if 0 <= x < img.width() and 0 <= y < img.height()
                       and img.pixelColor(x, y).red() < 120)
        return sum(1 for y in range(8, 26) for x in range(30, 140)
                   if 0 <= x < img.width() and 0 <= y < img.height()
                   and min(img.pixelColor(x, y).red(),
                           img.pixelColor(x, y).green(),
                           img.pixelColor(x, y).blue()) > 200)

    try:
        setThemeColor("#009faa", save=False)      # 出厂青：luma≈0.44 白字合适
        tune(b)
        assert _custom_qss(b) == ""               # 无覆盖
        assert glyph_pixels(light_bg=False) > 0   # 白字在

        setThemeColor("#f2f2f2", save=False)      # 极浅强调色
        tune(b)
        qss = _custom_qss(b)
        assert "rgba(0, 0, 0" in qss and ":hover" in qss and ":pressed" in qss
        assert glyph_pixels(light_bg=True) > 0    # 深字真实渲染

        setThemeColor("#009faa", save=False)      # 切回：覆盖必须撤销
        tune(b)
        assert _custom_qss(b) == ""
        assert glyph_pixels(light_bg=False) > 0   # 白字回来
    finally:
        setThemeColor("#009faa", save=False)
        b.deleteLater()


def test_tune_all_covers_existing_widgets(qapp):
    # 强调色应用后全量补调：已存在的 primary 按钮（含设置卡内部按钮）都修
    from qfluentwidgets import PrimaryPushButton
    from qfluentwidgets import PrimaryPushSettingCard
    from qfluentwidgets import PushButton
    from qfluentwidgets import setThemeColor

    from ass_font_substitute.gui.widgets.primary_text import tune_all

    try:
        setThemeColor("#f5f5f5", save=False)
        from qfluentwidgets import FluentIcon as FIF
        btn, plain, card = PrimaryPushButton("更新全部"), PushButton("普通"), \
            PrimaryPushSettingCard("打开", FIF.DELETE, "清除相似度缓存", "x")
        card.resize(320, 70)
        n = tune_all()
        assert "rgba(0, 0, 0" in _custom_qss(btn)
        assert _custom_qss(plain) == ""           # 非 primary 不动
        # 卡内按钮的白字规则来自卡片级联，按钮自身样式表必须直接翻案——
        # 像素断言（property 断言抓不住「写进 property 却没人生效」）
        card.show()
        qapp.processEvents()
        assert "rgba(0, 0, 0" in card.button.styleSheet()
        g = card.button.geometry()
        img = card.grab().toImage()
        darks = sum(1 for y in range(g.top() + 5, g.bottom() - 5)
                    for x in range(g.left() + 10, g.right() - 10)
                    if 0 <= x < img.width() and 0 <= y < img.height()
                    and img.pixelColor(x, y).red() < 120)
        assert darks > 0                          # 深色笔画真实渲染出来
        assert n >= 2
    finally:
        setThemeColor("#009faa", save=False)


def test_primary_icon_follows_accent(qapp):
    # qfw 把 primary 图标画死反色（浅色主题→白），不走 qss：patch 后浅
    # 强调色下图标深色、正常深度恢复原反白
    from PySide6.QtCore import QRect
    from qfluentwidgets import FluentIcon as FIF
    from qfluentwidgets import PrimaryPushButton
    from qfluentwidgets import setThemeColor

    from ass_font_substitute.gui.widgets.primary_text import patch_primary_icon

    def icon_stats(img):
        darks = lights = 0
        for y in range(icon_rect.top(), icon_rect.bottom()):
            for x in range(icon_rect.left(), icon_rect.right()):
                c = img.pixelColor(x, y)
                m = min(c.red(), c.green(), c.blue())
                darks += m < 100
                lights += m > 180
        return darks, lights

    patch_primary_icon()                          # 幂等，main() 同款调用点
    b = PrimaryPushButton(FIF.PLAY, "替换并保存")
    b.resize(150, 34)
    icon_rect = QRect(10, 9, 20, 16)              # 图标绘制区（左 padding）
    try:
        setThemeColor("#f5c1cb", save=False)
        b.show()
        qapp.processEvents()
        darks, _ = icon_stats(b.grab().toImage())
        assert darks > 0                          # 深色图标真实渲染

        setThemeColor("#009faa", save=False)      # 正常深度：恢复反白图标
        b.repaint()
        qapp.processEvents()
        darks, lights = icon_stats(b.grab().toImage())
        # teal 底 min=0 会污染 dark 桶，只断言浅笔画回来了（描边三角形
        # 反锯齿后 ~#c8c8c8，无纯白核心）
        assert lights > 0
    finally:
        setThemeColor("#009faa", save=False)
        b.deleteLater()


def test_dialog_buttons_tuned_on_show(qapp, tmp_path):
    # 弹窗按钮懒创建：showEvent 钩子在显示前补调 tune。直接调钩子内的
    # _tune_dialog_buttons 验证（真 show 会进动画层，offscreen 无
    # backingStore 撑不住，动画另有用例覆盖）
    from PySide6.QtWidgets import QWidget
    from qfluentwidgets import MessageBox
    from qfluentwidgets import setThemeColor

    from ass_font_substitute.gui.widgets.dialog_ani import _tune_dialog_buttons

    host = None
    try:
        setThemeColor("#f2f2f2", save=False)
        host = QWidget()
        host.resize(900, 600)
        box = MessageBox("标题", "内容", host)
        _tune_dialog_buttons(box)
        assert "rgba(0, 0, 0" in _custom_qss(box.yesButton)   # yes=Primary
        assert _custom_qss(box.cancelButton) == ""            # cancel 普通按钮不动
    finally:
        setThemeColor("#009faa", save=False)
        if host is not None:
            host.deleteLater()
