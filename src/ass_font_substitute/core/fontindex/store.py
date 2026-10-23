from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.core.fontindex.parser import FontParseError, parse_font_file

SCHEMA_VERSION = 1
DB_NAME = "fontindex.db"
WHITELIST = {".ttf", ".otf", ".ttc", ".otc"}

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
"""


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


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    row = conn.execute("SELECT v FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    elif row[0] != SCHEMA_VERSION:
        raise IndexSchemaError(f"schema v{row[0]} != v{SCHEMA_VERSION}")
    return conn


def scan_dir(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and p.suffix.lower() in WHITELIST
                  and p.name != DB_NAME)


def update_index(dir_path: Path, conn: sqlite3.Connection) -> UpdateResult:
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
            conn.execute("DELETE FROM bad WHERE rel_path=?", (rel,))
        for p in todo:
            rel = p.relative_to(dir_path).as_posix()
            try:
                recs = parse_font_file(p)
            except FontParseError as e:
                res.bad += 1
                st = p.stat()
                conn.execute("INSERT OR REPLACE INTO bad VALUES (?,?,?,?)",
                             (rel, st.st_size, st.st_mtime_ns, str(e)))
                conn.execute("DELETE FROM files WHERE rel_path=?", (rel,))
                conn.execute("DELETE FROM fonts WHERE rel_path=?", (rel,))
                continue
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
    res.fonts = stats(conn)["fonts"]
    return res


def stats(conn: sqlite3.Connection) -> dict:
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    fonts = conn.execute("SELECT COUNT(*) FROM fonts").fetchone()[0]
    sc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('简','简繁')").fetchone()[0]
    tc = conn.execute("SELECT COUNT(*) FROM fonts WHERE script IN ('繁','简繁')").fetchone()[0]
    return {"files": files, "fonts": fonts, "support_sc": sc, "support_tc": tc}


def load_font_rows(conn: sqlite3.Connection) -> Iterator[tuple]:
    for row in conn.execute(
            "SELECT f.rel_path, f.face_index, f.psname, f.version, f.aliases, "
            "f.weight, f.width, f.italic, f.cmap, f.script, f.category, "
            "fi.xxh, fi.size, fi.mtime_ns FROM fonts f JOIN files fi USING(rel_path)"):
        yield row


__all__ = ["open_db", "scan_dir", "update_index", "stats", "load_font_rows",
           "SCHEMA_VERSION", "IndexSchemaError", "UpdateResult"]
