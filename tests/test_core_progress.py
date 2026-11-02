from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import stats
from ass_font_substitute.core.fontindex.store import update_index
from tests.fonts import build_font


def _mk(tmp_path, n):
    d = tmp_path / "fonts"
    d.mkdir()
    for i in range(n):
        build_font(d / f"f{i}.ttf", chars="永宇", family=f"F{i}")
    return d


def test_progress_reports_each_file(tmp_path):
    d = _mk(tmp_path, 3)
    calls = []
    conn = open_db(d / "AssFontSubstitute.db")
    r = update_index(d, conn,
                     progress=lambda done, total, res: calls.append((done, total)) or True)
    assert calls == [(1, 3), (2, 3), (3, 3)]
    assert r.cancelled is False and r.added == 3


def test_cancel_keeps_committed_prefix(tmp_path):
    d = _mk(tmp_path, 3)
    conn = open_db(d / "AssFontSubstitute.db")
    r = update_index(d, conn,
                     progress=lambda done, total, res: done < 2)  # 第2个文件后取消
    assert r.cancelled is True
    assert stats(conn)["fonts"] == 2              # 前两个已提交
    conn.close()
    # 重跑按增量续上，cancelled 复位
    conn = open_db(d / "AssFontSubstitute.db")
    r2 = update_index(d, conn)
    assert r2.cancelled is False and r2.added == 1
    assert stats(conn)["fonts"] == 3
