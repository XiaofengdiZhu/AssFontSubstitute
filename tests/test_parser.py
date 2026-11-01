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


def test_parse_missing_os2_falls_back(tmp_path):
    # 终审 P1：缺 OS/2 表的老字库（恰是 v9 要救的文鼎系）曾整 face 丢弃
    # ——度量表缺失回退中性默认（400/5/非斜体）继续；name/cmap 缺失才判坏
    from fontTools.ttLib import TTFont

    p = tmp_path / "a.ttf"
    build_font(p, chars="永中", family="A體")
    f = TTFont(p)
    del f["OS/2"]
    f.save(tmp_path / "noos2.ttf")
    recs = parse_font_file(tmp_path / "noos2.ttf")
    assert len(recs) == 1
    r = recs[0]
    assert r.weight == 400 and r.width == 5 and not r.italic
    assert r.cmap.contains(ord("永"))


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


def test_parse_excludes_typographic_subfamily(tmp_path):
    # name ID 17（样式名称，FontCreator 的「样式名称」栏）是样式描述不
    # 是名称：FOTPunchello 家族它填 'Condensed Bold'、微软雅黑填
    # 'Regular'——入别名会污染按名搜索与展示名（用户 FontCreator 实查）
    from fontTools.ttLib import TTFont

    p = tmp_path / "a.ttf"
    build_font(p, chars="永", family="FOTPunchello", weight=700)
    f = TTFont(str(p))
    f["name"].setName("Condensed Bold", 17, 3, 1, 0x409)
    f.save(p)
    f.close()
    r = parse_font_file(p)[0]
    assert "FOTPunchello" in r.aliases
    assert "Condensed Bold" not in r.aliases


def test_parse_display_name_priority(tmp_path):
    # 展示名 = ID1 legacy family（Windows 字体查看器显示的就是它），
    # 缺失回退 ID16 → ID4；每级多语言 CJK 优先；@ 竖排变体名不作展示名
    from fontTools.ttLib import TTFont

    from ass_font_substitute.core.fontindex.parser import _display_name
    from ass_font_substitute.core.fontindex.parser import parse_font_file

    # ID1 有则用（优先于 ID16）：官方 Minion Pro Semibold 示例——
    # ID1='Minion Pro SmBd'、ID16='Minion Pro'，Windows 字体列表显示 SmBd
    p = tmp_path / "id16.ttf"
    build_font(p, chars="永", family="Minion Pro SmBd")
    f = TTFont(str(p))
    f["name"].setName("Minion Pro", 16, 3, 1, 0x409)
    f.save(p)
    f.close()
    assert parse_font_file(p)[0].display_name == "Minion Pro SmBd"

    # ID1 缺失 → ID16（FOTPunchello 系 ID1 若缺，typographic family 兜底）
    p = tmp_path / "no_id1.ttf"
    build_font(p, chars="永", family="Whatever")
    f = TTFont(str(p))
    f["name"].names = [r for r in f["name"].names if r.nameID != 1]
    f["name"].setName("Punchello Typo", 16, 3, 1, 0x409)
    f.save(p)
    f.close()
    assert parse_font_file(p)[0].display_name == "Punchello Typo"

    # display 桶只取 Windows 平台记录：Macintosh 平台同名记录序在前会
    # 劫持选择（FOTPunchelloCondBold 实证：Mac ID1='FOTPunchello' 排在
    # Win ID1='FOTPunchello Cond Bold' 前）；过滤只影响 display 桶，
    # Mac 名仍进 aliases 参与匹配
    p = tmp_path / "mac_first.ttf"
    build_font(p, chars="永", family="Temp")
    f = TTFont(str(p))
    nt = f["name"]
    nt.names = [r for r in nt.names if r.platformID == 3 and r.nameID != 1]
    nt.setName("FOTPunchello", 1, 1, 0, 0)                  # Mac 记录序在前
    nt.setName("FOTPunchello Cond Bold", 1, 3, 1, 0x409)    # Win 在后
    f.save(p)
    f.close()
    r = parse_font_file(p)[0]
    assert r.display_name == "FOTPunchello Cond Bold"
    assert "FOTPunchello" in r.aliases

    # ID16 缺失 → ID1，多语言 CJK 优先（微软雅黑）
    p = tmp_path / "cjk.ttf"
    build_font(p, chars="永", family="微软雅黑",
               extra_aliases={"en-US": "Microsoft YaHei"})
    assert parse_font_file(p)[0].display_name == "微软雅黑"

    # ID1/16 全缺（别名只剩 ps）→ ID4（build_font 会写 Macintosh 平台
    # 记录，ID1/4 全平台滤净再单写一条 Windows ID4）
    p = tmp_path / "full.ttf"
    build_font(p, chars="永", family="OnlyFamily")
    f = TTFont(str(p))
    nt = f["name"]
    nt.names = [r for r in nt.names if r.nameID not in (1, 4)]
    nt.setName("Only Family Full", 4, 3, 1, 0x409)
    f.save(p)
    f.close()
    r = parse_font_file(p)[0]
    assert r.display_name == "Only Family Full"

    # 纯函数级：@ 竖排变体跳过 + 别名首兜底
    assert _display_name({1: ["@ＭＳ ゴシック", "ＭＳ ゴシック"]},
                         ["@ＭＳ ゴシック", "ＭＳ ゴシック"]) == "ＭＳ ゴシック"
    assert _display_name({}, ["Whatever"]) == "Whatever"
    assert _display_name({}, []) == ""


def test_parse_family_names(tmp_path):
    # 字体系（FontCreator「字体系名称」）= ID16 实字段；缺失视 ID1
    # （spec：4 样式内字体 ID16 本就缺）；全语言集合；@ 竖排变体跳过
    from fontTools.ttLib import TTFont

    from ass_font_substitute.core.fontindex.parser import parse_font_file

    p = tmp_path / "fam.ttf"
    build_font(p, chars="永", family="Punchello")
    rec = parse_font_file(p)[0]
    assert rec.family_names == ("Punchello",)

    # ID16 在 → 压过 ID1；多语言去重保序
    f = TTFont(str(p))
    f["name"].setName("Punchello Typo", 16, 3, 1, 0x409)
    f["name"].setName("Punchello 排印", 16, 3, 1, 0x804)
    f["name"].setName("Punchello Typo", 16, 3, 1, 0x409)  # 重复记录
    f["name"].setName("@Punchello Vertical", 16, 3, 1, 0x411)
    f.save(p)
    f.close()
    assert parse_font_file(p)[0].family_names == (
        "Punchello Typo", "Punchello 排印")


def test_parse_name_langs(tmp_path):
    # 展示名语言候选（SCHEMA v7）：ID1/4/16 的 Windows 平台记录带
    # langID 入库，表序保序；样式名/PostScript/Mac 记录不参与
    p = tmp_path / "langs.ttf"
    build_font(p, chars="永", family="TestFam",
               extra_aliases={"zh-CN": "简体家族", "zh-TW": "繁體家族"})
    rec = parse_font_file(p)[0]
    # fontTools 保存时 name 表按 langID 升序重排，表序即 langID 序
    assert rec.name_langs[1] == [
        (0x404, "繁體家族"), (0x409, "TestFam"), (0x804, "简体家族")]
    assert rec.name_langs[4] == [
        (0x404, "繁體家族"), (0x409, "TestFam"), (0x804, "简体家族")]
    assert set(rec.name_langs) == {1, 4}    # 无 ID16/6（工厂不写 16）


def test_parse_name_records(tmp_path):
    # v8 命名卡原始记录：ID 1/2/3/4/16 全平台全语言入库（Mac 平台保留，
    # group_names 靠它去重）、保表序；PostScript(6)/版本(5) 不入
    from fontTools.ttLib import TTFont

    p = tmp_path / "recs.ttf"
    build_font(p, chars="永", family="Rec體")
    rec = parse_font_file(p)[0]
    nids = {nid for nid, _p, _l, _t in rec.name_records}
    assert nids == {1, 2, 3, 4}          # 工厂不写 16；6 不收
    assert (1, 3, 0x409, "Rec體") in rec.name_records
    # Mac 平台记录保留；@ 变体照存（展示层语义，别名侧才排除）
    f = TTFont(str(p))
    f["name"].setName("Mac Rec", 1, 1, 0, 0)
    f["name"].setName("@Rec體 Vertical", 4, 3, 1, 0x409)
    f.save(p)
    f.close()
    rec = parse_font_file(p)[0]
    assert (1, 1, 0, "Mac Rec") in rec.name_records
    assert (4, 3, 0x409, "@Rec體 Vertical") in rec.name_records


def test_parse_psname_prefers_windows_platform(tmp_path):
    # 审查二轮 P1：ID6 首条命中曾取到 platform0 变体名（corbell 实测
    # platform0='Corbel-Light' vs Windows='CorbelLight'）——去重键漂移。
    # PS 名只认 Windows 平台记录，无 Win 记录才回退首条
    from fontTools.ttLib.tables._n_a_m_e import NameRecord

    from tests.fonts import _make

    spec = {"chars": "永中", "family": "窗體", "psname": "CorbelLight"}
    tt = _make(spec)
    rec = NameRecord()
    rec.nameID, rec.platformID, rec.platEncID, rec.langID = 6, 0, 0, 0
    rec.string = "Corbel-Light"
    tt["name"].names.insert(0, rec)     # platform0 记录插到最前：旧码必中
    p = tmp_path / "w.ttf"
    tt.save(p)
    r = parse_font_file(p)[0]
    assert r.psname == "CorbelLight"
    # 无 Windows 记录的老字体：回退首条（Mac），不改键域
    tt2 = _make(spec)
    for rec2 in tt2["name"].names:
        if rec2.nameID == 6:
            rec2.platformID, rec2.platEncID, rec2.langID = 1, 0, 0
    p2 = tmp_path / "old.ttf"
    tt2.save(p2)
    assert parse_font_file(p2)[0].psname == "CorbelLight"


def _id6(platform: int, lang: int, s: str):
    from fontTools.ttLib.tables._n_a_m_e import NameRecord

    r = NameRecord()
    r.nameID = 6
    r.platformID = platform
    r.platEncID = 1 if platform == 3 else 0
    r.langID = lang
    r.string = s
    return r


def test_parse_psname_majority_fixes_single_mislabeled_record(tmp_path):
    # 腾祥嘉丽书宋简实证：厂商把 Win langID 0x404 那条 ID6 错标成
    # 'Tensentype JiaLiZhongHeiJ'（与中黑简撞键，整字体被误判重复版本跳过）；
    # 同文件 Mac 记录与 0x804 记录都写对。0x409 缺失时按众数取值，
    # 单条错标记录被多数修正
    from tests.fonts import _make

    spec = {"chars": "永中", "family": "腾祥嘉丽书宋简", "psname": "JiaLiShuSongJ"}
    tt = _make(spec)
    nt = tt["name"]
    # 重建 ID6 记录集：复刻书宋简实况（无 0x409）
    nt.names = [r for r in nt.names if r.nameID != 6]
    nt.names += [_id6(1, 0, "JiaLiShuSongJ"),
                 _id6(3, 0x404, "JiaLiZhongHeiJ"),   # 厂商错标：与中黑简同 PS 名
                 _id6(3, 0x804, "JiaLiShuSongJ")]
    p = tmp_path / "sfj.ttf"
    tt.save(p)
    assert parse_font_file(p)[0].psname == "JiaLiShuSongJ"


def test_parse_psname_win_english_canonical_over_majority(tmp_path):
    # 0x409 是 spec 正典：存在即直接采用，即使处于少数（多数只是
    # 0x409 缺失时的兜底推断，不能反过来否决显式正典记录）
    from tests.fonts import _make

    spec = {"chars": "永中", "family": "窗體", "psname": "Canonical-PS"}
    tt = _make(spec)
    nt = tt["name"]
    nt.names = [r for r in nt.names if r.nameID != 6]
    nt.names += [_id6(1, 0, "Legacy-PS"),
                 _id6(3, 0x804, "Legacy-PS"),
                 _id6(3, 0x409, "Canonical-PS")]
    p = tmp_path / "canon.ttf"
    tt.save(p)
    assert parse_font_file(p)[0].psname == "Canonical-PS"


def test_parse_psname_tie_prefers_win_over_mac_garbage(tmp_path):
    # 义启字心体实证：Mac 记录是乱码 'Î´'，Win 0x409 才有真名——
    # 众数并列（各 1 条）时必须回落 Win 首条而非 Mac 首条，
    # 回归护栏：防未来改动把并列回落改成 Mac 优先
    from tests.fonts import _make

    spec = {"chars": "永中", "family": "字心体", "psname": "WeiDingYi"}
    tt = _make(spec)
    nt = tt["name"]
    nt.names = [r for r in nt.names if r.nameID != 6]
    nt.names += [_id6(1, 0, "Î´"),               # Mac 乱码（序在前）
                 _id6(3, 0x404, "WeiDingYi")]
    p = tmp_path / "zxt.ttf"
    tt.save(p)
    assert parse_font_file(p)[0].psname == "WeiDingYi"


def test_parse_psname_mac_only_majority_never_beats_win(tmp_path):
    # 审查 P2：众数值只出现在 Mac 记录上时，去重键仍须取 Win 所见值——
    # 键域与 Windows 字体系统标识同源，Mac 重复记录不得劫持
    from tests.fonts import _make

    spec = {"chars": "永中", "family": "窗體", "psname": "WinUnique-B"}
    tt = _make(spec)
    nt = tt["name"]
    nt.names = [r for r in nt.names if r.nameID != 6]
    nt.names += [_id6(1, 0, "0Garbage-X"),
                 _id6(1, 0, "9Mac-Y"),
                 _id6(1, 0, "9Mac-Y"),
                 _id6(3, 0x404, "WinUnique-B")]
    p = tmp_path / "macmaj.ttf"
    tt.save(p)
    assert parse_font_file(p)[0].psname == "WinUnique-B"


def test_parse_at_variant_names_excluded_from_aliases(tmp_path):
    # 审查二轮 P1：@ 竖排变体名泄漏 aliases——'@'=0x40 排序恒最前，
    # 污染指纹/去重键/find_by_name，展示名回退 aliases[0] 时必占首位
    p = tmp_path / "v.ttf"
    build_font(p, chars="永中", family="甲體",
               extra_aliases={"zh-CN": "@甲體豎排"})
    r = parse_font_file(p)[0]
    assert not any(a.startswith("@") for a in r.aliases)
    assert "甲體" in r.aliases and "@甲體豎排" not in r.aliases
    assert r.display_name == "甲體"     # 回退链不被 @ 名劫持
    # 语言候选链（name_langs）同样不收 @ 变体（@ 名只进 name_records
    # 命名卡原始记录——test_parse_name_records 已断言其保留）
    assert all(not t.startswith("@")
               for recs in r.name_langs.values() for _, t in recs)


def test_parse_vf_enumerates_named_instances(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永宇", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Light", "location": {"wght": 300}},
                        {"name": "Black", "location": {"wght": 900}}])
    recs = parse_font_file(p)
    assert len(recs) == 3                      # 默认 + Light + Black
    assert [r.inst_index for r in recs] == [0, 1, 2]
    dflt, light, black = recs
    assert dflt.var_axes == ()                 # 默认行走默认态
    assert light.var_axes == (300.0,) and light.weight == 300
    assert black.var_axes == (900.0,) and black.weight == 900
    # 默认行 weight=OS/2（400），width 沿用 OS/2（审查 B1：wdth 不折算）
    assert dflt.weight == 400
    assert light.width == black.width == dflt.width
    # 限定名别名：族名+子族名同 langID 配对（fixture 族名 zh-CN/zh-TW/en？）
    # fixture 默认族名记录语言以 _EXTRA_LANGS 为准——断言限定名存在即可
    assert any("Light" in a for a in light.aliases)
    assert any("Black" in a for a in black.aliases)
    # display ∈ aliases 不变量（审查 B3）
    assert light.display_name in light.aliases
    assert black.display_name in black.aliases
    # name_langs 置空（审查 B2：GUI 展示链走 display_name 回退）
    assert light.name_langs == {} and black.name_langs == {}
    # cmap 同文件共享（同一 CmapRanges 对象）
    assert light.cmap is dflt.cmap and black.cmap is dflt.cmap
    # psname/version 文件级沿用
    assert light.psname == dflt.psname and black.version == dflt.version


def test_parse_vf_skips_default_equal_and_nameless_instances(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 400, "default": 400, "max": 900}],
             instances=[{"name": "Regular", "location": {"wght": 400}},   # 坐标=默认 → 跳过
                        {"name": "Black", "location": {"wght": 900}}])
    recs = parse_font_file(p)
    # inst_index 保持 fvar 声明序、跳过的实例留空洞（与 face_index
    # 允许空洞先例一致，preflight 裁定）：Regular=声明序 1 被跳过
    assert [r.inst_index for r in recs] == [0, 2]
    assert recs[1].weight == 900


def test_parse_static_unchanged(tmp_path):
    from tests.fonts import build_font
    p = tmp_path / "s.ttf"
    build_font(p, "永", family="靜態體")
    r, = parse_font_file(p)
    assert r.inst_index == 0 and r.var_axes == ()


def test_weight_verbatim_at_parse(tmp_path):
    # 2026-11-02 拍板：补偿出 parser——库内 weight 保真 OS/2 原值，
    # 「Heavy 名错标 400」不改写（补偿改在比较/排序时按需计算，
    # probes.effective_weight；指纹随之保真，规则迭代零重建）
    from tests.fonts import build_font
    cases = [
        ("演示 Heavy", 400),           # 错标体：保真 400（不写 900）
        ("演示 Bold", 400),
        ("演示 Condensed Bold", 400),
        ("Black Ops One", 400),
        ("演示 Heavy", 900),           # 正确标注同样不动
        ("演示 Thin", 100),
    ]
    for fam, os2 in cases:
        p = tmp_path / f"{fam.replace(' ', '_')}_{os2}.ttf"
        build_font(p, "永", family=fam, weight=os2)
        r, = parse_font_file(p)
        assert r.weight == os2, (fam, r.weight)


def test_weight_vf_axis_values_verbatim(tmp_path):
    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="重黑體 Heavy",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 400, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    dflt, black = parse_font_file(p)
    assert dflt.weight == 400            # 默认行保真 OS/2
    assert black.weight == 900 and black.inst_index == 1


def test_parse_vf_missing_subfamily_name_skipped(tmp_path):
    # subfamilyNameID 指向不存在的 name 记录 → 该实例跳过（拍板：无名不合成）
    from fontTools.ttLib import TTFont

    from tests.fonts import build_vf
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    tt = TTFont(str(p))
    tt["fvar"].instances[0].subfamilyNameID = 999   # 悬空 nameID
    tt.save(str(p))
    recs = parse_font_file(p)
    assert len(recs) == 1 and recs[0].inst_index == 0
