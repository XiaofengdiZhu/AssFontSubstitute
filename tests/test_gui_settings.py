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


def test_last_dir_roundtrip_and_dialog_seed(tmp_path, monkeypatch, qapp):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from ass_font_substitute.gui import lastdir

    s = Settings(tmp_path / "s.json")
    assert s.get("last_dir") == ""                 # 缺省空：不预置目录
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    monkeypatch.setattr(lastdir.QFileDialog, "getExistingDirectory",
                        lambda *a, **kw: str(new))
    assert lastdir.pick_existing_dir(None, s, "选目录") == str(new)
    assert s.get("last_dir") == str(new)           # 选定即回记

    seen = {}

    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda parent, title, d, f: (
                            seen.__setitem__("files_d", d) or
                            ([str(new / "a.ass")], "")))
    assert lastdir.pick_open_files(None, s, "选文件", "f") == [str(new / "a.ass")]
    assert s.get("last_dir") == str(new)           # 文件所在目录回记
    assert seen["files_d"] == str(new)             # 上次目录作为初始目录传入

    monkeypatch.setattr(lastdir.QFileDialog, "getOpenFileNames",
                        lambda *a, **kw: ([], ""))
    lastdir.pick_open_files(None, s, "选文件", "f")   # 取消不改记忆
    assert s.get("last_dir") == str(new)

    stale = tmp_path / "gone"                      # 记忆目录已删：种子回退为空
    s.set("last_dir", str(stale))
    seen = {}

    def fake_seed_call(parent, title, d):
        seen["d"] = d
        return ""

    monkeypatch.setattr(lastdir.QFileDialog, "getExistingDirectory",
                        lambda *a: fake_seed_call(*a))
    lastdir.pick_existing_dir(None, s, "选目录")
    assert seen["d"] == ""
