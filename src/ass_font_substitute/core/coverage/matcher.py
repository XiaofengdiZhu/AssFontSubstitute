from __future__ import annotations

from dataclasses import dataclass, field

import opencc

from ass_font_substitute.core.fontindex.library import FontEntity, LibraryIndex
from ass_font_substitute.core.models import CmapRanges, FontUsage

_CC = {"t2s": opencc.OpenCC("t2s"), "s2t": opencc.OpenCC("s2t")}


def twin_convert(name: str, direction: str) -> str:
    """简繁方向名称转换：direction ∈ "t2s" / "s2t"。"""
    return _CC[direction].convert(name)


def coverage_detail(cmap: CmapRanges, chars: set[str]) -> tuple[float, list[str]]:
    """覆盖率比例 + 缺字列表。"""
    if not chars:
        return 1.0, []
    missing = sorted(c for c in chars if not cmap.contains(ord(c)))
    return (len(chars) - len(missing)) / len(chars), missing


def find_fonts(lib: LibraryIndex, usage: FontUsage) -> list[FontEntity]:
    return lib.find_by_name(usage.norm_name)


def twin_candidates(lib: LibraryIndex, usage: FontUsage,
                    direction: str = "t2s") -> list[FontEntity]:
    """名称不在库时，经简繁转换后在库中寻找孪生候选。"""
    return lib.find_by_name(twin_convert(usage.norm_name, direction))


@dataclass
class UsageClass:
    kind: str                 # keep / twin / replace / missing
    entities: list[FontEntity] = field(default_factory=list)
    coverage: float = 1.0
    missing_chars: list[str] = field(default_factory=list)
    twin_hit: FontEntity | None = None


COV_THRESHOLD = 0.95       # 覆盖率阈值（默认设置，GUI 可调，范围 90–100%）


def classify_usage(usage: FontUsage, lib: LibraryIndex, threshold: float = COV_THRESHOLD,
                   direction: str = "t2s") -> UsageClass:
    """用字分类：零用字 keep；在库按覆盖率 keep；覆盖不足先试孪生再 replace；不在库走孪生。"""
    if not usage.chars:
        return UsageClass(kind="keep")
    entities = find_fonts(lib, usage)
    if entities:
        covs = [coverage_detail(e.cmap, usage.chars) for e in entities]
        best = max(range(len(entities)), key=lambda i: covs[i][0])
        ratio, missing = covs[best]
        if ratio >= threshold:
            return UsageClass("keep", entities, ratio, missing)
        # 在库但覆盖不足（如繁体原名在库、简体版同库）：先跑孪生检测
        twin = _first_adequate_twin(lib, usage, threshold, direction)
        if twin is not None:
            tratio, tmissing = coverage_detail(twin.cmap, usage.chars)
            return UsageClass("twin", [twin], tratio, tmissing, twin_hit=twin)
        return UsageClass("replace", entities, ratio, missing)
    # 不在库 → 孪生检测
    twin = _first_adequate_twin(lib, usage, threshold, direction)
    if twin is not None:
        tratio, tmissing = coverage_detail(twin.cmap, usage.chars)
        return UsageClass("twin", [twin], tratio, tmissing, twin_hit=twin)
    return UsageClass("missing")


def _first_adequate_twin(lib: LibraryIndex, usage: FontUsage, threshold: float,
                         direction: str) -> FontEntity | None:
    for t in twin_candidates(lib, usage, direction):
        ratio, _ = coverage_detail(t.cmap, usage.chars)
        if ratio >= threshold:
            return t
    return None
