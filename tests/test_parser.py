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
