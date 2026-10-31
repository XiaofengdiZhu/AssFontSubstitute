from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.probes import PROBE_SC
from ass_font_substitute.core.fontindex.probes import PROBE_TC
from ass_font_substitute.core.models import Candidate
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.cache import _encode as cache_encode
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.metrics import compare
from ass_font_substitute.core.similarity.render import render_bundle

MIN_SIM = 0.85              # 最低相似率（默认设置，GUI 可调）
SIZE_RANGE = (0.90, 1.10)   # 相对大小区间（默认设置，GUI 可调）
EVAL_SLICE = 24             # next_slice 单片对比数上限（缓存命中时对比极快，
SLICE_SECONDS = 0.5         # 另按时长切：两上限先到为准，回执节奏 ≤0.5s
RENDER_CHUNK = 8            # 池模式每任务（chunk）实体数：IPC 粒度与暂停
                            # 延迟的折中——冷渲染 ~14ms/字体，一片 8 个 ≈110ms
# 内置 v1：简繁同形字（spec §9 词表）+ 数字字母标点。2026-10-31 去隔字
# 空格（渲染按字符取字形，空格不产 tile 纯属排版残留）；文字版本入缓存
# 键（text_version），旧默认的存量渲染/比对行自然过期待 LRU 淘汰
SAMPLE_TEXT = ("永中木水火山田石目心手白的我你他它好行生"
               "同尖思明上下左右外大小不了在是有和也8Aa0?")


def text_version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


class TargetRenderError(Exception):
    """score_pair：目标（原字体）渲染失败。与候选渲染失败区分——搜索侧
    目标失败置 target_error 短路整池，候选失败仅跳过单个。"""


def score_pair(target: FontEntity, entity: FontEntity, cache: Cache,
               sample_text: str, tver: str | None = None):
    """两实体相似度对比，pair/render 缓存口径与 FontSearcher 一致
    （键 = text_version(sample_text)；搜索侧传已算的 _tver 免重哈希）。
    目标渲染失败抛 TargetRenderError；候选渲染失败或交集空返回 None。
    FontSearcher 与 GUI 手选评分共用，防缓存口径漂移。"""
    if tver is None:
        tver = text_version(sample_text)
    r = cache.get_pair(target.fingerprint, entity.fingerprint, tver)
    if r is not None:
        return r

    def bundle(e: FontEntity):
        return cache.get_render(
            e.fingerprint, tver,
            lambda: render_bundle(_entity_path(e), e.face_index, sample_text))

    try:
        tb = bundle(target)
    except Exception as exc:
        raise TargetRenderError(str(exc)) from exc
    try:
        r = compare(tb, bundle(entity))
    except Exception:
        return None             # 候选渲染失败无法证明达标（spec §11：跳过）
    if r is not None:
        cache.put_pair(target.fingerprint, entity.fingerprint, tver, r)
    return r


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
        self.pool_size = 0            # 对比池总数（GUI 进度分母）
        self.compared = 0             # 已对比字体数（GUI 进度分子）
        self._tver = text_version(sample_text)
        self._target_blob: bytes | None = None   # 池模式 target 编码（急渲后缓存）
        pool = [e for e in lib.entities()
                if target is None or e.fingerprint != target.fingerprint]
        ranked = rank_pool(target, pool, direction)
        self.pool_size = len(ranked)
        self._iter: Iterator[FontEntity] = iter(ranked)

    def resume(self) -> None:
        """续搜：清暂停**并重置 hit 计数**。暂停语义是「每批 n_pause 个
        命中」——只清 paused 的话残留计数 ≥ n_pause，续搜首个命中立刻
        再停，表现为每点一次「继续寻找」只多 1 个候选。"""
        self.paused = False
        self.hit_count = 0

    def next(self) -> Candidate | None:
        """推进到下一个「过全部门槛」的候选；None = 已暂停或耗尽（看 exhausted）。"""
        if self.paused:
            return None
        while True:
            e = next(self._iter, None)
            if e is None:
                self.exhausted = True
                return None
            self.compared += 1
            cand = self._evaluate(e)
            if cand is not None:
                return cand

    def next_slice(self, max_evals: int = EVAL_SLICE,
                   max_seconds: float = SLICE_SECONDS,
                   dispatcher=None) -> list[Candidate]:
        """推进至多 max_evals 次对比或 max_seconds 秒（两上限先到为准），
        返回期间全部命中；暂停/耗尽即停。GUI 分片拉取用：单次 next() 会
        一路吞到命中为止（稀疏命中时一次吞掉整池），按时长分片才能让进度
        计数（compared/pool_size）稳定跳动、暂停近即时生效——按对比数分片
        在缓存命中时一片瞬间扫完，回执照样成洪。

        dispatcher 非空走池模式（并行渲染）：gather 逐实体读 pair 缓存——
        命中就地过门槛（与串行同款快路径），未命中先覆盖率剪枝再按
        RENDER_CHUNK 分块派发 worker 渲染评分，收回同序逐个 absorb。
        dispatcher 契约 = (chunks, target_blob, sample_text) → 同序结果，
        chunk 结果 = ((score, size_ratio, n_chars) | None, blob | None) | None
        （blob = 候选编码 bundle，_absorb 回写渲染缓存）。worker 崩溃
        （BrokenProcessPool）的隔离在 dispatcher 层负责（jobs 侧弃池重建
        + 二分定位毒字体后跳过）；此处兜底收到 BrokenProcessPool 时**
        绝不在主进程重渲**（毒字体 AV 会杀整个应用）——直接弃派发集，
        gather 段的 pair 命中候选保留。同片内候选顺序与串行不保证逐位
        一致（pair 命中即时 absorb、派发结果后置），UI 层 add_candidates
        按综合分重排兜底；暂停越过 n_pause 也可能（片粒度软化，池模式
        独有）；target_error 短路即返不烧完 max_evals（比串行更省）。"""
        if dispatcher is None:
            return self._serial_slice(max_evals, max_seconds)
        return self._pool_slice(max_evals, max_seconds, dispatcher)

    def _serial_slice(self, max_evals: int,
                      max_seconds: float) -> list[Candidate]:
        out: list[Candidate] = []
        deadline = time.perf_counter() + max_seconds
        evals = 0
        while evals < max_evals and not self.paused:
            if time.perf_counter() >= deadline:
                break
            e = next(self._iter, None)
            if e is None:
                self.exhausted = True
                break
            self.compared += 1
            evals += 1
            cand = self._evaluate(e)
            if cand is not None:
                out.append(cand)
        return out

    def _pool_slice(self, max_evals: int, max_seconds: float,
                    dispatcher) -> list[Candidate]:
        if self.target is None:     # 无相似度可评：纯元数据，走串行
            return self._serial_slice(max_evals, max_seconds)
        if self.paused or self.target_error:
            return []
        out: list[Candidate] = []
        deadline = time.perf_counter() + max_seconds
        blobs: list[tuple[str, int]] = []       # 待派发 job（path, face_index）
        meta: list[tuple[FontEntity, float]] = []   # 与 blobs 对齐 (实体, 覆盖率)
        evals = 0
        while evals < max_evals and not self.paused:
            if time.perf_counter() >= deadline:
                break
            e = next(self._iter, None)
            if e is None:
                self.exhausted = True
                break
            self.compared += 1
            evals += 1
            # 与串行 _evaluate 同款：pair 命中就地过门槛（零渲染快路径）
            r = self.cache.get_pair(self.target.fingerprint, e.fingerprint,
                                    self._tver)
            if r is not None:
                cand = self._gate(e, r, None)
                if cand is not None:
                    out.append(cand)
                continue
            cov, _missing = coverage_detail(e.cmap, self.chars)
            if cov < self.cov_threshold:
                continue                        # 覆盖率剪枝：计 compared 不派发
            # 首个未命中才急渲 target（pair 全命中则永不解析，同串行懒渲）
            if self._target_blob is None:
                try:
                    tb = self.cache.get_render(
                        self.target.fingerprint, self._tver,
                        lambda: render_bundle(_entity_path(self.target),
                                              self.target.face_index,
                                              self.sample_text))
                except Exception:
                    self.target_error = True
                    return out
                self._target_blob = cache_encode(tb)
            blobs.append((str(_entity_path(e)), e.face_index))
            meta.append((e, cov))
        if not blobs:
            return out
        chunks = [blobs[i:i + RENDER_CHUNK]
                  for i in range(0, len(blobs), RENDER_CHUNK)]
        cmeta = [meta[i:i + RENDER_CHUNK]
                 for i in range(0, len(meta), RENDER_CHUNK)]
        try:
            results = dispatcher(chunks, self._target_blob, self.sample_text)
        except BrokenProcessPool:
            # 崩池（dispatcher 层隔离失效的兜底）：绝不主进程重渲——毒字体
            # AV 杀整个应用；弃派发集（compared 已计，进度不回退），本片
            # 只交 gather 段 pair 命中候选。无 deadline（兜底路径，秒级可受）
            return out
        for ents, res in zip(cmeta, results, strict=True):
            for (e, cov), item in zip(ents, res, strict=True):
                cand = self._absorb(e, cov, item)
                if cand is not None:
                    out.append(cand)
        return out

    def _absorb(self, e: FontEntity, cov: float,
                item: tuple | None) -> Candidate | None:
        """池结果落缓存 + 过门槛：item = (result, blob)，blob 为 worker
        渲染的编码 bundle——回写渲染缓存（与 score_pair 的 get_render 同
        口径；865bfdf 曾把 blob 丢掉，渲染缓存永远只有 target 一行，设置
        页「字形预览」恒 0 MB）。渲染成功即落 render 缓存（含交集空），
        pair 仅 compare 有效时写。"""
        if item is None:
            return None             # 渲染失败 → 跳过（不落缓存）
        t, blob = item
        if blob is not None:
            self.cache.put_render_encoded(e.fingerprint, self._tver, blob)
        if t is None:
            return None             # 交集空：渲染已缓存，无 pair 可写
        r = CompareResult(*t)
        self.cache.put_pair(self.target.fingerprint, e.fingerprint,
                            self._tver, r)
        return self._gate(e, r, cov)

    def _gate(self, e: FontEntity, r: CompareResult,
              cov: float | None) -> Candidate | None:
        """评分后门槛：相似率 → 相对大小 → 覆盖率 → Candidate + 暂停记账。"""
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
        # pair 缓存先读一次做覆盖率前置门槛（门槛按成本升序：缓存读 ~5µs
        # < coverage ~15µs < 渲染评分 ~57ms；score_pair 内会再读一次，µs 级）
        r = self.cache.get_pair(self.target.fingerprint, e.fingerprint, self._tver)
        cov: float | None = None
        if r is None:
            cov, _missing = coverage_detail(e.cmap, self.chars)
            if cov < self.cov_threshold:
                return None
        try:
            r = score_pair(self.target, e, self.cache, self.sample_text,
                           self._tver)
        except TargetRenderError:
            self.target_error = True
            return None             # 目标渲染失败：后续候选在顶部短路，不重试
        if r is None:
            return None             # 交集为空，无法比较 → 跳过
        return self._gate(e, r, cov)


def _entity_path(e: FontEntity) -> Path:
    return Path(e.dir_path) / e.rel_path
