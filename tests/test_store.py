import sqlite3

from ass_font_substitute.core.fontindex import store as store_mod
from ass_font_substitute.core.fontindex.store import DDL
from ass_font_substitute.core.fontindex.store import SCHEMA_VERSION
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.fontindex.store import stats
from ass_font_substitute.core.fontindex.store import update_index
from tests.fonts import build_font


def _mklib(d, n=2):
    for i in range(n):
        build_font(d / f"f{i}.ttf", chars="永中木", family=f"X體{i}")


def test_connect_wal_synchronous_normal(tmp_path):
    # WAL + synchronous=NORMAL：逐文件事务 COMMIT 免每次 fsync；
    # synchronous 非持久是连接级参数，经 open_db 的每个连接都必须带上
    conn = open_db(tmp_path / "fontindex.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL
    conn.close()


def test_incremental(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d)
    conn = open_db(d / "fontindex.db")
    r1 = update_index(d, conn)
    assert r1.added == 2 and r1.fonts == 2
    s1 = stats(conn)
    assert s1["files"] == 2

    # 无变化 → 零解析
    r2 = update_index(d, conn)
    assert (r2.added, r2.changed, r2.removed) == (0, 0, 0)

    # 新增 + 删除 + 变更
    build_font(d / "new.ttf", chars="水", family="Y體")
    (d / "f0.ttf").unlink()
    (d / "f1.ttf").write_bytes((d / "f1.ttf").read_bytes() + b"\0")  # 内容变
    r3 = update_index(d, conn)
    assert r3.added == 1 and r3.removed >= 1
    assert stats(conn)["files"] == 2


def test_bad_file_retry(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    bad = d / "bad.ttf"
    bad.write_bytes(b"junk")
    conn = open_db(d / "fontindex.db")
    r = update_index(d, conn)
    assert r.bad == 1
    build_font(bad, chars="永", family="Fixed體")  # mtime/size 变 → 自动重试
    r2 = update_index(d, conn)
    assert r2.bad_retried == 1 and r2.fonts == 1


def test_schema_mismatch_backs_up_and_rebuilds(tmp_path):
    # spec §5：索引版本不符 → 改名备份旧库（.bak，覆盖旧备份）后全量重建
    d = tmp_path / "fonts"
    d.mkdir()
    db = d / "fontindex.db"
    old = sqlite3.connect(db)
    old.executescript(DDL)
    old.execute("INSERT INTO schema_version VALUES (99)")
    old.execute("INSERT INTO files VALUES ('old.ttf', 1, 1)")
    old.commit()
    old.close()
    (d / "fontindex.db.bak").write_bytes(b"stale backup")   # 既有 .bak 须被覆盖
    conn = open_db(db)                    # 不得抛 IndexSchemaError
    assert stats(conn) == {"files": 0, "fonts": 0, "support_sc": 0, "support_tc": 0}
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    bak = d / "fontindex.db.bak"
    assert bak.read_bytes() != b"stale backup"
    backup = sqlite3.connect(bak)
    assert backup.execute("SELECT v FROM schema_version").fetchone()[0] == 99
    assert backup.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    backup.close()


def test_schema_v2_with_xxh_column_migrates(tmp_path):
    # 真实 v2 旧库形状（files/fingerprints 含 xxh 列）→ 备份重建后照常索引
    d = tmp_path / "fonts"
    d.mkdir()
    db = d / "fontindex.db"
    old = sqlite3.connect(db)
    old.executescript("""
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
    CREATE TABLE IF NOT EXISTS fingerprints(
      rel_path TEXT, face_index INTEGER, xxh TEXT, algo INTEGER, fp TEXT,
      PRIMARY KEY(rel_path, face_index));
    """)
    old.execute("INSERT INTO schema_version VALUES (2)")
    old.execute("INSERT INTO files VALUES ('v2.ttf', 1, 1, 'deadbeef')")
    old.commit()
    old.close()
    build_font(d / "new.ttf", chars="永", family="新體")
    conn = open_db(db)
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    assert update_index(d, conn).added == 1
    assert stats(conn)["fonts"] == 1
    bak = sqlite3.connect(d / "fontindex.db.bak")
    assert bak.execute("SELECT xxh FROM files").fetchone()[0] == "deadbeef"
    bak.close()
    conn.close()


def test_whitelist_and_self_exclude(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=1)
    (d / "note.txt").write_text("x")
    (d / "fontindex.db").write_bytes(b"")
    sub = d / "sub"
    sub.mkdir()
    build_font(sub / "nested.ttf", chars="永", family="Z體")
    conn = open_db(d / "fontindex.db")
    r = update_index(d, conn)
    assert r.added == 2  # 递归含 sub，排除 txt 与 db 自身


def test_update_index_precomputes_fingerprints(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中", family="甲體", psname="JIA")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    rows = conn.execute(
        "SELECT rel_path, face_index, mtime_ns, algo, fp FROM fingerprints").fetchall()
    assert len(rows) == 1
    rel, fi, mtime, algo, fp = rows[0]
    assert (rel, fi) == ("a.ttf", 0) and mtime and fp
    from ass_font_substitute.core.fontindex.store import FP_ALGO
    assert algo == FP_ALGO


def test_update_index_parallel_executor(tmp_path):
    # 注入线程池锁并行行为：乱序完成下逐文件事务入库与串行等价，坏文件照常入 bad
    from concurrent.futures import ThreadPoolExecutor
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=4)
    (d / "bad.ttf").write_bytes(b"junk")
    conn = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, executor=ex)
    # added=发现数（scan 阶段计数，含坏文件），非入库数
    assert (r.added, r.bad) == (5, 1)
    s = stats(conn)
    assert s["files"] == 4 and s["fonts"] == 4
    assert conn.execute("SELECT COUNT(*) FROM bad").fetchone()[0] == 1


def test_update_index_parallel_fingerprints(tmp_path):
    # 并行路径逐 face 指纹不串位（TTC 3 face 属性各异）：fingerprints 表
    # 值与按 fonts 行重算 fingerprint_of 逐一相等
    import json
    from concurrent.futures import ThreadPoolExecutor

    from ass_font_substitute.core.fontindex.store import FP_ALGO
    from ass_font_substitute.core.fontindex.store import fingerprint_of
    from ass_font_substitute.core.models import CmapRanges
    from tests.fonts import build_ttc
    d = tmp_path / "fonts"
    d.mkdir()
    build_ttc(d / "t.ttc", [{"chars": "永", "family": "A體", "weight": 400},
                            {"chars": "中", "family": "B體", "weight": 700},
                            {"chars": "木", "family": "C體", "weight": 900}])
    _mklib(d, n=1)
    conn = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, executor=ex)
    assert r.added == 2 and stats(conn)["fonts"] == 4
    fps = {fi: (algo, fp) for fi, algo, fp in conn.execute(
        "SELECT face_index, algo, fp FROM fingerprints WHERE rel_path='t.ttc'")}
    assert sorted(fps) == [0, 1, 2]
    assert {algo for algo, _ in fps.values()} == {FP_ALGO}
    assert len({fp for _, fp in fps.values()}) == 3   # 三 face 指纹互异
    rows = conn.execute(
        "SELECT face_index, aliases, weight, width, italic, cmap "
        "FROM fonts WHERE rel_path='t.ttc'").fetchall()
    for fi, aliases, weight, width, italic, cmap in rows:
        assert fps[fi][1] == fingerprint_of(json.loads(aliases),
                                            CmapRanges.from_bytes(cmap),
                                            weight, width, bool(italic))


def test_update_index_parallel_cancel(tmp_path):
    # progress 返 False → 停止消费：cancelled=True，已入库前缀保留，
    # 完成数与回调严格交替（apply 与 progress 不重不漏）
    from concurrent.futures import ThreadPoolExecutor
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=5)
    conn = open_db(d / "fontindex.db")
    seen = []

    def progress(done, total, res):
        seen.append((done, total))
        return done < 2

    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, progress=progress, executor=ex)
    # cancelled 且库中恰有已完成的 2 个（added 是发现数含未入库的 3 个）
    assert r.cancelled and r.added == 5
    assert stats(conn)["files"] == 2
    assert seen == [(1, 5), (2, 5)]


def test_update_index_broken_pool_falls_back(tmp_path, monkeypatch):
    # 池整体崩坏（任务体抛 BrokenProcessPool）→ 剩余文件回退串行续完，
    # 不丢不炸、无取消标记
    from concurrent.futures import ThreadPoolExecutor

    def boom(p):
        raise store_mod.BrokenProcessPool()

    monkeypatch.setattr(store_mod, "_parse_task", boom)
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=3)
    conn = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, executor=ex)
    assert not r.cancelled and r.added == 3
    assert stats(conn)["fonts"] == 3


def test_update_index_real_process_pool(tmp_path):
    # 真进程池 smoke：Windows spawn 导入链 + FontRecord pickle 全链
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=2)
    conn = open_db(d / "fontindex.db")
    r = update_index(d, conn, max_threads=2)
    assert r.added == 2 and stats(conn)["fonts"] == 2
