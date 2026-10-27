from ass_font_substitute.core.models import CmapRanges


def test_contains_and_merge():
    r = CmapRanges.from_chars({0x4E00, 0x4E01, 0x4E02, 0x4E10})
    assert r.contains(0x4E01)
    assert r.contains(0x4E10)
    assert not r.contains(0x4E03)
    assert not r.contains(0x4E0F)
    assert r.starts == (0x4E00, 0x4E10)  # 0x4E00-0x4E02 合并成一段


def test_bytes_roundtrip():
    r = CmapRanges.from_chars({1, 2, 3, 100, 200, 0x10FFFF})
    assert CmapRanges.from_bytes(r.to_bytes()) == r


def test_empty():
    r = CmapRanges.from_chars(set())
    assert not r.contains(0x4E00)
    assert r.starts == ()


def test_total_chars():
    # 区间求和总码点（详情页「字符覆盖（总）」）：from_chars 归并连续段
    assert CmapRanges.from_chars({1, 2, 3, 4, 5}).total_chars() == 5
    assert CmapRanges.from_chars({1, 2, 100}).total_chars() == 3
    assert CmapRanges((), ()).total_chars() == 0


def _fake_table(pid: int, eid: int, cmap: dict) -> object:
    from types import SimpleNamespace
    return SimpleNamespace(platformID=pid, platEncID=eid, cmap=cmap)


def test_from_cmap_subtables_legacy_big5_decoded():
    # legacy 编码子表（Big5 (3,4)）键是双字节原生码，按码页转回 Unicode：
    # 文鼎系老字库曾键直取 = 几万伪码点（判「未标注」+覆盖率全错的根因）
    from ass_font_substitute.core.models import CmapRanges

    tai = "永".encode("cp950")
    code = (tai[0] << 8) | tai[1]
    t = _fake_table(3, 4, {code: "g1", 0x41: "g2"})
    r = CmapRanges.from_cmap_subtables([t])
    assert r.contains(ord("永"))
    assert r.contains(ord("A"))
    assert not r.contains(code)          # 原生码本身不再入集


def test_from_cmap_subtables_legacy_bad_codes_dropped():
    # 造字区 0x8140（cp950 解不出）丢弃；0xA140 = U+3000 全角空格合法
    from ass_font_substitute.core.models import CmapRanges

    t = _fake_table(3, 4, {0x8140: "g1", 0xA140: "g2"})
    r = CmapRanges.from_cmap_subtables([t])
    assert r == CmapRanges.from_chars({0x3000})


def test_from_cmap_subtables_cp932_halfwidth_kana():
    # cp932 单字节 0xA1-0xDF 是半角片假名（U+FF61 起），直取会错记成
    # U+00A1-U+00DF 拉丁区（审查 P1 实错）
    from ass_font_substitute.core.models import CmapRanges

    t = _fake_table(3, 2, {0xA1: "g1", 0x41: "g2"})
    r = CmapRanges.from_cmap_subtables([t])
    assert r.contains(0xFF61)                 # ｡
    assert r.contains(ord("A"))
    assert not r.contains(0xA1)


def test_from_cmap_subtables_unknown_platform_skipped():
    # mac 平台等无法转 Unicode 的表：跳过不污染（键直取曾当码点入集）
    from ass_font_substitute.core.models import CmapRanges

    t = _fake_table(1, 0, {0xC9E7: "weird"})
    assert CmapRanges.from_cmap_subtables([t]).starts == ()


def test_from_cmap_subtables_unicode_keys_untouched():
    # Unicode 子表（0,3 / 3,1 / 3,10）键即码点，原样入集
    from ass_font_substitute.core.models import CmapRanges

    for pid, eid in ((0, 3), (3, 1), (3, 10)):
        t = _fake_table(pid, eid, {0x4E00: "g"})
        assert CmapRanges.from_cmap_subtables([t]).contains(0x4E00)
