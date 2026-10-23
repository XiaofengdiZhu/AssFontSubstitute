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
