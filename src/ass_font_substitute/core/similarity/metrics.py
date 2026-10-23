from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ass_font_substitute.core.similarity.render import RenderBundle


def _dwt_ll(a: np.ndarray, levels: int = 2) -> np.ndarray:
    cur = a.astype(np.float64)
    for _ in range(levels):
        h, w = cur.shape
        lh, lw = h // 2, w // 2
        x = cur[: lh * 2, : lw * 2]
        ll = (x[0::2, 0::2] + x[0::2, 1::2] + x[1::2, 0::2] + x[1::2, 1::2]) / 4
        cur = ll
    return cur


def phash(tile: np.ndarray) -> int:
    ll = _dwt_ll(_resize12(tile))
    flat = ll.flatten()
    med = np.median(flat)
    bits = 0
    for v in flat[:64]:
        bits = (bits << 1) | (1 if v > med else 0)
    return bits


def _resize12(tile: np.ndarray) -> np.ndarray:
    # 48 -> 32（3/2 缩小，两级 DWT 后 8×8）
    ys = (np.arange(32) * 48 // 32)
    return tile[ys][:, ys]


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    x, y = a.astype(np.float64), b.astype(np.float64)
    x, y = x - x.mean(), y - y.mean()
    denom = np.sqrt((x * x).sum() * (y * y).sum())
    if denom == 0:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float((x * y).sum() / denom)


# R13：权重 1.0 仅限 CJK 相关区段（部首/符号/假名/注音/统一汉字/兼容/扩展），
# 全角形式（U+FF00–）、彝文（U+A000–）、谚文音节等一律 0.3。
# 扩展 A（U+3400–4DBF）含于 U+2E80–9FFF，不单列；扩展 G 起第 3 平面极罕见，暂不含。
CJK_WEIGHT_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x9FFF),    # CJK 部首补充 ～ 统一汉字（含扩展 A、假名、注音、兼容）
    (0xF900, 0xFAFF),    # CJK 兼容表意文字
    (0x20000, 0x2EBEF),  # CJK 统一汉字扩展 B–F
)


def CHAR_WEIGHT(c: str) -> float:
    cp = ord(c)
    return 1.0 if any(lo <= cp <= hi for lo, hi in CJK_WEIGHT_RANGES) else 0.3


def _hamming_sim(a: int, b: int) -> float:
    return 1 - bin(a ^ b).count("1") / 64


def _resize12_batch(tiles: np.ndarray) -> np.ndarray:
    # (n,48,48) -> (n,32,32)（3/2 缩小，与单字版同索引）
    ys = np.arange(32) * 48 // 32
    return tiles[:, ys][:, :, ys]


def _phash_batch(tiles: np.ndarray) -> np.ndarray:
    """(n,48,48) float64 → (n,) uint64 pHash；与单字 phash 逐位一致。"""
    x = _resize12_batch(tiles)
    for _ in range(2):
        h, w = x.shape[1], x.shape[2]
        x = x[:, : h // 2 * 2, : w // 2 * 2]
        x = (x[:, 0::2, 0::2] + x[:, 0::2, 1::2]
             + x[:, 1::2, 0::2] + x[:, 1::2, 1::2]) / 4
    flat = x.reshape(len(tiles), 64)
    med = np.median(flat, axis=1, keepdims=True)
    bits = (flat > med).astype(np.uint64)
    powers = np.uint64(1) << np.arange(63, -1, -1, dtype=np.uint64)
    return bits @ powers


def _hamming_sims(pa: np.ndarray, pb: np.ndarray) -> np.ndarray:
    out = np.empty(len(pa))
    for i, (x, y) in enumerate(zip(pa, pb)):
        out[i] = 1 - bin(int(x) ^ int(y)).count("1") / 64
    return out


def _ncc_batch(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ac = a - a.mean(axis=(1, 2), keepdims=True)
    bc = b - b.mean(axis=(1, 2), keepdims=True)
    num = (ac * bc).sum(axis=(1, 2))
    denom = np.sqrt((ac * ac).sum(axis=(1, 2)) * (bc * bc).sum(axis=(1, 2)))
    out = np.zeros(len(a))
    ok = denom > 0
    out[ok] = num[ok] / denom[ok]
    # 常数 tile（denom=0）与单字 ncc 特例一致：完全相同=1.0，否则 0.0
    for i in np.flatnonzero(~ok):
        out[i] = 1.0 if np.array_equal(a[i], b[i]) else 0.0
    return out


@dataclass
class CompareResult:
    score: float
    size_ratio: float
    n_chars: int


def compare(a: RenderBundle, b: RenderBundle) -> CompareResult | None:
    """只按两字体可用字交集评分；交集空 = 无法比较（None）。

    全批向量化（整批堆叠一次算 pHash+NCC，不再逐字循环 numpy）。
    交集按排序序遍历：分数跨次调用/交换参数完全确定。
    """
    inter = sorted(set(a.tiles) & set(b.tiles))
    if not inter:
        return None
    ta = np.stack([a.tiles[c] for c in inter]).astype(np.float64)
    tb = np.stack([b.tiles[c] for c in inter]).astype(np.float64)
    hsim = _hamming_sims(_phash_batch(ta), _phash_batch(tb))
    nsim = np.maximum(_ncc_batch(ta, tb), 0.0)
    w = np.array([CHAR_WEIGHT(c) for c in inter])
    score = float((0.5 * hsim + 0.5 * nsim) @ w / w.sum())
    size_ratio = b.ink_median / a.ink_median if a.ink_median else 1.0
    return CompareResult(score, size_ratio, len(inter))
