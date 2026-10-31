"""语言显示名查询：CLDR 子集表组合口径（期望值取自 babel 2.18/CLDR 实测，
生成脚本 gen_lang_names.py 对拍同源）。纯 Python 表查询，无需 Qt。"""
from ass_font_substitute.gui.lang_names import display_name


def test_display_name_matches_cldr():
    # 期望值 = babel Locale.parse(tag).get_display_name('zh_CN'/'zh_TW')
    # 实测钉死（表再生成时 gen 脚本对拍兜底，这里防组合口径漂移）
    assert display_name("en-US", "zh_CN") == "英语 (美国)"
    assert display_name("en-US", "zh_TW") == "英文 (美國)"
    assert display_name("en", "zh_CN") == "英语"
    assert display_name("zh-TW", "zh_CN") == "中文 (繁体, 台湾)"
    assert display_name("zh-Hant", "zh_CN") == "中文 (繁体)"
    assert display_name("zh-Hant-TW", "zh_CN") == "中文 (繁体, 台湾)"
    assert display_name("zh-CN", "zh_TW") == "中文 (簡體, 中國)"
    assert display_name("sv-FI", "zh_CN") == "瑞典语 (芬兰)"
    assert display_name("es-419", "zh_TW") == "西班牙文 (拉丁美洲)"
    assert display_name("az-Arab", "zh_CN") == "阿塞拜疆语 (阿拉伯文)"


def test_display_name_unknown_returns_none():
    # 未知语言 → None（font_detail 走 LCID_LANGS 静态兜底）；已知语言
    # 配未知地区 → 裸语言名（babel 同：细节名缺省即不加括号）
    assert display_name("xx-YY", "zh_CN") is None
    assert display_name("en-QQ", "zh_CN") == "英语"
