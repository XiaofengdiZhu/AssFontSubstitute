import pytest

from ass_font_substitute.core.fontindex.parser import parse_font_file
from tests.fonts import build_font, build_ttc


def test_parse_single(tmp_path):
    p = tmp_path / "a.ttf"
    build_font(p, chars="永中木水", family="華康測試體W5", psname="DFTest-W5",
               version="Version 5.001", weight=700,
               extra_aliases={"zh-CN": "华康测试体 W5"})
    recs = parse_font_file(p)
    assert len(recs) == 1
    r = recs[0]
    assert r.face_index == 0
    assert r.psname == "DFTest-W5"
    assert r.version == (5, 1)
    assert r.weight == 700 and not r.italic
    assert "華康測試體W5" in r.aliases and "华康测试体 W5" in r.aliases
    assert r.cmap.contains(ord("永"))
    assert len(r.xxh) == 16


def test_parse_ttc(tmp_path):
    p = tmp_path / "a.ttc"
    build_ttc(p, [dict(chars="永", family="A體"), dict(chars="中", family="B體")])
    recs = parse_font_file(p)
    assert [r.face_index for r in recs] == [0, 1]
    assert {r.aliases[0] for r in recs} >= {"A體", "B體"}


def test_parse_bad_file(tmp_path):
    p = tmp_path / "bad.ttf"
    p.write_bytes(b"not a font")
    from ass_font_substitute.core.fontindex.parser import FontParseError
    with pytest.raises(FontParseError):
        parse_font_file(p)
