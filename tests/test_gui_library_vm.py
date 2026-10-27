from ass_font_substitute.gui.jobs import DirStatus
from ass_font_substitute.gui.library_vm import LibraryVM
from ass_font_substitute.gui.settings_store import Settings


def _vm(tmp_path):
    return LibraryVM(Settings(tmp_path / "s.json"))


def test_add_remove_persist(tmp_path):
    vm = _vm(tmp_path)
    assert vm.add_dir("J:/fonts") is True
    assert vm.add_dir("J:/fonts") is False          # 去重（Windows 大小写/分隔符无关）
    assert vm.add_dir("j:/FONTS/") is False
    vm.remove_dir("J:/fonts")
    assert vm.dirs == []
    assert Settings(tmp_path / "s.json").get("font_dirs") == []


def test_new_dir_pending_until_first_probe(tmp_path):
    # 回归：新加目录在首个探测结果未到时挂「检查中」而非「需更新」
    # （盘上实况未知，谈不上需不需要更新）；探测结果照常覆盖 pending
    vm = _vm(tmp_path)
    vm.add_dir("J:/new")
    d = vm.dirs[0]
    assert d.state == "pending"
    vm.refresh([DirStatus("J:/new", "stale", added=5, changed=0, total=5)],
               {}, [], [])
    assert d.state == "stale"


def test_status_button_texts():
    # 行内状态按钮文案（状态即按钮）：进行时「更新中/检查中」，
    # 静态词「已最新/需更新/不可用」
    from ass_font_substitute.gui.pages.library_page import _STATUS_TEXT

    assert _STATUS_TEXT == {"ok": "已最新", "stale": "需更新",
                            "indexing": "更新中", "pending": "检查中",
                            "offline": "不可用"}


def test_mark_pending_and_clear_indexing(tmp_path):
    # 取消/单停/失败路径：行即刻脱「更新中」挂「检查中」，收尾清残留
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    vm.add_dir("J:/b")
    vm.mark_indexing("J:/a")
    vm.mark_indexing("J:/b")
    vm.mark_pending("J:/a")
    assert vm.dirs[0].state == "pending"
    assert vm.dirs[1].state == "indexing"
    vm.clear_indexing()
    assert vm.dirs[1].state == "pending"
    vm.refresh([DirStatus("J:/a", "ok"), DirStatus("J:/b", "stale", added=1)],
               {}, [], [])
    assert [d.state for d in vm.dirs] == ["ok", "stale"]


def test_refresh_maps_status_and_reports_failures(tmp_path):
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    vm.add_dir("J:/b")
    vm.refresh(
        [DirStatus("J:/a", "stale", added=2, changed=1, total=7, bad=2)],
        {"dirs": 1, "files": 5, "fonts": 4, "support_sc": 3, "support_tc": 1},
        ["J:/a 嵌套于 J:/b"], ["J:/b"],
        dir_stats={"J:/a": {"fonts": 4, "sc": 3, "tc": 1, "skipped": 2}})
    a = vm.dirs[0]
    assert (a.state, a.added, a.changed) == ("stale", 2, 1)
    assert a.files == 7                          # 盘上全部支持后缀（含坏文件）
    assert a.fail == 4                           # bad 2 + skipped 2
    assert (a.fonts, a.sc, a.tc) == (4, 3, 1)
    b = vm.dirs[1]
    assert b.state == "offline"
    assert (b.files, b.fonts, b.sc, b.tc, b.fail) == (0, 0, 0, 0, 0)
    assert vm.failed_dirs == ["J:/b"]
    assert vm.stats["fonts"] == 4
    assert vm.overlaps == ["J:/a 嵌套于 J:/b"]


def test_indexing_row_survives_refresh(tmp_path):
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    vm.mark_indexing("J:/a")
    vm.refresh([DirStatus("J:/a", "stale", added=9)], {}, [], [])
    assert vm.dirs[0].state == "indexing"
    vm.mark_done("J:/a")
    vm.refresh([DirStatus("J:/a", "ok")], {}, [], [])
    assert vm.dirs[0].state == "ok"


def test_reset_stats_zeroes_derived_keeps_files(tmp_path):
    # 强制重建起步：派生统计（字体/简繁/失败重复）归零、总统计卡同步
    # 减去该目录旧值；文件数是盘上实况不属索引保留；未知路径静默
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    vm.refresh(
        [DirStatus("J:/a", "ok", total=7, bad=2)],
        {"dirs": 1, "files": 7, "fonts": 4, "support_sc": 3, "support_tc": 1},
        [], [],
        dir_stats={"J:/a": {"fonts": 4, "sc": 3, "tc": 1, "skipped": 2}})
    vm.reset_stats("J:/a")
    d = vm.dirs[0]
    assert (d.fonts, d.sc, d.tc, d.bad, d.skipped, d.fail) == (0, 0, 0, 0, 0, 0)
    assert d.files == 7
    assert vm.stats == {"dirs": 1, "files": 7, "fonts": 0,
                        "support_sc": 0, "support_tc": 0}
    vm.reset_stats("J:/nowhere")               # 不抛即过


def test_reset_stats_clamps_negative_totals(tmp_path):
    # 总统计减出负数（跨目录版本去重后 per-dir 之和可能大于总卡口径）钳 0
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    vm.refresh([DirStatus("J:/a", "ok", total=3)], {"fonts": 1}, [], [],
               dir_stats={"J:/a": {"fonts": 4, "sc": 2, "tc": 2, "skipped": 0}})
    vm.reset_stats("J:/a")
    assert vm.stats["fonts"] == 0 and vm.stats["support_sc"] == 0


def test_boots_from_settings(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("font_dirs", ["J:/x", "J:/y"])
    vm = LibraryVM(s)
    assert [d.path for d in vm.dirs] == ["J:/x", "J:/y"]
