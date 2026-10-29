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


def test_bom_endian_roundtrip(tmp_path):
    # 终审 P0：BE 文件曾被恒写回 LE（"utf-16-be" 检出后 encode("utf-16")
    # 恒出 LE+BOM），字节级往返破坏。-be/-le 检出须按原端序写回，BOM 保留
    for bom, enc in ((b"\xfe\xff", "utf-16-be"),
                     (b"\xff\xfe\x00\x00", "utf-32-le"),
                     (b"\x00\x00\xfe\xff", "utf-32-be")):
        p = tmp_path / f"in_{enc}.ass"
        p.write_bytes(bom + ASS.encode(enc))
        out = tmp_path / f"out_{enc}.ass"
        r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
        data = out.read_bytes()
        assert data.startswith(bom)                 # BOM 原样保留、端序不变
        text = data.decode(enc)
        assert "Style: Main,思源黑体,52" in text
        assert "華康主體W5(P)" in text               # 前缀不误伤
        assert "{\\fn思源黑体}甲" in text
        assert len(r.changed_lines) == 3


def test_no_chain_substitution_when_values_overlap_keys(tmp_path):
    # 终审 🔴：映射值与键相交时逐项 sub 链式误替——
    # {華康主體W5→SimSun, SimSun→思源黑体} 曾把 {\fn華康主體W5} 先替成
    # SimSun 再被第二条吞成思源黑体
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "SimSun", "SimSun": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "{\\fnSimSun}甲" in text            # 一步到位，不再被第二条吞
    assert "Style: Main,SimSun,52" in text
    assert "{\\fn思源黑体}丙" not in text


def test_inline_fn_outside_block_untouched(tmp_path):
    # 替换作用域=花括号块内：正文里的字面 \fn 片段（不在块内）不动
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode("utf-8-sig").replace(
        "不誤傷的中文字幕".encode(), "正文 \\fn華康主體W5} 片段的中文字幕".encode()))
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "正文 \\fn華康主體W5} 片段" in text


def test_fn_with_surrounding_spaces_replaced(tmp_path):
    # 终审 P1：extract 把「\fn名字 」strip 后建桶而 libass trim 尾随空格，
    # 不宽容则提得到改不掉；替换后原样保留两侧空白
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode("utf-8-sig").replace(
        "{\\fn華康主體W5}甲".encode(), "{\\fn 華康主體W5 }甲".encode()))
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "{\\fn 思源黑体 }甲" in text


def test_lowercase_keywords_rewritten(tmp_path):
    # 终审 P1：行关键字大小写敏感曾让小写关键字的文件静默零改写
    # （VSFilter 对关键字不敏感）
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode("utf-8-sig").replace(b"[Script Info]", b"[script info]")
                  .replace(b"[V4+ Styles]", b"[v4+ styles]")
                  .replace(b"Style:", b"style:")
                  .replace(b"[Events]", b"[events]")
                  .replace(b"Format:", b"format:")
                  .replace(b"Dialogue:", b"dialogue:"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "style: Main,思源黑体,52" in text   # 关键字原样
    assert "{\\fn思源黑体}甲" in text
    assert len(r.changed_lines) == 3


def test_style_field_padding_preserved(tmp_path):
    # Style 域内对齐空格保留："Style: Main, 舊字體 ," 的排版空格不抹
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode("utf-8-sig").replace(
        "Style: Main,華康主體W5,52".encode(),
        "Style: Main, 華康主體W5 ,52".encode()))
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "Style: Main, 思源黑体 ,52" in text


def test_big5_dual_encoding_bytes_preserved(tmp_path):
    # 终审 P1：big5 双编码字（a2 40 与 a2 42 同解 ＼）decode→encode 曾
    # 回写规范码 a2 42——未改动行字节也被翻转。未改行锁原字节。
    # 正文用纯中性高频字驱动编码检出（gb18030 硬解 big5 字节也能凑够
    # 命中数，长杂文会被误判 gb18030——检出层固有歧义，另案）
    body = ("[Script Info]\r\n"
            "Title: t\r\n"
            "\r\n"
            "[V4+ Styles]\r\n"
            "Format: Name, Fontname\r\n"
            "Style: Main,FontX,52\r\n"
            "\r\n"
            "[Events]\r\n"
            "Format: Layer, Style, Text\r\n"
            "Dialogue: 0,Main,,0,0,0,,的的是是中中大\r\n")
    p = tmp_path / "in.ass"
    # 字节级拼装：a2 40 是非规范 big5（与 a2 42 同解 ＼），直书进 Title 行
    p.write_bytes(body.encode("big5").replace(b"Title: t\r\n",
                                              b"Title: t\xa2\x40\r\n"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"FontX": "思源黑体"}, out=out)
    data = out.read_bytes()
    assert b"\xa2\x40" in data                   # 未改行的非规范字节原样
    assert "思源黑体".encode("big5") in data     # 改动行正常替换
    assert data.decode("big5").count("思源黑体") == 1
    assert len(r.changed_lines) == 1


def test_at_variant(tmp_path):
    _mk(tmp_path)
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
