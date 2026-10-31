"""界面文案翻译装载：.qm 抽译冒烟 + FluentTranslator locale 跟设置。
真 .qm 由 scripts/gen_translations.py 生成（lupdate → opencc s2twp →
lrelease），包装完成前本测试红——按预期驱动。"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_zh_hans_needs_no_app_translator(qapp):
    from ass_font_substitute.gui.i18n import load_app_translator
    # 源语言=简体：无 .qm 可装
    assert load_app_translator("zh-Hans") is None


def test_unknown_lang_falls_back_to_source(qapp):
    from ass_font_substitute.gui.i18n import load_app_translator
    assert load_app_translator("fr") is None


def test_zh_hant_loads_qm_and_translates(qapp):
    from PySide6.QtCore import QCoreApplication

    from ass_font_substitute.gui.i18n import load_app_translator

    t = load_app_translator("zh-Hant")
    assert t is not None, "afs_zh-Hant.qm 缺失或装载失败（先跑 gen_translations）"
    assert qapp.installTranslator(t)
    try:
        # 抽样：vm 渲染面 context 固定 "vm"；简繁字级差异可判真装载；
        # s2twp 带台湾惯用词（字体→字型）
        assert QCoreApplication.translate("vm", "需替换") == "需替換"
        assert QCoreApplication.translate("vm", "保留原字体名") == "保留原字型名"
    finally:
        qapp.removeTranslator(t)


def test_fluent_translator_locale_follows_setting(qapp):
    from ass_font_substitute.gui.i18n import fluent_translator

    # qfw 内置 .qm 按 locale.name() 选：跟设置而非系统语言
    hans = fluent_translator("zh-Hans")
    hant = fluent_translator("zh-Hant")
    assert not hans.isEmpty() and hans.language() == "zh_CN"
    assert not hant.isEmpty() and hant.language() == "zh_TW"
    # 未知语言值兜底简体
    assert fluent_translator("fr").language() == "zh_CN"


def test_ts_in_sync_with_sources(tmp_path):
    """源码 tr 文案与已提交 .ts 同集、且无 unfinished：新增/改动文案后
    忘跑 scripts/gen_translations.py 即红（繁体界面会漏译回落简体）。"""
    import xml.etree.ElementTree as ET

    from scripts import gen_translations as g

    fresh = tmp_path / "fresh.ts"
    g.lupdate(fresh)
    assert g.messages(fresh) == g.messages(g.TS), \
        "afs_zh-Hant.ts 与源码不同步：跑 uv run python scripts/gen_translations.py"
    unfinished = [m.findtext("source")
                  for m in ET.parse(g.TS).getroot().iter("message")
                  if m.find("translation").get("type") == "unfinished"]
    assert not unfinished
    # .qm 须由当前 .ts 编出（lrelease 确定性输出，比字节）
    qm = tmp_path / "fresh.qm"
    g.lrelease(g.TS, qm)
    assert qm.read_bytes() == g.QM.read_bytes(), ".qm 与 .ts 不同步：重跑管线"


def test_overrides_live_and_applied():
    """人工纠偏表每条源串仍在 .ts 且译文即覆盖值：源文案改动后 override
    静默失效（退回 s2twp 大陆腔）即红。"""
    import xml.etree.ElementTree as ET

    from scripts import gen_translations as g

    got = {m.findtext("source"): m.findtext("translation")
           for m in ET.parse(g.TS).getroot().iter("message")}
    for src, want in g._OVERRIDES.items():
        assert src in got, f"override 源串已不在源码：{src!r}"
        assert got[src] == want, src


def test_widgets_render_translated_after_install(qapp, tmp_path, monkeypatch):
    """装载后构造的控件取繁体文案（tr 在构造期求值）；卸载后新建控件
    回落简体源文案。抽设置页：语言卡标题/说明 + qfw 无关的自有文案。"""
    from PySide6.QtCore import QObject
    from PySide6.QtCore import Signal

    from ass_font_substitute.gui.i18n import load_app_translator
    from ass_font_substitute.gui.pages.settings_page import SettingsPage
    from ass_font_substitute.gui.settings_store import Settings

    class _W(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def submit(self, *a, **k):
            pass

    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    s = Settings(tmp_path / "settings.json")
    s.set("language", "zh-Hant")
    t = load_app_translator("zh-Hant")
    qapp.installTranslator(t)
    try:
        page = SettingsPage(s, _W())
        assert page.lang_card.titleLabel.text() == "語言"
        assert page.lang_card.contentLabel.text() == "設定介面語言（重新啟動應用程式後生效）"
        assert [page.lang_combo.itemText(i)
                for i in range(page.lang_combo.count())] == ["簡體中文", "繁體中文"]
        page.deleteLater()
    finally:
        qapp.removeTranslator(t)
    page = SettingsPage(s, _W())
    assert page.lang_card.titleLabel.text() == "语言"
    page.deleteLater()
