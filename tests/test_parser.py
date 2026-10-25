import pytest
from fontTools.ttLib import TTCollection

from ass_font_substitute.core.fontindex.parser import FontParseError
from ass_font_substitute.core.fontindex.parser import parse_font_file
from tests.fonts import build_font
from tests.fonts import build_ttc


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


def test_parse_no_full_read(tmp_path, monkeypatch):
    # 解析只定点读 name/OS/2/cmap 表，不读全文（文件级变更检测由
    # size+mtime 门负责）——全文读取（旧 xxh 指纹行为）回归即炸
    from pathlib import Path

    def boom(self, *a, **k):
        raise AssertionError("parse_font_file 不应全量读取文件")

    monkeypatch.setattr(Path, "read_bytes", boom)
    p = tmp_path / "a.ttf"
    build_font(p, chars="永中", family="A體")
    recs = parse_font_file(p)
    assert recs[0].cmap.contains(ord("永"))


def test_parse_ttc(tmp_path):
    p = tmp_path / "a.ttc"
    build_ttc(p, [{"chars": "永", "family": "A體"}, {"chars": "中", "family": "B體"}])
    recs = parse_font_file(p)
    assert [r.face_index for r in recs] == [0, 1]
    assert {r.aliases[0] for r in recs} >= {"A體", "B體"}


def test_parse_ttc_partial_face_failure(tmp_path):
    # spec §11：TTC 个别子字体解析失败 → 跳过该子字体，文件其余照常
    p = tmp_path / "a.ttc"
    build_ttc(p, [{"chars": "永", "family": "A體"}, {"chars": "中", "family": "B體"}])
    coll = TTCollection(str(p))
    for f in coll.fonts:                 # 强制读完，避免惰性句柄
        for tag in list(f.keys()):
            _ = f[tag]
    del coll.fonts[1]["cmap"]            # face 1 解析必失败
    p2 = tmp_path / "broken.ttc"
    coll.save(p2)
    coll.close()
    recs = parse_font_file(p2)
    assert [r.face_index for r in recs] == [0]   # 保留原始枚举下标
    assert "A體" in recs[0].aliases


def test_parse_ttc_all_faces_broken_raises(tmp_path):
    p = tmp_path / "a.ttc"
    build_ttc(p, [{"chars": "永", "family": "A體"}, {"chars": "中", "family": "B體"}])
    coll = TTCollection(str(p))
    for f in coll.fonts:
        for tag in list(f.keys()):
            _ = f[tag]
        del f["cmap"]
    p2 = tmp_path / "allbroken.ttc"
    coll.save(p2)
    coll.close()
    with pytest.raises(FontParseError):
        parse_font_file(p2)


def test_parse_bad_file(tmp_path):
    p = tmp_path / "bad.ttf"
    p.write_bytes(b"not a font")
    from ass_font_substitute.core.fontindex.parser import FontParseError
    with pytest.raises(FontParseError):
        parse_font_file(p)
