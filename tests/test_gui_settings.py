import json
import os

import pytest

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.similarity.search import MIN_SIM, SAMPLE_TEXT, SIZE_RANGE
from ass_font_substitute.gui.paths import (appdata_dir, default_cache_path,
                                           decisions_path, settings_path)
from ass_font_substitute.gui.settings_store import DEFAULTS, Settings


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_paths_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert appdata_dir() == tmp_path / "afs"
    assert settings_path() == tmp_path / "afs" / "settings.json"
    assert default_cache_path() == tmp_path / "afs" / "cache.db"
    assert decisions_path() == tmp_path / "afs" / "decisions.db"


def test_defaults_match_core_constants():
    assert DEFAULTS["cov_threshold"] == COV_THRESHOLD == 0.95
    assert DEFAULTS["min_sim"] == MIN_SIM == 0.85
    assert (DEFAULTS["size_min"], DEFAULTS["size_max"]) == SIZE_RANGE == (0.90, 1.10)
    assert DEFAULTS["sample_text"] == SAMPLE_TEXT
    assert DEFAULTS["n_pause"] == 5
    assert DEFAULTS["save_suffix"] == ".substituted.ass"
    assert DEFAULTS["overwrite_default"] is False
    assert DEFAULTS["max_threads"] >= 1


def test_roundtrip_and_missing_file(tmp_path):
    s = Settings(tmp_path / "s.json")
    assert s.get("n_pause") == 5                      # 无文件 → 全默认
    s.set("n_pause", 8)
    s.set("font_dirs", ["J:/fonts"])
    s2 = Settings(tmp_path / "s.json")
    assert s2.get("n_pause") == 8
    assert s2.get("font_dirs") == ["J:/fonts"]


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{broken", "utf-8")
    s = Settings(f)
    assert s.get("cov_threshold") == COV_THRESHOLD


def test_unknown_keys_and_bad_types_filtered(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"n_pause": "x", "future_key": 1,
                             "overwrite_default": "yes", "cov_threshold": 0.9}),
                 "utf-8")
    s = Settings(f)
    assert s.get("n_pause") == DEFAULTS["n_pause"]
    assert "future_key" not in json.loads(f.read_text("utf-8"))
    assert s.get("overwrite_default") is False
    assert s.get("cov_threshold") == 0.9


def test_empty_sample_text_rejected(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(ValueError):
        s.set("sample_text", "   ")


def test_bad_suffix_rejected(tmp_path):
    s = Settings(tmp_path / "s.json")
    with pytest.raises(ValueError):
        s.set("save_suffix", "substituted")           # 必须以点开头
    s.set("save_suffix", ".fixed.ass")
    assert s.get("save_suffix") == ".fixed.ass"


def test_atomic_write_no_tmp_leftover(tmp_path):
    s = Settings(tmp_path / "s.json")
    s.set("n_pause", 3)
    assert not (tmp_path / "s.json.tmp").exists()


def test_last_dir_scoped_roundtrip_and_independence(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui import lastdir

    s = Settings(tmp_path / "s.json")
    assert s.get("last_dirs") == {}                 # 缺省空：不预置目录
    lib_dir, sub_dir = tmp_path / "fonts", tmp_path / "subs"
    lib_dir.mkdir()
    sub_dir.mkdir()

    seen = {}

    def fake_dir(parent, title, d):
        seen["d"] = d
        return str(lib_dir)

    monkeypatch.setattr(lastdir.QFileDialog, "getExistingDirectory",
                        lambda *a: fake_dir(*a))
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda parent, title, d, f: (
                            seen.__setitem__("files_d", d) or
                            ([str(sub_dir / "a.ass")], "")))

    # library scope：选定回记
    assert lastdir.pick_existing_dir(None, s, "选目录", "library") == str(lib_dir)
    assert s.get("last_dirs")["library"] == str(lib_dir)

    # subs scope：回记自己的目录，不碰 library
    assert lastdir.pick_open_files(None, s, "选文件", "f", "subs") ==         [str(sub_dir / "a.ass")]
    assert s.get("last_dirs") == {"library": str(lib_dir), "subs": str(sub_dir)}
    assert seen["files_d"] == ""                    # subs 首次：种子为空

    # 种子按 scope 取：library 再开落在 fonts，subs 落在 subs
    lastdir.pick_existing_dir(None, s, "选目录", "library")
    assert seen["d"] == str(lib_dir)
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert seen["files_d"] == str(sub_dir)

    # 取消不改记忆
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda *a, **kw: ([], ""))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert s.get("last_dirs")["subs"] == str(sub_dir)

    # 记忆目录已删：种子回退为空，其他 scope 不受影响
    import shutil
    shutil.rmtree(sub_dir)
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda parent, title, d, f: (
                            seen.__setitem__("files_d2", d) or ([], "")))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert seen["files_d2"] == ""
    assert s.get("last_dirs")["library"] == str(lib_dir)


def test_last_dir_drive_root_gets_separator(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui import lastdir

    s = Settings(tmp_path / "s.json")
    drive = tmp_path.drive or "C:"
    # 真实对话框返回反斜杠路径：根目录文件的 dirname 已带分隔符
    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda *a, **kw: ([f"{drive}\a.ass"], ""))
    lastdir.pick_open_files(None, s, "选文件", "f", "subs")
    assert s.get("last_dirs")["subs"] == drive + "\\"
    # 盘相对路径（dirname 剥成裸 "C:"）经 _remember 归一成根
    lastdir._remember(s, "library", drive)
    assert s.get("last_dirs")["library"] == drive + "\\"


def test_nan_and_bad_legacy_values_filtered(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"cov_threshold": float("nan"),
                             "save_suffix": "no-dot",
                             "min_sim": float("inf")}), "utf-8")
    s = Settings(f)
    assert s.get("cov_threshold") == DEFAULTS["cov_threshold"]   # NaN 拒收
    assert s.get("min_sim") == DEFAULTS["min_sim"]               # Inf 拒收
    assert s.get("save_suffix") == DEFAULTS["save_suffix"]       # 存量后缀复验
    import math
    with pytest.raises(ValueError):
        s.set("cov_threshold", float("nan"))
    with pytest.raises(ValueError):
        s.set("cov_threshold", float("inf"))


def test_settings_concurrent_set_threadsafe(tmp_path):
    import threading
    s = Settings(tmp_path / "s.json")
    errs = []

    def worker(i):
        try:
            for _ in range(30):
                s.set("n_pause", 1 + i)
        except Exception as e:                     # tmp 撞名/句柄互斥即在此暴露
            errs.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs
    assert (tmp_path / "s.json.tmp").exists() is False


def test_wheel_filter_blocks_only_registered(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QSlider, QWidget
    from ass_font_substitute.gui.pages.settings_page import _WheelFreeCard
    from qfluentwidgets import FluentIcon as FIF

    card = _WheelFreeCard(FIF.FILTER, "t", "c")
    host = QWidget()
    slider = QSlider(host)
    other = QSlider(host)
    card.slider = slider
    wheel = QEvent(QEvent.Type.Wheel)
    assert card.eventFilter(slider, wheel) is True      # 注册过的控件被拦
    assert card.eventFilter(other, wheel) is False      # 未注册的放行
    assert card.eventFilter(slider, QEvent(QEvent.Type.None_)) is False


def test_wheel_block_filter(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QComboBox
    from ass_font_substitute.gui.widgets.wheel_block import WheelBlock

    f = WheelBlock()
    assert f.eventFilter(QComboBox(), QEvent(QEvent.Type.Wheel)) is True
    assert f.eventFilter(QComboBox(), QEvent(QEvent.Type.None_)) is False


def test_percent_card_init_does_not_commit(qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui.pages.settings_page import _PercentCard
    from qfluentwidgets import FluentIcon as FIF

    calls = []
    _PercentCard(FIF.FILTER, "t", "c", 90, 100, 1, 96, calls.append)
    assert calls == []                              # 构造不提交，存量值不被改写
