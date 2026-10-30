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


class Cache:
    """渲染/比对缓存（SQLite WAL，单连接）。

    线程约束：单线程使用（生产=CoreWorker 串行队列独占）；同库文件也不得
    开第二个 Cache 实例——_nb_sum/used_at 记账是实例内存态，双实例互不
    可见必漂移。连接按默认
    check_same_thread=True 创建，跨线程共享即 ProgrammingError——需共享
    时由调用方外部串行化，本层不加锁（缓存场景不值得）。
    键口径：text_ver 一律经 _kv 并入 ALGO_VER；fingerprint 语义由
    fontindex 定义（名称+cmap+度量，不含字形轮廓——字体文件更新而度量
    不变的窄场景缓存可能 stale，索引重算不失效，已知限制）。"""

    # 渲染/比对算法版本（单一事实源，变更时 +1）：TILE/TARGET_INK/
    # RULER_PX/ANCHOR_INK/CHAR_WEIGHT/phash/NCC 任一变更后缓存键随之整体
    # 失效，持久库（Data/cache.db）里的旧口径相似度不再静默命中。
    # 键前缀实现，零迁移。v2：量尺 96→48px + 锚 43 下限复用（tile 位级变）
    ALGO_VER = "2"
    # 读命中 used_at 记账条数阈值：满即批量落库（读路径零写入的关键参数）
    FLUSH_PENDING = 256
    # put_pair 触发 _trim_pairs 行数检查的节流间隔（COUNT(*) 全表扫不逐条付）
    PAIRS_TRIM_EVERY = 256

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
        # 读命中 used_at 延迟记账：命中只记内存，满 FLUSH_PENDING 条或
        # trim/move_to 前批量落库——旧口径逐命中 UPDATE+commit，渲染缓存
        # 命中实测最坏 2.8ms/次（WAL checkpoint 抖动），大库重扫被写放大
        self._pending_render: dict[tuple, float] = {}
        self._pending_pair: dict[tuple, float] = {}
        self._nb_sum: int | None = None    # renders 字节总量缓存（trim 门用）
        self._pair_puts = 0

    def _ensure_schema(self):
        # 旧库 pairs 无 used_at 列：结构不兼容直接弃表重建（缓存可丢）
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(pairs)")}
        if cols and "used_at" not in cols:
            self.conn.execute("DROP TABLE pairs")
        self.conn.executescript(DDL)
        self._purge_stale_algo()
        self._enable_auto_vacuum()

    def _purge_stale_algo(self):
        # 旧 ALGO 版本行打开即清：永不命中纯占空间，且污染 render_bytes
        # 统计（设置页显假 MB）。键前缀 2 位定宽（"02\x00…"），用 substr
        # 定长前缀比对——length()/LIKE 遇值内 \x00 截断，substr 取前缀安全
        prefix = f"{int(self.ALGO_VER):02d}"
        with self.conn:
            self.conn.execute(
                "DELETE FROM renders WHERE substr(text_ver,1,2) != ?", (prefix,))
            self.conn.execute(
                "DELETE FROM pairs WHERE substr(text_ver,1,2) != ?", (prefix,))

    def _enable_auto_vacuum(self):
        # FULL：DELETE 释放的页在 checkpoint 时归还 OS——清除缓存/LRU 淘汰
        # 后文件不再驻留空洞（实测旧行为：Data/cache.db 内容 2MB 文件
        # 108MB，freelist 占 99.6%）。存量库置位需 VACUUM 重建一次性付
        # （实测 113MB 库 0.25s）；新空库 VACUUM 近零成本
        try:
            if self.conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 1:
                self.conn.execute("PRAGMA auto_vacuum=FULL")
                self.conn.execute("VACUUM")
                # WAL 下 VACUUM 的截断要 checkpoint 才落盘（实测不 checkpoint
                # 则 113MB 文件整个会话期驻留，conn.close 才缩）——一次性
                # 成本顺手付掉，等同 clear_all 的还盘口径
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            # VACUUM 需闪断独占（并发读连接/双开实例即 BUSY）：置位只进内存
            # 未写 header，下次打开自动重试——不值得为此炸 CoreWorker 启动
            # （cache=None 整个应用残废直到重启）
            pass

    def _kv(self, text_ver: str) -> str:
        # ALGO_VER 转定宽 2 位前缀（_purge_stale_algo 的 substr 依赖定宽）
        return f"{int(self.ALGO_VER):02d}\x00{text_ver}"

    # ---- render ----
    def flush(self):
        """把读命中记账的 used_at 批量落库。trim/move_to 及记账满
        FLUSH_PENDING 条时自动触发；显式调用亦幂等。"""
        if not self._pending_render and not self._pending_pair:
            return
        with self.conn:
            if self._pending_render:
                self.conn.executemany(
                    "UPDATE renders SET used_at=? WHERE fingerprint=? AND text_ver=?",
                    [(ts, fp, tv) for (fp, tv), ts in self._pending_render.items()])
                self._pending_render.clear()
            if self._pending_pair:
                self.conn.executemany(
                    "UPDATE pairs SET used_at=? WHERE fpa=? AND fpb=? AND text_ver=?",
                    [(ts, a, b, tv) for (a, b, tv), ts in self._pending_pair.items()])
                self._pending_pair.clear()

    def get_render(self, fingerprint: str, text_ver: str, loader) -> RenderBundle:
        kv = self._kv(text_ver)
        row = self.conn.execute(
            "SELECT blob FROM renders WHERE fingerprint=? AND text_ver=?",
            (fingerprint, kv)).fetchone()
        if row:
            self._pending_render[(fingerprint, kv)] = time.time()
            if len(self._pending_render) >= self.FLUSH_PENDING:
                self.flush()
            return _decode(row[0])
        bundle = loader()
        self._put_render(fingerprint, text_ver, bundle)
        return bundle

    def _put_render(self, fingerprint, text_ver, bundle: RenderBundle):
        self.put_render_encoded(fingerprint, text_ver, _encode(bundle))

    def put_render_encoded(self, fingerprint: str, text_ver: str, blob: bytes):
        """直接落已编码 blob（池模式 worker 渲染后回传，免主进程重编码）。
        编码口径（_encode + 键前缀）与 worker 侧同库同版本，无需校验。"""
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO renders VALUES (?,?,?,?,?)",
                (fingerprint, self._kv(text_ver), blob, len(blob), time.time()))
        if self._nb_sum is not None:
            self._nb_sum += len(blob)
        self.trim()

    # ---- pair ----
    def get_pair(self, a: str, b: str, text_ver: str) -> CompareResult | None:
        a, b = sorted((a, b))
        kv = self._kv(text_ver)
        row = self.conn.execute(
            "SELECT score,size_ratio,n_chars FROM pairs "
            "WHERE fpa=? AND fpb=? AND text_ver=?",
            (a, b, kv)).fetchone()
        if not row:
            return None
        self._pending_pair[(a, b, kv)] = time.time()
        if len(self._pending_pair) >= self.FLUSH_PENDING:
            self.flush()
        return CompareResult(*row)

    def put_pair(self, a: str, b: str, text_ver: str, r: CompareResult):
        a, b = sorted((a, b))
        kv = self._kv(text_ver)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO pairs VALUES (?,?,?,?,?,?,?)",
                (a, b, kv, r.score, r.size_ratio, r.n_chars, time.time()))
        # 同键若已有读命中记账：DB 刚写的时间更新，留旧记账反而在 flush 时回退
        self._pending_pair.pop((a, b, kv), None)
        # 节流：COUNT(*) 全表扫不逐条付，PAIRS_TRIM_EVERY 查一次足够
        self._pair_puts += 1
        if self._pair_puts % self.PAIRS_TRIM_EVERY == 0:
            self._trim_pairs()

    def _trim_pairs(self, max_rows: int = PAIRS_MAX):
        self.flush()                       # LRU 序：记账未落库会排错淘汰序
        n = self.pair_count
        if n <= max_rows:
            return
        with self.conn:
            self.conn.execute(
                "DELETE FROM pairs WHERE rowid IN ("
                "SELECT rowid FROM pairs ORDER BY used_at LIMIT ?)",
                (n - max_rows,))

    # ---- 容量 ----
    @property
    def render_bytes(self) -> int:
        return self.conn.execute(
            "SELECT COALESCE(SUM(nbytes),0) FROM renders").fetchone()[0]

    @property
    def pair_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]

    def trim(self, max_bytes: int = DEFAULT_LIMIT):
        # 字节总量走内存缓存（_nb_sum），不再逐插入 SUM 全表扫；超限先 flush
        # （LRU 序正确），再按 64 行一批删最旧，批量删代替旧逐行删+反复 SUM
        if self._nb_sum is None:
            self._nb_sum = self.conn.execute(
                "SELECT COALESCE(SUM(nbytes),0) FROM renders").fetchone()[0]
        if self._nb_sum <= max_bytes:
            return
        self.flush()
        with self.conn:
            while self._nb_sum > max_bytes:
                rows = self.conn.execute(
                    "SELECT fingerprint, text_ver, nbytes FROM renders "
                    "ORDER BY used_at LIMIT 64").fetchall()
                if not rows:
                    self._nb_sum = 0
                    return
                # 批内逐行删、够数即停：整批 executemany 会砍过界
                for r in rows:
                    self.conn.execute(
                        "DELETE FROM renders WHERE fingerprint=? AND text_ver=?",
                        (r[0], r[1]))
                    self._nb_sum -= r[2]
                    if self._nb_sum <= max_bytes:
                        return

    def clear_all(self):
        with self.conn:
            self.conn.execute("DELETE FROM renders")
            self.conn.execute("DELETE FROM pairs")
        self._pending_render.clear()
        self._pending_pair.clear()
        self._nb_sum = 0
        # WAL 模式 auto_vacuum 的页归还推迟到 checkpoint：清除是用户显式
        # 一次性操作，TRUNCATE 立刻把磁盘还回去（实测 8MB→24KB）
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def move_to(self, new_path: Path, migrate: bool = True):
        # GUI 已绿色软件化（缓存定死 Data/cache.db 不可迁移）：核心层保留，
        # 现仅核心测试使用
        self.flush()                       # 记账落库再关连接，迁移不带丢
        self.conn.close()
        if migrate:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self.path.rename(new_path)
        else:
            self.path.unlink(missing_ok=True)
        self.__init__(new_path)


def _encode(b: RenderBundle) -> bytes:
    # 键与值取自同一 sorted(items) 序：decode 按同序还原，绝不 zip 错位。
    # savez 不压（成员原样存），外层 zlib level 2 单层压缩——旧口径
    # savez_compressed 先对每成员 deflate、外层再压整个 zip 纯浪费；
    # 单层低级别实测更快且更小（3.4ms/15KB → 2.1ms/14KB，44 字样例）
    items = sorted(b.tiles.items())
    buf = io.BytesIO()
    np.savez(buf, chars=np.array([c for c, _ in items]),
             **{f"t{i}": t for i, (_, t) in enumerate(items)})
    head = f"{b.ink_median}\x00".encode()
    return zlib.compress(head + buf.getvalue(), 2)


def _decode(blob: bytes) -> RenderBundle:
    raw = zlib.decompress(blob)
    head, body = raw.split(b"\x00", 1)
    ink = float(head)
    z = np.load(io.BytesIO(body))
    chars = [str(c) for c in z["chars"]]
    tiles = {c: z[f"t{i}"] for i, c in enumerate(chars)}
    return RenderBundle(tiles, ink, frozenset(chars))
