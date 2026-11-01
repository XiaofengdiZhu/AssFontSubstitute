import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mk(qapp, tmp_path):
    from ass_font_substitute.gui.settings_store import Settings
    return Settings(tmp_path / "settings.json")


def test_remember_scope_isolation(qapp, tmp_path):
    # scope 互不串扰：选字幕不把字体目录对话框带到字幕文件夹
    from ass_font_substitute.gui.lastdir import _remember
    s = _mk(qapp, tmp_path)
    d = tmp_path / "subs"
    d.mkdir()
    _remember(s, "subs", str(d))
    _remember(s, "library", "")
    assert s.get("last_dirs") == {"subs": str(d)}


def test_remember_drive_root_appends_sep(qapp, tmp_path):
    # 盘符根：os.path.dirname("D:") 给的是盘相对路径，补 os.sep 才可重放
    from ass_font_substitute.gui.lastdir import _remember
    s = _mk(qapp, tmp_path)
    _remember(s, "library", "D:")
    assert s.get("last_dirs")["library"] == "D:" + os.sep


def test_remember_missing_dir_ignored(qapp, tmp_path):
    from ass_font_substitute.gui.lastdir import _remember
    s = _mk(qapp, tmp_path)
    _remember(s, "subs", str(tmp_path / "不存在"))
    assert s.get("last_dirs") == {}


def test_pick_open_files_remember_first_dir(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    import ass_font_substitute.gui.lastdir as ld
    s = _mk(qapp, tmp_path)
    (tmp_path / "a").mkdir()                    # _remember 有 isdir 门
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(
                            lambda *a, **k: ([str(tmp_path / "a" / "x.ass"),
                                              str(tmp_path / "b" / "y.ass")],
                                             "")))
    out = ld.pick_open_files(None, s, "t", "f", "subs")
    assert len(out) == 2
    assert s.get("last_dirs")["subs"] == str(tmp_path / "a")
    # 空选不记忆
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([], "")))
    ld.pick_open_files(None, s, "t", "f", "other")
    assert "other" not in s.get("last_dirs")


def test_seed_falls_back_when_dir_gone(qapp, tmp_path):
    from ass_font_substitute.gui.lastdir import _seed
    s = _mk(qapp, tmp_path)
    s.set("last_dirs", {"subs": str(tmp_path / "没了")})
    assert _seed(s, "subs") == ""
    live = tmp_path / "在"
    live.mkdir()
    s.set("last_dirs", {"subs": str(live)})
    assert _seed(s, "subs") == str(live)
    assert _seed(s, "library") == ""            # 无 scope 记录
