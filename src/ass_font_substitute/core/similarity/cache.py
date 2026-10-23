from __future__ import annotations

import io
import sqlite3
import time
import zlib
from pathlib import Path

import numpy as np

from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.render import RenderBundle

DDL = """
CREATE TABLE IF NOT EXISTS renders(
  fingerprint TEXT, text_ver TEXT, blob BLOB, nbytes INTEGER, used_at REAL,
  PRIMARY KEY(fingerprint, text_ver));
CREATE TABLE IF NOT EXISTS pairs(
  fpa TEXT, fpb TEXT, text_ver TEXT, score REAL, size_ratio REAL, n_chars INT,
  PRIMARY KEY(fpa, fpb, text_ver));
"""

DEFAULT_LIMIT = 500 * 1024 * 1024


class Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL：commit 不逐条 fsync（实测 pair 写入 3081µs→51µs/条）。
        # 缓存场景可接受：掉电最多丢最后一次提交，数据本身可重建
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(DDL)

    # ---- render ----
    def get_render(self, fingerprint: str, text_ver: str, loader) -> RenderBundle:
        row = self.conn.execute(
            "SELECT blob FROM renders WHERE fingerprint=? AND text_ver=?",
            (fingerprint, text_ver)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE renders SET used_at=? WHERE fingerprint=? AND text_ver=?",
                (time.time(), fingerprint, text_ver))
            self.conn.commit()
            return _decode(row[0])
        bundle = loader()
        self._put_render(fingerprint, text_ver, bundle)
        return bundle

    def _put_render(self, fingerprint, text_ver, bundle: RenderBundle):
        blob = _encode(bundle)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO renders VALUES (?,?,?,?,?)",
                (fingerprint, text_ver, blob, len(blob), time.time()))
        self.trim()

    # ---- pair ----
    def get_pair(self, a: str, b: str, text_ver: str) -> CompareResult | None:
        a, b = sorted((a, b))
        row = self.conn.execute(
            "SELECT score,size_ratio,n_chars FROM pairs "
            "WHERE fpa=? AND fpb=? AND text_ver=?", (a, b, text_ver)).fetchone()
        return CompareResult(*row) if row else None

    def put_pair(self, a: str, b: str, text_ver: str, r: CompareResult):
        a, b = sorted((a, b))
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO pairs VALUES (?,?,?,?,?,?)",
                              (a, b, text_ver, r.score, r.size_ratio, r.n_chars))

    # ---- 容量 ----
    @property
    def render_bytes(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(nbytes),0) FROM renders").fetchone()[0]

    @property
    def pair_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]

    def trim(self, max_bytes: int = DEFAULT_LIMIT):
        while self.render_bytes > max_bytes:
            row = self.conn.execute(
                "SELECT fingerprint, text_ver FROM renders "
                "ORDER BY used_at LIMIT 1").fetchone()
            if not row:
                return
            with self.conn:
                self.conn.execute(
                    "DELETE FROM renders WHERE fingerprint=? AND text_ver=?",
                    row)

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM renders")
            self.conn.execute("DELETE FROM pairs")

    def move_to(self, new_path: Path, migrate: bool = True):
        self.conn.close()
        if migrate:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self.path.rename(new_path)
        else:
            self.path.unlink(missing_ok=True)
        self.__init__(new_path)


def _encode(b: RenderBundle) -> bytes:
    # 键与值取自同一 sorted(items) 序：decode 按同序还原，绝不 zip 错位
    items = sorted(b.tiles.items())
    buf = io.BytesIO()
    np.savez_compressed(buf, chars=np.array([c for c, _ in items]),
                        **{f"t{i}": t for i, (_, t) in enumerate(items)})
    head = f"{b.ink_median}\x00".encode()
    return zlib.compress(head + buf.getvalue(), 6)


def _decode(blob: bytes) -> RenderBundle:
    raw = zlib.decompress(blob)
    head, body = raw.split(b"\x00", 1)
    ink = float(head)
    z = np.load(io.BytesIO(body))
    chars = [str(c) for c in z["chars"]]
    tiles = {c: z[f"t{i}"] for i, c in enumerate(chars)}
    return RenderBundle(tiles, ink, frozenset(chars))
