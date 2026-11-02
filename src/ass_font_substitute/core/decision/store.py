from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

# schema 版本（照 fontindex 口径）：未来加列/改键时递增，旧库版本不符
# 备份 .bak 重建——没有版本标记，CREATE TABLE IF NOT EXISTS 会对旧库
# 静默跳建、INSERT 列数不匹配直穿（审查三轮 P2）
SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS decisions(
  norm_name TEXT, direction TEXT, fingerprint TEXT, keep INTEGER,
  updated_at REAL, PRIMARY KEY(norm_name, direction));
CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # 与 fontindex/similarity 缓存同口径：commit 不逐条 fsync，掉电
        # 最多丢最后一次提交（决定记忆可重建，二轮审查 P2 口径对齐）
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(DDL)
        row = conn.execute("SELECT v FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version VALUES (?)",
                         (SCHEMA_VERSION,))
            conn.commit()
        elif row[0] != SCHEMA_VERSION:
            conn.close()   # Windows：句柄不关，隔离 os.replace 必炸
            _rebuild_stale(db_path)
            return _connect(db_path)
    except sqlite3.DatabaseError:
        conn.close()   # Windows：句柄不关，隔离 os.replace 必炸
        raise
    return conn


def _rebuild_corrupt(db_path: Path) -> None:
    """记忆库损坏（非空但非法）→ 备份 .corrupt 隔离（记忆可丢：只影响
    回放默认值，不损字幕数据）。旁车一并隔离，防旧 WAL 回放污染。
    主库被占用转译 OperationalError（打不上≠损坏，别炸调用方）。
    只隔离不回连：重建由调用方再走 _connect 完成。"""
    _rebuild(db_path, ".corrupt")


def _rebuild_stale(db_path: Path) -> None:
    """schema 版本不符 → 备份 .bak（覆盖既有）重建空库（fontindex 口径：
    旧库列结构对不上新 DDL，INSERT 列数不匹配直穿，只能弃）。"""
    _rebuild(db_path, ".bak")


def _rebuild(db_path: Path, suffix: str) -> None:
    bak = db_path.parent / (db_path.name + suffix)
    try:
        os.replace(db_path, bak)
    except OSError as e:
        raise sqlite3.OperationalError(f"库隔离失败（被占用？）: {e}") from e
    for side in ("-wal", "-shm"):
        p = Path(str(db_path) + side)
        if p.exists():
            try:
                os.replace(p, Path(str(bak) + side))
            except OSError:
                with contextlib.suppress(OSError):
                    p.unlink()


def record_decision(db_path: Path, norm_name: str, direction: str,
                    fingerprint: str | None, keep: bool = False):
    """记录/覆盖一条决定；键 = 规范化字体名 + 方向，只保留最近一次。

    本层不做归一：norm_name 必须由调用方传 normalize_name 结果
    （生产调用点 subs_vm.record_snapshot 传快照 norm）。传原始名记忆
    静默 miss，不再补救。"""
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError:
        raise                                # 打不开≠损坏（目录缺失/权限）
    except sqlite3.DatabaseError:
        _rebuild_corrupt(db_path)
        conn = _connect(db_path)
    with closing(conn), conn:
        conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)",
                     (norm_name, direction, fingerprint, int(keep), time.time()))


def latest_decision(db_path: Path, norm_name: str,
                    direction: str) -> tuple[str | None, bool] | None:
    """返回 (fingerprint, keep)；无记录 None。norm_name 须传归一名，
    同 record_decision。"""
    try:
        conn = _connect(db_path)
    except sqlite3.OperationalError:
        raise                                # 打不开≠损坏（目录缺失/权限）
    except sqlite3.DatabaseError:
        _rebuild_corrupt(db_path)            # 损坏：空库视角，读侧返 None
        conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT fingerprint, keep FROM decisions WHERE norm_name=? AND direction=?",
            (norm_name, direction)).fetchone()
    finally:
        conn.close()
    return (row[0], bool(row[1])) if row else None
