import json

import pytest

from ass_font_substitute.core.coverage.matcher import COV_THRESHOLD
from ass_font_substitute.core.similarity.search import MIN_SIM, SAMPLE_TEXT, SIZE_RANGE
from ass_font_substitute.gui.paths import (appdata_dir, default_cache_path,
                                           decisions_path, settings_path)
from ass_font_substitute.gui.settings_store import DEFAULTS, Settings


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
