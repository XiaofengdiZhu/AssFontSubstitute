from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import update_index
from tests.fonts import build_font


def _mklib(d, n=2):
    """建库 helper：n 个文件、每文件 1 字体，建目录并增量索引入库。
    family 取目录名前缀保证跨目录身份互异（否则版本去重会合并同名字体）。"""
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        build_font(d / f"f{i}.ttf", chars="永中木", family=f"{d.name}體{i}")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.close()


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


def test_fingerprints_precomputed_and_reused(tmp_path, monkeypatch):
    # 指纹在 update_index 预计算入库；open 全命中缓存不再重算
    import ass_font_substitute.core.fontindex.library as library_mod
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    build_font(d / "b.ttf", chars="永中木", family="乙體", psname="YI")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    fps = {e.fingerprint for e in lib.entities()}
    assert len(fps) == 2

    def boom(*a, **k):
        raise AssertionError("open 不应重算指纹（缓存应全命中）")

    monkeypatch.setattr(library_mod, "fingerprint_of", boom)
    lib2 = LibraryIndex.open([d])
    assert {e.fingerprint for e in lib2.entities()} == fps


def test_fingerprint_recomputed_when_file_changes(tmp_path):
    # 文件内容变化（xxh 变）→ 缓存失效重算
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA",
               version="Version 1.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    fp1 = LibraryIndex.open([d]).entities()[0].fingerprint
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA",
               version="Version 2.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    fp2 = LibraryIndex.open([d]).entities()[0].fingerprint
    assert fp1 != fp2


def test_fingerprint_stale_xxh_recomputed(tmp_path):
    # 缓存表被篡改（xxh 不匹配）→ 回退重算，结果与现算一致
    from ass_font_substitute.core.fontindex.store import fingerprint_of
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.execute("UPDATE fingerprints SET xxh='tampered'")
    conn.commit()
    lib = LibraryIndex.open([d])
    e = lib.entities()[0]
    assert e.fingerprint == fingerprint_of(
        list(e.aliases), e.cmap, e.weight, e.width, e.italic,
        next(r[0] for r in open_db(d / "fontindex.db").execute(
            "SELECT xxh FROM files")))


def test_removed_file_cleans_fingerprints(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    build_font(d / "b.ttf", chars="永中木", family="乙體", psname="YI")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    (d / "b.ttf").unlink()
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    n = conn.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0]
    assert n == 1


def test_stats_counts_files(tmp_path):
    _mklib(tmp_path / "a", n=1)                    # 既有 helper：1 文件 1 字体
    _mklib(tmp_path / "b", n=2)
    lib = LibraryIndex.open([tmp_path / "a", tmp_path / "b"])
    st = lib.stats()
    assert st["files"] == 3                        # 文件总数（跨目录）
    assert st["fonts"] == 3
