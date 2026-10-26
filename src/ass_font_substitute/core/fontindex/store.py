from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zlib
from collections.abc import Callable
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.fontindex.parser import FontParseError
from ass_font_substitute.core.fontindex.parser import parse_font_file
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.core.models import FontRecord

# 库列即最终分类（建索引时由别名算好入库，装载直读）。分类规则改进属
# 内容语义变化：升此版本触发旧库备份+全量重建（spec §5）。v3：指纹去
# 内容哈希（xxh 列废除，解析不再读全文），fingerprints 校验列改 mtime_ns。
# v4：cmap 区间拆 cmap_data 表去重+zlib（同族各字重共享 cmap，18539
# face 仅 1455 份 distinct，232.9→21.8MB；zlib-1 再 3.2x），fonts 行存引用。
# v5：别名剔除 name ID 17（样式名称/typographic subfamily）——FOTPunchello
# 家族它填 'Condensed Bold'、微软雅黑填 'Regular'，是样式描述不是名称；
# fonts 表新增 display_name 列（parser 按 ID16→ID1→ID4 官方优先选好入库，
# 装载直读，展示规则改进同分类规则走升版重建）。
# v6：fonts 表新增 family_names 列（ID16 typographic family 全语言名，
# 缺失视 ID1——字体系是 name 表实字段，详情页「同字体系」按它归族；
# display_name 是 ID1 优先的展示名，两回事）。
SCHEMA_VERSION = 6
DB_NAME = "fontindex.db"
WHITELIST = {".ttf", ".otf", ".ttc", ".otc"}

# 指纹算法版本：fingerprint_of 的规范化方式变化时递增，旧缓存整批失效重算
FP_ALGO = 3

DDL = """
CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER);
CREATE TABLE IF NOT EXISTS cmap_data(
  id INTEGER PRIMARY KEY, data BLOB NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS fonts(
  id INTEGER PRIMARY KEY, rel_path TEXT, face_index INTEGER, psname TEXT,
  version TEXT, aliases TEXT, weight INTEGER, width INTEGER, italic INTEGER,
  cmap_id INTEGER NOT NULL, script TEXT, category TEXT, display_name TEXT,
  family_names TEXT,
  UNIQUE(rel_path, face_index));
CREATE TABLE IF NOT EXISTS bad(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS fingerprints(
  rel_path TEXT, face_index INTEGER, mtime_ns INTEGER, algo INTEGER, fp TEXT,
  PRIMARY KEY(rel_path, face_index));
"""


def fingerprint_of(aliases: list[str], cmap: CmapRanges, weight: int, width: int,
                   italic: bool) -> str:
    """字体指纹 = hash(全部名称别名 + cmap 区间表 + weight/width/italic)。
    纯语义身份：同语义字体（重打包/换路径/换 mtime）指纹稳定，渲染缓存
    与记忆可跨副本复用；文件是否变化由 update_index 的 size+mtime 门判定，
    fingerprints 表的 mtime_ns 列（与 fonts 行同事务写入）作装载侧防陈旧
    兜底。update_index 时预计算入表（键 rel_path+face_index），LibraryIndex
    聚合读命中直取、缺失回写——避免每次打开重算。"""
    canon = json.dumps({
        "aliases": sorted(aliases),
        "cmap": [list(r) for r in zip(cmap.starts, cmap.ends, strict=False)],
        "weight": weight, "width": width, "italic": int(italic),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


class IndexSchemaError(Exception):
    pass


@dataclass
class UpdateResult:
    added: int = 0
    changed: int = 0
    removed: int = 0
    bad: int = 0
    bad_retried: int = 0
    fonts: int = 0
    cancelled: bool = False


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL（WAL 安全默认，Cache 层同款）：进程崩溃不丢已提交事务，
    # 掉电最多丢最近几秒；逐文件事务的 COMMIT 免每次 fsync——17k 文件
    # 首建省 17k 次 fsync 等待。取消/续跑语义不变（增量门照常续传）。
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    return conn


def open_db(path: Path) -> sqlite3.Connection:
    conn = _connect(path)
    row = conn.execute("SELECT v FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        return conn
    if row[0] == SCHEMA_VERSION:
        return conn
    # spec §5：版本不符 → 改名备份旧库（覆盖既有 .bak）后全量重建
    conn.close()  # 干净关闭：WAL 落盘、清掉 -wal/-shm 旁车文件
    bak = path.parent / (path.name + ".bak")
    try:
        os.replace(path, bak)
    except OSError as e:
        raise IndexSchemaError(f"备份旧库失败（{bak}）: {e}") from e
    conn = _connect(path)
    conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def scan_dir(root: Path) -> list[Path]:
    # os.scandir 递归替代 rglob：Windows 上 DirEntry 的 is_dir/is_file
    # 直接用目录枚举附带信息，零额外 stat（Path.is_file() 每次真 stat）；
    # follow_symlinks=False 与 rglob 的「不跟随目录符号链接」语义一致
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(Path(e.path))
                elif (e.is_file(follow_symlinks=False)
                      and Path(e.path).suffix.lower() in WHITELIST
                      and e.name != DB_NAME):
                    out.append(Path(e.path))
    return sorted(out)


def _fingerprints(recs: list[FontRecord]) -> list[str]:
    """逐 face 预计算指纹：worker 解析后立即算（主线程只管写库），
    串行/池崩回退路径共用。"""
    return [fingerprint_of(r.aliases, r.cmap, r.weight, r.width, r.italic)
            for r in recs]


def _parse_task(p: Path) -> tuple[list[FontRecord] | FontParseError,
                                  list[str] | None]:
    """进程池任务体：解析 + 指纹预计算都在 worker（指纹 ~0.5ms/文件，
    留主线程曾是写侧天花板）。FontParseError 以值返回（不 raise，免池层
    异常包装），pickle 回主线程照常入 bad 表，此时指纹为 None。"""
    try:
        recs = parse_font_file(p)
    except FontParseError as e:
        return e, None
    return recs, _fingerprints(recs)


def _apply_result(conn: sqlite3.Connection, rel: str, st: os.stat_result,
                  recs: list[FontRecord] | FontParseError,
                  res: UpdateResult,
                  fps: list[str] | None = None,
                  cmap_ids: dict[bytes, int] | None = None) -> None:
    """单文件写库事务（串行/并行共用）：失败入 bad，成功清 bad 重灌条目。
    fps 为调用方预计算的逐 face 指纹（对齐 recs；worker 与串行/回退路径
    均显式传入），None 仅防御兜底就地补算。cmap_ids 是本次 update 的
    blob→id 进程内缓存（命中免 SQL），miss 走 cmap_data UNIQUE 去重
    （增量场景跨 run 也会命中历史行）。"""
    with conn:
        if isinstance(recs, FontParseError):
            res.bad += 1
            conn.execute("INSERT OR REPLACE INTO bad VALUES (?,?,?,?)",
                         (rel, st.st_size, st.st_mtime_ns, str(recs)))
            conn.execute("DELETE FROM files WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fingerprints WHERE rel_path=?", (rel,))
            return
        conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)",
                     (rel, st.st_size, st.st_mtime_ns))
        conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
        conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
        for i, r in enumerate(recs):
            blob = zlib.compress(r.cmap.to_bytes(), 1)
            cid = cmap_ids.get(blob) if cmap_ids is not None else None
            if cid is None:
                conn.execute("INSERT OR IGNORE INTO cmap_data(data) VALUES (?)",
                             (blob,))
                cid = conn.execute(
                    "SELECT id FROM cmap_data WHERE data=?", (blob,)).fetchone()[0]
                if cmap_ids is not None:
                    cmap_ids[blob] = cid
            conn.execute(
                "INSERT INTO fonts(rel_path,face_index,psname,version,aliases,"
                "weight,width,italic,cmap_id,script,category,display_name,"
                "family_names) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rel, r.face_index, r.psname,
                 json.dumps(r.version) if r.version else None,
                 json.dumps(r.aliases, ensure_ascii=False),
                 r.weight, r.width, int(r.italic),
                 cid, r.script, r.category, r.display_name,
                 json.dumps(r.family_names, ensure_ascii=False)))
            conn.execute(
                "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?)",
                (rel, r.face_index, st.st_mtime_ns, FP_ALGO,
                 fps[i] if fps is not None else
                 fingerprint_of(r.aliases, r.cmap, r.weight, r.width,
                                r.italic)))


def _update_parallel(dir_path: Path, conn: sqlite3.Connection,
                     todo: list[Path], res: UpdateResult,
                     progress: Callable[[int, int, UpdateResult], bool] | None,
                     total: int, cmap_ids: dict[bytes, int],
                     executor=None, max_threads: int = 1) -> None:
    """解析走进程池（fontTools 纯 Python CPU 与指纹预计算同摊多核），
    写库仍主线程逐文件事务（SQLite 单写者；取消/中断保留已入库前缀语义
    不变——progress 返 False 停止消费，在途结果丢弃不入库，未派发文件
    下次增量重跑）。
    executor 供测试注入（行为与真池一致），None 按 max_threads 建
    ProcessPoolExecutor。池整体崩坏（BrokenProcessPool，fontTools 纯
    Python 极难触发，纯保险）时剩余文件回退主线程串行续完。"""
    owned = executor is None
    ex = executor if executor is not None else ProcessPoolExecutor(max_workers=max_threads)
    try:
        futs = {}
        for p in todo:
            # 派发前取 stat：解析窗口内被外部覆写则记旧 mtime，下次必重检
            futs[ex.submit(_parse_task, p)] = (
                p.relative_to(dir_path).as_posix(), p.stat(), p)
        pending = dict(futs)
        done = 0
        for fut in as_completed(futs):
            try:
                recs, fps = fut.result()
            except BrokenProcessPool:
                break
            rel, st, _ = futs[fut]
            del pending[fut]
            _apply_result(conn, rel, st, recs, res, fps, cmap_ids)
            done += 1
            if progress is not None and not progress(done, total, res):
                res.cancelled = True
                return
        for rel, st, p in pending.values():
            try:
                recs = parse_font_file(p)
            except FontParseError as e:
                recs = e
            fps = None if isinstance(recs, FontParseError) else _fingerprints(recs)
            _apply_result(conn, rel, st, recs, res, fps, cmap_ids)
            done += 1
            if progress is not None and not progress(done, total, res):
                res.cancelled = True
                return
    finally:
        if owned:
            ex.shutdown(wait=True, cancel_futures=res.cancelled)


def update_index(dir_path: Path, conn: sqlite3.Connection,
                 progress: Callable[[int, int, UpdateResult], bool] | None = None,
                 executor=None, max_threads: int = 1) -> UpdateResult:
    """progress(done, total, res) -> bool 在每解析完一个文件后回调（并行
    时按完成序，done=已完成数）；返回 False 停止剩余文件并置
    res.cancelled=True。逐文件事务提交，取消/中断保留已入库前缀，重跑按
    增量续（GUI 进度/取消依赖此语义）。max_threads>1 且待解析多于一个
    文件时解析走进程池（jobs.update_dir 传 settings.max_threads），
    executor 参数供测试注入线程池替代。"""
    res = UpdateResult()
    known = {r[0]: (r[1], r[2]) for r in
             conn.execute("SELECT rel_path, size, mtime_ns FROM files")}
    bad = {r[0]: (r[1], r[2]) for r in
           conn.execute("SELECT rel_path, size, mtime_ns FROM bad")}
    current: dict[str, tuple[int, int]] = {}
    todo: list[Path] = []
    for p in scan_dir(dir_path):
        rel = p.relative_to(dir_path).as_posix()
        st = p.stat()
        current[rel] = (st.st_size, st.st_mtime_ns)
        if rel in known:
            if known[rel] != current[rel]:
                todo.append(p)
                res.changed += 1
        elif rel in bad:
            if bad[rel] != current[rel]:
                todo.append(p)
                res.bad_retried += 1
        else:
            todo.append(p)
            res.added += 1

    with conn:
        for rel in set(known) - set(current):
            res.removed += 1
            conn.execute("DELETE FROM files WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM fingerprints WHERE rel_path=?", (rel,))
            conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))

    total = len(todo)
    # 建索引期间停自动 checkpoint：逐文件 COMMIT 只 append WAL（页缓存，
    # NORMAL 无 fsync），默认每 ~4MB 触发一次「WAL 抄回主库+fsync」，17k
    # 文件首建要 100~200 次；改为主线程全程顺序写 WAL、收尾一次性
    # TRUNCATE checkpoint，物理 I/O 收敛为 1 次。取消/崩溃语义不变（已
    # 提交前缀本就活在 WAL，恢复路径同款），代价是建索引期间 -wal 涨到
    # 与库同量级。checkpoint busy（GUI 恰在读）时忽略，留待 close 兜底。
    saved_ac = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    conn.execute("PRAGMA wal_autocheckpoint=0")
    cmap_ids: dict[bytes, int] = {}
    try:
        if total > 1 and (executor is not None or max_threads > 1):
            _update_parallel(dir_path, conn, todo, res, progress, total,
                             cmap_ids, executor=executor, max_threads=max_threads)
        else:
            for done, p in enumerate(todo, 1):
                rel = p.relative_to(dir_path).as_posix()
                # 解析前取 stat：若解析窗口内文件被外部覆写，记下的是旧 mtime，
                # 下次必重检；解析后取会记到新 mtime，门误判「未变」→ 静默陈旧
                st = p.stat()
                try:
                    recs = parse_font_file(p)
                except FontParseError as e:
                    recs = e
                fps = None if isinstance(recs, FontParseError) else _fingerprints(recs)
                _apply_result(conn, rel, st, recs, res, fps, cmap_ids)
                if progress is not None and not progress(done, total, res):
                    res.cancelled = True
                    break
        # cmap GC：增量路径（变更重灌/坏文件/文件删除）只删 fonts 行不回收
        # cmap，孤儿随更新无上界积累；收尾统一清一次（表 ~1.5k 行，成本
        # 可忽略）。取消时 cmap_data 与 fonts 同事务写入、前缀自洽，此处
        # 最多清掉历史崩溃遗留孤儿，同样安全。
        with conn:
            conn.execute("DELETE FROM cmap_data "
                         "WHERE id NOT IN (SELECT cmap_id FROM fonts)")
    finally:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute(f"PRAGMA wal_autocheckpoint={saved_ac}")
    res.fonts = stats(conn)["fonts"]
    return res


def stats(conn: sqlite3.Connection) -> dict:
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    fonts = conn.execute("SELECT COUNT(*) FROM fonts").fetchone()[0]
    sc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('简','简繁')").fetchone()[0]
    tc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('繁','简繁')").fetchone()[0]
    return {"files": files, "fonts": fonts, "support_sc": sc, "support_tc": tc}


def load_font_rows(conn: sqlite3.Connection) -> Iterator[tuple]:
    """cmap 列 = zlib 压缩的 CmapRanges.to_bytes()（v4 去重存储，
    消费方解压；同 blob 解压多次的调用方建议 memo 化）。"""
    yield from conn.execute(
            "SELECT f.rel_path, f.face_index, f.psname, f.version, f.aliases, "
            "f.display_name, f.family_names, f.weight, f.width, f.italic, "
            "cd.data, f.script, f.category, fi.size, fi.mtime_ns FROM fonts f "
            "JOIN files fi USING(rel_path) "
            "JOIN cmap_data cd ON cd.id = f.cmap_id")


__all__ = [
    "FP_ALGO",
    "SCHEMA_VERSION",
    "IndexSchemaError",
    "UpdateResult",
    "fingerprint_of",
    "load_font_rows",
    "open_db",
    "scan_dir",
    "stats",
    "update_index",
]
