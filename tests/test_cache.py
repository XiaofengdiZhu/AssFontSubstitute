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


def test_algo_ver_isolates_entries(tmp_path):
    # 算法版本入键：ver 变更后旧口径持久缓存不命中（静默过期根因）
    c = Cache(tmp_path / "cache.db")
    calls = []
    c.get_render("fp", "v1", lambda: calls.append(1) or _bundle())
    assert len(calls) == 1
    c.ALGO_VER = "2"                       # 模拟算法升级
    c.get_render("fp", "v1", lambda: calls.append(1) or _bundle())
    assert len(calls) == 2                 # 旧口径不命中，重算


def test_pairs_lru_trim(tmp_path):
    c = Cache(tmp_path / "cache.db")
    r = CompareResult(0.5, 1.0, 4)
    for i in range(6):
        c.put_pair(f"a{i}", f"b{i}", "v1", r)
    c._trim_pairs(max_rows=3)
    assert c.pair_count == 3
    # 早写的被淘汰（used_at 递增序，同秒全等时删任意——只验数量与存活数）
    assert c.pair_count == 3


def test_old_schema_pairs_dropped(tmp_path):
    # 旧库 pairs 无 used_at 列：打开即弃表重建，不炸
    import sqlite3
    p = tmp_path / "cache.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE pairs(fpa TEXT, fpb TEXT, text_ver TEXT, "
                 "score REAL, size_ratio REAL, n_chars INT, "
                 "PRIMARY KEY(fpa, fpb, text_ver))")
    conn.execute("INSERT INTO pairs VALUES ('a','b','v1',0.5,1.0,4)")
    conn.commit()
    conn.close()
    c = Cache(p)
    assert c.pair_count == 0               # 旧行弃用（键前缀也不兼容）
    c.put_pair("a", "b", "v1", CompareResult(0.5, 1.0, 4))
    assert c.get_pair("a", "b", "v1").score == 0.5


def test_move(tmp_path):
    p1, p2 = tmp_path / "a.db", tmp_path / "b" / "c.db"
    p2.parent.mkdir()
    c = Cache(p1)
    c.get_render("fp", "v1", _bundle)
    c.move_to(p2, migrate=True)
    assert p2.exists()
    c2 = Cache(p2)
    assert c2.get_render("fp", "v1", _bundle).tiles
