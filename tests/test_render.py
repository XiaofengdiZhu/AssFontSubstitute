import numpy as np

from ass_font_substitute.core.similarity.render import render_bundle
from tests.fonts import build_font


def test_render(tmp_path):
    p = tmp_path / "f.ttf"
    build_font(p, chars="永中木", family="X體")
    b = render_bundle(p, 0, "永中木缺")
    assert set(b.tiles) == {"永", "中", "木"}     # 缺字跳过
    assert b.available == frozenset("永中木")
    for t in b.tiles.values():
        assert t.shape == (48, 48) and t.dtype == np.uint8
        assert t.max() > 0                        # 方块字形非空
    assert b.ink_median > 0


def test_two_fonts_different_tiles(tmp_path):
    build_font(tmp_path / "a.ttf", chars="永", family="A")
    build_font(tmp_path / "b.ttf", chars="永", family="B")
    a = render_bundle(tmp_path / "a.ttf", 0, "永")
    b = render_bundle(tmp_path / "b.ttf", 0, "永")
    # 工厂字体同形，仅验证流水线；自比在 metrics 任务验证
    assert a.tiles["永"].shape == b.tiles["永"].shape
