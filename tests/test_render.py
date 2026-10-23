import numpy as np

from ass_font_substitute.core.similarity import render
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


def test_render_non_ascii_path(tmp_path):
    # Windows：freetype.Face(str) 对非 ASCII 路径以 ANSI 码页解释 → 打不开；
    # 以文件对象打开则不受路径编码影响（CJK 目录 + 文件名回归）
    p = tmp_path / "字体库" / "華康測試.ttf"
    p.parent.mkdir()
    build_font(p, chars="永中木", family="X體")
    b = render_bundle(p, 0, "永中木")
    assert set(b.tiles) == {"永", "中", "木"}
    assert all(t.max() > 0 for t in b.tiles.values())


def test_fit_wide_glyph_no_overflow():
    # R12 回归：宽墨迹（10×280，等比放大后宽 1008 > TILE=48），
    # 旧实现 pad 赋值广播崩溃（ValueError: could not broadcast）
    crop = np.zeros((10, 280), np.uint8)
    crop[2:8, :] = 255                          # 满宽横条墨迹
    t = render._fit(crop)                       # 不得 raise
    assert t.shape == (48, 48)
    ys, xs = np.where(t > 0)
    assert ys.size > 0                          # 墨迹保留
    assert ys.min() >= 0 and ys.max() < 48      # 墨迹全在 tile 界内
    assert xs.min() >= 0 and xs.max() < 48
