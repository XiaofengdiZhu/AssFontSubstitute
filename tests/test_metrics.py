import numpy as np

from ass_font_substitute.core.similarity.metrics import compare, ncc, phash
from ass_font_substitute.core.similarity.render import RenderBundle


def _bundle(tiles, ink=40.0):
    return RenderBundle(dict(tiles), ink, frozenset(tiles))


def test_phash_identical():
    rng = np.random.default_rng(0)
    t = rng.integers(0, 255, (48, 48)).astype(np.uint8)
    assert phash(t) == phash(t)
    assert phash(t) != phash(np.roll(t, 12, axis=1))


def test_ncc_self():
    t = np.arange(48 * 48, dtype=np.float64).reshape(48, 48)
    assert abs(ncc(t, t) - 1.0) < 1e-9


def test_compare_self_is_one():
    t = np.full((48, 48), 128, np.uint8)
    t[10:38, 10:38] = 200
    b = _bundle({"永": t, "8": t})
    r = compare(b, b)
    assert r.score > 0.99
    assert r.size_ratio == 1.0 and r.n_chars == 2


def test_compare_intersection_only():
    a = _bundle({"永": np.zeros((48, 48), np.uint8)})
    b = _bundle({"中": np.zeros((48, 48), np.uint8)})
    assert compare(a, b) is None  # 无交集 = 无法比较
