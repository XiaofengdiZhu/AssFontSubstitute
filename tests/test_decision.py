from ass_font_substitute.core.decision.store import latest_decision
from ass_font_substitute.core.decision.store import record_decision


def test_record_and_latest(tmp_path):
    db = tmp_path / "cache.db"
    # 键是归一名：调用方职责（docstring 钉死），这里照生产口径传
    # normalize_name 结果
    record_decision(db, "华康圆体w5", "t2s", "fp1")
    assert latest_decision(db, "华康圆体w5", "t2s") == ("fp1", False)
    # 只记最近一次
    record_decision(db, "华康圆体w5", "t2s", "fp2")
    assert latest_decision(db, "华康圆体w5", "t2s") == ("fp2", False)
    # 方向独立
    assert latest_decision(db, "华康圆体w5", "s2t") is None
    # 保留原字体名也可记
    record_decision(db, "新细明体", "t2s", None, keep=True)
    assert latest_decision(db, "新细明体", "t2s") == (None, True)


def test_corrupt_db_rebuilt(tmp_path):
    # 终审 P1：记忆库损坏曾直崩替换流水线——备份 .corrupt 重建空库，
    # 读侧返 None，后续写入照常
    db = tmp_path / "decisions.db"
    db.write_bytes(b"garbage not sqlite" * 8)
    assert latest_decision(db, "华康圆体w5", "t2s") is None
    record_decision(db, "华康圆体w5", "t2s", "fp1")
    assert latest_decision(db, "华康圆体w5", "t2s") == ("fp1", False)
    assert (tmp_path / "decisions.db.corrupt").read_bytes().startswith(
        b"garbage not sqlite")


def test_connect_pragmas_match_store_conventions(tmp_path):
    # 二轮审查 P2：decision 库连接参数与 fontindex/similarity 缓存对齐
    # （WAL + synchronous=NORMAL + busy_timeout）——NORMAL 免逐条 fsync，
    # 掉电最多丢最后一次提交（记忆可重建）
    from ass_font_substitute.core.decision.store import _connect
    conn = _connect(tmp_path / "d.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()
