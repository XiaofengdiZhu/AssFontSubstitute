import ass_font_substitute.core.similarity.search as search_mod
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.search import FontSearcher, text_version
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
               dict(name="c0", chars=CHARS, family="圓體0"),
               dict(name="c1", chars=CHARS, family="圓體1"),
               dict(name="c2", chars=CHARS, family="圓體2"))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=3)
    found = _drain(s)
    assert len(found) == 3                      # 攒满 n_pause 个「过全部门槛」即暂停
    assert all(c.similarity is not None for c in found)
    assert all(c.coverage >= 0.98 for c in found)
    assert not s.exhausted


def test_low_coverage_never_returned(tmp_path):
    # 严格门槛：覆盖率低于阈值的候选一律跳过，即使配额没攒满也绝不列出
    lib = _lib(tmp_path, dict(name="c0", chars=CHARS, family="圓體0"))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "C0").fingerprint]
    assert s.exhausted                          # 配额 5 未攒满，池已耗尽


def test_min_sim_gate(tmp_path):
    # 同形（box）过门槛；异形（circle）相似率低于 85% 被拒
    lib = _lib(tmp_path,
               dict(name="cbox", chars=CHARS, family="同形體", shape="box"),
               dict(name="cround", chars=CHARS, family="圓形體", shape="circle"))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "CBOX").fingerprint]
    assert s.exhausted


def test_size_range_gate(tmp_path):
    # 同形但小一号：相似率高、size_ratio≈0.5 出区间 → 拒
    lib = _lib(tmp_path,
               dict(name="cfit", chars=CHARS, family="合適體"),
               dict(name="csmall", chars=CHARS, family="小號體", scale=0.5))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98)
    found = _drain(s)
    assert [c.fingerprint for c in found] == [_by(lib, "CFIT").fingerprint]
    assert s.exhausted


def test_cached_pair_gates_without_render(tmp_path, monkeypatch):
    # pair 缓存命中 → 相似率/大小门槛零渲染可判；命中率门槛放行
    lib = _lib(tmp_path, dict(name="c0", chars=CHARS, family="圓體0"))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    cache = Cache(tmp_path / "cache.db")
    cache.put_pair(target.fingerprint, "nonexistent-fp", text_version(CHARS),
                   CompareResult(0.9, 1.0, 8))

    def explode(path, face_index, chars):
        raise RuntimeError("不应触发渲染")

    monkeypatch.setattr(search_mod, "render_bundle", explode)
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text=CHARS, cov_threshold=0.98)
    # c0 无缓存 → 需渲染 → 渲染被禁 → 跳过；耗尽而非崩溃
    found = _drain(s)
    assert found == []
    assert s.exhausted


def test_cache_hit_accepted_without_render(tmp_path, monkeypatch):
    # 预埋 pair 缓存（相似率/大小/覆盖率全达标）→ 整轮搜索零渲染
    from ass_font_substitute.core.similarity.metrics import CompareResult
    lib = _lib(tmp_path, dict(name="c0", chars=CHARS, family="圓體0"))
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
               dict(name="c0", chars=CHARS, family="圓體0"),
               dict(name="c1", chars=CHARS, family="壞形體"),
               dict(name="c2", chars=CHARS, family="圓體2"))
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
    lib = _lib(tmp_path, dict(name="c0", chars=CHARS, family="圓體0"))
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = _searcher(tmp_path, lib, target, cov_threshold=0.98, n_pause=1)
    first = s.next()
    assert first is not None and s.paused
    s.paused = False                            # 用户点继续
    assert s.next() is None
    assert s.exhausted                          # 远字体被覆盖率门槛跳过，无更多候选


def test_defaults_pinned():
    # 设置默认值钉死：覆盖率 95%、最低相似率 85%、相对大小 90%–110%
    import inspect
    sig = inspect.signature(FontSearcher.__init__)
    assert sig.parameters["cov_threshold"].default == 0.95
    assert sig.parameters["min_sim"].default == 0.85
    assert sig.parameters["size_range"].default == (0.90, 1.10)
