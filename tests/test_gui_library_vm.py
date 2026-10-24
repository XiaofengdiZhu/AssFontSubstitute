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
    # 回归：新加目录在首个探测结果未到时显示「更新中」而非「需更新」
    # （盘上实况未知，谈不上需不需要更新）；探测结果照常覆盖 pending
    vm = _vm(tmp_path)
    vm.add_dir("J:/new")
    d = vm.dirs[0]
    assert d.state == "pending" and d.badge == "更新中"
    vm.refresh([DirStatus("J:/new", "stale", added=5, changed=0, total=5)],
               {}, [], [])
    assert d.state == "stale" and "需更新" in d.badge


def test_badge_texts(tmp_path):
    vm = _vm(tmp_path)
    vm.add_dir("J:/a")
    d = vm.dirs[0]
    d.state = "ok"
    assert d.badge == "已更新"
    d.state, d.added, d.changed = "stale", 3, 1
    assert d.badge == "需更新：新增 3 · 修改 1"
    d.state = "indexing"
    assert d.badge == "索引中"
    d.state = "offline"
    assert d.badge == "不可用"


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


def test_boots_from_settings(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("font_dirs", ["J:/x", "J:/y"])
    vm = LibraryVM(s)
    assert [d.path for d in vm.dirs] == ["J:/x", "J:/y"]
