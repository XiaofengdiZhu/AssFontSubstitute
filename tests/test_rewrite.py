from ass_font_substitute.core.assparse.rewrite import rewrite_file

# 注：末行 Dialogue 的文本在 brief 模板（"不誤傷"）基础上补了中性高频字"的中文字幕"，
# 否则该微型样例无法通过既有的编码合理性校验（gb18030 需 ≥5 个中性高频字命中）。
ASS = ("[Script Info]\r\n"
       "Title: t\r\n"
       "\r\n"
       "[V4+ Styles]\r\n"
       "Format: Name, Fontname, Fontsize\r\n"
       "Style: Main,華康主體W5,52\r\n"
       "Style: MainP,華康主體W5(P),52\r\n"
       "\r\n"
       "[Events]\r\n"
       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
       "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fn華康主體W5}甲{\\fn0}乙\r\n"
       "Comment: 0,0:00:05.00,0:00:06.00,Main,,0,0,0,,{\\fn華康主體W5}丙\r\n"
       "Dialogue: 0,0:00:06.00,0:00:08.00,Main,,0,0,0,,{\\fn華康主體W5X}不誤傷的中文字幕\r\n")


def _mk(tmp_path, enc="utf-8-sig"):
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode(enc))
    return p


def test_exact_column_replace_no_prefix_hit(tmp_path):
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: Main,思源黑体,52" in text
    assert "華康主體W5(P)" in text          # (P) 不误伤
    assert "{\\fn思源黑体}甲" in text        # 行内替换
    assert "{\\fn華康主體W5X}" in text       # 前缀不误伤
    assert "Style: MainP,華康主體W5(P),52" in text
    assert len(r.changed_lines) == 3          # 1 Style + Dialogue + Comment


def test_encoding_preserved(tmp_path):
    for enc in ("utf-16", "gb18030"):
        p = _mk(tmp_path, enc)
        out = tmp_path / f"out_{enc}.ass"
        rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
        data = out.read_bytes()
        assert data[:2] != b"\r\n"
        text = data.decode(enc)
        assert "Style: Main,思源黑体,52" in text
        assert "\r\n" in text


def test_at_variant(tmp_path):
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    p2 = tmp_path / "at.ass"
    p2.write_bytes(ASS.replace("Main,華康主體W5,", "Main,@華康主體W5,").encode("utf-8-sig"))
    rewrite_file(p2, {"華康主體W5": "思源黑体"}, out=out)
    assert "Main,@思源黑体," in out.read_text(encoding="utf-8-sig")


def test_at_key_symmetric_expand(tmp_path):
    # 键自带 @（extract 的 raw_name 以 @ 开头的场景）：@舊 → @新 且 舊 → 新，
    # 竖排标记不得丢；横排引用（无 @）也要被改写
    p = tmp_path / "v.ass"
    ass = ("[Script Info]\r\n"
           "Title: t\r\n"
           "\r\n"
           "[V4+ Styles]\r\n"
           "Format: Name, Fontname, Fontsize\r\n"
           "Style: V,@舊字體,52\r\n"
           "Style: H,舊字體,52\r\n"
           "\r\n"
           "[Events]\r\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
           "Dialogue: 0,0:00:00.00,0:00:05.00,V,,0,0,0,,{\\fn@舊字體}甲\r\n")
    p.write_bytes(ass.encode("utf-8-sig"))
    out = tmp_path / "out.ass"
    rewrite_file(p, {"@舊字體": "@新字體"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: V,@新字體,52" in text
    assert "Style: H,新字體,52" in text
    assert "{\\fn@新字體}" in text


def test_atomic_overwrite(tmp_path):
    p = _mk(tmp_path)
    before = p.read_bytes()
    rewrite_file(p, {"華康主體W5": "思源黑体"}, overwrite_src=True)
    text = p.read_text(encoding="utf-8-sig")
    assert "思源黑体" in text
    assert p.read_bytes() != before
    assert not list(tmp_path.glob("*.tmp*"))
