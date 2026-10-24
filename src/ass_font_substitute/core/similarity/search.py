from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.probes import PROBE_SC
from ass_font_substitute.core.fontindex.probes import PROBE_TC
from ass_font_substitute.core.models import Candidate
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import compare
from ass_font_substitute.core.similarity.render import render_bundle

MIN_SIM = 0.85              # 最低相似率（默认设置，GUI 可调）
SIZE_RANGE = (0.90, 1.10)   # 相对大小区间（默认设置，GUI 可调）
# 内置 v1：简繁同形字（spec §9 词表）+ 数字字母标点
SAMPLE_TEXT = ("永 中 木 水 火 山 田 石 目 心 手 白 的 我 你 他 它 好 行 生 "
               "同 尖 思 明 上 下 左 右 外 大 小 不 了 在 是 有 和 也 8 A a 0 ?")


def text_version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _probe_score(e: FontEntity, direction: str) -> float:
    probes = PROBE_SC if direction == "t2s" else PROBE_TC
    return e.cmap.hit_ratio(probes)


def rank_pool(target: FontEntity | None, pool: list[FontEntity],
              direction: str) -> list[FontEntity]:
    """元数据粗排：探针命中率 + 分类匹配 + weight/width 差；target=None 仅探针。"""
    def meta_score(e: FontEntity) -> float:
        s = _probe_score(e, direction)
        if target is not None:
            if e.category == target.category:
                s += 1.0
            s -= abs(e.weight - target.weight) / 1000
            s -= abs(e.width - target.width) / 100
        return s
    return sorted(pool, key=meta_score, reverse=True)


class FontSearcher:
    """逐候选严格门槛搜索：候选须连过 相似率 / 相对大小 / 覆盖率 三道门槛
    才算命中并列出，任一不过即跳过（不再垫底展示）；攒够 n_pause 个命中即
    paused，续算置 paused=False；池耗尽置 exhausted（GUI 显「无更多相似字体」）。
    target 渲染失败置 target_error（区别于 exhausted，GUI 显「原字体无法渲染」；
    渲染条首次需要时解析一次，pair 全命中则永不解析）；
    target=None（库中没有）无暂停语义，一次搜穿整池返回覆盖率达标的元数据粗排列表。
    """

    def __init__(self, target: FontEntity | None, chars: set[str],
                 lib: LibraryIndex, cache: Cache, sample_text: str,
                 cov_threshold: float = COV_THRESHOLD, n_pause: int = 5,
                 min_sim: float = MIN_SIM,
                 size_range: tuple[float, float] = SIZE_RANGE,
                 direction: str = "t2s"):
        if size_range[0] > size_range[1]:
            raise ValueError("size_range 下限不得大于上限")
        self.target = target
        self.chars = chars
        self.lib = lib
        self.cache = cache
        self.sample_text = sample_text
        self.cov_threshold = cov_threshold
        self.n_pause = n_pause
        self.min_sim = min_sim
        self.size_range = size_range
        self.direction = direction
        self.paused = False
        self.exhausted = False
        self.hit_count = 0
        self.target_error = False
        self._tver = text_version(sample_text)
        self._tcache: dict[str, object] = {}
        pool = [e for e in lib.entities()
                if target is None or e.fingerprint != target.fingerprint]
        self._iter: Iterator[FontEntity] = iter(rank_pool(target, pool, direction))
        self._tgt = None
        self._tgt_resolved = False

    def _bundle(self, e: FontEntity):
        def loader():
            return render_bundle(
                _entity_path(e), e.face_index, self.sample_text)
        return self.cache.get_render(e.fingerprint, self._tver, loader)

    def _target(self):
        """目标渲染条首次需要时解析一次；失败置 target_error，不逐候选重试。"""
        if self._tgt_resolved:
            return self._tgt
        self._tgt_resolved = True
        try:
            self._tgt = self._bundle(self.target)
        except Exception:
            self._tgt = None
            self.target_error = True
        return self._tgt

    def next(self) -> Candidate | None:
        """推进到下一个「过全部门槛」的候选；None = 已暂停或耗尽（看 exhausted）。"""
        if self.paused:
            return None
        while True:
            e = next(self._iter, None)
            if e is None:
                self.exhausted = True
                return None
            cand = self._evaluate(e)
            if cand is not None:
                return cand

    def _evaluate(self, e: FontEntity) -> Candidate | None:
        # 门槛按成本升序（实测：pair 缓存读取 ~5µs、coverage ~15µs、渲染+评分 ~57ms）：
        # 缓存命中 → 相似率/大小（纯比较）→ 覆盖率 → 未命中才渲染评分。
        # 命中路径全程零渲染。
        if self.target is None:
            # 无 target（库中没有的字体找替身）：无相似度可评，元数据粗排 +
            # 覆盖率严格门槛
            cov, _missing = coverage_detail(e.cmap, self.chars)
            if cov < self.cov_threshold:
                return None
            return Candidate(fingerprint=e.fingerprint, display_name=e.display_name,
                             coverage=cov, script=e.script, category=e.category,
                             weight=e.weight, source="recommend")
        if self.target_error:
            return None             # 目标渲染失败：不逐候选重试，GUI 显「原字体无法渲染」
        r = self.cache.get_pair(self.target.fingerprint, e.fingerprint, self._tver)
        cov: float | None = None
        if r is None:
            cov, _missing = coverage_detail(e.cmap, self.chars)
            if cov < self.cov_threshold:
                return None
            try:
                tb = self._target()
                if tb is None:
                    return None     # 目标渲染失败：后续候选在顶部短路，不重试
                r = compare(tb, self._bundle(e))
            except Exception:
                # spec §11：候选渲染失败无法证明达标 → 跳过，不中断搜索
                return None
            if r is not None:
                self.cache.put_pair(self.target.fingerprint, e.fingerprint,
                                    self._tver, r)
        if r is None:
            return None             # 交集为空，无法比较 → 跳过
        if r.score < self.min_sim:
            return None
        if not (self.size_range[0] <= r.size_ratio <= self.size_range[1]):
            return None
        if cov is None:
            cov, _missing = coverage_detail(e.cmap, self.chars)
            if cov < self.cov_threshold:
                return None
        cand = Candidate(fingerprint=e.fingerprint, display_name=e.display_name,
                         coverage=cov, script=e.script, category=e.category,
                         weight=e.weight, source="recommend")
        cand.similarity, cand.size_ratio, cand.n_chars = \
            r.score, r.size_ratio, r.n_chars
        self.hit_count += 1
        if self.hit_count >= self.n_pause:
            self.paused = True
        return cand


def _entity_path(e: FontEntity) -> Path:
    return Path(e.dir_path) / e.rel_path
