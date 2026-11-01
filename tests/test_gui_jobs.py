
import pytest

from ass_font_substitute.gui.jobs import apply_to_file
from ass_font_substitute.gui.jobs import ensure_library
from ass_font_substitute.gui.jobs import lib_key
from ass_font_substitute.gui.jobs import load_library
from ass_font_substitute.gui.jobs import parse_subtitle
from ass_font_substitute.gui.jobs import probe_dir_status
from ass_font_substitute.gui.jobs import pull_chunk
from ass_font_substitute.gui.jobs import render_preview
from ass_font_substitute.gui.jobs import update_dir
from tests.fonts import build_font


@pytest.fixture()
def font_dir(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "hei.ttf", chars="永宇好", family="测试黑体", psname="TestHei")
    return d


@pytest.fixture()
def sub_file(tmp_path):
    p = tmp_path / "demo.ass"
    p.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
        "Style: Def,测试黑体,54\n\n"
        "[Events]\nFormat: Layer, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,Def,,0,0,0,,永宇好\\N测试\n",
        "utf-8-sig")
    return p


def test_probe_dir_status_states(tmp_path, font_dir):
    assert probe_dir_status(str(tmp_path / "nope")).state == "missing"
    # 未建索引：全部计新增
    st = probe_dir_status(str(font_dir))
    assert st.state == "stale" and st.added == 1 and st.indexed_files == 0
    assert st.total == 1 and st.bad == 0
    # 建索引后：ok
    update_dir(str(font_dir))
    st = probe_dir_status(str(font_dir))
    assert st.state == "ok" and st.indexed_files == 1
    assert st.total == 1 and st.bad == 0
    # 追加文件：stale with added
    build_font(font_dir / "new.ttf", chars="永", family="新字体")
    st = probe_dir_status(str(font_dir))
    assert st.state == "stale" and st.added == 1
    assert st.total == 2
    # 坏文件入 bad 表：total 含它，bad 计数
    (font_dir / "junk.ttf").write_bytes(b"not a font")
    update_dir(str(font_dir))
    st = probe_dir_status(str(font_dir))
    assert st.total == 3 and st.bad == 1 and st.indexed_files == 2


def test_probe_does_not_create_db(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    probe_dir_status(str(d))
    assert not (d / "fontindex.db").exists()


def test_load_library_filters_offline(tmp_path, font_dir):
    update_dir(str(font_dir))       # 建索引归「更新全部索引」流程，此处显式建
    snap = load_library([str(font_dir), str(tmp_path / "ghost")])
    assert len(snap.lib.entities()) == 1
    assert snap.offline_dirs == [str(tmp_path / "ghost")]


def test_ensure_library_caches_snapshot(ctx_stub, font_dir):
    s1 = ensure_library(ctx_stub, [str(font_dir)])
    s2 = ensure_library(ctx_stub, [str(font_dir)])
    assert s1 is s2
    s3 = ensure_library(ctx_stub, [str(font_dir), str(font_dir)])
    assert s3 is not s2


def test_ensure_library_invalidated_by_index_update(ctx_stub, font_dir):
    # 回归：IndexWorker 更新索引（fontindex.db mtime 变）后必须弃旧快照，
    # 否则聚合库永远看不到新字体
    update_dir(str(font_dir))                            # 先建索引：1 款字体
    s1 = ensure_library(ctx_stub, [str(font_dir)])
    n1 = len(s1.lib.entities())
    assert n1 == 1
    build_font(font_dir / "new.ttf", chars="永中", family="乙字体")
    update_dir(str(font_dir))                            # 索引纳入新字体
    s2 = ensure_library(ctx_stub, [str(font_dir)])
    assert s2 is not s1
    assert len(s2.lib.entities()) == n1 + 1


def test_parse_subtitle(tmp_path, sub_file):
    r = parse_subtitle(str(sub_file))
    assert r.error is None and r.font_count == 1
    u = r.usages[0]
    assert u.raw_name == "测试黑体" and u.chars == set("永宇好测试")
    bad = parse_subtitle(str(tmp_path / "nope.ass"))
    assert bad.error is not None and bad.font_count == 0


def test_parse_subtitle_degraded_partial(tmp_path):
    # 降级部分提取透传：error=None（照常参与 checked_parses 合并）、
    # skipped 列坏行、usages 保留好行——与整文件失败（error 表）不同
    p = tmp_path / "bad.ass"
    p.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
        "Style: Def,测试黑体,54\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:05.00,Def,,0,0,0,,永宇好\n"
        "Dialogue: 0,0:00:00.00\n", "utf-8-sig")
    r = parse_subtitle(str(p))
    assert r.error is None
    assert len(r.skipped) == 1 and "第 11 行" in r.skipped[0]
    assert r.font_count == 1 and r.usages[0].chars == set("永宇好")


def test_score_pick_smoke(ctx_stub, font_dir):
    from ass_font_substitute.gui.jobs import ensure_library
    from ass_font_substitute.gui.jobs import score_pick
    update_dir(str(font_dir))       # 建索引归「更新全部索引」流程，此处显式建
    snap = ensure_library(ctx_stub, [str(font_dir)])
    ents = snap.lib.entities()
    e0, e1 = ents[0], ents[-1]
    assert score_pick(ctx_stub, None, e0) == (e0, None, None)   # 库中没有卡
    r1 = score_pick(ctx_stub, e0, e1)                           # 评分入 pair 缓存
    assert r1[0] is e1
    if r1[1] is not None:                                       # 缓存复算一致
        assert score_pick(ctx_stub, e0, e1)[1:] == r1[1:]


def test_pull_chunk_states(ctx_stub, font_dir):
    from ass_font_substitute.gui.jobs import make_searcher
    update_dir(str(font_dir))       # 建索引归「更新全部索引」流程，此处显式建
    snap = ensure_library(ctx_stub, [str(font_dir)])
    lib = snap.lib
    chars = set("永宇")
    s = make_searcher(ctx_stub, target=lib.entities()[0], chars=chars,
                      threshold=0.95, n_pause=5, min_sim=0.85,
                      size_range=(0.90, 1.10), direction="t2s")
    cands, state = pull_chunk(s)
    assert cands == [] and state == "exhausted"    # 单字体库无其他候选
    assert pull_chunk(s, {"cancelled": True}) == ([], "cancelled")


def test_render_preview_skips_missing(font_dir):
    # 独立 ctx 以免与其它用例共享
    class C:
        pass
    ctx = C()
    update_dir(str(font_dir))       # 建索引归「更新全部索引」流程，此处显式建
    snap = load_library([str(font_dir)])
    ctx.snapshot, ctx.lib, ctx.lib_key = snap, snap.lib, lib_key([str(font_dir)])
    e = snap.lib.entities()[0]
    strip = render_preview(e, "永X", True)         # X 不在 cmap → 留空占位
    assert strip is not None and strip.ndim == 2
    assert strip.max() > 0                         # 永渲染成墨迹
    # 缺字留空前进（半角 0.5em≈36px@72）：后续字符不错位，多行对比逐字
    # 对得上（X 变 tofu 或被吞掉错位时这里会炸）
    assert abs(strip.shape[1] - render_preview(e, "永", True).shape[1] - 36) <= 2


def test_apply_to_file_suffix_and_overwrite(tmp_path, sub_file):
    r = apply_to_file(str(sub_file), {"测试黑体": "替换体"}, ".substituted.ass",
                      overwrite=False)
    assert r.error is None
    out = tmp_path / "demo.substituted.ass"
    assert r.out_path == str(out) and out.exists()
    assert "替换体" in out.read_text("utf-8")
    assert "测试黑体" not in out.read_text("utf-8")
    r2 = apply_to_file(str(sub_file), {"测试黑体": "覆盖体"}, ".x.ass", overwrite=True)
    assert r2.out_path == str(sub_file)
    assert "覆盖体" in sub_file.read_text("utf-8")


def test_apply_error_folded(tmp_path):
    r = apply_to_file(str(tmp_path / "nope.ass"), {}, ".s.ass", overwrite=False)
    assert r.error is not None and r.out_path is None


def test_cache_ops(ctx_stub):
    # 缓存位置不可迁移后（绿色软件定死 Data/cache.db），jobs 侧只剩统计/清除
    from ass_font_substitute.gui.jobs import cache_clear
    from ass_font_substitute.gui.jobs import cache_stats
    st = cache_stats(ctx_stub)
    assert st["pair_count"] == 0
    cache_clear(ctx_stub)


@pytest.fixture()
def ctx_stub(tmp_path, monkeypatch):
    """CoreWorker ctx 等价物（非 Qt）：真实 Cache，真实设置。"""
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path / "appdata"))
    from types import SimpleNamespace

    from ass_font_substitute.core.similarity.cache import Cache
    from ass_font_substitute.gui.settings_store import Settings
    s = Settings(tmp_path / "settings.json")
    return SimpleNamespace(settings=s, cache=Cache(tmp_path / "cache.db"),
                           snapshot=None, lib=None, lib_key=None)


def test_ensure_render_pool_reuse_and_cap(ctx_stub, monkeypatch):
    # 池按需建、复用同一实例；W = min(设置 max_threads, cpu_count)
    from concurrent.futures import ProcessPoolExecutor

    from ass_font_substitute.gui.jobs import ensure_render_pool
    ctx_stub.settings.set("max_threads", 3)
    p1 = ensure_render_pool(ctx_stub)
    try:
        assert isinstance(p1, ProcessPoolExecutor)
        assert p1._max_workers == 3
        assert ensure_render_pool(ctx_stub) is p1      # 复用
    finally:
        from ass_font_substitute.gui.jobs import shutdown_ctx
        shutdown_ctx(ctx_stub)
    assert ctx_stub.pool is None                       # 收敛置 None


def test_pull_chunk_pool_mode(ctx_stub, font_dir, tmp_path):
    # 池模式端到端：真 ProcessPoolExecutor 渲染评分，搜索耗尽、候选与串行一致
    from ass_font_substitute.gui.jobs import make_searcher
    from ass_font_substitute.gui.jobs import shutdown_ctx
    build_font(font_dir / "c0.ttf", chars="永宇好", family="测试圆体0",
               psname="C0")
    build_font(font_dir / "c1.ttf", chars="永宇好", family="测试圆体1",
               psname="C1")
    update_dir(str(font_dir))
    snap = ensure_library(ctx_stub, [str(font_dir)])
    lib = snap.lib
    target = next(e for e in lib.entities() if e.psname == "TestHei")

    def search(ctx):
        s = make_searcher(ctx_stub, target=target, chars=set("永宇好"),
                          threshold=0.95, n_pause=10, min_sim=0.85,
                          size_range=(0.90, 1.10), direction="t2s")
        out, state = [], None
        while True:
            cands, state = pull_chunk(s, None, ctx=ctx)
            out += cands
            if state != "running":
                return out, state

    serial, state_s = search(None)
    ctx_stub.pool = None
    try:
        parallel, state_p = search(ctx_stub)   # ctx 走真池（ensure 惰建）
    finally:
        shutdown_ctx(ctx_stub)
    assert state_s == state_p == "exhausted"
    assert [c.fingerprint for c in serial] == [c.fingerprint for c in parallel]
    assert serial                                      # 两候选命中（box 同形）


def test_dispatch_pool_isolates_poison_font(ctx_stub, monkeypatch, tmp_path):
    # 池崩坏隔离：弃崩池 → 逐字体新池试探 → 毒字体 None 跳过，普通字体不丢
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool
    from types import SimpleNamespace

    from ass_font_substitute.gui import jobs as jobs_mod
    from ass_font_substitute.gui.jobs import _dispatch_pool

    d = tmp_path / "po"
    d.mkdir()
    from tests.fonts import build_font as bf
    bf(d / "t.ttf", chars="永", family="目標", psname="T")
    bf(d / "g.ttf", chars="永", family="好字體")
    from ass_font_substitute.core.similarity.cache import _encode
    from ass_font_substitute.core.similarity.render import render_bundle
    blob = _encode(render_bundle(d / "t.ttf", 0, "永"))
    poison = (str(d / "poison.ttf"), 0)
    good = (str(d / "g.ttf"), 0)

    class FakePool:
        def __init__(self):
            self.shutdown_called = False
        def submit(self, fn, blob_, text_, ch):
            if any(j == poison for j in ch):
                raise BrokenProcessPool()
            f = Future()
            f.set_result([fn(blob_, text_, [j])[0] for j in ch])
            return f
        def shutdown(self, wait=False):
            self.shutdown_called = True

    ctx = SimpleNamespace(pool=FakePool(), pool_workers=2, settings=ctx_stub.settings)
    broken_pool = ctx.pool
    monkeypatch.setattr(jobs_mod, "ensure_render_pool",
                        lambda c: FakePool())          # 试探期恒新池
    res = _dispatch_pool(ctx, broken_pool, [[good, poison, good]], blob, "永")
    assert len(res) == 1 and len(res[0]) == 3
    g1, po, g2 = res[0]
    assert po is None                                  # 毒字体跳过
    assert g1 is not None and g2 is not None and g1 == g2
    assert broken_pool.shutdown_called                 # 崩池即弃
    assert ctx.pool is None                            # 下次 pull 重建自愈


def test_dispatch_pool_submits_all_before_waiting(monkeypatch):
    # 回归钉：result() 套 submit 生成器 = 提交一个等一个全串行（GUI CPU 20%
    # 实锤）。须先全部 submit 再逐个 result
    from types import SimpleNamespace

    from ass_font_substitute.gui import jobs as jm

    class LatchPool:
        def __init__(self, n):
            self.n = n
            self.submitted = 0

        def submit(self, fn, blob, text, ch):
            self.submitted += 1
            pool = self

            class _R:
                def result(self):
                    assert pool.submitted == pool.n, \
                        "result 在全部 submit 前被取——派发被串行化"
                    return fn(blob, text, ch)

            return _R()

    calls = {"n": 0}

    def fake_score(blob, text, ch):
        calls["n"] += 1
        return [None] * len(ch)

    monkeypatch.setattr("ass_font_substitute.core.similarity.pool.score_chunk",
                        fake_score)
    chunks = [[("a", 0)], [("b", 0)], [("c", 0)]]
    ctx = SimpleNamespace(pool=LatchPool(len(chunks)), pool_workers=3,
                          settings=None)
    res = jm._dispatch_pool(ctx, ctx.pool, chunks, b"", "x")
    assert calls["n"] == 3 and len(res) == 3
