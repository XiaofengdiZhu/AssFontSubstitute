from ass_font_substitute.core.fontindex.library import LibraryIndex, normalize_name
from ass_font_substitute.core.fontindex.store import open_db, update_index
from tests.fonts import build_font


def test_version_dedup(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "old.ttf", chars="永", family="X體", psname="X-Medium",
               version="Version 1.000")
    build_font(d / "new.ttf", chars="永中", family="X體", psname="X-Medium",
               version="Version 2.000")
    build_font(d / "newer_offdir.ttf", chars="永", family="Y體", psname="Y-Light",
               version="Version 1.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    ents = lib.entities()
    assert len(ents) == 2  # X-Medium 去重 + Y-Light
    x = next(e for e in ents if e.psname == "X-Medium")
    assert x.version == (2, 0)
    assert any("旧版本" in s for s in lib.skipped())


def test_find_by_name_normalized(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="華康測試體W5",
               extra_aliases={"zh-CN": "华康测试体 W5"})
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    assert lib.find_by_name(normalize_name(" 华康测试体 W5 "))
    assert lib.find_by_name(normalize_name("華康測試體W5"))
    assert not lib.find_by_name("nope")
