from fontTools.ttLib import TTFont

from tests.fonts import build_font
from tests.fonts import build_ttc


def test_build_font_roundtrip(tmp_path):
    p = tmp_path / "f.ttf"
    build_font(p, chars="永中木水", family="華康測試體W5", psname="DFTest-W5",
               version="Version 5.001", weight=700)
    tt = TTFont(p)
    names = {r.nameID: r.toUnicode() for r in tt["name"].names if r.platformID == 3}
    assert names[1] == "華康測試體W5"
    assert names[6] == "DFTest-W5"
    assert "5.001" in names[5]
    assert ord("永") in tt.getBestCmap()
    assert tt["OS/2"].usWeightClass == 700


def test_build_ttc(tmp_path):
    p = tmp_path / "c.ttc"
    build_ttc(p, [{"chars": "永", "family": "A體"}, {"chars": "中", "family": "B體"}])
    assert len(TTFont(p, fontNumber=0)["name"].names) > 0
    from fontTools.ttLib import TTCollection
    assert len(TTCollection(p).fonts) == 2
