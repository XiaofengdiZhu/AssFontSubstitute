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
    # 语义变化（cmap 变）→ 缓存失效重算
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA",
               version="Version 1.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    fp1 = LibraryIndex.open([d]).entities()[0].fingerprint
    build_font(d / "a.ttf", chars="永中木", family="甲體", psname="JIA",
               version="Version 1.000")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    fp2 = LibraryIndex.open([d]).entities()[0].fingerprint
    assert fp1 != fp2


def test_fingerprint_stable_when_only_version_changes(tmp_path):
    # 指纹是纯语义身份：version 变而别名/cmap/样式未变 → 指纹不变，
    # 渲染缓存与记忆跨文件更新可复用
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
    assert fp1 == fp2


def test_fingerprint_stale_mtime_recomputed(tmp_path):
    # 缓存表被篡改（mtime 令牌不匹配）→ 回退重算，结果与现算一致
    from ass_font_substitute.core.fontindex.store import fingerprint_of
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.execute("UPDATE fingerprints SET mtime_ns=1")
    conn.commit()
    lib = LibraryIndex.open([d])
    e = lib.entities()[0]
    assert e.fingerprint == fingerprint_of(
        list(e.aliases), e.cmap, e.weight, e.width, e.italic)


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


def test_display_name_prefers_cjk_alias(tmp_path):
    # 展示名取含 CJK 的别名（微软雅黑 而非排序更前的 Microsoft YaHei）；
    # 无 CJK 别名退回排序首名
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="微软雅黑",
               extra_aliases={"en-US": "Microsoft YaHei"})
    build_font(d / "b.ttf", chars="永", family="Arial")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    ents = LibraryIndex.open([d]).entities()
    a = next(e for e in ents if "微软雅黑" in e.aliases)
    assert a.display_name == "微软雅黑"
    b = next(e for e in ents if "Arial" in e.aliases)
    assert b.display_name == "Arial"


def test_category_read_from_db(tmp_path):
    # 库列即最终分类：建索引时由分类器算好入库，装载直读不重算——
    # 改分类规则须升 SCHEMA_VERSION 重建（旧库列才会过期）
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="微软雅黑")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.execute("UPDATE fonts SET category='未分类'")
    conn.commit()
    lib = LibraryIndex.open([d])
    assert lib.entities()[0].category == "未分类"


def test_display_name_read_from_db(tmp_path):
    # 展示名同分类：parser 按 name ID 官方优先（ID16→ID1→ID4，Windows
    # 字体查看器同款）选好入库，装载直读库列——改展示规则走升版重建
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="微软雅黑")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.execute("UPDATE fonts SET display_name='改过的名'")
    conn.commit()
    lib = LibraryIndex.open([d])
    assert lib.entities()[0].display_name == "改过的名"


def test_size_mtime_read_from_db(tmp_path):
    # 详情页文件信息：size/mtime_ns 随实体直读（SELECT 本就查出，
    # 此前被 _load 丢弃）
    d = tmp_path / "lib"
    d.mkdir()
    p = d / "a.ttf"
    build_font(p, chars="永", family="A體")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    e = LibraryIndex.open([d]).entities()[0]
    assert e.size == p.stat().st_size
    assert e.mtime_ns == p.stat().st_mtime_ns


def test_family_names_read_from_db(tmp_path):
    # 字体系库列直读（ID16 缺视 ID1，全语言集合）；详情页同字体系归族
    # 按集合交集，不入库则每次解析全库不可行
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="Punchello")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    e = LibraryIndex.open([d]).entities()[0]
    assert e.family_names == ("Punchello",)


def test_name_langs_read_from_db_and_chain_pick(tmp_path):
    # v7：语言记录入库装载还原 int 键；展示名按界面语言链现挑——
    # 简中出简体名、繁中出繁体名，链不中退表序首条
    from ass_font_substitute.gui.name_lang import pick_display_name

    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="Microsoft YaHei",
               extra_aliases={"zh-CN": "微软雅黑", "zh-TW": "微軟雅黑"})
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    e = LibraryIndex.open([d]).entities()[0]
    assert e.name_langs[1] == [
        (0x404, "微軟雅黑"), (0x409, "Microsoft YaHei"), (0x804, "微软雅黑")]
    assert all(isinstance(lg, int) and isinstance(t, str)
               for lg, t in e.name_langs[1])
    assert pick_display_name(e, "zh-Hans") == "微软雅黑"
    assert pick_display_name(e, "zh-Hant") == "微軟雅黑"
    # 入库 display_name 不变（CJK 优先旧规则：表序首条 CJK——fontTools
    # 重排后 0x404 繁体在前），作链挑选兜底
    assert e.display_name == "微軟雅黑"
