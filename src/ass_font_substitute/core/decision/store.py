from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS decisions(
  norm_name TEXT, direction TEXT, fingerprint TEXT, keep INTEGER,
  updated_at REAL, PRIMARY KEY(norm_name, direction));
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
    except sqlite3.DatabaseError:
        conn.close()   # Windows：句柄不关，隔离 os.replace 必炸
        raise
    return conn


def _rebuild_corrupt(db_path: Path) -> sqlite3.Connection:
    """记忆库损坏（非空但非法）→ 备份 .corrupt 重建空库（记忆可丢：只
    影响回放默认值，不损字幕数据）。旁车一并隔离，防旧 WAL 回放污染。
    主库被占用转译 OperationalError（打不上≠损坏，别炸调用方）。"""
    bak = db_path.parent / (db_path.name + ".corrupt")
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
    return _connect(db_path)


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
        conn = _rebuild_corrupt(db_path)
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
        conn = _rebuild_corrupt(db_path)     # 损坏：空库视角，读侧返 None
    try:
        row = conn.execute(
            "SELECT fingerprint, keep FROM decisions WHERE norm_name=? AND direction=?",
            (norm_name, direction)).fetchone()
    finally:
        conn.close()
    return (row[0], bool(row[1])) if row else None
