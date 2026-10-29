from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.rewrite import rewrite_file
from ass_font_substitute.core.coverage.matcher import classify_usage
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import scan_dir
from ass_font_substitute.core.fontindex.store import update_index
from ass_font_substitute.core.models import FontUsage
from ass_font_substitute.core.similarity.render import render_text_strip
from ass_font_substitute.core.similarity.search import FontSearcher


@dataclass
class DirStatus:
    path: str
    state: str                  # ok / stale / missing / offline
    added: int = 0
    changed: int = 0
    indexed_files: int = 0
    total: int = 0              # 盘上全部支持后缀文件数（含坏文件）
    bad: int = 0                # 索引坏文件表行数


def probe_dir_status(dir_path: str) -> DirStatus:
    """只读探测（URI mode=ro，绝不创建/写库）。missing=目录不存在；
    offline=库存在但打不开（只读盘/权限/损坏）；stale=有未入索引文件。"""
    d = Path(dir_path)
    if not d.is_dir():
        return DirStatus(dir_path, "missing")
    dbf = d / "fontindex.db"
    if not dbf.exists():
        disk = scan_dir(d)
        st = DirStatus(dir_path, "stale")
        st.total = st.added = len(disk)
        return st
    conn = None
    try:
        conn = sqlite3.connect(dbf.resolve().as_uri() + "?mode=ro", uri=True)
        known = {r[0]: (r[1], r[2]) for r in
                 conn.execute("SELECT rel_path, size, mtime_ns FROM files")}
        bad = {r[0]: (r[1], r[2]) for r in
               conn.execute("SELECT rel_path, size, mtime_ns FROM bad")}
    except sqlite3.Error:
        return DirStatus(dir_path, "offline")
    finally:
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
    st = DirStatus(dir_path, "ok", indexed_files=len(known))
    st.bad = len(bad)
    disk = scan_dir(d)
    st.total = len(disk)
    for p in disk:
        rel = p.relative_to(d).as_posix()
        try:
            s = p.stat()
        except OSError:
            continue   # 扫描与 stat 之间被删/盘掉线：跳过，别整任务失败卡「检查中」
        cur = (s.st_size, s.st_mtime_ns)
        if rel in known:
            if known[rel] != cur:
                st.changed += 1
        elif rel in bad:
            if bad[rel] != cur:
                st.changed += 1
        else:
            st.added += 1
    if st.added or st.changed:
        st.state = "stale"
    return st


@dataclass
class LibrarySnapshot:
    lib: LibraryIndex
    offline_dirs: list[str] = field(default_factory=list)


def load_library(dirs: list[str]) -> LibrarySnapshot:
    """聚合打开库；打不开的目录（离线/只读）剔除并报告，不拖垮整体。
    尚无索引的目录建空库骨架，实体为 0；建索引进「更新全部索引」流程。"""
    usable, offline = [], []
    for d in dirs:
        try:
            open_db(Path(d) / "fontindex.db").close()
            usable.append(Path(d))
        except sqlite3.Error:
            offline.append(d)
    return LibrarySnapshot(lib=LibraryIndex.open(usable), offline_dirs=offline)


def lib_key(dirs) -> tuple:
    """聚合库快照失效键：目录串 + 各目录 fontindex.db 的 st_mtime_ns
    （缺库为 None）。IndexWorker 重建/更新索引后 mtime 变化 → 键变，
    ensure_library 弃旧快照，聚合库即时反映新字体。"""
    out = []
    for d in dirs:
        try:
            mtime = (Path(d) / "fontindex.db").stat().st_mtime_ns
        except OSError:                     # 缺库/不可达 → None
            mtime = None
        out.append((d, mtime))
    return tuple(out)


def ensure_library(ctx, dirs: list[str]) -> LibrarySnapshot:
    """聚合库按需（重）建：目录集与各索引 mtime 不变则复用 ctx 上缓存；
    目录增删或索引更新（fontindex.db mtime 变）都触发重建。"""
    key = lib_key(dirs)
    if getattr(ctx, "snapshot", None) is not None and getattr(ctx, "lib_key", None) == key:
        return ctx.snapshot
    snap = load_library(dirs)
    # 缓存键取装载后的实况：load_library 会为尚无索引的目录建空库骨架
    # （fontindex.db 由无到有），骨架创建本身不算索引更新
    ctx.snapshot, ctx.lib, ctx.lib_key = snap, snap.lib, lib_key(dirs)
    return snap


def update_dir(dir_path: str, progress=None, max_threads: int = 1,
               force: bool = False):
    conn = open_db(Path(dir_path) / "fontindex.db")
    try:
        return update_index(Path(dir_path), conn, progress=progress,
                            max_threads=max_threads, force=force)
    finally:
        conn.close()


def read_failures(ctx) -> dict:
    """各目录 bad 清单 + 跨目录跳过（重复版本）。mode=ro 只读。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for d in ctx.settings.get("font_dirs"):
        dbf = Path(d) / "fontindex.db"
        if not dbf.exists():
            continue
        try:
            conn = sqlite3.connect(dbf.resolve().as_uri() + "?mode=ro", uri=True)
            rows = conn.execute("SELECT rel_path, reason FROM bad").fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        if rows:
            out[d] = rows
    skipped = ctx.snapshot.lib.skipped() if getattr(ctx, "snapshot", None) else []
    return {"bad": out, "skipped": skipped}


def retry_bad(ctx, dir_path: str, rel: str):
    """手动重试：移出 bad 清单后增量更新（size/mtime 未变的也会强制重解析）。"""
    conn = open_db(Path(dir_path) / "fontindex.db")
    conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
    conn.commit()
    conn.close()
    return update_dir(dir_path)


@dataclass
class FileParse:
    path: str
    error: str | None
    usages: list
    unknown_styles: list[str]
    invalid_styles: list[str]
    checked: bool = True

    @property
    def font_count(self) -> int:
        return len(self.usages)


def parse_subtitle(path: str) -> FileParse:
    try:
        r = extract_usages(Path(path))
    except Exception as e:
        return FileParse(path, f"{type(e).__name__}: {e}", [], [], [])
    if r.parse_error:
        return FileParse(path, r.parse_error, [], [], [])
    return FileParse(path, None, r.usages, r.unknown_styles, r.invalid_styles)


def classify_one(lib: LibraryIndex, usage: FontUsage, threshold: float,
                 direction: str):
    return classify_usage(usage, lib, threshold=threshold, direction=direction)


def make_searcher(ctx, target, chars, threshold: float, n_pause: int,
                  min_sim: float, size_range: tuple[float, float],
                  direction: str) -> FontSearcher:
    return FontSearcher(
        target=target, chars=chars, lib=ctx.lib, cache=ctx.cache,
        sample_text=ctx.settings.get("sample_text"),
        cov_threshold=threshold, n_pause=n_pause, min_sim=min_sim,
        size_range=size_range, direction=direction)


def pull_chunk(searcher: FontSearcher, cancel: dict | None = None):
    """推进**一片**（至多 EVAL_SLICE 次对比 / SLICE_SECONDS 秒，先到为
    准）即回执：state ∈ running/paused/exhausted/target_error/cancelled。
    切忌在此攒批（凑满 N 个命中才回）——稀疏命中时一个任务吞掉整池，
    UI 进度计数冻结整个任务期，手动暂停也得等任务尾才生效，且攒下的
    命中在暂停瞬间一次性上屏。命中语义（暂停累计 hit_count、
    target_error 懒解析一次）全部由 searcher 承担，此层不解读。"""
    if cancel is not None and cancel.get("cancelled"):
        return [], "cancelled"
    cands = searcher.next_slice()
    if searcher.target_error or searcher.paused or searcher.exhausted:
        return cands, ("target_error" if searcher.target_error
                       else "paused" if searcher.paused else "exhausted")
    return cands, "running"


def score_pick(ctx, target, entity):
    """手动选择字体评分：相似率+相对大小（vs target，经 core score_pair
    走 pair 缓存，口径与 FontSearcher 单一来源）。target=None（库中没有
    卡）或渲染失败/交集空 → None。覆盖率由 UI 侧现算。返回 (entity, sim,
    size)。"""
    from ass_font_substitute.core.similarity.search import score_pair

    if target is None:
        return entity, None, None
    try:
        r = score_pair(target, entity, ctx.cache,
                       ctx.settings.get("sample_text"))
    except Exception:
        return entity, None, None
    if r is None:
        return entity, None, None
    return entity, r.score, r.size_ratio


def render_preview(entity, text: str, mono_cells: bool):
    """预览渲染条：真实排版高分辨率条带（render_text_strip），不走全局
    相似度缓存（键按 text_version），GUI 自带（指纹，文字，mono）LRU。
    mono_cells 强制等宽（全角 1em/半角 0.5em 统一格宽）逐字对位；缺字
    一律留空占位。mono 不给默认：core render_text_strip 默认 True（比对
    管线口径）与 GUI 开关默认 False 相反，显式传参防两口径漂移。"""
    return render_text_strip(Path(entity.dir_path) / entity.rel_path,
                             entity.face_index, text, mono_cells=mono_cells)


@dataclass
class ApplyFileResult:
    path: str
    out_path: str | None = None
    changed_lines: int = 0
    error: str | None = None


def apply_to_file(path: str, mapping: dict[str, str], suffix: str,
                  overwrite: bool) -> ApplyFileResult:
    src = Path(path)
    try:
        if overwrite:
            r = rewrite_file(src, mapping, overwrite_src=True)
            return ApplyFileResult(path, str(r.out_path), len(r.changed_lines))
        out = src.with_name(src.stem + suffix)
        r = rewrite_file(src, mapping, out=out)
        return ApplyFileResult(path, str(out), len(r.changed_lines))
    except Exception as e:
        return ApplyFileResult(path, error=f"{type(e).__name__}: {e}")


def cache_stats(ctx) -> dict:
    return {"render_bytes": ctx.cache.render_bytes,
            "pair_count": ctx.cache.pair_count, "path": str(ctx.cache.path)}


def cache_clear(ctx) -> None:
    ctx.cache.clear_all()
