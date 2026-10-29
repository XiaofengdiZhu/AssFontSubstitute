from __future__ import annotations

import os
import sys
from pathlib import Path


def appdata_dir() -> Path:
    """应用数据目录（绿色软件，2026-10-29 拍板）：随程序走 <运行目录>/Data，
    不再写 %APPDATA%。运行目录 = 可执行文件所在目录；源码运行取仓库根
    （src 上级，src 树里不长 Data）。AFS_DATA_DIR 覆盖全部分辨（测试隔离/
    便携双实例用）。"""
    env = os.environ.get("AFS_DATA_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "Data"
    return Path(__file__).resolve().parents[3] / "Data"


def settings_path() -> Path:
    return appdata_dir() / "settings.json"


def default_cache_path() -> Path:
    return appdata_dir() / "cache.db"


def decisions_path() -> Path:
    return appdata_dir() / "decisions.db"
