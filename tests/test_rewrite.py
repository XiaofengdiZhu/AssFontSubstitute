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


def test_bomless_utf16_roundtrip(tmp_path):
    # 审查二轮 P2：BOM 缺失的 UTF-16 此前不被探测（纯 ASCII 内容被判
    # utf-8 解出 NUL 串静默 0 改写）。检出后按显式端序文本级整编，写回
    # 不补 BOM、端序不变
    for enc in ("utf-16-le", "utf-16-be"):
        p = tmp_path / f"in_{enc}.ass"
        p.write_bytes(ASS.encode(enc))
        out = tmp_path / f"out_{enc}.ass"
        rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
        data = out.read_bytes()
        assert not data.startswith((b"\xff\xfe", b"\xfe\xff"))
        text = data.decode(enc)
        assert "Style: Main,思源黑体,52" in text
        assert "{\\fn思源黑体}甲" in text
        assert "{\\fn華康主體W5X}不誤傷的中文字幕" in text   # 未改行原样


def test_unencodable_replacement_packaged(tmp_path):
    # P2：替换值含目标编码不可表示的字符（简体「华」写回 big5）时
    # UnicodeEncodeError 裸逃——折包 EncodingDetectError 对齐错误契约，
    # gui 兜底显示的报错可读。注意 _mk 的 big5 样本会被误判 gb18030
    # （gb 硬解恰 5 个高频字命中），此处用繁体高频台词样本确保检出 big5
    import pytest

    from ass_font_substitute.core.assparse.encoding import EncodingDetectError
    text = ("[Script Info]\r\n"
            "Title: t\r\n"
            "\r\n"
            "[V4+ Styles]\r\n"
            "Format: Name, Fontname, Fontsize\r\n"
            "Style: Main,華康主體W5,52\r\n"
            "\r\n"
            "[Events]\r\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\r\n"
            "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
            "這是繁體字幕的一句話，我們在這裡說話。\r\n")
    p = tmp_path / "in_big5.ass"
    p.write_bytes(text.encode("big5"))
    with pytest.raises(EncodingDetectError, match="无法写回"):
        rewrite_file(p, {"華康主體W5": "华文黑体"})


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


def test_style_trailing_space_mapping_key_still_replaces(tmp_path):
    # 审查二轮 P1：light_ass 不 strip Style 域，fontname 带尾随空格时
    # extract 按原样建桶（raw_name="華康主體W5 "），映射键随之带空格；
    # rewrite 取样式域先 strip，键不归一则静默漏替
    p = tmp_path / "in.ass"
    p.write_bytes(ASS.encode("utf-8-sig").replace(
        "Style: Main,華康主體W5,52".encode(),
        "Style: Main,華康主體W5 ,52".encode()))
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5 ": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "Style: Main,思源黑体 ,52" in text
    # 纯空格键剥空后丢弃：不产生零宽 alt 让 fn 正则全场误替
    rewrite_file(p, {"  ": "思源黑体"}, out=out)
    assert "華康主體W5" in out.read_text("utf-8-sig")


def test_trailing_space_key_also_hits_inline_fn(tmp_path):
    # 键 strip 归一与行内 \fn 路径自洽：键来自 Style 域带尾随空格，行内
    # \fn 名字两侧空白由正则组单独保留——剥空键不得破坏行内命中
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5 ": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "{\\fn思源黑体}甲" in text
    assert "{\\fn思源黑体}丙" in text
    assert "{\\fn華康主體W5X}" in text       # 前缀不误伤
    assert "Style: Main,思源黑体,52" in text
    assert len(r.changed_lines) == 3


def test_leading_space_mapping_key_still_replaces(tmp_path):
    # light_ass 不 strip Style 域：", 華康主體W5" 的前导空格同样进
    # raw_name/映射键；键 strip 归一后照常命中
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    rewrite_file(p, {" 華康主體W5": "思源黑体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "Style: Main,思源黑体,52" in text
    assert "{\\fn思源黑体}甲" in text


def test_at_key_with_trailing_space_still_expands_both_directions(tmp_path):
    # 键先 strip 再进 _expand_at：带尾随空格的 @ 键剥空后两个方向照常
    # 展开（@舊→@新 且 舊→新），展开不得重新引入空白
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
    rewrite_file(p, {"@舊字體 ": "@新字體"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: V,@新字體,52" in text
    assert "Style: H,新字體,52" in text
    assert "{\\fn@新字體}" in text


def test_colliding_keys_after_strip_latter_wins(tmp_path):
    # 同名键剥空后相撞按后者覆盖（与 dict 字面量语义一致）：现有调用方
    # build_mapping 的键是同一卡的 variants，剥空相撞时值必相同，无静默
    # 换值面；此测试仅钉死确定性语义防回归
    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "甲字体", "華康主體W5 ": "乙字体"}, out=out)
    text = out.read_text("utf-8-sig")
    assert "Style: Main,乙字体,52" in text
    assert "{\\fn乙字体}甲" in text


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


def test_transform_inner_fn_replaced(tmp_path):
    # \\t(...) 动画内嵌 \\fn：名字后随 ")" 时 lookahead 须容右括号，
    # 否则提得到改不掉（extract 已建卡、rewrite 漏替=静默漏替换）
    extra = ("Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
             "{\\t(0,1000,\\fn華康主體W5\\fscx110)}甲\r\n"
             "Dialogue: 0,0:00:05.00,0:00:06.00,Main,,0,0,0,,"
             "{\\t(\\fn華康主體W5)}乙\r\n")
    p = tmp_path / "in.ass"
    p.write_bytes((ASS + extra).encode("utf-8-sig"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "{\\t(0,1000,\\fn思源黑体\\fscx110)}甲" in text
    assert "{\\t(\\fn思源黑体)}乙" in text
    # 模板自带 3 行命中（Style Main + Dialogue + Comment）+ 新增 2 行
    assert len(r.changed_lines) == 5


def test_out_branch_atomic_and_no_tmp_leftover(tmp_path, monkeypatch):
    # 审查二轮 P0-2 顺带：另存分支原为 write_bytes 直写——盘满/崩溃截断
    # 目标文件。现走 tmp+os.replace；替换失败时 finally 清 tmp 残骸
    import contextlib
    import os

    import ass_font_substitute.core.assparse.rewrite as rw

    p = _mk(tmp_path)
    out = tmp_path / "out.ass"
    rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: Main,思源黑体,52" in text
    assert not list(tmp_path.glob("*.tmp*"))   # 成功路径无残骸

    # os.replace 失败（目标被占用等）：目标保持原样、tmp 被清理
    real_replace = os.replace

    def boom(a, b):
        raise OSError(32, "被占用")
    monkeypatch.setattr(rw.os, "replace", boom)
    out2 = tmp_path / "out2.ass"
    out2.write_text("旧内容", encoding="utf-8")
    with contextlib.suppress(OSError):
        rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out2)
    monkeypatch.setattr(rw.os, "replace", real_replace)
    assert out2.read_text(encoding="utf-8") == "旧内容"
    assert not list(tmp_path.glob("*.tmp*"))


NO_FORMAT_ASS = ("[Script Info]\r\n"
                 "Title: t\r\n"
                 "\r\n"
                 "[V4+ Styles]\r\n"
                 "Style: Main,華康主體W5,52\r\n"
                 "\r\n"
                 "[Events]\r\n"
                 "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\r\n"
                 "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,甲的中文字幕\r\n")


def test_style_without_format_line_still_rewritten(tmp_path):
    # 审查三轮 P1：styles 段缺 Format 行时 light_ass/extract 按
    # DEFAULT_STYLE_FORMAT 解析建桶（fontname 位 1），rewrite 曾静默
    # 跳过——同一文件「提得到、改不掉」
    p = tmp_path / "in.ass"
    p.write_bytes(NO_FORMAT_ASS.encode("utf-8-sig"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "Style: Main,思源黑体,52" in text
    assert r.changed_lines == [4]


def test_style_format_without_fontname_column_skipped(tmp_path):
    # Format 行存在但缺 fontname 列：extract 已判样式失效
    # （_missing_fontname_styles），rewrite 维持跳过口径一致
    ass = NO_FORMAT_ASS.replace(
        "Style: Main,華康主體W5,52\r\n",
        "Format: Name, Fontsize\r\nStyle: Main,52\r\n")
    p = tmp_path / "in.ass"
    p.write_bytes(ass.encode("utf-8-sig"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    assert r.changed_lines == []


def test_escaped_brace_body_text_untouched(tmp_path):
    # 审查三轮 P2：正文里的字面 \{\fnX\}（libass 视 \{ 为字面 {）不是
    # override 块，曾因块匹配正则缺 (?<!\\) 守卫被改写——用户可见文本
    # 被篡改。extract 侧同口径（_BLOCK_ESCAPED_PATTERN）本就不建桶
    line = ("Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
            "\\{\\fn華康主體W5\\}后的中文字幕\r\n")
    ass = ASS.replace(
        "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fn華康主體W5}甲{\\fn0}乙\r\n",
        line)
    p = tmp_path / "in.ass"
    p.write_bytes(ass.encode("utf-8-sig"))
    out = tmp_path / "out.ass"
    r = rewrite_file(p, {"華康主體W5": "思源黑体"}, out=out)
    text = out.read_text(encoding="utf-8-sig")
    assert "\\{\\fn華康主體W5\\}后的中文字幕" in text
    # Style 行 + Comment 行（真块 {\fn...}丙 照常改写）；换入的字面行不动
    assert r.changed_lines == [5, 11]
