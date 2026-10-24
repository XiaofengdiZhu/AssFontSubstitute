from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.fontindex.parser import FontParseError
from ass_font_substitute.core.fontindex.parser import parse_font_file
from ass_font_substitute.core.models import CmapRanges

SCHEMA_VERSION = 1
DB_NAME = "fontindex.db"
WHITELIST = {".ttf", ".otf", ".ttc", ".otc"}

# 指纹算法版本：fingerprint_of 的规范化方式变化时递增，旧缓存整批失效重算
FP_ALGO = 1

DDL = """
CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, xxh TEXT);
CREATE TABLE IF NOT EXISTS fonts(
  id INTEGER PRIMARY KEY, rel_path TEXT, face_index INTEGER, psname TEXT,
  version TEXT, aliases TEXT, weight INTEGER, width INTEGER, italic INTEGER,
  cmap BLOB, script TEXT, category TEXT,
  UNIQUE(rel_path, face_index));
CREATE TABLE IF NOT EXISTS bad(
  rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS fingerprints(
  rel_path TEXT, face_index INTEGER, xxh TEXT, algo INTEGER, fp TEXT,
  PRIMARY KEY(rel_path, face_index));
"""


def fingerprint_of(aliases: list[str], cmap: CmapRanges, weight: int, width: int,
                   italic: bool, xxh: str) -> str:
    """字体指纹 = hash(全部名称别名 + cmap 区间表 + weight/width/italic + 文件 xxh64)。
    update_index 时预计算入 fingerprints 表（键 rel_path+face_index，xxh+algo
    双重防陈旧），LibraryIndex 聚合读命中直取、缺失回写——避免每次打开重算。"""
    canon = json.dumps({
        "aliases": sorted(aliases),
        "cmap": [list(r) for r in zip(cmap.starts, cmap.ends, strict=False)],
        "weight": weight, "width": width, "italic": int(italic), "xxh": xxh,
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
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in WHITELIST
                  and p.name != DB_NAME)


def update_index(dir_path: Path, conn: sqlite3.Connection,
                 progress: Callable[[int, int, UpdateResult], bool] | None = None) -> UpdateResult:
    """progress(done, total, res) -> bool 在每解析完一个文件后回调；
    返回 False 停止剩余文件并置 res.cancelled=True。逐文件事务提交，
    取消/中断保留已解析前缀，重跑按增量续（GUI 进度/取消依赖此语义）。"""
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
    for done, p in enumerate(todo, 1):
        rel = p.relative_to(dir_path).as_posix()
        with conn:
            recs = None
            try:
                recs = parse_font_file(p)
            except FontParseError as e:
                res.bad += 1
                st = p.stat()
                conn.execute("INSERT OR REPLACE INTO bad VALUES (?,?,?,?)",
                             (rel, st.st_size, st.st_mtime_ns, str(e)))
                conn.execute("DELETE FROM files WHERE rel_path=?", (rel,))
                conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
                conn.execute("DELETE FROM fingerprints WHERE rel_path=?", (rel,))
            if recs is not None:
                st = p.stat()
                conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)",
                             (rel, st.st_size, st.st_mtime_ns, recs[0].xxh))
                conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
                conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
                for r in recs:
                    conn.execute(
                        "INSERT INTO fonts(rel_path,face_index,psname,version,aliases,"
                        "weight,width,italic,cmap,script,category) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rel, r.face_index, r.psname,
                         json.dumps(r.version) if r.version else None,
                         json.dumps(r.aliases, ensure_ascii=False),
                         r.weight, r.width, int(r.italic),
                         r.cmap.to_bytes(), r.script, r.category))
                    conn.execute(
                        "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?)",
                        (rel, r.face_index, recs[0].xxh, FP_ALGO,
                         fingerprint_of(r.aliases, r.cmap, r.weight, r.width,
                                        r.italic, recs[0].xxh)))
        if progress is not None and not progress(done, total, res):
            res.cancelled = True
            break
    res.fonts = stats(conn)["fonts"]
    return res


def stats(conn: sqlite3.Connection) -> dict:
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    fonts = conn.execute("SELECT COUNT(*) FROM fonts").fetchone()[0]
    sc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('简','简繁')").fetchone()[0]
    tc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('繁','简繁')").fetchone()[0]
    return {"files": files, "fonts": fonts, "support_sc": sc, "support_tc": tc}


def load_font_rows(conn: sqlite3.Connection) -> Iterator[tuple]:
    yield from conn.execute(
            "SELECT f.rel_path, f.face_index, f.psname, f.version, f.aliases, "
            "f.weight, f.width, f.italic, f.cmap, f.script, f.category, "
            "fi.xxh, fi.size, fi.mtime_ns FROM fonts f JOIN files fi USING(rel_path)")


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
