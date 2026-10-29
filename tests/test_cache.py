import numpy as np

from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.render import RenderBundle


def _bundle(chars="永"):
    # 每字符灰度不同：解码值逐字符断言才能抓到 keys/values 错位
    tiles = {c: np.full((48, 48), 100 + i * 7, np.uint8) for i, c in enumerate(chars)}
    return RenderBundle(tiles, 40.0, frozenset(chars))


def test_render_lazy_and_hit(tmp_path):
    c = Cache(tmp_path / "cache.db")
    calls = []
    chars = "永中木8b"  # 插入序 ≠ sorted 序，错位可被值断言抓到
    def loader():
        calls.append(1)
        return _bundle(chars)
    b1 = c.get_render("fpA", "v1", loader)
    b2 = c.get_render("fpA", "v1", loader)
    assert len(calls) == 1 and set(b2.tiles) == set(b1.tiles)
    # 控制器裁定：逐字符校验 tile 值（编码按 sorted(items) 同序取键值，防 zip 错位）
    for ch in chars:
        assert np.array_equal(b2.tiles[ch], b1.tiles[ch])
    c.get_render("fpA", "v2", loader)  # 文字版本变 → miss
    assert len(calls) == 2


def test_pair_roundtrip_and_order(tmp_path):
    c = Cache(tmp_path / "cache.db")
    r = CompareResult(0.91, 1.08, 40)
    c.put_pair("zzz", "aaa", "v1", r)
    got = c.get_pair("aaa", "zzz", "v1")   # 键序无关
    assert got.score == 0.91 and got.size_ratio == 1.08


def test_trim_and_clear(tmp_path):
    c = Cache(tmp_path / "cache.db")
    for i in range(3):
        c.get_render(f"fp{i}", "v1", _bundle)
    assert c.render_bytes > 0
    c.trim(max_bytes=1)   # 强制淘汰
    assert c.render_bytes == 0   # renders 真被清（pair_count 恒 0 无验证力）
    assert c.pair_count == 0
    c.clear_all()


def test_move(tmp_path):
    p1, p2 = tmp_path / "a.db", tmp_path / "b" / "c.db"
    p2.parent.mkdir()
    c = Cache(p1)
    c.get_render("fp", "v1", _bundle)
    c.move_to(p2, migrate=True)
    assert p2.exists()
    c2 = Cache(p2)
    assert c2.get_render("fp", "v1", _bundle).tiles
