"""相似度渲染进程池 worker：无状态纯函数。

GIL 微基准实锤线程不可行（freetype-py 走 FT_Stream 回调，每次
load_glyph 的字形读取从 C 回调进 Python 抢 GIL；numpy 胶水是小数组，
ufunc 循环纳秒级 GIL 往返开销主导——纯 load_glyph 2 线程 25ms vs 串行
16ms 负加速），故并行走进程。worker 只做 render_bundle+compare，
零缓存零状态：pair 缓存读写、覆盖率剪枝、门槛判定全留主进程
（Cache 单连接契约原样保留，无 sqlite 并发）。附带收益：freetype 对
坏字体的原生崩溃从杀整个应用降级为杀一个 worker（BrokenProcessPool
由调用方回退串行）。"""

from __future__ import annotations

from pathlib import Path

from ass_font_substitute.core.similarity.cache import _decode
from ass_font_substitute.core.similarity.cache import _encode
from ass_font_substitute.core.similarity.metrics import compare
from ass_font_substitute.core.similarity.render import render_bundle


def score_chunk(target_blob: bytes, sample_text: str,
                jobs: list[tuple[str, int, tuple[float, ...]]]
                ) -> list[tuple | None]:
    """一批候选评分。jobs = [(path, face_index, var_axes)…]（var_axes =
    VF 实例设计坐标，静态行空元组），返回同序
    (result, blob)：result = (score, size_ratio, n_chars) | None（渲染
    失败/交集空，调用方跳过）；blob = _encode(候选 RenderBundle)（渲染
    成功才有，含交集空）——调用方写渲染缓存，worker 本身仍零缓存零状态
    （缓存写留主进程，Cache 单连接契约不变）。blob 编码 ~2ms，14KB/字体
    IPC 可忽略。target_blob = _encode(target RenderBundle)——每任务解码
    一次（~3ms）摊到批内逐字体。"""
    tb = _decode(target_blob)
    out: list[tuple | None] = []
    for path, face_index, var_axes in jobs:
        try:
            b = render_bundle(Path(path), face_index, sample_text, var_axes)
            r = compare(tb, b)
        except Exception:
            out.append(None)        # 候选渲染失败无法证明达标（spec §11：跳过）
            continue
        out.append((None if r is None else (r.score, r.size_ratio, r.n_chars),
                    _encode(b)))
    return out
