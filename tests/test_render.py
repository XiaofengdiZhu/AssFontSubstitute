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


def test_render_text_strip_basic(tmp_path):
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永中木", family="X體")
    s1 = render_text_strip(p, 0, "永")
    assert s1 is not None and s1.ndim == 2 and s1.dtype == np.uint8
    assert s1.max() > 0                             # 有墨迹
    s2 = render_text_strip(p, 0, "永中")
    assert s2.shape[1] > s1.shape[1]                # advance 累加定宽


def test_render_text_strip_repeats_not_deduped(tmp_path):
    # 丢字回归钉：重复字符必须各自渲染归位（RenderBundle 的 dict 去重
    # 曾让预览丢字，条带管线不允许）。工厂字体 advance = 500/1000×72 =
    # 36.0px 精确，钉关系式而非魔法阈值
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永", family="X體")
    one = render_text_strip(p, 0, "永")
    two = render_text_strip(p, 0, "永永")
    # 每多一个字符宽一块 advance：two-one ≈ one-左右留白(2×pad)
    assert abs((two.shape[1] - one.shape[1]) - (one.shape[1] - 8)) <= 1


def test_strip_helpers():
    # 画布超挂范围与度量兜底（纯函数，不依赖字体文件）
    from types import SimpleNamespace

    from ass_font_substitute.core.similarity.render import _baseline_metrics
    from ass_font_substitute.core.similarity.render import _strip_extent

    arr = np.zeros((10, 3), np.uint8)
    # 首字负 lsb（bitmap_left=-5）+ 末字墨迹超 advance 右缘
    slots = [(arr, -5, 0), (arr, 40, 0)]
    left, right = _strip_extent(slots, adv_right=20, pad=4)
    assert left == -5
    assert right == 40 + 3 + 4                      # 末字超挂 + pad，非 24
    # 度量正常：26.6 定点换算
    assert _baseline_metrics(SimpleNamespace(ascender=77 * 64,
                                             descender=-18 * 64), 72) == (77, 18)
    # 度量异常兜底
    assert _baseline_metrics(SimpleNamespace(ascender=0, descender=0), 72) == (72, 18)


def test_render_text_strip_space_and_missing(tmp_path):
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永中", family="X體")
    with_space = render_text_strip(p, 0, "永 中")
    no_space = render_text_strip(p, 0, "永中")
    assert with_space.shape[1] > no_space.shape[1]  # 空格出词距
    ideographic = render_text_strip(p, 0, "永　中")
    assert ideographic.shape[1] > no_space.shape[1]  # U+3000 同样出词距
    assert render_text_strip(p, 0, "XYZ") is None   # 全缺字
    assert render_text_strip(p, 0, "永X中") is not None   # 缺字跳过不炸
