from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterator, Optional

from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.fontindex.library import FontEntity, LibraryIndex
from ass_font_substitute.core.fontindex.probes import PROBE_SC, PROBE_TC
from ass_font_substitute.core.models import Candidate
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import compare
from ass_font_substitute.core.similarity.render import render_bundle

HIGH_SIM = 0.70
# 内置 v1：简繁同形字（spec §9 词表）+ 数字字母标点
SAMPLE_TEXT = ("永 中 木 水 火 山 田 石 目 心 手 白 的 我 你 他 它 好 行 生 "
               "同 尖 思 明 上 下 左 右 外 大 小 不 了 在 是 有 和 也 8 A a 0 ?")


def text_version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _probe_score(e: FontEntity, direction: str) -> float:
    probes = PROBE_SC if direction == "t2s" else PROBE_TC
    return e.cmap.hit_ratio(probes)


def rank_pool(target: Optional[FontEntity], pool: list[FontEntity],
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
    """可暂停续算的逐候选搜索：攒够 n_pause 个高相似即 paused，续算置 paused=False。"""

    def __init__(self, target: Optional[FontEntity], chars: set[str],
                 lib: LibraryIndex, cache: Cache, sample_text: str,
                 cov_threshold: float, n_pause: int = 5,
                 direction: str = "t2s"):
        self.target = target
        self.chars = chars
        self.lib = lib
        self.cache = cache
        self.sample_text = sample_text
        self.cov_threshold = cov_threshold
        self.n_pause = n_pause
        self.direction = direction
        self.paused = False
        self.high_sim_count = 0
        tver = text_version(sample_text)
        pool = [e for e in lib.entities()
                if target is None or e.fingerprint != target.fingerprint]
        pool = rank_pool(target, pool, direction)
        # 覆盖率 < 阈值的候选垫底（stable partition，不剔除）
        low = [e for e in pool if coverage_detail(e.cmap, chars)[0] < cov_threshold]
        high = [e for e in pool if e not in low]
        self._iter: Iterator[FontEntity] = iter(high + low)
        self._tver = tver
        self._tcache: dict[str, object] = {}

    def _bundle(self, e: FontEntity):
        def loader():
            return render_bundle(
                _entity_path(e), e.face_index, self.sample_text)
        return self.cache.get_render(e.fingerprint, self._tver, loader)

    def _target_bundle(self):
        if self.target is None:
            return None
        if "tgt" not in self._tcache:
            self._tcache["tgt"] = self._bundle(self.target)
        return self._tcache["tgt"]

    def next(self) -> Optional[Candidate]:
        """推进一个候选；None = 已暂停或搜索耗尽。"""
        if self.paused:
            return None
        e = next(self._iter, None)
        if e is None:
            return None
        cov, _missing = coverage_detail(e.cmap, self.chars)
        cand = Candidate(fingerprint=e.fingerprint, display_name=e.display_name,
                         coverage=cov, script=e.script, category=e.category,
                         weight=e.weight, source="recommend")
        tb = self._target_bundle()
        if tb is not None:
            cached = self.cache.get_pair(self.target.fingerprint, e.fingerprint,
                                         self._tver)
            r = cached if cached else compare(tb, self._bundle(e))
            if not cached and r is not None:
                self.cache.put_pair(self.target.fingerprint, e.fingerprint,
                                    self._tver, r)
            if r is not None:
                cand.similarity, cand.size_ratio, cand.n_chars = \
                    r.score, r.size_ratio, r.n_chars
                if r.score >= HIGH_SIM:
                    self.high_sim_count += 1
                    if self.high_sim_count >= self.n_pause:
                        self.paused = True
        return cand


def _entity_path(e: FontEntity) -> Path:
    return Path(e.dir_path) / e.rel_path
