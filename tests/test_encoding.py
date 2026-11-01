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


def test_short_utf8_cjk_below_freq_threshold():
    # P2：合法 UTF-8 短文件（CJK 高频字 <5）曾被 _plausible 误拒。
    # utf-8 严格整解本身就是判据：legacy 双字节编码首字节 0x81+ 与
    # utf-8 序列结构冲突，2 字节/3 字节单位错位，64KB 全文干净解出
    # 几乎必为 utf-8（纯 ASCII 各候选同解，同在此收编）
    assert detect_encoding("你好世界".encode()) == "utf-8"
    assert detect_encoding("Style: 微軟\r\n".encode()) == "utf-8"
    assert detect_encoding(b"[Script Info]\r\n") == "utf-8"


def test_big5_beats_gb18030_misread():
    # 繁体高频文本：big5 解码命中高频字，gb18030 硬解必产生低频怪字
    big5_text = "這是繁體字幕的一句話，我們在這裡說話。" * 3
    assert detect_encoding(big5_text.encode("big5")) == "big5"


def _wide_vocab_legacy_sample(enc: str) -> bytes:
    # 宽词汇自然文本（词汇多样性大 → gb18030 硬解乱码域扩散、中性高频
    # 命中随长度涨过 ≥5 绝对阈：旧码 ≥3KB 即 10/10 误判 gb18030，
    # 20KB 实测 20 命中）——密度选优的回归护栏
    import random
    pool = ("的一是了我們你他在這裡那個說話時間地方人們事情問題時候現在"
            "因為所以但是可以會很也都還是有沒有自己什麼怎樣如何多麼少些"
            "已經正在將要應該必須需要想要喜歡討厭知道覺得認為相信明白了"
            "解記得忘記開始結束繼續停止選擇決定改變發生發現創造建造製作"
            "使用操作學習工作生活休息睡覺起床吃飯喝水走路跑步開車坐車飛"
            "機火車汽車腳踏車房子房間門窗桌子椅子床鋪衣服褲子鞋子帽子書"
            "本紙張筆記電話電腦手機網路電視電影音樂歌曲遊戲運動健康疾病"
            "醫院藥品醫生護士學校老師學生同學朋友家人父母兄弟姊妹兒子女")
    random.seed(42)
    body = "\r\n".join(
        "Dialogue: 0,0:00:01.00,0:00:05.00,字幕,編劇,0,0,0,,"
        + "".join(random.choice(pool) for _ in range(25))
        for _ in range(200))
    return ("[Script Info]\r\n\r\n" + body).encode(enc, errors="ignore")


def test_big5_long_file_not_misjudged_gb18030():
    data = _wide_vocab_legacy_sample("big5")
    assert len(data) > 16 * 1024
    assert detect_encoding(data) == "big5"


def test_gb18030_long_file_wins_density_race():
    # 真 gb 长文件且 big5 乱码候选也过 _plausible 的密度竞速：gb 流尾字
    # 节避开 Python big5 拒收区时可被 big5 整解，混入「gb 编码后 big5
    # 硬解恰落中性字」的字符（琌/辊，全 CJK 区扫描仅此两字）可把乱码
    # 候选喂过 ≥5 底线（实测 138 命中/2.4%）。密度选优必须仍判真 gb
    # （~24%，十倍间隔）——钉的是「big5 候选在场不翻盘」的 argmax 路径
    # （旧码 TRY_ORDER 首判 gb 同结果，本测试护新路径）
    def big5_safe(ch):
        b = ch.encode("gb18030")
        if len(b) != 2:
            return False
        try:
            return ord(b.decode("big5")) > 0x7F
        except UnicodeDecodeError:
            return False

    pool = [c for c in
            "的一是了我們你他在這裡那個說話時間地方人們事情問題時候現在"
            "因為所以但是可以會很也都還是有沒有自己什麼怎樣如何多麼少些"
            "已經正在將要應該必須需要想要喜歡討厭知道覺得認為相信明白了"
            "解記得忘記開始結束繼續停止選擇決定改變發生發現" if big5_safe(c)]
    neutral_via_big5 = []   # gb 编码后 big5 硬解恰落 FREQ_NEUTRAL 的字符
    for cp in range(0x4E00, 0x9FA6):
        ch = chr(cp)
        b = ch.encode("gb18030")
        if len(b) == 2:
            try:
                if b.decode("big5") in (
                        "的一是我人不他在有中大小上下山川日月金木水火土田"
                        "石心手足口目耳好可何你我把來去出入同天地時中文字"
                        "幕的一是在"):
                    neutral_via_big5.append(ch)
            except UnicodeDecodeError:
                pass
    assert neutral_via_big5 == ["琌", "辊"]

    import random
    mix = pool + neutral_via_big5
    random.seed(42)
    body = "\r\n".join(
        "Dialogue: 0,0:00:01.00,0:00:05.00,字幕,编者,0,0,0,,"
        + "".join(random.choice(mix) for _ in range(25)) for _ in range(200))
    data = ("[Script Info]\r\n\r\n" + body).encode("gb18030")
    assert len(data) > 16 * 1024
    assert detect_encoding(data) == "gb18030"


def test_shift_jis_long_file_not_misjudged_gb18030():
    # sjis 同病护栏：宽词汇假名文本 20KB，gb 硬解 0 命中（当前安全），
    # 钉住「gb 候选在场不翻盘」
    import random
    pool = ("のにはをたがでとしてれさもいますんとかどしばあこまるえんっ"
            "つじぇゆきおわらすてぃぼーよなひふかきくけこさしすせそたち"
            "つてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん")
    random.seed(7)
    body = "\r\n".join(
        "Dialogue: 0,0:00:01.00,0:00:05.00,字幕,作者,0,0,0,,"
        + "".join(random.choice(pool) for _ in range(25)) for _ in range(200))
    data = ("[Script Info]\r\n\r\n" + body).encode("shift_jis")
    assert detect_encoding(data) == "shift_jis"


def test_failure():
    with pytest.raises(Exception):
        detect_encoding(bytes(range(0x80, 0xC0)) * 64)
