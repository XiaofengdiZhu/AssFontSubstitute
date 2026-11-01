import pytest

from ass_font_substitute.core.assparse.encoding import detect_encoding

SAMPLE = "[Script Info]\nTitle: 測試字幕\n; 繁體中文的一句話，包含常用字。\n\n[V4+ Styles]\n"


def test_bom_utf8():
    assert detect_encoding(SAMPLE.encode("utf-8-sig")) == "utf-8-sig"


def test_bom_utf16le():
    assert detect_encoding(SAMPLE.encode("utf-16")) == "utf-16"


def test_plain_utf8():
    assert detect_encoding(SAMPLE.encode("utf-8")) == "utf-8"


def test_gb18030():
    assert detect_encoding(SAMPLE.encode("gb18030")) == "gb18030"


def test_pure_ascii_utf8():
    assert detect_encoding(b"[Script Info]\r\nTitle: hello\r\n") == "utf-8"


def test_bomless_utf16_le():
    assert detect_encoding(SAMPLE.encode("utf-16-le")) == "utf-16-le"


def test_bomless_utf16_be():
    assert detect_encoding(SAMPLE.encode("utf-16-be")) == "utf-16-be"


def test_bomless_utf16_pure_ascii_endian_kept():
    # 旧逻辑：纯 ASCII 内容的 BOM-less UTF-16 被 utf-8 整解（NUL 是合法
    # utf-8）直判 utf-8 → 解出一串 NUL 静默 0 提取。端序靠零字节奇偶判
    # ——判错会让改写回盘整流换端序（未改行字节也翻）
    raw = "[Script Info]\r\nTitle: hello\r\n"
    assert detect_encoding(raw.encode("utf-16-le")) == "utf-16-le"
    assert detect_encoding(raw.encode("utf-16-be")) == "utf-16-be"


def test_bomless_utf16_be_cjk_not_misread_as_le():
    # 端序验证：LE 硬解 BE 的 CJK 字节对高低换位，字符撒进杂块；高频字
    # 校验可被对调巧合命中（三千 CJK 字期望 ~5 命中）不足以单独判信，
    # 需 sane 字符占比门兜底
    text = "這是繁體字幕的一句話，我們在這裡說話。甲乙丙丁戊己庚辛壬癸。" * 40
    assert detect_encoding(text.encode("utf-16-be")) == "utf-16-be"


def test_bomless_utf16_freq_sparse_minimal_ass():
    # 审查 agent 反例：FREQ 稀少的真实小字幕——LE 解出 sane 通过但高频
    # 字合理性不足（仅 2 命中），此时候选是端序唯一解必须直接接受；若
    # 回落 legacy，utf-8 会把「你好世界」（常用汉字区两字节恰都 <0x80）
    # 整解成 NUL 串直判 utf-8 静默 0 改写
    text = ("[Script Info]\n"
            "Title: t\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize\n"
            "Style: Default,MS Gothic,60\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,"
            "你好{\\fnMS Gothic}世界\n")
    assert detect_encoding(text.encode("utf-16-le")) == "utf-16-le"
    assert detect_encoding(text.encode("utf-16-be")) == "utf-16-be"


@pytest.mark.parametrize("enc", ["utf-8", "gb18030", "big5", "shift_jis"])
def test_utf16_gate_never_fires_for_legacy(enc):
    # NUL 密度门不得误伤 legacy：合法 legacy 文本 0x00 恒非法，猜测恒空
    from ass_font_substitute.core.assparse.encoding import _utf16_hypotheses
    assert _utf16_hypotheses(SAMPLE.encode(enc)[:65536]) == ()


def test_nul_density_raise_when_neither_endian_sane():
    # hyp 非空（NUL 密度过门）但两端序 sane 全空 → 显式报错，不得回落
    # legacy（BOM 缺失 UTF-32 的 ASCII/CJK 也在此封死：旧码静默误判
    # utf-8 解出 NUL 串）
    from ass_font_substitute.core.assparse.encoding import EncodingDetectError
    with pytest.raises(EncodingDetectError):
        detect_encoding("hello world\r\n".encode("utf-32-le") * 100)


def test_big5_beats_gb18030_misread():
    # 繁体高频文本：big5 解码命中高频字，gb18030 硬解必产生低频怪字
    big5_text = "這是繁體字幕的一句話，我們在這裡說話。" * 3
    assert detect_encoding(big5_text.encode("big5")) == "big5"


def test_failure():
    with pytest.raises(Exception):
        detect_encoding(bytes(range(0x80, 0xC0)) * 64)
