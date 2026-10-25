from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass
from dataclasses import field


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

    def to_bytes(self) -> bytes:
        n = len(self.starts)
        return struct.pack(f"<I{n * 2}I", n, *sum(zip(self.starts, self.ends, strict=False), ()))

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
            cps.update(t.cmap.keys())
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


@dataclass
class FontUsage:
    raw_name: str
    norm_name: str
    style_count: int
    inline_count: int
    chars: set[str] = field(default_factory=set)
    variants: set[str] = field(default_factory=set)   # 同桶全部原始拼写（写回映射键全集）


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
