import numpy as np

from ass_font_substitute.core.similarity.metrics import CHAR_WEIGHT, compare, ncc, phash
from ass_font_substitute.core.similarity.render import RenderBundle, render_bundle
from tests.fonts import build_font


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


def test_char_weight_cjk_ranges():
    # R13：权重 1.0 限 CJK 区段，全角形式/彝文等一律 0.3
    assert CHAR_WEIGHT("永") == 1.0            # U+6C38 统一汉字
    assert CHAR_WEIGHT("\U00020000") == 1.0    # U+20000 扩展 B
    assert CHAR_WEIGHT("Ａ") == 0.3            # U+FF21 全角形式
    assert CHAR_WEIGHT("ꀀ") == 0.3            # U+A000 彝文音节
    assert CHAR_WEIGHT("A") == 0.3             # ASCII


def test_engine_discriminates_shapes(tmp_path):
    # 正向验证：同形字体互比得分，须高于方块 vs 圆形（引擎能区分字形，而非只会自比）
    chars = "永中木"
    build_font(tmp_path / "box1.ttf", chars=chars, family="Box體一")
    build_font(tmp_path / "box2.ttf", chars=chars, family="Box體二")
    build_font(tmp_path / "circle.ttf", chars=chars, family="圓體", shape="circle")
    box1 = render_bundle(tmp_path / "box1.ttf", 0, chars)
    box2 = render_bundle(tmp_path / "box2.ttf", 0, chars)
    circ = render_bundle(tmp_path / "circle.ttf", 0, chars)
    assert all(t.max() > 0 for t in circ.tiles.values())   # 圆形有墨迹
    same = compare(box1, box2)
    diff = compare(box1, circ)
    assert same is not None and diff is not None
    assert same.score > diff.score
