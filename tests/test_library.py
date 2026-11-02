from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import update_index
from tests.fonts import build_font
from tests.fonts import build_vf


def _mklib(d, n=2):
    """建库 helper：n 个文件、每文件 1 字体，建目录并增量索引入库。
    family 取目录名前缀保证跨目录身份互异（否则版本去重会合并同名字体）。"""
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        build_font(d / f"f{i}.ttf", chars="永中木", family=f"{d.name}體{i}")
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    ents = lib.entities()
    assert len(ents) == 2  # X-Medium 去重 + Y-Light
    x = next(e for e in ents if e.psname == "X-Medium")
    assert x.version == (2, 0)
    assert any(e.psname == "X-Medium" for e in ents)
    # 审查三轮 P2：skipped 结构化 (dir, rel, face, inst)，展示文案归 GUI
    assert len(lib.skipped()) == 1
    d0, rel0, _fi0, _ix0 = lib.skipped()[0]
    assert d0 == str(d) and rel0 == "old.ttf"


def test_find_by_name_normalized(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="華康測試體W5",
               extra_aliases={"zh-CN": "华康测试体 W5"})
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    fp1 = LibraryIndex.open([d]).entities()[0].fingerprint
    build_font(d / "a.ttf", chars="永中木", family="甲體", psname="JIA",
               version="Version 1.000")
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    fp1 = LibraryIndex.open([d]).entities()[0].fingerprint
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA",
               version="Version 2.000")
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    fp2 = LibraryIndex.open([d]).entities()[0].fingerprint
    assert fp1 == fp2


def test_fingerprint_stale_mtime_recomputed(tmp_path):
    # 缓存表被篡改（mtime 令牌不匹配）→ 回退重算，结果与现算一致
    from ass_font_substitute.core.fontindex.store import fingerprint_of
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    (d / "b.ttf").unlink()
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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
    conn = open_db(d / "AssFontSubstitute.db")
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


def test_same_psname_instances_survive_dedup(tmp_path):
    # 同一 VF 文件各实例行共享 psname——去重键加 inst 维度后不得折叠
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    li = LibraryIndex.open([d])
    ents = li.entities()
    assert len(ents) == 3
    assert {e.inst_index for e in ents} == {0, 1, 2}
    black = next(e for e in ents if e.inst_index == 2)
    assert black.weight == 900 and black.var_axes == (900.0,)
    assert next(e for e in ents if e.inst_index == 0).var_axes == ()
    # find_by_name：限定名命中实例、族名命中默认+实例全体
    assert any(e.inst_index == 2
               for e in li.find_by_name(normalize_name("变化體 Black")))
    assert ({e.inst_index for e in li.find_by_name(normalize_name("变化體"))}
            >= {0, 1, 2})


def test_cross_dir_duplicate_vf_dedup_per_instance(tmp_path):
    # 两目录放同一 VF：按 (psname, inst_index) 对齐去重，每实例只留一份，
    # 目录序靠前者胜，另一目录整份进 _skipped
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    spec = {"chars": "永", "family": "变化體",
            "axes": [{"name": "Weight", "tag": "wght",
                      "min": 300, "default": 400, "max": 900}],
            "instances": [{"name": "Light", "location": {"wght": 300}},
                          {"name": "Black", "location": {"wght": 900}}]}
    for d in (d1, d2):
        d.mkdir()
        build_vf(d / "vf.ttf", **spec)
        conn = open_db(d / "AssFontSubstitute.db")
        update_index(d, conn)
        conn.close()
    li = LibraryIndex.open([d1, d2])
    ents = li.entities()
    assert len(ents) == 3
    assert {e.inst_index for e in ents} == {0, 1, 2}
    assert all(e.dir_path == str(d1) for e in ents)
    assert len([s for s in li.skipped() if str(d2) == s[0]]) == 3
    assert all(rel == "vf.ttf" for d, rel, fi, ix in li.skipped())


def test_load_name_records_from_db(tmp_path):
    # v8：详情命名卡按需读库（裸连接、行缺失/库不可读一律 None 兜底）
    from ass_font_substitute.core.fontindex.library import load_name_records

    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="TestFam")
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    conn.close()
    recs = load_name_records(str(d), "a.ttf", 0)
    assert (1, 3, 0x409, "TestFam") in recs
    assert all(isinstance(t, str) for _n, _p, _l, t in recs)
    # 行缺失 / 库不存在：None（详情走别名平铺兜底）
    assert load_name_records(str(d), "missing.ttf", 0) is None
    assert load_name_records(str(tmp_path / "nope"), "a.ttf", 0) is None


def test_load_name_records_uri_escaping(tmp_path):
    # 二轮审查 P2：手工 quote 改 as_uri 后的转义回归——中文/空格目录
    # （as_uri 百分号编码，sqlite 解码）必须照常读出行；此前与 jobs.py
    # as_uri 口径不一致正是本修动机
    from ass_font_substitute.core.fontindex.library import load_name_records

    d = tmp_path / "字 体 库#1"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="TestFam")
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    conn.close()
    recs = load_name_records(str(d), "a.ttf", 0)
    assert recs is not None
    assert (1, 3, 0x409, "TestFam") in recs


def test_deep_corruption_degrades_per_dir(tmp_path):
    # 审查二轮 P1 回归：深层损坏（数据页坏块/截断）能过 open_db 头级
    # 探测，但在装载 SELECT / zlib 解压层炸出——曾把整个聚合装载炸成
    # failed；现按目录降级 offline，其余目录照常装载
    d_ok = tmp_path / "ok"
    d_bad = tmp_path / "bad"
    _mklib(d_ok, n=1)
    _mklib(d_bad, n=1)
    # 深层损坏形态一：cmap blob 坏——装载 SELECT 正常、zlib.decompress
    # 层 zlib.error（open_db 头级探测挡不住；纯页翻转 FF 小库上 SQLite
    # 静默视作空表，不触发装载异常）
    conn = open_db(d_bad / "AssFontSubstitute.db")
    conn.execute("UPDATE cmap_data SET data=X'00ff00ff00'")
    conn.commit()
    conn.close()

    lib = LibraryIndex.open([d_ok, d_bad])
    assert d_bad in lib.offline_dirs
    names = {e.display_name for e in lib.entities()}
    assert any(n.startswith("ok") for n in names)   # 好目录实体在
    assert not any(n.startswith("bad") for n in names)

    # 中途失败形态：同目录两字体只坏一个 cmap——目录判 offline，
    # 坏页前已解析的行也不得泄漏进聚合（口径一致）
    d_two = tmp_path / "two"
    d_two.mkdir()
    # 两个不同 chars → 两份 cmap blob：坏第二个，f0 解析进 buf 后 f1
    # 才炸——中途失败形态（同 chars 会 UNIQUE 去重成单 blob，第一行
    # 即炸，buf 恒空，测不到泄漏防护）
    build_font(d_two / "f0.ttf", chars="永", family="兩體0")
    build_font(d_two / "f1.ttf", chars="中木國", family="兩體1")
    conn = open_db(d_two / "AssFontSubstitute.db")
    update_index(d_two, conn)
    last = conn.execute("SELECT MAX(id) FROM cmap_data").fetchone()[0]
    conn.execute("UPDATE cmap_data SET data=X'00ff00ff00' WHERE id=?", (last,))
    conn.commit()
    conn.close()
    lib2 = LibraryIndex.open([d_two])
    assert d_two in lib2.offline_dirs
    assert lib2.entities() == []
    assert lib2.stats()["fonts"] == 0

    # 聚合层：load_library 把装载期离线并入快照（gui/jobs）
    from ass_font_substitute.gui.jobs import load_library
    snap = load_library([str(d_ok), str(d_bad)])
    assert str(d_bad) in snap.offline_dirs
    assert str(d_ok) not in snap.offline_dirs


def test_eff_weight_compensated_at_load(tmp_path):
    # 2026-11-02 拍板：补偿在聚合装载时算成 eff_weight，weight 保真 OS/2；
    # 实例行（inst_index>0）与「名称无重量词」实体 eff==raw
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "heavy.ttf", chars="永", family="演示 Heavy", weight=400)
    build_font(d / "reg.ttf", chars="永", family="演示 Regular", weight=400)
    build_vf(d / "vf.ttf", chars="永", family="重黑體 Heavy",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 400, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    conn = open_db(d / "AssFontSubstitute.db")
    update_index(d, conn)
    conn.close()
    ents = LibraryIndex.open([d]).entities()
    by = {e.display_name: e for e in ents}
    assert by["演示 Heavy"].weight == 400       # 库内保真
    assert by["演示 Heavy"].eff_weight == 900   # 补偿在 eff
    assert by["演示 Regular"].eff_weight == 400
    assert by["重黑體 Heavy"].weight == 400     # VF 默认行：raw 保真
    assert by["重黑體 Heavy"].eff_weight == 900  # 默认行走补偿（统一规则）
    assert by["重黑體 Heavy Black"].weight == 900
    assert by["重黑體 Heavy Black"].eff_weight == 900  # 实例行不补偿


def test_ro_uri_shapes():
    # 审查三轮 P1 回归：UNC 路径 as_uri 产出 file://host/share/...（带
    # authority），SQLite 一律拒绝（invalid uri authority，审查 agent 在
    # admin share 实测）——ro_uri 对 UNC 改拼 4 斜杠 file:////host/...；
    # 本地盘符维持 3 斜杠、中文/空格照旧 percent-encode。真 UNC 连通
    # 依赖网络环境，此处钉死形状
    from pathlib import Path

    from ass_font_substitute.core.fontindex.library import ro_uri

    assert ro_uri(Path("D:/库 目 录/AssFontSubstitute.db")) == (
        "file:///D:/%E5%BA%93%20%E7%9B%AE%20%E5%BD%95/AssFontSubstitute.db")
    unc = Path(r"//nas/share/x.db")
    raw = unc.resolve().as_uri()
    assert raw.startswith("file://nas/") and not raw.startswith("file:///nas/")
    assert ro_uri(unc) == "file:////" + raw[len("file://"):]
