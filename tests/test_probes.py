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


def test_effective_weight_tail_word_table():
    # 词表全量：尾词命中 ∧ OS/2==400 → 词表值（spec §1）
    from ass_font_substitute.core.fontindex.probes import effective_weight
    cases = [("Thin", 100), ("ExtraLight", 200), ("UltraLight", 200),
             ("Light", 300), ("Medium", 500), ("SemiBold", 600),
             ("DemiBold", 600), ("Bold", 700), ("ExtraBold", 800),
             ("UltraBold", 800), ("Black", 900), ("Heavy", 900)]
    for word, val in cases:
        nl = {1: [(0x409, f"演示 {word}")]}
        assert effective_weight(nl, 400) == val, (word,)


def test_effective_weight_rules():
    from ass_font_substitute.core.fontindex.probes import effective_weight
    # 尾词多命中取最大；非 400 不动；首词重量词不触发（S1 假阳）；
    # camelCase 无词边界不触发；PS 名不在扫描域（只收 ID1/4/16）
    assert effective_weight({1: [(0x409, "A Light")], 4: [(0x409, "A Bold")]},
                            400) == 700
    assert effective_weight({1: [(0x409, "演示 Heavy")]}, 900) == 900
    assert effective_weight({1: [(0x409, "Black Ops One")]}, 400) == 400
    assert effective_weight({1: [(0x409, "CorbelLight")]}, 400) == 400
    assert effective_weight({6: [(0x409, "Corbel-Heavy")]}, 400) == 400
    # VF 实例行：轴值可信，inst_index>0 一律不补偿
    assert effective_weight({1: [(0x409, "A Heavy")]}, 400, 1) == 400
    # 无 name_langs（实例行置空口径）安全
    assert effective_weight({}, 400) == 400
