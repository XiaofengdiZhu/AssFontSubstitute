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


def test_force_rebuild_clears_and_reparses(tmp_path):
    # force=True：清空全部数据表再走增量 → 盘上文件全部按新增重解析。
    # 「已最新」目录强制重建的入口：增量门只看 size+mtime，索引内容
    # 坏了它判定不了
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d)
    conn = open_db(d / "fontindex.db")
    assert update_index(d, conn).fonts == 2
    (d / "bad.ttf").write_bytes(b"junk")
    r2 = update_index(d, conn)
    assert r2.bad == 1 and r2.added == 1

    # 无变化：增量门放行，不清表
    r3 = update_index(d, conn)
    assert (r3.added, r3.changed, r3.removed) == (0, 0, 0)
    assert stats(conn)["fonts"] == 2

    # 强制：旧库清空重来，好文件重入、坏文件重判
    r4 = update_index(d, conn, force=True)
    assert r4.added == 3 and r4.fonts == 2 and r4.bad == 1
    assert stats(conn)["fonts"] == 2
    assert conn.execute("SELECT COUNT(*) FROM bad").fetchone()[0] == 1
    # cmap_data 同步清空重灌（fonts 行的 cmap_id 引用自洽）
    assert conn.execute(
        "SELECT COUNT(*) FROM cmap_data WHERE id NOT IN "
        "(SELECT cmap_id FROM fonts)").fetchone()[0] == 0


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
    conn = open_db(db)                    # 隔离失败须折成 OperationalError（offline 语义）
    assert stats(conn) == {"files": 0, "fonts": 0, "support_sc": 0, "support_tc": 0}
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    bak = d / "fontindex.db.bak"
    assert bak.read_bytes() != b"stale backup"
    backup = sqlite3.connect(bak)
    assert backup.execute("SELECT v FROM schema_version").fetchone()[0] == 99
    assert backup.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    backup.close()


def test_corrupt_db_backs_up_and_rebuilds(tmp_path):
    # 终审 P1：库损坏（非空但非法，NAS 截断/磁盘坏块）曾 executescript
    # 直崩无重建——备份 .corrupt 重建空库
    d = tmp_path / "fonts"
    d.mkdir()
    db = d / "fontindex.db"
    db.write_bytes(b"this is not a sqlite database" * 10)
    conn = open_db(db)
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] \
        == SCHEMA_VERSION
    assert stats(conn) == {"files": 0, "fonts": 0, "support_sc": 0,
                           "support_tc": 0}
    conn.close()
    assert (d / "fontindex.db.corrupt").read_bytes().startswith(
        b"this is not a sqlite database")
    # 重建后的库可正常使用
    conn2 = open_db(db)
    assert conn2.execute("SELECT v FROM schema_version").fetchone()[0] \
        == SCHEMA_VERSION
    conn2.close()


def test_schema_mismatch_quarantines_wal_sidecars(tmp_path):
    # 终审 P1：版本升级只挪主库时遗留 -wal/-shm 被新库捡到——并发读者
    # 场景旧 WAL 回放污染新库（schema_version 双行）。旁车须一并隔离。
    # sqlite 自身 close 会清旁车，并发残留直接单测 _quarantine
    from ass_font_substitute.core.fontindex.store import _quarantine

    d = tmp_path / "fonts"
    d.mkdir()
    db = d / "fontindex.db"
    db.write_bytes(b"main db bytes")
    (d / "fontindex.db-wal").write_bytes(b"stale wal")
    (d / "fontindex.db-shm").write_bytes(b"stale shm")
    bak = _quarantine(db, ".bak")
    assert bak.read_bytes() == b"main db bytes"
    assert not (d / "fontindex.db-wal").exists()
    assert not (d / "fontindex.db-shm").exists()
    assert (d / "fontindex.db.bak-wal").read_bytes() == b"stale wal"
    assert (d / "fontindex.db.bak-shm").read_bytes() == b"stale shm"


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
    # 历史编号 v2（xxh 时代）在新发布基线下属任意旧版（基线重置为 1 后
    # 现行版本已用到 2），改标 1 触发版本不符——旧形状库照常备份重建
    old.execute("INSERT INTO schema_version VALUES (1)")
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


_VF_AXES = [{"name": "Weight", "tag": "wght",
             "min": 300, "default": 400, "max": 900}]
_VF_INSTANCES = [{"name": "Light", "location": {"wght": 300}},
                 {"name": "Black", "location": {"wght": 900}}]


def test_update_index_persists_instances(tmp_path):
    # v2：VF 解析出的默认行+命名实例行连同 inst_index/var_axes 入库，
    # fingerprints 主键三元组 (rel_path, face_index, inst_index) 齐备
    import struct

    from tests.fonts import build_vf
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體", axes=_VF_AXES,
             instances=_VF_INSTANCES)
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    rows = conn.execute(
        "SELECT inst_index, weight, var_axes FROM fonts ORDER BY inst_index"
    ).fetchall()
    assert [r[0] for r in rows] == [0, 1, 2]
    # 默认行：inst_index=0、var_axes 为 NULL（空元组不占库）
    assert rows[0][2] is None
    light = next(r for r in rows if r[0] == 1)
    assert light[1] == 300
    assert struct.unpack("<1f", light[2]) == (300.0,)
    black = next(r for r in rows if r[0] == 2)
    assert black[1] == 900
    assert struct.unpack("<1f", black[2]) == (900.0,)
    # 指纹预计算逐实例对齐（三行指纹互异）
    fps = conn.execute(
        "SELECT inst_index, fp FROM fingerprints WHERE rel_path='vf.ttf' "
        "ORDER BY inst_index").fetchall()
    assert [r[0] for r in fps] == [0, 1, 2]
    assert len({r[1] for r in fps}) == 3


def test_rescan_same_file_idempotent(tmp_path):
    # 同文件重扫：UNIQUE(rel_path, face_index, inst_index) 冲突不炸，
    # 行数不翻倍（_apply_result 先 DELETE 再插，天然幂等——回归锁）
    from tests.fonts import build_vf
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體", axes=_VF_AXES,
             instances=_VF_INSTANCES)
    conn = open_db(d / "fontindex.db")
    assert update_index(d, conn).fonts == 3
    import os
    os.utime(d / "vf.ttf")          # mtime 变 → changed → 重灌
    assert update_index(d, conn).changed == 1
    assert stats(conn)["fonts"] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM fingerprints WHERE rel_path='vf.ttf'"
    ).fetchone()[0] == 3
    update_index(d, conn, force=True)   # 强制全量重灌同锁
    assert stats(conn)["fonts"] == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM fingerprints WHERE rel_path='vf.ttf'"
    ).fetchone()[0] == 3


def test_v1_db_backed_up_and_rebuilt(tmp_path):
    # v1（无实例维度）旧库：版本不符 → .bak 备份（覆盖既有）+ v2 全量重建
    d = tmp_path / "lib"
    d.mkdir()
    db = d / "fontindex.db"
    old = sqlite3.connect(db)
    old.executescript("""
    CREATE TABLE IF NOT EXISTS schema_version(v INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS files(
      rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER);
    CREATE TABLE IF NOT EXISTS cmap_data(
      id INTEGER PRIMARY KEY, data BLOB NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS fonts(
      id INTEGER PRIMARY KEY, rel_path TEXT, face_index INTEGER, psname TEXT,
      version TEXT, aliases TEXT, weight INTEGER, width INTEGER, italic INTEGER,
      cmap_id INTEGER NOT NULL, script TEXT, category TEXT, display_name TEXT,
      family_names TEXT, name_langs TEXT, name_records BLOB,
      UNIQUE(rel_path, face_index));
    CREATE TABLE IF NOT EXISTS bad(
      rel_path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, reason TEXT);
    CREATE TABLE IF NOT EXISTS fingerprints(
      rel_path TEXT, face_index INTEGER, mtime_ns INTEGER, algo INTEGER, fp TEXT,
      PRIMARY KEY(rel_path, face_index));
    """)
    old.execute("INSERT INTO schema_version VALUES (1)")
    old.execute("INSERT INTO files VALUES ('old.ttf', 1, 1)")
    old.commit()
    old.close()
    (d / "fontindex.db.bak").write_bytes(b"stale backup")
    build_font(d / "a.ttf", chars="永", family="新體")
    conn = open_db(db)                    # 隔离失败须折成 OperationalError（offline 语义）
    assert conn.execute("SELECT v FROM schema_version").fetchone()[0] \
        == SCHEMA_VERSION
    assert update_index(d, conn).added == 1
    assert stats(conn)["fonts"] == 1
    bak = sqlite3.connect(d / "fontindex.db.bak")
    assert bak.execute("SELECT v FROM schema_version").fetchone()[0] == 1
    assert bak.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    bak.close()


def test_load_font_rows_instance_columns(tmp_path):
    # load_font_rows 行尾追加 inst_index(16)/var_axes(17)：var_axes BLOB
    # 还原 tuple，NULL（静态行）→ ()
    from ass_font_substitute.core.fontindex.store import load_font_rows
    from tests.fonts import build_vf
    d = tmp_path / "lib"
    d.mkdir()
    build_vf(d / "vf.ttf", "永", family="变化體", axes=_VF_AXES,
             instances=_VF_INSTANCES)
    build_font(d / "s.ttf", chars="永", family="静體")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    got = {}
    for row in load_font_rows(conn):
        got[(row[0], row[16])] = row[17]
    assert got[("vf.ttf", 1)] == (300.0,)
    assert got[("vf.ttf", 2)] == (900.0,)
    assert got[("vf.ttf", 0)] == ()
    assert got[("s.ttf", 0)] == ()


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
    import zlib
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
        "SELECT f.face_index, f.aliases, f.weight, f.width, f.italic, cd.data "
        "FROM fonts f JOIN cmap_data cd ON cd.id=f.cmap_id "
        "WHERE f.rel_path='t.ttc'").fetchall()
    for fi, aliases, weight, width, italic, blob in rows:
        assert fps[fi][1] == fingerprint_of(json.loads(aliases),
                                            CmapRanges.from_bytes(zlib.decompress(blob)),
                                            weight, width, bool(italic))


def test_cmap_dedup_and_roundtrip(tmp_path):
    # v4：同 cmap（同 chars 异字重）入库共享一行 cmap_data，fonts 行存
    # 引用；不同 chars 各自一行；load_font_rows 解压往返精确无损
    import zlib
    from concurrent.futures import ThreadPoolExecutor

    from ass_font_substitute.core.fontindex.store import load_font_rows
    from ass_font_substitute.core.models import CmapRanges
    from tests.fonts import build_ttc
    d = tmp_path / "fonts"
    d.mkdir()
    build_ttc(d / "same.ttc", [{"chars": "永中", "family": "A體", "weight": 400},
                               {"chars": "永中", "family": "A體", "weight": 700}])
    build_font(d / "diff.ttf", chars="木", family="B體")
    conn = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, executor=ex)
    assert r.added == 2 and stats(conn)["fonts"] == 3
    assert conn.execute("SELECT COUNT(*) FROM cmap_data").fetchone()[0] == 2
    same_ids = [c for (c,) in conn.execute(
        "SELECT cmap_id FROM fonts WHERE rel_path='same.ttc' ORDER BY face_index")]
    assert same_ids[0] == same_ids[1]
    # tests/fonts.py 字形集 = 空格 + 数字字母 + 指定 chars；往返须与
    # from_chars 期望精确相等（抓 starts/ends 错位与区间截断）
    base = {0x20} | {ord(c) for c in "0123456789Aa"}
    expect = {"same.ttc": CmapRanges.from_chars(base | {ord("永"), ord("中")}),
              "diff.ttf": CmapRanges.from_chars(base | {ord("木")})}
    for row in load_font_rows(conn):
        # 列序：rel_path(0) face_index(1) psname(2) version(3) aliases(4)
        # display_name(5) family_names(6) name_langs(7) weight(8) width(9)
        # italic(10) cmap blob(11) ……
        assert CmapRanges.from_bytes(zlib.decompress(row[11])) == expect[row[0]]


def test_cmap_gc_on_incremental(tmp_path):
    # 独占 cmap 的文件被删除/变更 → 收尾 GC 回收孤儿 cmap_data 行
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永中木水火", family="A體")
    build_font(d / "b.ttf", chars="永中木水火", family="B體")   # 与 a 共享 cmap
    build_font(d / "c.ttf", chars="山河大海", family="C體")     # 独占 cmap
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    assert conn.execute("SELECT COUNT(*) FROM cmap_data").fetchone()[0] == 2
    (d / "c.ttf").unlink()
    update_index(d, conn)
    assert conn.execute("SELECT COUNT(*) FROM cmap_data").fetchone()[0] == 1
    build_font(d / "a.ttf", chars="风云雷电", family="A體")     # 共享组换 blob
    update_index(d, conn)
    assert conn.execute("SELECT COUNT(*) FROM cmap_data").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM cmap_data WHERE id NOT IN "
                        "(SELECT cmap_id FROM fonts)").fetchone()[0] == 0


def test_update_index_defers_checkpoints(tmp_path):
    # 建索引期间停自动 checkpoint、收尾一次 TRUNCATE：-wal 归零，
    # 连接级 wal_autocheckpoint 还原（连接被 GUI 复用，不能留 0）
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=3)
    db = d / "fontindex.db"
    conn = open_db(db)
    update_index(d, conn)
    assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 1000
    assert db.with_name(db.name + "-wal").stat().st_size == 0
    conn.close()


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


def test_name_langs_roundtrip(tmp_path):
    # v7：name_langs 列 {nameID: [[langID, 文本]]} 原样入库，装载侧
    # （library）还原 int 键；无对应记录的 nameID 不出键
    import json

    from ass_font_substitute.core.fontindex.store import load_font_rows
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="TestFam",
               extra_aliases={"zh-CN": "简体家族", "zh-TW": "繁體家族"})
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    row = next(load_font_rows(conn))
    nl = json.loads(row[7])            # 列序见 test_cmap_dedup_and_roundtrip
    assert nl["1"] == [[0x404, "繁體家族"], [0x409, "TestFam"],
                       [0x804, "简体家族"]]   # name 表按 langID 升序
    assert set(nl) == {"1", "4"}


def test_name_records_blob_roundtrip(tmp_path):
    # v8：name_records BLOB（zlib JSON）入库；load_font_rows 不取此列
    # （全量装实体是几十 MB 常驻内存），消费方按需单行读
    import json
    import zlib

    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="TestFam")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    (blob,) = conn.execute(
        "SELECT name_records FROM fonts WHERE rel_path='a.ttf'").fetchone()
    recs = [tuple(r) for r in json.loads(zlib.decompress(blob))]
    assert (1, 3, 0x409, "TestFam") in recs
    assert all(r[0] in (1, 2, 3, 4, 16) for r in recs)
    assert all(r[1] in (1, 3) for r in recs)   # Windows+Mac 平台


def test_update_index_parallel_deleted_between_scan_and_dispatch(tmp_path, monkeypatch):
    # 审查二轮 P1 回归：并行派发期（与扫描期）p.stat() 曾无删文件守卫——
    # 扫描后文件被外部删（清理/杀毒）炸整目录更新。现两处都跳过，
    # 下轮 removed 收敛；total 收缩防进度永不达满
    import os
    from concurrent.futures import ThreadPoolExecutor

    import ass_font_substitute.core.fontindex.store as st_mod

    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, n=3)
    ghost = d / "ghost.ttf"
    real_scan = st_mod.scan_dir

    def scan_with_ghost(root):
        return [*real_scan(root), ghost]   # 扫描窗口内还在，stat 前被删

    monkeypatch.setattr(st_mod, "scan_dir", scan_with_ghost)
    conn = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r = update_index(d, conn, executor=ex)
    assert r.added == 3                       # 幽灵不计入
    assert stats(conn)["fonts"] == 3

    # 派发期被删：victim 触碰成 changed 进 todo，扫描期 stat 放行、
    # 派发循环 stat（同路径第二次调用）时已删——跳过不炸
    monkeypatch.setattr(st_mod, "scan_dir", real_scan)
    victim = d / "f1.ttf"
    os.utime(victim)                          # mtime 变化 → changed → todo
    os.utime(d / "f0.ttf")                    # 再 touch 一个：todo≥2 才走并行分支
    real_stat = type(victim).stat
    calls = {"n": 0}

    def stat_delete_on_second(self, *a, **kw):
        if self == victim:
            calls["n"] += 1
            if calls["n"] >= 2:
                victim.unlink(missing_ok=True)
                raise OSError(2, "gone")
        return real_stat(self, *a, **kw)
    monkeypatch.setattr(type(victim), "stat", stat_delete_on_second)
    conn2 = open_db(d / "fontindex.db")
    with ThreadPoolExecutor(max_workers=2) as ex:
        r2 = update_index(d, conn2, executor=ex)
    assert r2.changed == 2                    # changed 在扫描期已计（幻影计数，
                                              # 与串行路径同款）；victim 行仍在，
                                              # 不炸不跳整个任务
    assert stats(conn2)["fonts"] == 3
    monkeypatch.undo()                        # 下轮真实扫描：removed 收敛
    conn3 = open_db(d / "fontindex.db")
    r3 = update_index(d, conn3, executor=ThreadPoolExecutor(max_workers=2))
    assert r3.removed == 1 and stats(conn3)["fonts"] == 2


def test_scan_dir_skips_junction(tmp_path):
    # 自指 junction：is_symlink()=False 但成环，不跳过则递归到路径极限
    # OSError 炸整次扫描（真机复现 WinError 1921「系统无法辨识文件名」）
    import subprocess
    import sys

    import pytest

    from ass_font_substitute.core.fontindex.store import scan_dir

    if sys.platform != "win32":
        pytest.skip("Windows junction")
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "a.ttf", chars="永", family="A體")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(d / "loop"),
                        str(d)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("junction 创建失败: "
                    + r.stderr.decode("gbk", "replace").strip())
    out = scan_dir(d)
    assert [p.name for p in out] == ["a.ttf"]


def test_serial_progress_reaches_total_on_skip(tmp_path, monkeypatch):
    # 串行路径守卫窗口 = 分类 stat 之后、串行 stat 之前文件被删（杀毒
    # 隔离/清理恰落 parse 窗口；分类前的删除由扫描期 stat 守卫处理，
    # 进不了 todo）：旧码 done-=1 复用序号——后续回执停 total-1 永不
    # 达满；修后与并行路径同口径（stat 前收缩 total）
    d = tmp_path / "fonts"
    d.mkdir()
    _mklib(d, 3)
    real_parse = store_mod.parse_font_file
    state = {"n": 0}

    def parse_then_delete_next(p):
        state["n"] += 1
        if state["n"] == 1:
            (d / "f1.ttf").unlink()   # 首文件 parse 窗口内删下一个
        return real_parse(p)

    monkeypatch.setattr(store_mod, "parse_font_file", parse_then_delete_next)
    conn = open_db(d / "fontindex.db")
    calls = []
    update_index(
        d, conn, max_threads=1,
        progress=lambda done, total, res:
            (calls.append((done, total)) or True))
    assert calls == [(1, 3), (2, 2)]            # 收缩后达满、序号不重复
    # skip 路径书记正确：f2 入库、f1 不留行（added 在分类期已计 3，虚高
    # 为旧码同病——GUI 不消费 res，仅记账）
    assert (d / "f2.ttf").relative_to(d).as_posix() in dict(
        conn.execute("SELECT rel_path, size FROM files"))
    rows = dict(conn.execute("SELECT rel_path, size FROM files"))
    assert set(rows) == {"f0.ttf", "f2.ttf"}    # f1 被删不留行


def test_scan_dir_skips_permission_denied_dir(tmp_path, monkeypatch):
    # 审查三轮 P2 回归：权限锁定子目录（盘根 System Volume Information、
    # ACL 锁定目录，实测 WinError 5）曾炸整次索引——现与 junction 守卫
    # 同口径跳过该子树。真权限目录构造依赖账户环境，注入 scandir 异常
    import os

    from ass_font_substitute.core.fontindex.store import scan_dir

    d = tmp_path / "fonts"
    (d / "locked").mkdir(parents=True)
    build_font(d / "a.ttf", chars="永", family="A體")
    real_scandir = os.scandir

    def fake_scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(5, "拒绝访问。")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    out = scan_dir(d)
    assert [p.name for p in out] == ["a.ttf"]
