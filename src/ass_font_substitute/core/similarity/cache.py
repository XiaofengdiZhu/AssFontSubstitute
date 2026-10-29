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
  used_at REAL, PRIMARY KEY(fpa, fpb, text_ver));
"""

DEFAULT_LIMIT = 500 * 1024 * 1024
# pairs 行数上限：N 字体库 O(N²) 增长曾无上界持久膨胀
PAIRS_MAX = 300_000

# 渲染/比对算法版本：TILE/TARGET_INK/BASE_PX/CHAR_WEIGHT/phash/NCC 任一
# 变更时 +1——缓存键随之整体失效，持久库（Data/cache.db）里的旧口径
# 相似度不再静默命中。改用键前缀实现，无需 schema 迁移
ALGO_VER = "1"


class Cache:
    """渲染/比对缓存（SQLite WAL，单连接）。

    线程约束：单线程使用（生产=CoreWorker 串行队列独占）。连接按默认
    check_same_thread=True 创建，跨线程共享即 ProgrammingError——需共享
    时由调用方外部串行化，本层不加锁（缓存场景不值得）。
    键口径：text_ver 一律经 _kv 并入 ALGO_VER；fingerprint 语义由
    fontindex 定义（名称+cmap+度量，不含字形轮廓——字体文件更新而度量
    不变的窄场景缓存可能 stale，索引重算不失效，已知限制）。"""

    ALGO_VER = "1"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5)
        self.conn.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL：commit 不逐条 fsync（实测 pair 写入 3081µs→51µs/条）。
        # 缓存场景可接受：掉电最多丢最后一次提交，数据本身可重建
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self):
        # 旧库 pairs 无 used_at 列：结构不兼容直接弃表重建（缓存可丢）
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(pairs)")}
        if cols and "used_at" not in cols:
            self.conn.execute("DROP TABLE pairs")
        self.conn.executescript(DDL)

    def _kv(self, text_ver: str) -> str:
        return f"{self.ALGO_VER}\x00{text_ver}"

    # ---- render ----
    def get_render(self, fingerprint: str, text_ver: str, loader) -> RenderBundle:
        row = self.conn.execute(
            "SELECT blob FROM renders WHERE fingerprint=? AND text_ver=?",
            (fingerprint, self._kv(text_ver))).fetchone()
        if row:
            self.conn.execute(
                "UPDATE renders SET used_at=? WHERE fingerprint=? AND text_ver=?",
                (time.time(), fingerprint, self._kv(text_ver)))
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
                (fingerprint, self._kv(text_ver), blob, len(blob), time.time()))
        self.trim()

    # ---- pair ----
    def get_pair(self, a: str, b: str, text_ver: str) -> CompareResult | None:
        a, b = sorted((a, b))
        row = self.conn.execute(
            "SELECT score,size_ratio,n_chars FROM pairs "
            "WHERE fpa=? AND fpb=? AND text_ver=?",
            (a, b, self._kv(text_ver))).fetchone()
        if not row:
            return None
        with self.conn:
            self.conn.execute(
                "UPDATE pairs SET used_at=? WHERE fpa=? AND fpb=? AND text_ver=?",
                (time.time(), a, b, self._kv(text_ver)))
        return CompareResult(*row)

    def put_pair(self, a: str, b: str, text_ver: str, r: CompareResult):
        a, b = sorted((a, b))
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO pairs VALUES (?,?,?,?,?,?,?)",
                (a, b, self._kv(text_ver), r.score, r.size_ratio, r.n_chars,
                 time.time()))
        self._trim_pairs()

    def _trim_pairs(self, max_rows: int = PAIRS_MAX):
        if self.pair_count <= max_rows:
            return
        with self.conn:
            self.conn.execute(
                "DELETE FROM pairs WHERE rowid IN ("
                "SELECT rowid FROM pairs ORDER BY used_at LIMIT ?)",
                (self.pair_count - max_rows,))

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
        # GUI 已绿色软件化（缓存定死 Data/cache.db 不可迁移）：核心层保留，
        # 现仅核心测试使用
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
