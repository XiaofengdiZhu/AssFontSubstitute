from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ass_font_substitute.core.assparse.extract import extract_usages
from ass_font_substitute.core.assparse.rewrite import rewrite_file
from ass_font_substitute.core.coverage.matcher import classify_usage
from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import (open_db, scan_dir,
                                                      update_index)
from ass_font_substitute.core.models import FontUsage
from ass_font_substitute.core.similarity.render import render_bundle
from ass_font_substitute.core.similarity.search import FontSearcher

SEARCH_CHUNK = 8          # pull_chunk 每批候选数（UI 渐进填充节奏）


@dataclass
class DirStatus:
    path: str
    state: str                  # ok / stale / missing / offline
    added: int = 0
    changed: int = 0
    indexed_files: int = 0


def probe_dir_status(dir_path: str) -> DirStatus:
    """只读探测（URI mode=ro，绝不创建/写库）。missing=目录不存在；
    offline=库存在但打不开（只读盘/权限/损坏）；stale=有未入索引文件。"""
    d = Path(dir_path)
    if not d.is_dir():
        return DirStatus(dir_path, "missing")
    dbf = d / "fontindex.db"
    if not dbf.exists():
        st = DirStatus(dir_path, "stale")
        st.added = len(scan_dir(d))
        return st
    try:
        conn = sqlite3.connect(dbf.resolve().as_uri() + "?mode=ro", uri=True)
        known = {r[0]: (r[1], r[2]) for r in
                 conn.execute("SELECT rel_path, size, mtime_ns FROM files")}
        bad = {r[0]: (r[1], r[2]) for r in
               conn.execute("SELECT rel_path, size, mtime_ns FROM bad")}
        conn.close()
    except sqlite3.Error:
        return DirStatus(dir_path, "offline")
    st = DirStatus(dir_path, "ok", indexed_files=len(known))
    for p in scan_dir(d):
        rel = p.relative_to(d).as_posix()
        s = p.stat()
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


def update_dir(dir_path: str, progress=None):
    conn = open_db(Path(dir_path) / "fontindex.db")
    try:
        return update_index(Path(dir_path), conn, progress=progress)
    finally:
        conn.close()


def read_failures(ctx) -> dict:
    """各目录 bad 清单 + 跨目录跳过（旧版本）。mode=ro 只读。"""
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
    except Exception as e:  # noqa: BLE001  文件缺失/不可读折进 error，不中断批量
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


def pull_chunk(searcher: FontSearcher, cancel: dict | None = None,
               chunk: int = SEARCH_CHUNK):
    """推进搜索器至 批满/暂停/耗尽/目标渲染失败/取消。
    返回 (candidates, state)；state ∈ running/paused/exhausted/
    target_error/cancelled。命中语义（暂停累计 hit_count、target_error
    懒解析一次）全部由 searcher 承担，此层不解读。"""
    cands = []
    state = "running"
    while len(cands) < chunk:
        if cancel is not None and cancel.get("cancelled"):
            state = "cancelled"
            break
        c = searcher.next()
        if c is None:
            state = ("target_error" if searcher.target_error
                     else "paused" if searcher.paused else "exhausted")
            break
        cands.append(c)
    return cands, state


def render_preview(entity, text: str):
    """预览渲染条：不走全局相似度缓存（键按 text_version），GUI 自带
    （指纹，文字）LRU。缺字跳过，绝不渲染 tofu。"""
    return render_bundle(Path(entity.dir_path) / entity.rel_path,
                         entity.face_index, text)


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
    except Exception as e:  # noqa: BLE001  单文件失败不拖垮整批
        return ApplyFileResult(path, error=f"{type(e).__name__}: {e}")


def cache_stats(ctx) -> dict:
    return {"render_bytes": ctx.cache.render_bytes,
            "pair_count": ctx.cache.pair_count, "path": str(ctx.cache.path)}


def cache_clear(ctx) -> None:
    ctx.cache.clear_all()


def cache_move(ctx, new_path: str, migrate: bool) -> str:
    p = Path(new_path)
    if migrate and p.exists():
        p.unlink()                  # 覆盖既有文件：页面已弹确认框
    ctx.cache.move_to(p, migrate=migrate)
    ctx.settings.set("cache_path", str(p))
    return str(p)
