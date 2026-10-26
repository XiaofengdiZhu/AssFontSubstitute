"""展示名语言挑选（gui/name_lang.py）：界面语言优先链挑名、字段序、
兜底链。纯函数零 Qt。"""
from types import SimpleNamespace

from ass_font_substitute.gui.name_lang import _UI_LANG_ORDER
from ass_font_substitute.gui.name_lang import pick_display_name
from ass_font_substitute.gui.name_lang import ui_lang


def _ent(name_langs=None, display_name="Legacy"):
    return SimpleNamespace(name_langs=name_langs or {},
                           display_name=display_name)


def test_chain_hans_prefers_simplified():
    e = _ent({1: [(0x409, "En Name"), (0x804, "简体名"), (0x404, "繁體名")]})
    assert pick_display_name(e, "zh-Hans") == "简体名"


def test_chain_hant_prefers_traditional():
    # 繁中界面链繁在简前：同一条记录集挑出繁体名
    e = _ent({1: [(0x409, "En Name"), (0x804, "简体名"), (0x404, "繁體名")]})
    assert pick_display_name(e, "zh-Hant") == "繁體名"


def test_chain_fallback_first_record_when_no_chain_lang():
    # 链语言全不中：取该字段表序首条（与旧 _cjk_first 兜底同源）
    e = _ent({1: [(0x0412, "한글 이름"), (0x0419, "Русское имя")]})
    assert pick_display_name(e, "zh-Hans") == "한글 이름"


def test_field_priority_id1_id16_id4_then_legacy():
    assert pick_display_name(_ent(
        {1: [(0x804, "名1")], 16: [(0x804, "名16")], 4: [(0x804, "名4")]})) \
        == "名1"
    assert pick_display_name(_ent(
        {16: [(0x804, "名16")], 4: [(0x804, "名4")]})) == "名16"
    assert pick_display_name(_ent({4: [(0x804, "名4")]})) == "名4"
    # 全字段无记录（旧库/无平台 3 记录）：退入库 display_name
    assert pick_display_name(_ent()) == "Legacy"


def test_unknown_ui_uses_hans_chain():
    e = _ent({1: [(0x404, "繁體"), (0x804, "简体")]})
    assert pick_display_name(e, "fr") == "简体"


def test_ui_lang_from_settings():
    assert ui_lang(SimpleNamespace(get=lambda _k: "zh-Hant")) == "zh-Hant"
    assert ui_lang(SimpleNamespace(get=lambda _k: "zh-Hans")) == "zh-Hans"
    assert ui_lang(None) == "zh-Hans"
    assert ui_lang(SimpleNamespace(get=lambda _k, _d=None: _d)) == "zh-Hans"


def test_chain_constant_matches_detail_dialog_default():
    # 详情页命名卡默认语言与各界面展示名共用同一条链常量（迁移防分叉）
    assert _UI_LANG_ORDER["zh-Hans"][:2] == (0x804, 0x1004)
    assert _UI_LANG_ORDER["zh-Hant"][:2] == (0x404, 0xC04)
    assert _UI_LANG_ORDER["zh-Hans"] == (0x804, 0x1004, 0x404, 0xC04,
                                         0x409, 0x411)
    assert _UI_LANG_ORDER["zh-Hant"] == (0x404, 0xC04, 0x804, 0x1004,
                                         0x409, 0x411)
