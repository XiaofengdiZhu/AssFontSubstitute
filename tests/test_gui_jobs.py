
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


def test_render_preview_skips_missing(font_dir):
    # 独立 ctx 以免与其它用例共享
    class C:
        pass
    ctx = C()
    update_dir(str(font_dir))       # 建索引归「更新全部索引」流程，此处显式建
    snap = load_library([str(font_dir)])
    ctx.snapshot, ctx.lib, ctx.lib_key = snap, snap.lib, lib_key([str(font_dir)])
    e = snap.lib.entities()[0]
    rb = render_preview(e, "永X")                  # X 不在 cmap → 跳过
    assert set(rb.tiles) == {"永"}


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


def test_cache_ops(ctx_stub, tmp_path):
    from ass_font_substitute.gui.jobs import cache_clear
    from ass_font_substitute.gui.jobs import cache_move
    from ass_font_substitute.gui.jobs import cache_stats
    st = cache_stats(ctx_stub)
    assert st["pair_count"] == 0
    new = tmp_path / "moved" / "cache.db"
    cache_move(ctx_stub, str(new), migrate=True)
    assert ctx_stub.cache.path == new
    cache_clear(ctx_stub)


@pytest.fixture()
def ctx_stub(tmp_path, monkeypatch):
    """CoreWorker ctx 等价物（非 Qt）：真实 Cache，真实设置。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from types import SimpleNamespace

    from ass_font_substitute.core.similarity.cache import Cache
    from ass_font_substitute.gui.settings_store import Settings
    s = Settings(tmp_path / "settings.json")
    return SimpleNamespace(settings=s, cache=Cache(tmp_path / "cache.db"),
                           snapshot=None, lib=None, lib_key=None)
