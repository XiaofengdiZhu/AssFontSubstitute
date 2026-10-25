from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import freetype
import numpy as np

TILE = 48
TARGET_INK = 36
BASE_PX = 96


@dataclass
class RenderBundle:
    tiles: dict[str, np.ndarray]
    ink_median: float
    available: frozenset[str]


def _render_char(face, ch: str, px: int) -> np.ndarray | None:
    face.set_pixel_sizes(0, px)
    try:
        face.load_char(ch, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_NORMAL)
    except Exception:
        return None
    bm = face.glyph.bitmap
    if bm.width == 0 or bm.rows == 0:
        return None
    arr = np.frombuffer(bytes(bm.buffer), dtype=np.uint8).reshape(bm.rows, bm.width)
    if not arr.any():
        return None
    return arr


def _ink_height(arr: np.ndarray) -> int:
    rows = np.where(arr.any(axis=1))[0]
    return int(rows[-1] - rows[0] + 1)


def _fit(arr: np.ndarray) -> np.ndarray:
    """裁墨迹 bbox，等比缩放到高度 TARGET_INK，居中 pad 到 TILE×TILE。

    宽字形（等比后宽/高超 TILE）整体再等比钳制到 TILE×TILE 内：
    绝不溢出、绝不变形，仍居中。
    """
    cols = np.where(arr.any(axis=0))[0]
    rows = np.where(arr.any(axis=1))[0]
    crop = arr[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    h, w = crop.shape
    scale = TARGET_INK / h
    nw = max(1, round(w * scale))
    nh = TARGET_INK
    if nw > TILE or nh > TILE:  # 宽字形钳制（R12）：等比缩回 TILE 界内
        k = min(TILE / nw, TILE / nh)
        nw = max(1, round(nw * k))
        nh = max(1, round(nh * k))
    # 最近邻缩放（numpy 索引）
    ys = (np.arange(nh) * h // nh)
    xs = (np.arange(nw) * w // nw)
    small = crop[ys][:, xs]
    out = np.zeros((TILE, TILE), dtype=np.uint8)
    y0 = (TILE - nh) // 2
    x0 = (TILE - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


def render_bundle(path: Path, face_index: int, chars: str) -> RenderBundle:
    """两遍渲染：96px 测墨迹高度中位数 → 按 TARGET_INK/中位数 定尺寸重渲。

    缺字（cmap 无映射）直接跳过，绝不渲染 .notdef tofu。
    以文件对象打开：Windows 上 FreeType 按 ANSI 码页解释路径字符串，
    非 ASCII 路径会打不开；传文件对象则由 Python 负责读文件，不受编码影响。
    """
    with open(path, "rb") as fh:
        face = freetype.Face(fh, face_index)
        # 反 tofu：cmap 缺字先剔除（F3），避免 load_char 落到 .notdef 方块
        wanted = [ch for ch in chars if face.get_char_index(ord(ch)) != 0]
        # pass 1: 基准尺寸墨迹高度中位数
        heights = {}
        for ch in wanted:
            arr = _render_char(face, ch, BASE_PX)
            if arr is not None:
                heights[ch] = _ink_height(arr)
        if not heights:
            return RenderBundle({}, 0.0, frozenset())
        median = float(np.median(sorted(heights.values())))
        tiles: dict[str, np.ndarray] = {}
        for ch in wanted:
            px = max(8, round(BASE_PX * TARGET_INK / median)) if median else BASE_PX
            arr = _render_char(face, ch, px)
            if arr is None:
                continue
            tiles[ch] = _fit(arr)
        return RenderBundle(tiles, median, frozenset(tiles))


STRIP_PX = 72          # 预览条带渲染尺寸：显示高 28 逻辑 px 的 ~2.5x 超采样
_SPACE_ADV = 0.28      # cmap 无空格字形时的空白 advance 占 px 比例（兜底）


def _baseline_metrics(metrics, px: int) -> tuple[int, int]:
    """ascender/descender（26.6 定点 → px）。度量异常（怪字体 asc+desc 近 0）
    兜底成 px/px÷4，保证条带有可用行高。"""
    asc = max(1, round(metrics.ascender / 64))
    desc = max(1, round(-metrics.descender / 64))
    if asc + desc < px // 2:
        return px, px // 4
    return asc, desc


def _strip_extent(slots: list, adv_right: float, pad: int) -> tuple[int, int]:
    """画布横向范围 [left, right)。含超挂（overhang）：斜体 f/j 墨迹可超出
    advance 远超 pad（实测 +14px）、首字可负 lsb——按实际墨迹范围开画布，
    纯 advance 定宽会裁掉末字右缘/首字左缘。"""
    left = min([0] + [gx for _, gx, _ in slots])
    right = max([round(adv_right) + pad]
                + [gx + arr.shape[1] + pad for arr, gx, _ in slots])
    return left, right


def render_text_strip(path: Path, face_index: int, text: str,
                      px: int = STRIP_PX) -> np.ndarray | None:
    """预览条带：真实排版渲染（统一 px 尺寸 + baseline + 字符 advance），
    字形比例与字距保真——区别于相似度管线 render_bundle 的等高归一 tile
    （那套为比对设计，逐字裁缩到 48px，放大即糊）。

    缺字跳过（不前进），空白（含 U+3000/NBSP）cmap 有映射走真实 advance、
    无映射按比例兜底；全缺字返回 None。重复字符各自渲染归位（RenderBundle
    的 dict 会去重，预览不能丢字）。单个坏字形跳过不毁整条。
    以文件对象打开，理由同 render_bundle。
    """
    with open(path, "rb") as fh:
        face = freetype.Face(fh, face_index)
        face.set_pixel_sizes(0, px)
        asc, desc = _baseline_metrics(face.size, px)
        pad = 4
        h = asc + desc + pad * 2
        slots: list[tuple[np.ndarray, int, int]] = []
        x = float(pad)
        for ch in text:
            if face.get_char_index(ord(ch)) == 0:
                # 缺字跳过不前进；cmap 也没有的空白字符按比例兜底出词距
                if ch.isspace():
                    x += px * _SPACE_ADV
                continue
            try:
                face.load_char(ch, freetype.FT_LOAD_RENDER
                               | freetype.FT_LOAD_TARGET_NORMAL)
            except Exception:
                continue                      # 坏字形：跳过不前进，不毁整条
            g = face.glyph
            if g.bitmap.width and g.bitmap.rows:
                arr = np.frombuffer(bytes(g.bitmap.buffer), dtype=np.uint8)
                arr = arr.reshape(g.bitmap.rows, g.bitmap.width)
                slots.append((arr, round(x) + g.bitmap_left,
                              pad + asc - g.bitmap_top))
            x += g.advance.x / 64
        if not slots:
            return None
        left, right = _strip_extent(slots, x, pad)
        canvas = np.zeros((h, right - left), dtype=np.uint8)
        for arr, gx, gy in slots:             # 超界裁边 + maximum 防叠字互抹
            gx -= left
            y0, x0 = max(0, gy), max(0, gx)
            y1 = min(h, gy + arr.shape[0])
            x1 = min(canvas.shape[1], gx + arr.shape[1])
            if y0 < y1 and x0 < x1:
                region = canvas[y0:y1, x0:x1]
                np.maximum(region, arr[y0 - gy:y1 - gy, x0 - gx:x1 - gx],
                           out=region)
        # 裁上下空白到墨迹 bbox（留 2px 余量）：显示按条高缩放，不裁则
        # ascender/descender 空白让字形显示偏小；条内相对比例不受影响
        rows = np.where(canvas.any(axis=1))[0]
        canvas = canvas[max(0, rows[0] - 2):rows[-1] + 3, :]
        return canvas
