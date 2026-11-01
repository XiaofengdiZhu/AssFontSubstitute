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
    (b"\xfe\xff", "utf-16-be"),   # 端序显式：rewrite 写回需按原端序
)

# "-le"/"-be" 显式编解码器不剥/补 BOM（通用 "utf-16"/"utf-32" 才自管）。
# 需要通用 codec 名的调用方（pysubs2）经 ALIAS_BOM 归一。
ALIAS_BOM = {"utf-16-be": "utf-16", "utf-32-le": "utf-32", "utf-32-be": "utf-32"}

# 探测范围截断：MB 级文件逐候选编码全文解码+逐字符扫描曾秒级卡顿——
# 编码判定看分布，64KB 足够
_SNIFF_BYTES = 64 * 1024

TRY_ORDER = ("utf-8", "gb18030", "big5", "shift_jis")
# 不收 euc-kr：FREQ_NEUTRAL 无谚文，纯韩文 _plausible 永假=假支持；
# 中文字幕工具的编码面就到 shift_jis 为止


class EncodingDetectError(Exception):
    pass


def _is_cjk(c: str) -> bool:
    return 0x4E00 <= ord(c) <= 0x9FFF


def _plausible(text: str) -> bool:
    # 合理性必须有中性高频字证据：gb18030 对 big5/shift_jis 文本的硬解
    # 会产出全 CJK 的低频怪字（无 U+FFFD），故 CJK 占比本身不构成证据。
    if not text or "\ufffd" in text:
        return False
    return sum(c in FREQ_NEUTRAL for c in text) >= 5


def _try_decode(sample: bytes, codec: str) -> str | None:
    """sample 末尾可能截半多字节字符（截断点不看边界）：逐字节回退重试，
    最多 3 字节（utf-8 CJK/utf-16 半字/utf-32 半字）；中段非法序列仍
    strict 拒绝（合理性判定不因截断放水）。LookupError=未知 codec。"""
    for trim in range(4):
        s = sample[:len(sample) - trim] if trim else sample
        if not s:
            return None
        try:
            return s.decode(codec)
        except UnicodeDecodeError:
            continue
        except LookupError:
            return None
    return None


def _sane_char(c: str) -> bool:
    """字幕正文的合理码位段：ASCII 可打印/常用控制、General Punctuation
    （—— …… “”）、CJK（含假名/注音/CJK 统一表意及扩展 A）、兼容表意
    与全角形式。谚文音节/CJK 扩展 B 不收（与 TRY_ORDER 不收 euc-kr 同
    口径，重度使用的文件报错不误判）。"""
    o = ord(c)
    return (0x20 <= o <= 0x7E or o in (0x09, 0x0A, 0x0D)
            or 0x2000 <= o <= 0x206F
            or 0x3000 <= o <= 0x9FFF or 0xF900 <= o <= 0xFFEF)


def _utf16_hypotheses(sample: bytes) -> tuple[str, ...]:
    """BOM 缺失的 UTF-16 猜测（审查二轮 P2）：UTF-16 文本的 ASCII 骨架
    （标签/括号/换行）每字符贡献一个 0x00 字节，而 legacy 单/双字节编码
    的字幕几乎不含 0x00——NUL 密度过低不猜，不动 legacy 流程。端序按
    零字节奇偶占比排候选（LE 的 ASCII 零落奇位、BE 落偶位；纯 ASCII 时
    两个 codec 解出的文本相同，端序只能靠它保回盘字节不翻）。<16 字节
    样本不猜（真实 ASS 远大于此；极短 BOM-less UTF-16 仍走 legacy 误判
    路径，属有意取舍）。"""
    n = len(sample) // 2 * 2
    if n < 16:
        return ()
    body = sample[:n]
    # NUL 占比 < 1/64 不猜：legacy 单/双字节编码 0x00 恒非法（占比 0），
    # UTF-16 即使纯 CJK 行也有换行/句号（U+3002 等低位 0 字符）供零字节，
    # 典型 ≥ 3%；再稀疏的文件宁可报错也不误入 legacy 硬解
    if body.count(0) * 64 < n:
        return ()
    even = body[0::2].count(0)
    odd = body[1::2].count(0)
    return ("utf-16-le", "utf-16-be") if odd >= even \
        else ("utf-16-be", "utf-16-le")


def _utf16_sane(text: str) -> bool:
    """文本性验证（非端序判据——ASCII 主导时错端序解同样 sane）：把 NUL
    密度达标的二进制流（内嵌 UTF-16 字符串的可执行/资源文件）挡成显式
    报错，不误当字幕文本。真实字幕字符几乎全落 _sane_char 各段；随机
    字节对的 sane 占比 ~0.5，0.8 阈值分隔充分。"""
    if not text:
        return False
    ok = sum(1 for c in text if _sane_char(c))
    return ok / len(text) >= 0.8


def detect_encoding(data: bytes) -> str:
    for bom, codec in BOMS:
        if data.startswith(bom):
            return codec
    sample = data[:_SNIFF_BYTES]
    # BOM 缺失 UTF-16 先猜（_utf16_hypotheses 自带 NUL 密度门，不过门
    # 完全不影响 legacy 流程）：纯 ASCII 内容会被 utf-8 整解直判 utf-8，
    # 解出一串 NUL 静默 0 提取（审查二轮 P2）
    hyp = _utf16_hypotheses(sample)
    sane: list[tuple[str, str]] = []
    for codec in hyp:
        text = _try_decode(sample, codec)
        if text is not None and _utf16_sane(text):
            sane.append((codec, text))
    if len(sane) == 2 and sane[0][1] != sane[1][1]:
        # 双候选同 sane 且解文不同（正文过半由 U+x000 族生僻字构成、
        # 全程避开 0xD8-0xDF 尾字的对抗输入可让奇偶投票翻错——审查
        # agent 构造复现）：真实 ASS 必含 ASCII 冒号（Format:/时间戳），
        # 错端序把 : 对调成 U+3A00——含冒号的解优先，消灭该域
        hits = [i for i, (_c, t) in enumerate(sane) if ":" in t]
        if len(hits) == 1:
            sane.insert(0, sane.pop(hits[0]))
    if sane:
        # 端序判决=候选序（零字节奇偶投票）。两端序对纯 ASCII 解出同文，
        # ASCII 的错端序解（高位字节 <<8）恰落 CJK sane 段，sane 无从
        # 区分端序——投票是唯一通用判据（上述冒号补刀封对抗特例）；
        # 自然 CJK 主导时错端序解 sane 必不过（实测 unsane ≈46%）。
        # sane 门的作用是把 NUL 密度高的二进制流（内嵌 UTF-16 字符串的
        # 可执行/资源文件，随机字节对 sane 占比 ~0.5）挡成显式报错，
        # 而非落 legacy 硬解：utf-8 会把高位字节 0 的常用汉字区整解成
        # NUL 串静默 0 改写，gb18030 硬解可在 64KB 尺度凑出高频字骗过
        # 合理性门、误回 gb 重编码损坏全文（审查 agent 反例）
        return sane[0][0]
    if hyp:
        raise EncodingDetectError(
            "样本 NUL 密度呈 UTF-16 特征，但端序解码均不可判信")
    for codec in TRY_ORDER:
        text = _try_decode(sample, codec)
        if text is None:
            continue
        # 纯 ASCII（utf-8 全文解出且无 U+FFFD、无 CJK）：各候选解码结果相同，
        # 接受无歧义，直判 utf-8（否则纯 ASCII 字幕会被高频字校验误拒）。
        if (codec == "utf-8" and "\ufffd" not in text
                and not any(_is_cjk(c) for c in text)):
            return codec
        if _plausible(text):
            return codec
    raise EncodingDetectError("无法识别编码（utf-8/gb18030/big5/shift_jis 均不合理）")
