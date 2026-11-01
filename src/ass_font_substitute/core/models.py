from __future__ import annotations

import bisect
import contextlib
import struct
from dataclasses import dataclass
from dataclasses import field
from itertools import chain

# 键即 Unicode 码点的 cmap 子表：platform 0 全系、(3,0) symbol、
# (3,1) BMP、(3,10) UCS-4
CMAP_UNICODE_KEYS = frozenset({
    (0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 6),
    (3, 0), (3, 1), (3, 10),
})
# 键为双字节原生码的 legacy 子表：码点按高字节在前的整字经对应码页解
# 出 Unicode。老字库常见（文鼎/华康繁体 Big5、日文 Shift-JIS）——GDI
# 认这类表所以 Windows 能预览，键直取则是几万个伪码点（探针判「未标
# 注」+覆盖率全错的根因，实锤 文鼎細黑.ttf）
CMAP_LEGACY_CODECS = {
    (3, 2): "cp932",       # Shift-JIS
    (3, 3): "gbk",         # GB2312
    (3, 4): "cp950",       # Big5（+MS 扩展）
    (3, 5): "cp949",       # Wansung
    (3, 6): "johab",       # Johab
}


@dataclass(frozen=True)
class CmapRanges:
    starts: tuple[int, ...]
    ends: tuple[int, ...]  # 含端点

    def contains(self, cp: int) -> bool:
        i = bisect.bisect_right(self.starts, cp) - 1
        return i >= 0 and cp <= self.ends[i]

    def hit_ratio(self, chars) -> float:
        s = list(chars)
        if not s:
            return 0.0
        return sum(self.contains(ord(c)) for c in s) / len(s)

    def total_chars(self) -> int:
        """区间求和的总码点数（详情页字符覆盖用）。"""
        return sum(e - s + 1
                   for s, e in zip(self.starts, self.ends, strict=False))

    def to_bytes(self) -> bytes:
        # 不可用 sum(zip(...), ()) 拼元组——O(n²)，CJK 字体 4000+ 区间时
        # 单次 9ms，曾是索引写库 92% 耗时；chain 展平 O(n)
        n = len(self.starts)
        return struct.pack(
            f"<I{n * 2}I", n,
            *chain.from_iterable(zip(self.starts, self.ends, strict=False)))

    @classmethod
    def from_bytes(cls, b: bytes) -> CmapRanges:
        (n,) = struct.unpack("<I", b[:4])
        vals = struct.unpack(f"<{2 * n}I", b[4 : 4 + 8 * n])
        return cls(tuple(vals[0::2]), tuple(vals[1::2]))

    @classmethod
    def from_chars(cls, cps) -> CmapRanges:
        pts = sorted(set(cps))
        if not pts:
            return cls((), ())
        starts, ends = [pts[0]], [pts[0]]
        for cp in pts[1:]:
            if cp == ends[-1] + 1:
                ends[-1] = cp
            else:
                starts.append(cp)
                ends.append(cp)
        return cls(tuple(starts), tuple(ends))

    @classmethod
    def from_cmap_subtables(cls, tables) -> CmapRanges:
        cps: set[int] = set()
        for t in tables:
            key = (t.platformID, t.platEncID)
            codec = CMAP_LEGACY_CODECS.get(key)
            if codec is None and key not in CMAP_UNICODE_KEYS:
                # mac 平台等：键非 Unicode 码点，转不出来，跳过不污染
                continue
            for code in t.cmap:
                if codec is None:
                    cps.add(code)
                else:
                    # 单双字节都经码页解出：cp932 单字节 0xA1-0xDF 是半角
                    # 片假名（U+FF61 起），直取会错记成 U+00A1-U+00DF 拉丁
                    # 区；cp950/gbk ≥0x80 单字节解不出 → 丢弃（造字区同）；
                    # 0x00-0x1F 控制码解出来也只虚增 total_chars，滤掉
                    if code < 0x20:
                        continue
                    b = bytes([code]) if code < 0x100 else code.to_bytes(2, "big")
                    with contextlib.suppress(UnicodeDecodeError):
                        cps.add(ord(b.decode(codec)))
        return cls.from_chars(cps)


@dataclass
class FontRecord:
    dir_path: str
    rel_path: str
    face_index: int
    psname: str | None
    version: tuple[int, ...] | None
    aliases: list[str]
    weight: int
    width: int
    italic: bool
    cmap: CmapRanges
    script: str  # 简/繁/简繁/未标注
    category: str  # 黑/宋/圆/楷/仿/未分类
    display_name: str = ""  # 展示名（parser 按 name ID 官方优先选好入库）
    family_names: tuple[str, ...] = ()  # 字体系全语言名（ID16 缺视 ID1）
    # 展示名语言候选：{nameID: [(langID, 文本)]}，仅 Windows 平台 ID1/4/16
    # 记录（GUI 按界面语言链挑名用）
    name_langs: dict[int, list[tuple[int, str]]] = field(default_factory=dict)
    # 命名卡原始记录 (nameID, platformID, langID, 文本)，ID1/2/3/4/16
    # 全平台全语言（SCHEMA v8 入库，详情打开同步读零闪烁）
    name_records: tuple[tuple[int, int, int, str], ...] = ()
    # fvar 命名实例序：0=默认实例/静态，1..N=命名实例（fvar 声明序；
    # 跳过的实例留空洞，与 face_index 允许空洞先例一致）
    inst_index: int = 0
    # fvar 轴坐标（axes 声明序全长）；空=默认态（静态字体/默认实例行）
    var_axes: tuple[float, ...] = ()


@dataclass
class FontUsage:
    raw_name: str
    norm_name: str
    style_count: int
    inline_count: int
    chars: set[str] = field(default_factory=set)
    variants: set[str] = field(default_factory=set)   # 同桶全部原始拼写（写回映射键全集）
    embedded: bool = False                            # 该字体已内嵌进字幕 [Fonts] 段


@dataclass
class Candidate:
    fingerprint: str
    display_name: str
    coverage: float
    similarity: float | None = None
    size_ratio: float | None = None
    n_chars: int | None = None
    script: str = "未标注"
    category: str = "未分类"
    weight: int = 400
    source: str = "recommend"  # recommend/last/twin/pick
