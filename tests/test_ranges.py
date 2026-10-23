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
