from ass_font_substitute.core.coverage.matcher import classify_usage, twin_convert
from ass_font_substitute.core.fontindex.library import LibraryIndex, normalize_name
from ass_font_substitute.core.fontindex.store import open_db, update_index
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
