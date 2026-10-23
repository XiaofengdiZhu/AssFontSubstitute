from ass_font_substitute.core.assparse.extract import extract_usages, merge_usages

ASS = """[Script Info]
Title: t
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour
Style: Main,華康主體W5,52,&H00FFFFFF
Style: Alt,華康副體W7,48,&H00FFFFFF

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字
Dialogue: 0,0:00:05.00,0:00:09.00,Alt,,0,0,0,,{\\fn華康行內體}行內字體{text
Comment: 0,0:00:09.00,0:00:10.00,Main,,0,0,0,,{\\fn華康註解體}註解不計字
Dialogue: 0,0:00:10.00,0:00:12.00,Ghost,,0,0,0,,幽靈樣式
"""


def _write(tmp_path, text=ASS, name="a.ass"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8-sig", newline="")
    return p


def test_extract(tmp_path):
    r = extract_usages(_write(tmp_path))
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].style_count == 1
    assert set(by["華康主體w5"].chars) >= {"永", "和", "的", "中", "文"}
    # 行内切换：行内体拿到行内的字
    assert by["華康行內體"].inline_count == 1
    assert "行" in by["華康行內體"].chars
    # Comment：引用但不计字
    assert "華康註解體" in by and len(by["華康註解體"].chars) == 0
    # 未知样式
    assert r.unknown_styles == ["Ghost"]


def test_empty_fontname_style(tmp_path):
    bad = ASS.replace("Style: Alt,華康副體W7,", "Style: Alt,,")
    r = extract_usages(_write(tmp_path, bad))
    assert "Alt" in r.invalid_styles


def test_merge(tmp_path):
    a = extract_usages(_write(tmp_path, name="a.ass"))
    b = extract_usages(_write(tmp_path, ASS.replace("永和的中文字", "另外的字"), "b.ass"))
    merged = merge_usages([a, b])
    m = {u.norm_name: u for u in merged}
    assert m["華康主體w5"].style_count == 2
    assert {"永", "另"} <= set(m["華康主體w5"].chars)
