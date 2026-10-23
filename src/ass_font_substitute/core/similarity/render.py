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
    """裁墨迹 bbox，等比缩放到高度 TARGET_INK，居中 pad 到 TILE×TILE。"""
    cols = np.where(arr.any(axis=0))[0]
    rows = np.where(arr.any(axis=1))[0]
    crop = arr[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    h, w = crop.shape
    scale = TARGET_INK / h
    nw = max(1, round(w * scale))
    nh = TARGET_INK
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
    """
    face = freetype.Face(str(path), face_index)
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
            if ch in heights:
                heights.pop(ch)
            continue
        tiles[ch] = _fit(arr)
    return RenderBundle(tiles, median, frozenset(tiles))
