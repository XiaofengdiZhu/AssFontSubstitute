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


def test_missing_fontname_column_no_phantom_arial(tmp_path):
    # 审查二轮 P2：Style 行列数不足到 fontname 位时 light_ass 回退默认
    # fontname 'Arial'——幻影桶吞正文用字（覆盖率决策错位）。缺列样式
    # 判失效入 invalid_styles：不建桶、正文不误归 Arial、不误报 unknown。
    # （列数够但整体左移的畸形行 fontname 位有值，light_ass 无法识别，
    # 不属本修）
    bad = ASS.replace("Style: Main,華康主體W5,52,&H00FFFFFF", "Style: Main")
    r = extract_usages(_write(tmp_path, bad))
    by = {u.norm_name: u for u in r.usages}
    assert "arial" not in by
    assert "Main" in r.invalid_styles
    assert not any("永" in u.chars for u in r.usages)   # 正文不误归他桶
    assert r.unknown_styles == ["Ghost"]                # 失效≠未知且不误伤


def test_format_without_fontname_all_styles_invalid(tmp_path):
    # Format 行整体缺 fontname 列：全部样式判失效。样式值须能按位移后
    # 的格式解析（fontsize 位给数字），否则行在 light_ass 侧解析即炸、
    # 走降级剔除路径（样式直接丢失，非 Arial 回退）
    bad = ASS.replace("Format: Name, Fontname, Fontsize, PrimaryColour",
                      "Format: Name, Fontsize, PrimaryColour")
    bad = bad.replace("Style: Main,華康主體W5,52,&H00FFFFFF",
                      "Style: Main,52,&H00FFFFFF")
    bad = bad.replace("Style: Alt,華康副體W7,48,&H00FFFFFF",
                      "Style: Alt,48,&H00FFFFFF")
    r = extract_usages(_write(tmp_path, bad))
    by = {u.norm_name: u for u in r.usages}
    assert "arial" not in by
    assert set(r.invalid_styles) >= {"Main", "Alt"}
    assert not any("永" in u.chars for u in r.usages)


def test_broken_style_casefold_reference_not_unknown(tmp_path):
    # 审查补刀：事件引用失效样式常带大小写漂移（VSFilter 不敏感匹配），
    # 失效判定须同口径——broken 'Main'、引用 'main' 不进 unknown
    bad = ASS.replace("Style: Main,華康主體W5,52,&H00FFFFFF", "Style: Main")
    bad = bad.replace("Dialogue: 0,0:00:00.00,0:00:05.00,Main,,",
                      "Dialogue: 0,0:00:00.00,0:00:05.00,main,,")
    r = extract_usages(_write(tmp_path, bad))
    assert "main" not in r.unknown_styles
    assert "Main" in r.invalid_styles
    assert "arial" not in {u.norm_name for u in r.usages}


def test_style_line_before_format_line_no_phantom_arial(tmp_path):
    # 审查补刀：Style 行先于 Format 行时 light_ass 回落默认 Format
    # （fontname 位 1）——扫描同口径，缺列照样判失效
    text = ("[Script Info]\nTitle: t\n\n[V4+ Styles]\n"
            "Style: Main\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour\n"
            "Style: Alt,華康副體W7,48,&H00FFFFFF\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字\n")
    r = extract_usages(_write(tmp_path, text))
    assert "arial" not in {u.norm_name for u in r.usages}
    assert "Main" in r.invalid_styles


def test_degraded_style_lines_not_judged_broken(tmp_path):
    # N4 钉死：缺列扫描改扫降级后文本——被剔除的样式行不再冤枉入
    # invalid（诚实报 unknown），存活的 Arial 回退行照判失效
    text = ("[Script Info]\nTitle: t\n\n[V4+ Styles]\n"
            "Format: Name, Fontsize, PrimaryColour\n"
            "Style: Main,華康主體W5,52,&H00FFFFFF\n"   # 位移解析炸 → 剔除
            "Style: Alt,48,&H00FFFFFF\n"               # 可解析 → Arial 回退
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,永和的中文字\n")
    r = extract_usages(_write(tmp_path, text))
    assert len(r.skipped_lines) == 1
    assert "arial" not in {u.norm_name for u in r.usages}
    assert "Alt" in r.invalid_styles
    assert "Main" in r.unknown_styles


def test_comment_unknown_style_not_reported(tmp_path):
    # 审查二轮 P2：Comment 行不渲染，引用未知样式不构成误报信号
    line = ("Comment: 0,0:00:11.00,0:00:12.00,GhostNote,,0,0,0,,註解\n")
    r = extract_usages(_write(tmp_path, ASS + line))
    assert "GhostNote" not in r.unknown_styles


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


def test_fn_paren_name_with_suffix_recovered(tmp_path):
    # 审查二轮 P1：light_ass 把 \fn名字(...) 截到 ")"，其后到下一个 \
    # 或 } 的残余变成 CommentNode——libass/rewrite 口径下这些字属于字体
    # 名，须吞并恢复全名；否则提取 Foo(Bar) 幽灵卡、真名 Foo(Bar)X 永不可替
    line = "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fnFoo(Bar)X}甲"
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    assert by["foo(bar)x"].raw_name == "Foo(Bar)X"
    assert by["foo(bar)x"].inline_count == 1
    assert {"甲"} <= by["foo(bar)x"].chars
    assert "foo(bar)" not in by          # 截断幽灵卡不得建桶
    # 残余跨到下一个标签为止：\fnFoo(Bar)X\i1 → 全名仍是 Foo(Bar)X
    line2 = "Dialogue: 0,0:00:01.00,0:00:02.00,Main,,0,0,0,,{\\fnFoo(Bar)X\\i1}乙"
    r2 = extract_usages(_write(tmp_path, ASS + line2 + "\n"))
    by2 = {u.norm_name: u for u in r2.usages}
    assert "foo(bar)x" in by2
    # 无残余（} 直接收尾）不误吞：仍是 Foo(Bar)
    line3 = "Dialogue: 0,0:00:02.00,0:00:03.00,Main,,0,0,0,,{\\fnFoo(Bar)}丙"
    r3 = extract_usages(_write(tmp_path, ASS + line3 + "\n"))
    by3 = {u.norm_name: u for u in r3.usages}
    assert "foo(bar)" in by3 and "foo(bar)x" not in by3


def test_fn_paren_tail_no_overswallow(tmp_path):
    # 吞并只限同一 {} 块内：跨块边界的残余（相邻块的 CommentNode 文本、
    # 块内 \h 硬空格 RawTag）不得误吞进名字——libass 名字止于 } 或 \
    line = "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fnFoo(Bar)}{X}甲"
    r = extract_usages(_write(tmp_path, ASS + line + "\n"))
    by = {u.norm_name: u for u in r.usages}
    assert "foo(bar)" in by and "foo(bar)x" not in by
    assert {"甲"} <= by["foo(bar)"].chars   # 跨块的 X 块后正文照常计字
    # 块内残余止于 \h（未知标签回退 RawTag，无 _raw）：名字不含 \hY
    line2 = "Dialogue: 0,0:00:01.00,0:00:02.00,Main,,0,0,0,,{\\fnFoo(Bar)X\\hY}乙"
    r2 = extract_usages(_write(tmp_path, ASS + line2 + "\n"))
    by2 = {u.norm_name: u for u in r2.usages}
    assert "foo(bar)x" in by2 and "foo(bar)x\\hy" not in by2
    # 残余带尾随空格：normalize strip 后与无空格形态同桶（rewrite 键 strip
    # 口径闭环，正则侧 ([ \t]*) 保留空格命中）
    line3 = "Dialogue: 0,0:00:02.00,0:00:03.00,Main,,0,0,0,,{\\fnFoo(Bar)X }丙"
    r3 = extract_usages(_write(tmp_path, ASS + line3 + "\n"))
    by3 = {u.norm_name: u for u in r3.usages}
    assert "foo(bar)x" in by3
    assert "Foo(Bar)X " in by3["foo(bar)x"].variants


def test_fn_paren_suffix_extract_rewrite_roundtrip(tmp_path):
    # 端到端：提取全名 → 映射 → rewrite 行内替换命中（与 rewrite 键 strip
    # 口径闭环），幽灵名不进映射
    line = "Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,{\\fnFoo(Bar)X}甲"
    p = _write(tmp_path, ASS + line + "\n")
    r = extract_usages(p)
    mapping = {u.raw_name: "新字體" for u in r.usages}
    assert "Foo(Bar)X" in mapping and "Foo(Bar)" not in mapping
    from ass_font_substitute.core.assparse.rewrite import rewrite_file
    out = tmp_path / "out.ass"
    rewrite_file(p, mapping, out=out)
    assert "{\\fn新字體}甲" in out.read_text("utf-8-sig")


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


def test_bad_line_degraded_not_whole_file_lost(tmp_path):
    # 审查二轮 P1：单条畸形行（缺 10 域/坏时间）曾让 Document.load 整文件
    # 报废——降级剔除坏行重载，其余行照常提取，skipped_lines 记行号
    bad = "Dialogue: 0,xx:yy,0:00:05.00,Main,,0,0,0,,正文的中文字幕"
    p = _write(tmp_path, ASS + bad + "\n")
    r = extract_usages(p)
    assert r.parse_error is None
    assert len(r.skipped_lines) == 1
    assert "第 16 行" in r.skipped_lines[0]      # bad 行在夹具末尾的行号
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].style_count == 1     # 好行完整保留
    assert r.unknown_styles == ["Ghost"]
    # 多条坏行：循环剔除
    bad2 = "Dialogue: 0,0:00:00.00"
    r2 = extract_usages(_write(tmp_path, ASS + bad + "\n" + bad2 + "\n"))
    assert r2.parse_error is None
    assert len(r2.skipped_lines) == 2
    assert {u.norm_name for u in r2.usages} >= {"華康主體w5"}


def test_bad_line_degraded_crlf_utf16be_and_style_family(tmp_path):
    # 降级路径口径：CRLF 换行下行号与文本行一致；Style 家族失败形态
    # （空样式名）同样降级；utf-16-be BOM 走 _bom_and_codec 手工剥 BOM +
    # 显式端序正文解码，与旧 load(ALIAS_BOM) 路径结果一致
    bad = "Style: ,Arial,32"                     # 空样式名（Style 域失败家族）
    lines = ASS.split("\n")                      # 末尾换行 → 尾随空串
    text = "\r\n".join([*lines[:7], bad, *lines[7:]]) + "\r\n"
    p = tmp_path / "crlf.ass"
    p.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    r = extract_usages(p)
    assert r.parse_error is None
    assert r.skipped_lines == [f"第 8 行无法解析已跳过：{bad}"]
    assert {u.norm_name for u in r.usages} >= {"華康主體w5"}

    p2 = tmp_path / "be.ass"
    p2.write_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
    r2 = extract_usages(p2)
    assert r2.parse_error is None
    assert r2.skipped_lines == r.skipped_lines
    assert {u.norm_name for u in r2.usages} == {u.norm_name for u in r.usages}


def test_over_degrade_cap_falls_back_to_parse_error(tmp_path):
    # 坏行超过 _MAX_DEGRADE_REMOVALS：视为文件整体损坏，回退原 parse_error
    p = _write(tmp_path, ASS + "".join(f"Dialogue: 0,bad{i}\n"
                                       for i in range(60)))
    r = extract_usages(p)
    assert r.parse_error is not None and not r.usages


def _count_full_parses(monkeypatch):
    """替身统计整文件 Document.from_string 调用次数（逐行探针走
    Events/Styles/ScriptInfo.from_lines，不计入）。"""
    import ass_font_substitute.core.assparse.extract as ex
    real = ex.Document.from_string
    calls: list[int] = []

    def fake_from_string(text, strict=True, **kw):
        calls.append(1)
        return real(text, strict=strict, **kw)

    monkeypatch.setattr(ex, "Document", type("DocSpy", (),
                                             {"from_string": staticmethod(
                                                 fake_from_string)}))
    return calls


def test_many_bad_lines_probed_once_not_bisect(monkeypatch, tmp_path):
    # 审查三轮 P1 回归：坏行行级局部 → 逐行探针预筛 O(n)，50 条坏行只许
    # 2 次整文件解析（主路径失败 1 + 剔除复核 1）。旧二分 = 每条坏行
    # ~log2(n) 次全量解析，17.5MB 实测 312s；若本用例回退二分兜底，
    # 调用次数立刻爆表报红
    body = [f"Dialogue: 0,0:00:{i % 60:02d}.00,0:00:{(i % 60) + 1:02d}.00,"
            f"Main,,0,0,0,,第{i}句中文字幕" for i in range(5000)]
    for i in range(0, 5000, 100):            # 50 条坏行均匀散布
        body[i] = f"Dialogue: 0,bad{i}"
    text = ASS + "\n".join(body) + "\n"
    calls = _count_full_parses(monkeypatch)
    r = extract_usages(_write(tmp_path, text))
    assert r.parse_error is None
    assert len(r.skipped_lines) == 50
    assert len(calls) == 2, f"全量解析 {len(calls)} 次，快路径失效"
    by = {u.norm_name: u for u in r.usages}
    assert by["華康主體w5"].style_count == 1  # 好行照常提取


def test_probe_covers_info_style_and_custom_format(monkeypatch, tmp_path):
    # 探针三节覆盖：Script Info 坏键值（Timer 非数字，parse_int 系宽容
    # 回 0 不可用）、Style 空名、非默认 Format 节（同名 [Events] 合并后
    # 探针须带新 Format）的坏行——任一漏探都会落二分兜底，调用数爆表
    text = (ASS
            .replace("Title: t", "Timer: x")
            .replace("[Events]\n", "Style: ,Arial,32\n[Events]\n")  # 空样式名
            + "[Events]\n"
            + "Format: Start, End, Style, Name, Effect, Text\n"
            + "Dialogue: xx:yy,0:00:05.00,Main,,0,{\\fn華康行內體}字\n")
    calls = _count_full_parses(monkeypatch)
    r = extract_usages(_write(tmp_path, text))
    assert r.parse_error is None
    assert len(r.skipped_lines) == 3
    assert len(calls) == 2, f"全量解析 {len(calls)} 次，快路径失效"
    # 好行与 Format 无关的引用照常提取
    assert "華康主體w5" in {u.norm_name for u in r.usages}


def test_probe_misfire_falls_back_to_bisect(monkeypatch, tmp_path):
    # 预筛非终判：探针漏报形态（此处以坏行伪装成探针通过的行为模拟——
    # monkeypatch 探针放行全部行）复核解析必失败，须回退二分兜底找回
    # 肇事行，不误判整文件报废
    import ass_font_substitute.core.assparse.extract as ex
    bad = "Dialogue: 0,xx:yy,0:00:05.00,Main,,0,0,0,,坏行不拖全文件"
    monkeypatch.setattr(ex, "_flag_culprit_lines", lambda lines: set())
    r = extract_usages(_write(tmp_path, ASS + bad + "\n"))
    assert r.parse_error is None
    assert len(r.skipped_lines) == 1
    assert "第 16 行" in r.skipped_lines[0]


def test_undecodable_tail_still_parse_error(tmp_path):
    # 降级的前提是行文本可解：>64KB 处非法序列 decode 失败 → 保持原错误
    p = tmp_path / "big.ass"
    body = ASS.encode("utf-8") + b"\x80" * 70000
    p.write_bytes(b"\xef\xbb\xbf" + body)
    r = extract_usages(p)
    assert r.parse_error is not None
    assert not r.usages


def test_splitline_only_separators_counted_as_content(tmp_path):
    # 审查二轮 P1：str.splitlines 独有断行符（U+2028/\v/\f/\x85/\x1c-\x1e/
    # U+2029）上 light_ass 会把一条 Dialogue 劈成两行，后半句静默不计数
    # → 覆盖率虚高放行缺字候选；rewrite 的 _LINE_RE 只认 \r\n|\r|\n。
    # 解析前归一剔除，分隔符两侧文字都计入用字集
    for cp in (0x2028, 0x0B, 0x0C, 0x85, 0x1C, 0x1D, 0x1E, 0x2029):
        line = ("Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
                f"甲{chr(cp)}乙的中文字幕")
        r = extract_usages(_write(tmp_path, ASS + line + "\n"))
        assert r.parse_error is None, hex(cp)
        by = {u.norm_name: u for u in r.usages}
        assert {"甲", "乙"} <= by["華康主體w5"].chars, hex(cp)


def test_splitline_separator_with_bad_line_keeps_line_numbers(tmp_path):
    # 归一须发生在喂 light_ass 之前（主/降级共用同一段归一后文本）：
    # 分隔符剔除不位移行号——降级报的"第 N 行"仍按 \r\n|\r|\n 口径对原始
    # 文件行数；若降级收到未归一文本，splitlines 会把分隔符行劈成两行，
    # 行号 +1 且好行后半句丢失
    sep = chr(0x2028)
    good = ("Dialogue: 0,0:00:00.00,0:00:05.00,Main,,0,0,0,,"
            f"甲{sep}乙的中文字幕")
    bad = "Dialogue: 0,xx:yy,0:00:05.00,Main,,0,0,0,,坏行不拖全文件"
    r = extract_usages(_write(tmp_path, ASS + good + "\n" + bad + "\n"))
    assert r.parse_error is None
    assert len(r.skipped_lines) == 1
    assert "第 17 行" in r.skipped_lines[0]     # good=16、bad=17（分隔符不占行）
    by = {u.norm_name: u for u in r.usages}
    assert {"甲", "乙"} <= by["華康主體w5"].chars  # 分隔符行两侧文字照常计字


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
