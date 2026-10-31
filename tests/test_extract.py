from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.extract import merge_usages

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


def test_fn0_reset_no_phantom_bucket(tmp_path):
    # \fn0 = 还原样式字体：不得产生名为 "0" 的幽灵桶，重置后的字归样式字体
    line = "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字"
    ass = ASS.replace(line, "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fn華康主體W5}甲{\\fn0}乙")
    r = extract_usages(_write(tmp_path, ass))
    by = {u.norm_name: u for u in r.usages}
    assert "0" not in by
    assert by["華康主體w5"].inline_count == 1
    assert {"甲", "乙"} <= by["華康主體w5"].chars


def test_fn_paren_name_full(tmp_path):
    # 行内 \fn 名含括号：light_ass 会把 "(...)" 当参数截断 value，须恢复完整写法
    line = "Dialogue: 0,0:00:05.00,0:00:09.00,Alt,,0,0,0,,{\\fn華康行內體}行內字體{text"
    ass = ASS.replace(line, "Dialogue: 0,0:00:05.00,0:00:09.00,Alt,,0,0,0,,{\\fn華康行內體(P)}行內字體")
    r = extract_usages(_write(tmp_path, ass))
    by = {u.norm_name: u for u in r.usages}
    assert "華康行內體(p)" in by
    assert by["華康行內體(p)"].inline_count == 1
    assert "行" in by["華康行內體(p)"].chars


def test_style_name_case_insensitive(tmp_path):
    # 终审 P1：VSFilter/libass 对样式名不敏感——`main` 引用 `Main` 样式
    # 曾整行丢字+误报 unknown_styles
    ass = ASS.replace("0,0:00:00.00,0:00:05.00,Main,,", "0,0:00:00.00,0:00:05.00,main,,")
    r = extract_usages(_write(tmp_path, ass))
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].inline_count == 0
    assert {"永", "和"} <= by["華康主體w5"].chars
    assert r.unknown_styles == ["Ghost"]
    # \\r 引用样式名同口径
    line = "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\ralt}天氣的中文字幕"
    r2 = extract_usages(_write(tmp_path, ASS.replace(
        "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字", line)))
    by2 = {u.norm_name: u for u in r2.usages}
    assert {"天", "氣"} <= by2["華康副體w7"].chars


def test_transform_nested_fn(tmp_path):
    # \\t(...) 动画内嵌 \\fn：此前 TransformTag 不下钻，该字体静默漏提取
    # （不建卡 → build_mapping 无此键 → rewrite 漏替）
    line = ("Dialogue: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,"
            "{\\t(0,1000,\\fn華康變換體)}動畫甲{\\t(\\fn華康變換體)}乙")
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    u = by["華康變換體"]
    assert u.inline_count == 2
    # 保守口径：整行文字归动画字体（动画进行时这些字以该字体渲染）
    assert {"動", "畫", "甲", "乙"} <= set(u.chars)
    # 顶层字体状态不被动画切换影响：后续文本仍归样式字体
    assert {"動", "畫", "甲", "乙"} <= set(by["華康主體w5"].chars)


def test_transform_fn0_no_phantom_bucket(tmp_path):
    # \\t(\\fn0) 是还原信号：不得产生 "0" 幽灵桶
    line = ("Dialogue: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,"
            "{\\t(0,1000,\\fn0)}動畫")
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    assert "0" not in by
    assert {"動", "畫"} <= set(by["華康主體w5"].chars)


def test_transform_fn_comment_reference_only(tmp_path):
    # Comment 行的 \\t(\\fn)：引用建桶但不计行内次数与字（与顶层 \\fn 同口径）
    line = ("Comment: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,"
            "{\\t(0,1000,\\fn華康註解換體)}註解")
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    assert "華康註解換體" in by
    assert by["華康註解換體"].inline_count == 0
    assert len(by["華康註解換體"].chars) == 0


def test_embedded_font_flagged(tmp_path):
    # [Fonts] 段 fontname: 行=内嵌字体：命中引用桶标 embedded=True，
    # 未引用的内嵌字体不建桶（无卡可标）；段外 "fontname:" 字样不算
    emb = ("\n[Fonts]\n"
           "fontname: 華康內嵌體W5.TTF\n"
           "AAAB3NzaC1kc3MAAA\n"
           "fontname: 無人引用.ttf\n"
           "BBB\n")
    line = "Dialogue: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,{\\fn華康內嵌體W5}嵌字"
    r = extract_usages(_write(tmp_path, ASS + line + "\n" + emb))
    by = {u.norm_name: u for u in r.usages}
    assert by["華康內嵌體w5"].embedded is True
    assert by["華康主體w5"].embedded is False
    assert by["華康內嵌體w5"].inline_count == 1   # 引用计数照常


def test_embedded_scan_section_gated(tmp_path):
    # 非 [Fonts] 段的 fontname: 字样不算内嵌（如 [Events] 正文）
    line = ("Dialogue: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,"
            "fontname: 華康主體W5.TTF")
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].embedded is False


def test_merge_embedded_or(tmp_path):
    # 跨文件合并：任一文件内嵌即 True（OR 口径）
    a = extract_usages(_write(tmp_path, ASS, name="a.ass"))
    b = extract_usages(_write(tmp_path, ASS, name="b.ass"))
    assert all(u.embedded is False for u in merge_usages([a, b]))
    emb = ("\n[Fonts]\nfontname: 華康主體W5.TTF\nAA\n")
    c = extract_usages(_write(tmp_path, ASS + emb, name="c.ass"))
    m = {u.norm_name: u for u in merge_usages([a, c])}
    assert m["華康主體w5"].embedded is True


def test_merge_embedded_or_order_independent(tmp_path):
    # 审查 P0：merge 首建桶分支曾丢 embedded——内嵌文件排前/单文件都丢
    emb = "\n[Fonts]\nfontname: 華康主體W5.TTF\nAA\n"
    c = extract_usages(_write(tmp_path, ASS + emb, name="c.ass"))
    a = extract_usages(_write(tmp_path, ASS, name="a.ass"))
    single = {u.norm_name: u for u in merge_usages([c])}    # 单文件
    assert single["華康主體w5"].embedded is True
    m = {u.norm_name: u for u in merge_usages([c, a])}          # 内嵌在前
    assert m["華康主體w5"].embedded is True


def test_embedded_extension_strip_known_only(tmp_path):
    # 只剥已知字体扩展：含点号版本名（"X 1.02"）不得被截成 "X 1"
    emb = "\n[Fonts]\nfontname: Version Font 1.02.ttf\nAA\n"
    line = "Dialogue: 0,0:00:12.00,0:00:14.00,Main,,0,0,0,,{\\fnVersion Font 1.02}嵌字"
    r = extract_usages(_write(tmp_path, ASS + line + "\n" + emb))
    by = {u.norm_name: u for u in r.usages}
    assert by["version font 1.02"].embedded is True
