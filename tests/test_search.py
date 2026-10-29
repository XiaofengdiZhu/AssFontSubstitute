import ass_font_substitute.core.similarity.search as search_mod
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.search import FontSearcher
from ass_font_substitute.core.similarity.search import text_version
from tests.fonts import build_font

CHARS = "永中木水火土石田8Aa0?"


def _lib(tmp_path, *extra):
    """target + 远字体（低覆盖）+ 任意额外候选字体。"""
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "target.ttf", chars=CHARS, family="目標圓體", psname="TGT")
    build_font(d / "far.ttf", chars="8Aa0?", family="黑體遠", psname="FAR")
    for i, kw in enumerate(extra):
        kw = dict(kw)
        name = kw.pop("name", f"c{i}")
        build_font(d / f"{name}.ttf", psname=name.upper(), **kw)
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    return LibraryIndex.open([d])


def _searcher(tmp_path, lib, target, **kw):
    kw.setdefault("sample_text", CHARS)
    return FontSearcher(target, set("永中木水火土石田"), lib,
                        Cache(tmp_path / "cache.db"), **kw)


def _by(lib, ps):
    return next(e for e in lib.entities() if e.psname == ps)


def _drain(s):
    out = []
    while not s.paused:
        c = s.next()
        if c is None:
            break
        out.append(c)
    return out


def test_search_pauses_and_ranking(tmp_path):
    lib = _lib(tmp_path,
               {"name": "c0", "chars": CHARS, "family": "圓體0"},
               {"name": "c1", "chars": CHARS, "family": "圓體1"},
               {"name": "c2", "chars": CHARS, "family": "圓體2"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=3)
    found = _drain(s)
    assert len(found) == 3                      # 攒满 n_pause 个「过全部门槛」即暂停
    assert all(c.similarity is not None for c in found)
    assert all(c.coverage >= 0.98 for c in found)
    assert not s.exhausted


def test_low_coverage_never_returned(tmp_path):
    # 严格门槛：覆盖率低于阈值的候选一律跳过，即使配额没攒满也绝不列出
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "C0").fingerprint]
    assert s.exhausted                          # 配额 5 未攒满，池已耗尽


def test_min_sim_gate(tmp_path):
    # 同形（box）过门槛；异形（circle）相似率低于 85% 被拒
    lib = _lib(tmp_path,
               {"name": "cbox", "chars": CHARS, "family": "同形體", "shape": "box"},
               {"name": "cround", "chars": CHARS, "family": "圓形體", "shape": "circle"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "CBOX").fingerprint]
    assert s.exhausted


def test_size_range_gate(tmp_path):
    # 同形但小一号：相似率高、size_ratio≈0.5 出区间 → 拒
    lib = _lib(tmp_path,
               {"name": "cfit", "chars": CHARS, "family": "合適體"},
               {"name": "csmall", "chars": CHARS, "family": "小號體", "scale": 0.5})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "CFIT").fingerprint]
    assert s.exhausted


def test_miss_path_render_failure_skips(tmp_path, monkeypatch):
    # 未命中路径：候选无缓存 → 需渲染 → 渲染失败 → 跳过；耗尽而非崩溃
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    cache = Cache(tmp_path / "cache.db")

    def explode(path, face_index, chars):
        raise RuntimeError("不应触发渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text=CHARS, cov_threshold=0.98)
    found = _drain(s)
    assert found == []
    assert s.exhausted


def test_rejected_results_still_cached(tmp_path, monkeypatch):
    # 门槛拒绝的结果也必须先入缓存：放宽阈值重算时零渲染复用（spec §9 步骤5）
    lib = _lib(tmp_path,
               {"name": "cround", "chars": CHARS, "family": "圓形體", "shape": "circle"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    cache = Cache(tmp_path / "cache.db")
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text=CHARS, cov_threshold=0.98, min_sim=0.99)
    assert _drain(s) == []                      # 0.81 < 0.99 被拒，但 pair 已入库
    assert cache.pair_count >= 1

    def explode(path, face_index, chars):
        raise RuntimeError("放宽阈值后不应触发渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    s2 = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                      sample_text=CHARS, cov_threshold=0.98, min_sim=0.80)
    found = _drain(s2)
    assert [c.fingerprint for c in found] == \
        [next(e for e in lib.entities() if e.psname == "CROUND").fingerprint]


def test_cache_hit_path_coverage_gate(tmp_path, monkeypatch):
    # 缓存命中路径的覆盖率门槛：pair 达标 + 候选低覆盖 → 仍拒，不因缓存放行
    lib = _lib(tmp_path)
    target = next(e for e in lib.entities() if e.psname == "TGT")
    far = next(e for e in lib.entities() if e.psname == "FAR")
    cache = Cache(tmp_path / "cache.db")
    cache.put_pair(target.fingerprint, far.fingerprint, text_version(CHARS),
                   CompareResult(0.92, 1.05, 5))

    def explode(path, face_index, chars):
        raise RuntimeError("不应触发渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text=CHARS, cov_threshold=0.98)
    assert _drain(s) == []                      # 命中路径走到覆盖率门槛被拒
    assert s.exhausted


def test_target_render_failure_flagged(tmp_path, monkeypatch):
    # 目标渲染失败 → target_error（区别于 exhausted），只解析一次不逐候选重试，
    # 不伪装成「库里没有相似字体」
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    tpath = str(search_mod._entity_path(target))
    calls: list[str] = []

    def flaky(path, face_index, chars):
        calls.append(str(path))
        raise RuntimeError("目标坏了")

    monkeypatch.setattr(search_mod, "render_bundle", flaky)
    s = FontSearcher(target, set("永中木水火土石田"), lib,
                     Cache(tmp_path / "cache.db"),
                     sample_text=CHARS, cov_threshold=0.98)
    found = _drain(s)
    assert found == []
    assert s.target_error and s.exhausted
    assert calls == [tpath]                     # 恰一次，无重试风暴


def test_cache_hit_accepted_without_render(tmp_path, monkeypatch):
    # 预埋 pair 缓存（相似率/大小/覆盖率全达标）→ 整轮搜索零渲染
    from ass_font_substitute.core.similarity.metrics import CompareResult
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    c0 = next(e for e in lib.entities() if e.psname == "C0")
    cache = Cache(tmp_path / "cache.db")
    cache.put_pair(target.fingerprint, c0.fingerprint, text_version(CHARS),
                   CompareResult(0.92, 1.05, 8))

    def explode(path, face_index, chars):
        raise RuntimeError("不应触发渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text=CHARS, cov_threshold=0.98, n_pause=1)
    found = _drain(s)
    assert len(found) == 1 and found[0].similarity == 0.92
    assert found[0].coverage == 1.0             # 命中路径的覆盖率照常填写
    assert s.paused


def test_render_failure_skipped(tmp_path, monkeypatch):
    # 严格门槛：渲染失败的候选无法证明达标 → 跳过，不置灰列出，不中断搜索
    lib = _lib(tmp_path,
               {"name": "c0", "chars": CHARS, "family": "圓體0"},
               {"name": "c1", "chars": CHARS, "family": "壞形體"},
               {"name": "c2", "chars": CHARS, "family": "圓體2"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    broken = next(e for e in lib.entities() if e.psname == "C1")
    broken_path = str(search_mod._entity_path(broken))

    real_render = search_mod.render_bundle

    def flaky(path, face_index, chars):
        if str(path) == broken_path:
            raise RuntimeError("boom: 不可渲染")
        return real_render(path, face_index, chars)

    monkeypatch.setattr(search_mod, "render_bundle", flaky)
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == \
        [_by(lib, "C0").fingerprint, _by(lib, "C2").fingerprint]
    assert all(c.similarity is not None for c in found)
    assert s.exhausted


def test_resume_then_exhausted(tmp_path):
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=1)
    first = s.next()
    assert first is not None and s.paused
    s.paused = False                            # 用户点继续
    assert s.next() is None
    assert s.exhausted                          # 远字体被覆盖率门槛跳过，无更多候选


def test_slice_stops_on_pause(tmp_path):
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=1)
    batch = s.next_slice(8)
    assert len(batch) == 1 and s.paused and s.compared == 1


def test_slice_progress_counters(tmp_path):
    """next_slice 分片推进：pool_size/compared 供 GUI 进度计数；单片至多
    max_evals 次对比，跨片累计；耗尽即停。"""
    lib = _lib(tmp_path,
               {"name": "c0", "chars": CHARS, "family": "圓體0"},
               {"name": "c1", "chars": CHARS, "family": "圓體1"},
               {"name": "c2", "chars": CHARS, "family": "圓體2"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=10)
    assert s.pool_size == 4                     # 池不含 target 自身
    seen: list = []
    while not (s.paused or s.exhausted):
        seen += s.next_slice(2)
    assert s.compared == 4 and s.exhausted      # 片间累计、池尽即停
    assert len(seen) == 3


def test_slice_time_bound(tmp_path, monkeypatch):
    """一片两上限（对比数/时长）先到为准：慢评分时按时长切，池未尽即
    回执、计数已累加——GUI 进度计数逐片跳动，不攒到批满才回头。"""
    import time as _time
    lib = _lib(tmp_path, *[{"name": f"c{i}", "chars": CHARS,
                            "family": f"圓體{i}"} for i in range(8)])
    target = next(e for e in lib.entities() if e.psname == "TGT")
    real_compare = search_mod.compare

    def slow_compare(a, b):
        _time.sleep(0.12)               # 模拟渲染+评分 ~57ms 的慢路径
        return real_compare(a, b)

    monkeypatch.setattr(search_mod, "compare", slow_compare)
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=99,
                  min_sim=0.5, size_range=(0.5, 2.0))
    t0 = _time.perf_counter()
    first = s.next_slice()
    dt = _time.perf_counter() - t0
    assert 4 <= s.compared <= 6         # ~0.5s 一片：4~5 次慢评分即切
    assert not s.exhausted and dt < 1.5
    rest: list = []
    while not s.exhausted:              # 剩池继续按片抽干（片间计数累加）
        rest += s.next_slice()
    assert s.compared == 9 and s.exhausted   # 池 = 8 候选 + 远字体
    assert len(first) + len(rest) == 8


def test_defaults_pinned():
    # 设置默认值钉死：覆盖率 95%、最低相似率 85%、相对大小 90%–110%
    import inspect
    sig = inspect.signature(FontSearcher.__init__)
    assert sig.parameters["cov_threshold"].default == 0.95
    assert sig.parameters["min_sim"].default == 0.85
    assert sig.parameters["size_range"].default == (0.90, 1.10)



def test_resume_resets_batch_counter(tmp_path):
    """resume() 清暂停并重置批计数：只清 paused 的话残留 hit_count ≥
    n_pause，续搜首个命中立刻再停（每点一次只多 1 个候选）。"""
    lib = _lib(tmp_path,
               {"name": "c0", "chars": CHARS, "family": "圓體0"},
               {"name": "c1", "chars": CHARS, "family": "圓體1"},
               {"name": "c2", "chars": CHARS, "family": "圓體2"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=2)
    first = _drain(s)
    assert len(first) == 2 and s.paused
    s.resume()
    second = _drain(s)
    assert [c.fingerprint for c in second] == [_by(lib, "C2").fingerprint]
    assert s.exhausted                          # 批计数已重置：不一命中就停


def test_score_pair_shared_cache_semantics(tmp_path, monkeypatch):
    """score_pair（搜索器与 GUI 手选评分共用缓存口径）：pair 命中零渲染；
    未命中计算并回填；目标渲染失败抛 TargetRenderError（区别于候选失败
    返 None）；候选渲染失败返 None 不落缓存。"""
    lib = _lib(tmp_path, {"name": "c0", "chars": CHARS, "family": "圓體0"})
    target = next(e for e in lib.entities() if e.psname == "TGT")
    c0 = next(e for e in lib.entities() if e.psname == "C0")
    real = search_mod.render_bundle
    tpath = str(search_mod._entity_path(target))
    bpath = str(search_mod._entity_path(c0))

    # pair 命中：零渲染
    cache = Cache(tmp_path / "cache.db")
    cache.put_pair(target.fingerprint, c0.fingerprint, text_version(CHARS),
                   CompareResult(0.9, 1.0, 3))

    def explode(path, face_index, chars):
        raise RuntimeError("缓存命中不应渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    r = search_mod.score_pair(target, c0, cache, CHARS)
    assert (r.score, r.size_ratio) == (0.9, 1.0)
    monkeypatch.setattr(search_mod, "render_bundle", real)

    # 未命中：真渲染一次并回填（第二次读命中，不再渲染）
    cache2 = Cache(tmp_path / "c2.db")
    r2 = search_mod.score_pair(target, c0, cache2, CHARS)
    assert r2 is not None and cache2.pair_count == 1
    r2b = search_mod.score_pair(target, c0, cache2, CHARS)
    assert r2b is not None and cache2.pair_count == 1

    # 目标渲染失败 → TargetRenderError
    def tgt_explode(path, face_index, chars):
        if str(path) == tpath:
            raise RuntimeError("目标坏了")
        return real(path, face_index, chars)

    monkeypatch.setattr(search_mod, "render_bundle", tgt_explode)
    try:
        search_mod.score_pair(target, c0, Cache(tmp_path / "c3.db"), CHARS)
        raise AssertionError("应抛 TargetRenderError")
    except search_mod.TargetRenderError:
        pass

    # 候选渲染失败 → None，且不落 pair 缓存
    def cand_explode(path, face_index, chars):
        if str(path) == bpath:
            raise RuntimeError("候选坏了")
        return real(path, face_index, chars)

    monkeypatch.setattr(search_mod, "render_bundle", cand_explode)
    cache4 = Cache(tmp_path / "c4.db")
    assert search_mod.score_pair(target, c0, cache4, CHARS) is None
    assert cache4.pair_count == 0
