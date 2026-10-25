from ass_font_substitute.core.fontindex.probes import PROBE_PAIRS
from ass_font_substitute.core.fontindex.probes import PROBE_SC
from ass_font_substitute.core.fontindex.probes import PROBE_TC
from ass_font_substitute.core.fontindex.probes import classify_category
from ass_font_substitute.core.fontindex.probes import classify_script
from ass_font_substitute.core.models import CmapRanges

_COMMON = "的一是我"


def _cmap(scss, stcs):
    cps = {ord(c) for c in scss} | {ord(c) for c in stcs} | {ord(c) for c in _COMMON}
    return CmapRanges.from_chars(cps)


def test_script_simple():
    assert classify_script(_cmap(PROBE_SC, frozenset())) == "简"


def test_script_traditional():
    assert classify_script(_cmap(frozenset(), PROBE_TC)) == "繁"


def test_script_both():
    assert classify_script(_cmap(PROBE_SC, PROBE_TC)) == "简繁"


def test_script_none():
    assert classify_script(CmapRanges.from_chars({ord(c) for c in _COMMON})) == "未标注"


def test_script_below_threshold():
    partial = set(list(PROBE_SC)[: len(PROBE_SC) // 2])
    assert classify_script(_cmap(partial, frozenset())) == "未标注"


def test_category_keywords():
    assert classify_category(["華康中黑體"]) == "黑"
    assert classify_category(["仿宋 GB2312"]) == "仿"
    assert classify_category(["华康华综体 W5"]) == "未分类"
    assert classify_category(["汉仪旗圆", "HYQiYuan"]) == "圆"


def test_category_romanized_names():
    # 无中文名的字体按罗马化/英文名兜底（微软雅黑 UI 的 zh-CN 名就是
    # 英文「Microsoft YaHei UI」，别名里无「黑」字）；非 CJK 字体不误伤
    assert classify_category(["Microsoft YaHei UI"]) == "黑"
    assert classify_category(["SimHei"]) == "黑"
    assert classify_category(["Noto Sans CJK SC"]) == "黑"
    assert classify_category(["SimSun"]) == "宋"
    assert classify_category(["Songti SC"]) == "宋"
    assert classify_category(["FangSong"]) == "仿"
    assert classify_category(["Kaiti SC"]) == "楷"
    assert classify_category(["Yuanti SC"]) == "圆"
    assert classify_category(["MS Gothic"]) == "黑"
    assert classify_category(["Yu Gothic UI"]) == "黑"
    assert classify_category(["Meiryo"]) == "黑"
    assert classify_category(["MS Mincho"]) == "宋"
    assert classify_category(["Arial"]) == "未分类"


def test_probe_sets_aligned():
    assert len(PROBE_SC) == len(PROBE_TC)
    assert not (PROBE_SC & PROBE_TC)


def test_probe_pairs_strict():
    assert len(PROBE_PAIRS) >= 60
    assert all(sc != tc for sc, tc in PROBE_PAIRS)
