import numpy as np
import pytest

from ass_font_substitute.core.similarity import render
from ass_font_substitute.core.similarity.render import render_bundle
from tests.fonts import build_font
from tests.fonts import build_vf


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
    # 曾让预览丢字，条带管线不允许）。每字按统一格宽推进：全角 1em
    # @72px = 72px，two−one 应恰好多一块格宽
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永", family="X體")
    one = render_text_strip(p, 0, "永")
    two = render_text_strip(p, 0, "永永")
    assert abs((two.shape[1] - one.shape[1]) - 72) <= 2


def test_strip_helpers():
    # 画布超挂范围（纯函数，不依赖字体文件）
    from ass_font_substitute.core.similarity.render import _strip_extent

    arr = np.zeros((10, 3), np.uint8)
    # 首字负 lsb（bitmap_left=-5）+ 末字墨迹超 advance 右缘
    slots = [(arr, -5, 0), (arr, 40, 0)]
    left, right = _strip_extent(slots, adv_right=20, pad=4)
    assert left == -5
    assert right == 40 + 3 + 4                      # 末字超挂 + pad，非 24


def test_render_text_strip_space_and_missing(tmp_path):
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永中", family="X體")
    with_space = render_text_strip(p, 0, "永 中")
    no_space = render_text_strip(p, 0, "永中")
    assert with_space.shape[1] > no_space.shape[1]  # 空格出词距
    ideographic = render_text_strip(p, 0, "永　中")
    assert ideographic.shape[1] > no_space.shape[1]  # U+3000 同样出词距
    assert render_text_strip(p, 0, "XYZ") is None   # 全缺字（无墨迹可画）
    assert render_text_strip(p, 0, "永X中") is not None   # 缺字留空不炸


def test_render_text_strip_missing_leaves_gap(tmp_path):
    # 缺字留空占位前进（用户拍板 2026-10-29）：多字体条带对比需逐字对位，
    # 跳过会让后续字符错位。占位宽按统一格宽（全角 1em / 半角 0.5em），
    # @72px 全角占位 72、半角 36（±舍入）
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "f.ttf"
    build_font(p, chars="永中", family="X體")
    base = render_text_strip(p, 0, "永中")
    wide = render_text_strip(p, 0, "永缺中")       # 全角缺字占 1em
    latin = render_text_strip(p, 0, "永X中")       # 半角缺字占 0.5em
    assert abs((wide.shape[1] - base.shape[1]) - 72) <= 2
    assert abs((latin.shape[1] - base.shape[1]) - 36) <= 2


def test_strip_cell_width_uniform_across_fonts(tmp_path):
    # 统一格宽 + 统一画布（用户拍板 2026-10-29）：各字体自身 advance、
    # hhea 度量、墨迹大小不同都不影响条带尺寸与逐字位置——否则
    # PreviewBar 按显示高缩放时每行比例不同，对位漂移（真机宋/黑实锤）。
    # 乙字体 advance 800/300、度量 1500/-500、墨迹缩到 0.7，同文本条带
    # 必须同尺寸（墨迹内容自然不同，只钉几何）
    from fontTools.ttLib import TTFont

    from ass_font_substitute.core.similarity.render import render_text_strip

    pa = tmp_path / "a.ttf"
    pb = tmp_path / "b.ttf"
    build_font(pa, chars="永中", family="甲")
    build_font(pb, chars="永中", family="乙", scale=0.7)
    f = TTFont(pb)
    f["hmtx"]["g0"] = (800, 0)                     # 永的 advance 拉宽
    f["hmtx"]["g1"] = (300, 0)                     # 中的 advance 压窄
    f["hhea"].ascent, f["hhea"].descent = 1500, -500
    f["OS/2"].sTypoAscender, f["OS/2"].sTypoDescender = 1500, -500
    f.save(pb)
    sa = render_text_strip(pa, 0, "永中永")
    sb = render_text_strip(pb, 0, "永中永")
    assert sa.shape == sb.shape
    assert sa.any() and sb.any()


def test_strip_natural_advance_mode(tmp_path):
    # mono_cells=False（强制等宽开关关，默认）：走字体自身 advance，
    # 宽度随字体变（乙的永 advance 800 > 甲 500）；同参数 mono 版仍等宽
    from fontTools.ttLib import TTFont

    from ass_font_substitute.core.similarity.render import render_text_strip

    pa = tmp_path / "a.ttf"
    pb = tmp_path / "b.ttf"
    build_font(pa, chars="永", family="甲")
    build_font(pb, chars="永", family="乙")
    f = TTFont(pb)
    f["hmtx"]["g0"] = (800, 0)
    f.save(pb)
    na = render_text_strip(pa, 0, "永", mono_cells=False)
    nb = render_text_strip(pb, 0, "永", mono_cells=False)
    assert nb.shape[1] > na.shape[1]               # 自然字距：宽 advance 更宽
    ma = render_text_strip(pa, 0, "永", mono_cells=True)
    mb = render_text_strip(pb, 0, "永", mono_cells=True)
    assert ma.shape == mb.shape                    # 等宽口径不受 advance 影响


def _save_format2_font(path, uni_text: str) -> None:
    """把 build_font 产物的 cmap 换成单 (3,4) Big5 format 2 子表：模拟
    文鼎系老繁体字库（无 Unicode 子表）。码点 = 文本按 cp950 编码。"""
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_2

    from tests.fonts import build_font

    build_font(path, chars=uni_text)
    f = TTFont(path)
    sub = cmap_format_2(2)
    sub.platformID, sub.platEncID, sub.language = 3, 4, 0
    sub.cmap = {}
    for ch in uni_text:
        b = ch.encode("cp950")
        code = b[0] if len(b) == 1 else (b[0] << 8) | b[1]
        sub.cmap[code] = f["cmap"].getBestCmap()[ord(ch)]
    f["cmap"].tables = [sub]
    f.save(path)


def test_render_legacy_big5_cmap(tmp_path):
    # 无 Unicode charmap 的 Big5 format 2 字体：FreeType 查 Unicode 码点
    # 全落空（文鼎系「（无可渲染字形）」根因），渲染走 码页转码 + 自解析
    # cmap2 拿字形索引

    from ass_font_substitute.core.similarity.render import render_bundle
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "big5.ttf"
    _save_format2_font(p, "永字A")
    strip = render_text_strip(p, 0, "永字A")
    assert strip is not None and strip.shape[0] > 0
    b = render_bundle(p, 0, "永字A")
    assert set(b.tiles) == {"永", "字", "A"}
    assert (b.tiles["永"] > 0).any()          # 真墨迹非全零


def test_render_legacy_missing_char_skipped(tmp_path):
    # legacy 模式缺字（码页解不出/cmap 无条目）留空占位不炸；全缺返回 None
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "big5.ttf"
    _save_format2_font(p, "永字A")
    assert render_text_strip(p, 0, "永?") is not None     # ? 留空占位
    assert render_text_strip(p, 0, "€¥") is None          # 全缺


def _cmap2_blob(bad_length: bool, truncate: int = 0) -> bytes:
    """手拼最小 format 2 cmap 表（fontTools 编译器造不出的形态）：
    子表1（delta=-1）映射 0xA5C3→1；子表0（delta=-2）映射 0x0000→3、
    0x0002→5；子表2 ro=0 全缺字。glyphIdArray 统一垫在子表区之后。"""
    import struct as _s

    keys = [0] * 256
    keys[0] = 0          # 单字节段 + hi=0x00 → 子表0
    keys[0xA5] = 8       # hi=0xA5 → 子表1
    keys[0xA6] = 16      # hi=0xA6 → 子表2（ro=0）
    GI1, GI0 = 542, 546  # 子表区止于 542；gi1 占 542、gi0 占 546..553
    body = _s.pack(">HHH", 2, 100 if bad_length else 0, 0)   # 谎报 length
    body += _s.pack(">256H", *keys)
    body += _s.pack(">HHhH", 0x00, 4, -2, GI0 - (518 + 6))   # 子表0 @518
    body += _s.pack(">HHhH", 0xC3, 1, -1, GI1 - (526 + 6))   # 子表1 @526
    body += _s.pack(">HHhH", 0x40, 8, 0, 0)                  # 子表2 @534 ro=0
    assert len(body) == 542
    out = bytearray(body)
    out += _s.pack(">H", 2)                      # 0xA5C3 → (2-1)&0xFFFF = 1
    out += b"\x00\x00"                          # 垫到 GI0=546
    out += _s.pack(">4H", 5, 0, 7, 0)            # 0x0000→3、0x0002→5
    return bytes(out[:len(out) - truncate] if truncate else out)


def test_parse_cmap2_handbuilt_blob(tmp_path):
    # fontTools 编译器造不出的三条真实文鼎路径：负 idDelta、ro=0、谎报
    # length。手拼 blob 逐条钉死
    import io as _io

    from ass_font_substitute.core.similarity.render import _parse_cmap2

    for bad_length in (True, False):
        fh = _io.BytesIO(_cmap2_blob(bad_length))
        m = _parse_cmap2(fh, 0)
        assert m[0xA5C3] == 1                    # 负 idDelta 掩码等价
        assert m[0x0002] == 5                    # delta -2
        assert 0xA640 not in m                   # ro=0 段全缺字
        assert 0x41 not in m                     # 子表0 cnt=4 外的码
        # keys[hi]=0 → 全部高位共享子表0：spec 行为，每个高位都映射
        # X000/X002/X003；0xA5C3 是子表1 唯一条目
        # 0xA5 走子表1、0xA6 走 ro=0 子表2，不落子表0
        assert set(m) == ({(hi << 8) | lo for hi in range(256)
                           if hi not in (0xA5, 0xA6)
                           for lo in (0, 2)} | {0xA5C3})


def test_encoder_for_truncated_falls_back(tmp_path):
    # 截断损坏：_encoder_for 兜成 Unicode 模式（codec=None），不炸
    import io as _io

    from ass_font_substitute.core.similarity.render import _encoder_for

    fh = _io.BytesIO(_cmap2_blob(False, truncate=8))
    enc = _encoder_for(None, fh, 0)
    assert enc.codec is None and enc.cmap2 == {}


def test_second_pass_px_anchor_and_floor():
    # 量尺 48px：median 墨迹锚 ANCHOR_INK=43；锚后 ≤48 全踩下限 48
    # （pass1 位图直接复用免重渲）；median 偏小才放大字号防 _fit 上采样
    from ass_font_substitute.core.similarity.render import RULER_PX
    from ass_font_substitute.core.similarity.render import _second_pass_px

    assert RULER_PX == 48
    assert _second_pass_px(43.0) == 48          # 锚点正中：免重渲
    assert _second_pass_px(43.2) == 48          # round 后 48
    assert _second_pass_px(47.0) == 48          # 大墨迹踩下限
    assert _second_pass_px(44.0) == 48          # 48*43/44≈46.9 → floor 48
    assert _second_pass_px(28.0) == 74          # 小墨迹重渲放大 48*43/28≈73.7
    assert _second_pass_px(0.0) == 48           # 空 bundle 兜底


def test_render_bundle_ruler_reuse(monkeypatch, tmp_path):
    # 量尺 48px + 锚 43 + 下限：墨迹 ≥43px 的字体 pass2 免重渲（pass1 位图
    # 直接 _fit）；ink_median 是 48px 口径（size_ratio 尺度不变量）。
    # 工厂 box scale=1.5 → 0.9em 墨迹 → 48px 下 43.2px ≥ 锚点 → 复用路径
    calls = []
    real = render._render_char

    def counting(face, enc, ch, px):
        calls.append(px)
        return real(face, enc, ch, px)

    monkeypatch.setattr(render, "_render_char", counting)
    p = tmp_path / "tall.ttf"
    build_font(p, chars="永中木", scale=1.5)
    b = render_bundle(p, 0, "永中木")
    assert set(b.tiles) == {"永", "中", "木"}
    assert len(calls) == 3 and calls == [48, 48, 48]   # 只渲一遍
    assert 42 <= b.ink_median <= 45                     # 48px 量尺口径


def test_render_bundle_small_ink_rerenders(monkeypatch, tmp_path):
    # 墨迹 < 锚点（默认 box 0.6em → 48px 下 28.8px）：px2=72 重渲，位图不复用
    calls = []
    real = render._render_char

    def counting(face, enc, ch, px):
        calls.append(px)
        return real(face, enc, ch, px)

    monkeypatch.setattr(render, "_render_char", counting)
    p = tmp_path / "low.ttf"
    build_font(p, chars="永中木")
    b = render_bundle(p, 0, "永中木")
    assert set(b.tiles) == {"永", "中", "木"}
    assert len(calls) == 6                       # pass1 恰 3 次，后 3 次是 pass2
    # 第二遍按锚点公式放大重渲（hinting 使 median 29 → 71px，不硬编）
    px2 = calls[3]
    assert px2 > 48 and calls.count(px2) == 3
    assert abs(px2 - 48 * 43 / b.ink_median) <= 1
    assert 27 <= b.ink_median <= 31              # 量尺仍是 48px 口径


def test_render_bundle_with_var_axes(tmp_path):
    # VF 实例态渲染管道：带设计坐标渲染不炸、产出结构有效位图
    # （无 gvar 轮廓不随轴变——此处只锁管道口径，spec §6）
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[{"name": "Black", "location": {"wght": 900}}])
    b1 = render_bundle(p, 0, "永")                     # 默认态
    b2 = render_bundle(p, 0, "永", var_axes=(900.0,))  # 实例态
    assert len(b2.tiles) == len(b1.tiles) == 1
    assert b2.available == frozenset("永")


def test_render_text_strip_with_var_axes(tmp_path):
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900}],
             instances=[])
    s = render.render_text_strip(p, 0, "永", var_axes=(900.0,))
    assert s is not None and s.any()


def test_render_partial_axes_rejected(tmp_path):
    # freetype 对部分长度坐标静默只设首轴（审查实测）——调用方须传全长；
    # 本层对长度不等于轴数的坐标直接抛错防呆
    p = tmp_path / "vf.ttf"
    build_vf(p, "永", family="变化體",
             axes=[{"name": "Weight", "tag": "wght",
                    "min": 300, "default": 400, "max": 900},
                   {"name": "Width", "tag": "wdth",
                    "min": 50, "default": 100, "max": 200}],
             instances=[])
    with pytest.raises(ValueError):
        render_bundle(p, 0, "永", var_axes=(900.0,))   # 2 轴只给 1 值
