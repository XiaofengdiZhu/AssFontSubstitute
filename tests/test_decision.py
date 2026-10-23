from ass_font_substitute.core.decision.store import latest_decision, record_decision


def test_record_and_latest(tmp_path):
    db = tmp_path / "cache.db"
    record_decision(db, "華康圓體w5", "t2s", "fp1")
    assert latest_decision(db, "華康圓體w5", "t2s") == ("fp1", False)
    # 只记最近一次
    record_decision(db, "華康圓體w5", "t2s", "fp2")
    assert latest_decision(db, "華康圓體w5", "t2s") == ("fp2", False)
    # 方向独立
    assert latest_decision(db, "華康圓體w5", "s2t") is None
    # 保留原字体名也可记
    record_decision(db, "新細明體", "t2s", None, keep=True)
    assert latest_decision(db, "新細明體", "t2s") == (None, True)
