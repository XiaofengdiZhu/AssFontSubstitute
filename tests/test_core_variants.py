from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.extract import merge_usages


def _write(tmp_path, style_font, inline_font):
    # 与 tests/test_extract.py 同口径：utf-8-sig（BOM 直判，绕开高频字合理性
    # 校验——本文件中性字不足 5 个）；Style 行字段数须与 Format 声明一致，
    # 否则 light_ass 把多余字段折进最后一个字段（字体名会带上 ",54"）。
    p = tmp_path / "a.ass"
    p.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
        f"Style: Def,{style_font},54\n\n"
        "[Events]\nFormat: Layer, Style, Text\n"
        f"Dialogue: 0,Def,,0,0,0,,字{{\\fn{inline_font}}}字\n",
        "utf-8-sig")
    return p


def test_variants_collect_all_spellings(tmp_path):
    r = extract_usages(_write(tmp_path, "FooBar", "foobar"))
    assert len(r.usages) == 1
    assert r.usages[0].raw_name == "FooBar"
    assert r.usages[0].variants == {"FooBar", "foobar"}


def test_merge_unions_variants(tmp_path):
    a = extract_usages(_write(tmp_path, "FooBar", "FooBar"))
    # 三种拼写须落入同一规范化桶（casefold 后同 norm）才能合并为一张卡
    b = extract_usages(_write(tmp_path, "foobar", "FOOBAR"))
    m = merge_usages([a, b])
    assert len(m) == 1
    assert m[0].variants == {"FooBar", "foobar", "FOOBAR"}
