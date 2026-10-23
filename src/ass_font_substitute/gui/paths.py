from __future__ import annotations

import os
from pathlib import Path


def appdata_dir() -> Path:
    """应用数据目录：%APPDATA%\\afs（非 Windows 退回 ~/.config/afs，测试/跨平台开发用）。"""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "afs"


def settings_path() -> Path:
    return appdata_dir() / "settings.json"


def default_cache_path() -> Path:
    return appdata_dir() / "cache.db"


def decisions_path() -> Path:
    return appdata_dir() / "decisions.db"
