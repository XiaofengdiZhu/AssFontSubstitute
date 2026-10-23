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


def test_big5_beats_gb18030_misread():
    # 繁体高频文本：big5 解码命中高频字，gb18030 硬解必产生低频怪字
    big5_text = "這是繁體字幕的一句話，我們在這裡說話。" * 3
    assert detect_encoding(big5_text.encode("big5")) == "big5"


def test_failure():
    with pytest.raises(Exception):
        detect_encoding(bytes(range(0x80, 0xC0)) * 64)
