from __future__ import annotations

# 中性高频字（简繁同形），合理性校验用
FREQ_NEUTRAL = frozenset(
    "的一是我人不他在有中大小上下山川日月金木水火土田石心手足口目耳"
    "好可何你我把來去出入同天地時中文字幕的一是在"
)

BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

TRY_ORDER = ("utf-8", "gb18030", "big5", "shift_jis", "euc-kr")


class EncodingDetectError(Exception):
    pass


def _plausible(text: str) -> bool:
    # 合理性必须有中性高频字证据：gb18030 对 big5/shift_jis 文本的硬解
    # 会产出全 CJK 的低频怪字（无 U+FFFD），故 CJK 占比本身不构成证据。
    if not text or "\ufffd" in text:
        return False
    return sum(c in FREQ_NEUTRAL for c in text) >= 5


def detect_encoding(data: bytes) -> str:
    for bom, codec in BOMS:
        if data.startswith(bom):
            return codec
    for codec in TRY_ORDER:
        try:
            text = data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
        if _plausible(text):
            return codec
    raise EncodingDetectError("无法识别编码（utf-8/gb18030/big5/shift_jis/euc-kr 均不合理）")
