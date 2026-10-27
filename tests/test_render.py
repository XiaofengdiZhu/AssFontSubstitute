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
    # legacy 模式缺字（码页解不出/cmap 无条目）跳过不炸；全缺返回 None
    from ass_font_substitute.core.similarity.render import render_text_strip

    p = tmp_path / "big5.ttf"
    _save_format2_font(p, "永字A")
    assert render_text_strip(p, 0, "永?") is not None     # ? 兜底空白
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
