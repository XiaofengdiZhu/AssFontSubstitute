import ass_font_substitute.core.similarity.search as search_mod
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.search import SAMPLE_TEXT, FontSearcher
from tests.fonts import build_font


def _lib(tmp_path, n=4):
    d = tmp_path / "lib"
    d.mkdir()
    chars = "永中木水火土石田" + "8Aa0?"
    build_font(d / "target.ttf", chars=chars, family="目標圓體", psname="TGT")
    for i in range(n):
        build_font(d / f"c{i}.ttf", chars=chars, family=f"圓體{i}", psname=f"C{i}")
    build_font(d / "far.ttf", chars="8Aa0?", family="黑體遠", psname="FAR")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    return LibraryIndex.open([d])


def test_search_pauses_and_ranking(tmp_path):
    lib = _lib(tmp_path)
    cache = Cache(tmp_path / "cache.db")
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text="永中木水火土石田8Aa0?", cov_threshold=0.98)
    found = []
    while not s.paused:
        c = s.next()
        if c is None:
            break
        found.append(c)
    assert len(found) == 5                      # n_pause 暂停
    assert all(c.similarity is not None for c in found)
    # 同形工厂字体自高分；无 CJK 字形的远字体垫底或不出现
    assert found[0].coverage >= found[-1].coverage or \
           found[0].similarity >= found[-1].similarity


def test_resume_after_pause(tmp_path):
    lib = _lib(tmp_path, n=1)
    cache = Cache(tmp_path / "cache.db")
    target = next(e for e in lib.entities() if e.psname == "TGT")
    s = FontSearcher(target, set("永中木"), lib, cache,
                     sample_text="永中木8Aa0?", cov_threshold=0.98)
    while not s.paused:
        if s.next() is None:
            break
    s.paused = False                            # 用户点继续
    more = s.next()
    assert more is None or more.similarity is not None


def test_render_failure_isolated(tmp_path, monkeypatch):
    # spec §11：单候选渲染失败 → 置灰（similarity=None），不炸整个搜索
    lib = _lib(tmp_path, n=3)
    cache = Cache(tmp_path / "cache.db")
    target = next(e for e in lib.entities() if e.psname == "TGT")
    broken = next(e for e in lib.entities() if e.psname == "C1")
    broken_path = str(search_mod._entity_path(broken))

    real_render = search_mod.render_bundle

    def flaky(path, face_index, chars):
        if str(path) == broken_path:
            raise RuntimeError("boom: 不可渲染")
        return real_render(path, face_index, chars)

    monkeypatch.setattr(search_mod, "render_bundle", flaky)
    s = FontSearcher(target, set("永中木水火土石田"), lib, cache,
                     sample_text="永中木水火土石田8Aa0?", cov_threshold=0.98)
    results = {}
    while not s.paused:
        c = s.next()
        if c is None:
            break
        results[c.fingerprint] = c
    assert broken.fingerprint in results
    bc = results[broken.fingerprint]
    assert bc.similarity is None                # 置灰
    assert bc.coverage == 1.0                   # 覆盖率照常填写
    others = [c for fp, c in results.items() if fp != broken.fingerprint]
    assert others and all(c.similarity is not None for c in others)
