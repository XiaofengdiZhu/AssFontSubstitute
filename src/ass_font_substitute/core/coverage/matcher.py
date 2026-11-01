from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from dataclasses import field

import opencc

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.core.models import FontUsage

_CC = {"t2s": opencc.OpenCC("t2s"), "s2t": opencc.OpenCC("s2t")}


def twin_convert(name: str, direction: str) -> str:
    """简繁方向名称转换：direction ∈ "t2s" / "s2t"。"""
    return _CC[direction].convert(name)


def _cov_irrelevant(c: str) -> bool:
    """不影响字形选择的字符（审查二轮 P2）：组合记号(Mn：含变体选择符
    FE0F)、格式控制(Cf：ZWJ/ZWNJ、BOM、方向标记等不可见字符)与空白
    ——cmap 查不到它们只虚低覆盖率，渲染器自会跳过/回退，不该算缺字
    （与渲染口径一致：宁多勿少的保守方向是缺字列表只增真字）。"""
    return c.isspace() or unicodedata.category(c) in ("Mn", "Cf")


def coverage_detail(cmap: CmapRanges, chars: set[str]) -> tuple[float, list[str]]:
    """覆盖率比例 + 缺字列表。不可见/空白字符不入分子分母（_cov_irrelevant），
    全部不可见时视为全覆盖。"""
    relevant = [c for c in chars if not _cov_irrelevant(c)]
    if not relevant:
        return 1.0, []
    missing = sorted(c for c in relevant if not cmap.contains(ord(c)))
    return (len(relevant) - len(missing)) / len(relevant), missing


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
        # （查找源含命中实体自身的全部别名——字幕常写 PostScript 名，转换要靠别名里的中文名）
        twin = _first_adequate_twin(lib, usage, threshold, direction,
                                    entities=entities)
        if twin is not None:
            t, tratio, tmissing = twin
            return UsageClass("twin", [t], tratio, tmissing, twin_hit=t)
        return UsageClass("replace", entities, ratio, missing)
    # 不在库 → 孪生检测
    twin = _first_adequate_twin(lib, usage, threshold, direction)
    if twin is not None:
        t, tratio, tmissing = twin
        return UsageClass("twin", [t], tratio, tmissing, twin_hit=t)
    return UsageClass("missing")


def _first_adequate_twin(lib: LibraryIndex, usage: FontUsage, threshold: float,
                         direction: str,
                         entities: list[FontEntity] | None = None,
                         ) -> tuple[FontEntity, float, list[str]] | None:
    """孪生查找名 = 字幕名转换优先，其后在库实体自身别名逐个转换。
    PostScript 名（纯 ASCII）OpenCC 转不动，但同实体别名常带中文名——
    如字幕写 DFHuaZongW5-B5，其别名 華康華綜體W5 转换后命中简版 华康华综体W5。
    entities 非空时排除这些自身指纹（别名原样查回自己不算孪生，自身覆盖
    必然同样不达标）。返回 (实体, 覆盖率, 缺字)——classify 侧免二次全扫。"""
    exclude = {e.fingerprint for e in entities} if entities else set()
    seen: set[str] = set()
    names = [usage.norm_name]
    for e in entities or []:
        names.extend(a for a in e.aliases)
    for name in names:
        converted = twin_convert(normalize_name(name), direction)
        if converted in seen:
            continue
        seen.add(converted)
        for t in lib.find_by_name(converted):
            if t.fingerprint in exclude:
                continue
            ratio, missing = coverage_detail(t.cmap, usage.chars)
            if ratio >= threshold:
                return t, ratio, missing
    return None
