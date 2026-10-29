from __future__ import annotations

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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    return conn


def record_decision(db_path: Path, norm_name: str, direction: str,
                    fingerprint: str | None, keep: bool = False):
    """记录/覆盖一条决定；键 = 规范化字体名 + 方向，只保留最近一次。"""
    with closing(_connect(db_path)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)",
                     (norm_name, direction, fingerprint, int(keep), time.time()))


def latest_decision(db_path: Path, norm_name: str,
                    direction: str) -> tuple[str | None, bool] | None:
    """返回 (fingerprint, keep)；无记录 None。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT fingerprint, keep FROM decisions WHERE norm_name=? AND direction=?",
            (norm_name, direction)).fetchone()
    finally:
        conn.close()
    return (row[0], bool(row[1])) if row else None
