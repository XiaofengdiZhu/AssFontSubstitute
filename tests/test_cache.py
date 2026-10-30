import time

import numpy as np

from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.core.similarity.metrics import CompareResult
from ass_font_substitute.core.similarity.render import RenderBundle


def _bundle(chars="永"):
    # 每字符灰度不同：解码值逐字符断言才能抓到 keys/values 错位
    tiles = {c: np.full((48, 48), 100 + i * 7, np.uint8) for i, c in enumerate(chars)}
    return RenderBundle(tiles, 40.0, frozenset(chars))


def test_render_lazy_and_hit(tmp_path):
    c = Cache(tmp_path / "cache.db")
    calls = []
    chars = "永中木8b"  # 插入序 ≠ sorted 序，错位可被值断言抓到
    def loader():
        calls.append(1)
        return _bundle(chars)
    b1 = c.get_render("fpA", "v1", loader)
    b2 = c.get_render("fpA", "v1", loader)
    assert len(calls) == 1 and set(b2.tiles) == set(b1.tiles)
    # 控制器裁定：逐字符校验 tile 值（编码按 sorted(items) 同序取键值，防 zip 错位）
    for ch in chars:
        assert np.array_equal(b2.tiles[ch], b1.tiles[ch])
    c.get_render("fpA", "v2", loader)  # 文字版本变 → miss
    assert len(calls) == 2


def test_pair_roundtrip_and_order(tmp_path):
    c = Cache(tmp_path / "cache.db")
    r = CompareResult(0.91, 1.08, 40)
    c.put_pair("zzz", "aaa", "v1", r)
    got = c.get_pair("aaa", "zzz", "v1")   # 键序无关
    assert got.score == 0.91 and got.size_ratio == 1.08


def test_trim_and_clear(tmp_path):
    c = Cache(tmp_path / "cache.db")
    for i in range(3):
        c.get_render(f"fp{i}", "v1", _bundle)
    assert c.render_bytes > 0
    c.trim(max_bytes=1)   # 强制淘汰
    assert c.render_bytes == 0   # renders 真被清（pair_count 恒 0 无验证力）
    assert c.pair_count == 0
    c.clear_all()


def test_algo_ver_isolates_entries(tmp_path):
    # 算法版本入键：ver 变更后旧口径持久缓存不命中（静默过期根因）
    c = Cache(tmp_path / "cache.db")
    calls = []
    c.get_render("fp", "v1", lambda: calls.append(1) or _bundle())
    assert len(calls) == 1
    c.ALGO_VER = "3"                       # 模拟算法升级（当前已是 v2）
    c.get_render("fp", "v1", lambda: calls.append(1) or _bundle())
    assert len(calls) == 2                 # 旧口径不命中，重算


def test_pairs_lru_trim(tmp_path):
    c = Cache(tmp_path / "cache.db")
    r = CompareResult(0.5, 1.0, 4)
    for i in range(6):
        c.put_pair(f"a{i}", f"b{i}", "v1", r)
    c._trim_pairs(max_rows=3)
    assert c.pair_count == 3
    # 早写的被淘汰（used_at 递增序，同秒全等时删任意——只验数量与存活数）
    assert c.pair_count == 3


def test_old_schema_pairs_dropped(tmp_path):
    # 旧库 pairs 无 used_at 列：打开即弃表重建，不炸
    import sqlite3
    p = tmp_path / "cache.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE pairs(fpa TEXT, fpb TEXT, text_ver TEXT, "
                 "score REAL, size_ratio REAL, n_chars INT, "
                 "PRIMARY KEY(fpa, fpb, text_ver))")
    conn.execute("INSERT INTO pairs VALUES ('a','b','v1',0.5,1.0,4)")
    conn.commit()
    conn.close()
    c = Cache(p)
    assert c.pair_count == 0               # 旧行弃用（键前缀也不兼容）
    c.put_pair("a", "b", "v1", CompareResult(0.5, 1.0, 4))
    assert c.get_pair("a", "b", "v1").score == 0.5


def test_move(tmp_path):
    p1, p2 = tmp_path / "a.db", tmp_path / "b" / "c.db"
    p2.parent.mkdir()
    c = Cache(p1)
    c.get_render("fp", "v1", _bundle)
    c.move_to(p2, migrate=True)
    assert p2.exists()
    c2 = Cache(p2)
    assert c2.get_render("fp", "v1", _bundle).tiles


def test_hit_defers_used_at(tmp_path):
    # P2：读命中不再逐条 UPDATE+commit，只内存记账，flush() 才落库
    c = Cache(tmp_path / "cache.db")
    c.get_render("fp", "v1", _bundle)
    t0 = c.conn.execute("SELECT used_at FROM renders").fetchone()[0]
    time.sleep(0.01)
    c.get_render("fp", "v1", _bundle)              # 命中
    t1 = c.conn.execute("SELECT used_at FROM renders").fetchone()[0]
    assert t1 == t0                                # 未落库
    c.flush()
    assert c.conn.execute("SELECT used_at FROM renders").fetchone()[0] > t0


def test_pair_hit_defers_used_at(tmp_path):
    c = Cache(tmp_path / "cache.db")
    c.put_pair("a", "b", "v1", CompareResult(0.5, 1.0, 4))
    t0 = c.conn.execute("SELECT used_at FROM pairs").fetchone()[0]
    time.sleep(0.01)
    c.get_pair("a", "b", "v1")
    assert c.conn.execute("SELECT used_at FROM pairs").fetchone()[0] == t0
    c.flush()
    assert c.conn.execute("SELECT used_at FROM pairs").fetchone()[0] > t0


def test_pending_autoflush_at_threshold(tmp_path):
    # 记账满 FLUSH_PENDING 条自动落库，无需显式 flush
    c = Cache(tmp_path / "cache.db")
    c.FLUSH_PENDING = 4
    ts = {}
    for i in range(4):
        c.put_pair(f"a{i}", f"b{i}", "v1", CompareResult(0.5, 1.0, 4))
        ts[i] = c.conn.execute(
            "SELECT used_at FROM pairs WHERE fpa=? AND fpb=?",
            (f"a{i}", f"b{i}")).fetchone()[0]          # 写入时的时间戳
        time.sleep(0.01)
        c.get_pair(f"a{i}", f"b{i}", "v1")             # 命中记账（比写入新）
    n = c.conn.execute(
        "SELECT COUNT(*) FROM pairs WHERE used_at>?", (ts[0],)).fetchone()[0]
    assert n == 4 and not c._pending_pair              # 命中时间戳已批量落库


def test_trim_flushes_pending_lru(tmp_path):
    # LRU 正确性：大 bundle 旧写、小 bundle 新写，命中大 bundle（记账未落库）
    # → trim 须先 flush 再淘汰，留下的应是刚用过的大 bundle 而非盘上时间戳新的小 bundle
    big = _bundle("永中木水火")
    small = _bundle("永")
    c = Cache(tmp_path / "cache.db")
    c.get_render("fpbig", "v1", lambda: big)
    c.get_render("fpsmall", "v1", lambda: small)
    nb = c.conn.execute("SELECT nbytes FROM renders WHERE fingerprint='fpbig'").fetchone()[0]
    ns = c.conn.execute("SELECT nbytes FROM renders WHERE fingerprint='fpsmall'").fetchone()[0]
    time.sleep(0.01)
    c.get_render("fpbig", "v1", lambda: big)       # 命中记账（最新），未落库
    c.trim(max_bytes=nb + ns - 1)
    kept = {r[0] for r in c.conn.execute("SELECT fingerprint FROM renders")}
    assert kept == {"fpbig"}                       # 盘上时间戳旧的 big 因 flush 幸存


def test_clear_and_move_settle_pending(tmp_path):
    # clear_all 清记账；move_to 落库后再迁移（命中记账的时间戳带到新库）
    c = Cache(tmp_path / "a.db")
    c.get_render("fp", "v1", _bundle)
    c.get_render("fp", "v1", _bundle)
    c.clear_all()
    assert not c._pending_render and c.pair_count == 0
    c.get_render("fp", "v1", _bundle)
    t_old = c.conn.execute("SELECT used_at FROM renders").fetchone()[0]
    time.sleep(0.01)
    c.get_render("fp", "v1", _bundle)              # 命中记账（比 t_old 新），未落库
    c.move_to(tmp_path / "b" / "c.db", migrate=True)
    c2 = Cache(tmp_path / "b" / "c.db")
    assert c2.conn.execute("SELECT used_at FROM renders").fetchone()[0] > t_old


def test_put_render_encoded_direct(tmp_path):
    # 池模式 worker 回传已编码 blob：直接落库免重编码，读命中不重渲
    from ass_font_substitute.core.similarity.cache import _encode
    c = Cache(tmp_path / "cache.db")
    src = _bundle("永中")
    c.put_render_encoded("fp", "v1", _encode(src))
    calls = []
    b = c.get_render("fp", "v1", lambda: calls.append(1) or _bundle("永中"))
    assert not calls
    for ch in "永中":
        assert np.array_equal(b.tiles[ch], src.tiles[ch])
    assert c.render_bytes == len(_encode(src))


def test_open_purges_stale_algo_rows(tmp_path):
    # ALGO 升版后旧前缀行打开即清：永不命中纯占空间，还污染 render_bytes
    # 统计（设置页显假 MB）
    p = tmp_path / "cache.db"
    c = Cache(p)
    c.put_render_encoded("fp", "v1", b"keep")
    with c.conn:        # \x00 不能进 SQL 字面量，一律参数绑定
        c.conn.execute("INSERT INTO renders VALUES (?,?,?,?,?)",
                       ("old", "1\x00v0", b"", 99999, 0))
        c.conn.execute("INSERT INTO pairs VALUES (?,?,?,?,?,?,?)",
                       ("a", "b", "1\x00v0", 0.5, 1.0, 4, 0))
    c.conn.close()
    c2 = Cache(p)
    assert c2.render_bytes == 4              # 旧行不计（b"keep"）
    assert c2.pair_count == 0
    n = c2.conn.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
    assert n == 1                            # 当前版本行幸存


def test_delete_reclaims_disk_space(tmp_path):
    # auto_vacuum=FULL：删除后页归还 OS，文件不驻留百 MB 空洞（实测旧行为：
    # Data/cache.db 27710/27829 页 freelist，内容 2MB 文件 108MB）
    p = tmp_path / "cache.db"
    c = Cache(p)
    big = b"\0" * (8 * 1024 * 1024)
    with c.conn:
        c.conn.execute("INSERT INTO renders VALUES ('fp', ?, ?, ?, 0)",
                       (c._kv("v1"), big, len(big)))
    c.conn.commit()
    assert p.stat().st_size > 8 * 1024 * 1024
    c.clear_all()
    assert p.stat().st_size < 4 * 1024 * 1024


def test_first_open_reclaims_legacy_bloat(tmp_path):
    # 存量库首次打开（升级路径）：旧前缀行即清 + auto_vacuum 置位 + 截断
    # 落盘一次完成。回归钉：VACUUM 在 WAL 下只重建不缩文件，缺 TRUNCATE
    # checkpoint 时 113MB 文件整个会话期驻留（实测 conn.close 才还）
    import sqlite3
    p = tmp_path / "cache.db"
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE renders(fingerprint TEXT, text_ver TEXT, "
                 "blob BLOB, nbytes INTEGER, used_at REAL, "
                 "PRIMARY KEY(fingerprint, text_ver))")
    big = b"\0" * (8 * 1024 * 1024)
    # 旧格式键（裸 ALGO_VER 前缀）+ 大 blob：升级前的真实存量形态
    # （\x00 不能进 SQL 字面量，键也参数绑定）
    conn.execute("INSERT INTO renders VALUES ('fp', ?, ?, ?, 0)",
                 ("2\x00v0", big, len(big)))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert p.stat().st_size > 8 * 1024 * 1024
    c = Cache(p)
    try:
        assert c.render_bytes == 0                # 旧前缀行已清
        assert c.conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 1
        assert p.stat().st_size < 4 * 1024 * 1024  # 打开即还盘，不等 close
    finally:
        c.conn.close()
