from ass_font_substitute.core.coverage.matcher import classify_usage
from ass_font_substitute.core.coverage.matcher import twin_convert
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import update_index
from ass_font_substitute.core.models import FontUsage
from tests.fonts import build_font


def _usage(name, chars, styles=1):
    return FontUsage(name, normalize_name(name), styles, 0, set(chars))


def _lib(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    # 简体替身：名称=华康测试体W5（繁名经 t2s 转换后恰好命中，真正走孪生分支）
    build_font(d / "sc.ttf", chars="永和的中文字", family="华康测试体W5")
    # 繁体原字体：缺 "中"
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    return LibraryIndex.open([d])


def test_twin_under_coverage_falls_back_to_missing(tmp_path):
    # R14：孪生命中但覆盖不达标 → 库中没有（missing），不得归 twin/replace
    d = tmp_path / "lib"
    d.mkdir()
    # 简体替身：名称恰为繁名 t2s 转换结果（孪生可命中），但 cmap 缺 "中"
    build_font(d / "sc.ttf", chars="永和的文字", family="华康测试体W5")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    r = classify_usage(_usage("華康測試體W5", "永和的中文字"), lib)
    assert r.kind == "missing"
    assert r.twin_hit is None and r.entities == []


def test_in_library_low_coverage_prefers_twin(tmp_path):
    # 方案A：本名在库但覆盖不足 → 先跑孪生；转换名（简体版）在库且达标 → 孪生对应
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5")    # 本名在库，缺 "中"
    build_font(d / "sc.ttf", chars="永和的中文字", family="华康原体W5")  # t2s(華康原體W5) 精确命中
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    r = classify_usage(_usage("華康原體W5", "永和的中文字"), lib)
    assert r.kind == "twin"
    assert r.twin_hit is not None and r.twin_hit.display_name == "华康原体W5"
    assert r.coverage == 1.0


def test_in_library_low_coverage_twin_under_coverage_still_replace(tmp_path):
    # 方案A回落：孪生也在库但覆盖不达标 → 仍需替换（在库实体作候选）
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5")
    build_font(d / "sc.ttf", chars="永和的文字", family="华康原体W5")  # 孪生也缺 "中"
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    r = classify_usage(_usage("華康原體W5", "永和的中文字"), lib)
    assert r.kind == "replace" and "中" in r.missing_chars
    assert r.entities and r.twin_hit is None


def test_twin_via_in_library_alias_expansion(tmp_path):
    # 字幕写 PostScript 名（纯 ASCII，OpenCC 转不动），但在库实体自身别名
    # 带中文名 → 别名扩展转换后命中简版孪生（真实案例：DFHuaZongW5-B5 → 华康华综体W5）
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5",
               psname="DFYuanTi-B5")      # 别名 = {DFYuanTi-B5, 華康原體W5}，缺 "中"
    build_font(d / "sc.ttf", chars="永和的中文字", family="华康原体W5",
               psname="DFYuanTi-GB")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    r = classify_usage(_usage("DFYuanTi-B5", "永和的中文字"), lib)
    assert r.kind == "twin"
    assert r.twin_hit is not None
    assert "华康原体W5" in r.twin_hit.aliases
    assert r.coverage == 1.0


def test_alias_expansion_excludes_self(tmp_path):
    # 别名转换回自身（繁名实体的中文名别名经 t2s 命中简名实体、或 ASCII 别名原样
    # 查回自己）不得算孪生：自身覆盖必然不达标路径里的实体要排除
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "tc.ttf", chars="永和的文字", family="華康原體W5",
               psname="DFYuanTi-B5")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    r = classify_usage(_usage("DFYuanTi-B5", "永和的中文字"), lib)
    assert r.kind == "replace"                # 没有简版 → 孪生失败回落 replace
    assert r.twin_hit is None
    assert any(e.psname == "DFYuanTi-B5" for e in r.entities)


def test_twin_convert():
    assert twin_convert("華康測試體W5", "t2s") == "华康测试体 W5" or \
           twin_convert("華康測試體W5", "t2s").replace(" ", "") == "华康测试体W5"


def test_classify(tmp_path):
    lib = _lib(tmp_path)
    # 孪生命中：繁名转简 = 华康测试体W5，覆盖达标
    r = classify_usage(_usage("華康測試體W5", "永和的中文字"), lib)
    assert r.kind == "twin"
    # 需替换：在库但缺字
    r2 = classify_usage(_usage("華康原體W5", "永和的中文字"), lib, threshold=0.98)
    assert r2.kind == "replace" and "中" in r2.missing_chars
    # 保留：在库且覆盖达标
    r3 = classify_usage(_usage("華康原體W5", "永和的文"), lib)
    assert r3.kind == "keep"
    # 不在库
    r4 = classify_usage(_usage("不存在體", "永"), lib)
    assert r4.kind == "missing"
    # 零用字 → 保留
    r5 = classify_usage(_usage("不存在體", ""), lib)
    assert r5.kind == "keep"


def test_default_threshold_is_95(tmp_path):
    # 设置默认值钉死：24/25 = 96% 达默认 95%（keep），不达旧默认 98%（replace）
    import inspect

    from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
    assert COV_THRESHOLD == 0.95
    assert inspect.signature(classify_usage).parameters["threshold"].default == 0.95
    d = tmp_path / "lib"
    d.mkdir()
    chars = "永和的文字春夏秋冬山川田石目心手白木水火土金竹米"   # 24 字
    assert len(chars) == 24
    build_font(d / "f.ttf", chars=chars, family="覆蓋體")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    u = _usage("覆蓋體", chars + "中")            # 24/25 = 96%
    assert classify_usage(u, lib).kind == "keep"
    assert classify_usage(u, lib, threshold=0.98).kind == "replace"


def test_coverage_ignores_invisible_chars():
    # 审查二轮 P2：U+FE0F/U+200D/U+FEFF/U+2009 等不可见/空白字符曾全算
    # 缺字——覆盖率虚低放行缺字候选，且与渲染口径不一致。不入分子分母
    from ass_font_substitute.core.coverage.matcher import coverage_detail
    from ass_font_substitute.core.models import CmapRanges

    cmap = CmapRanges.from_chars([ord(c) for c in "永宇"])
    # 无关字符单独成集：视为全覆盖（全不可见时不因 cmap 查不到报缺）。
    # 码点全用 \u 转义：FE0F/U+2009 这类字面量会被编辑器规范化成可见形
    for c in ("️", "‍", "﻿", " ", " ", "​", "̈", "­"):
        ratio, missing = coverage_detail(cmap, {c})
        assert (ratio, missing) == (1.0, []), hex(ord(c))
    # 真缺字不受影响；无关字符不进缺字列表也不摊薄比例
    ratio, missing = coverage_detail(cmap, {"永", "中", "️", " "})
    assert ratio == 0.5 and missing == ["中"]


def test_classify_ignores_invisible_at_threshold_boundary(tmp_path):
    # 修复的原始症状（P2）：不可见字符摊薄比例，恰好压线的用字被误判
    # replace——真字 19/20 = 95% 恰达默认阈值；混入 5 个不可见字符后
    # 旧口径 19/25 = 76% → replace，新口径仍按 19/20 判 keep
    d = tmp_path / "lib"
    d.mkdir()
    covered = "永和的文字春夏秋冬山川田石目心手白木水"    # 19 字
    assert len(covered) == 19
    build_font(d / "f.ttf", chars=covered, family="边线体")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    lib = LibraryIndex.open([d])
    invisible = "️‍﻿ \u2009̈­"                # FE0F/200D/FEFF/2009/200B/0308/00AD
    assert len(invisible) == 7
    u = _usage("边线体", covered + "中" + invisible)
    r = classify_usage(u, lib)
    assert r.kind == "keep" and r.coverage == 0.95
    assert r.missing_chars == ["中"]               # 缺字列表不混入不可见字符
