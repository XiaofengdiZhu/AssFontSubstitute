"""文件/目录对话框统一入口：以设置里的 last_dir 为初始目录并随手回记。

用户要求：记住上次选择文件、目录时的目录——三种对话框（字幕文件、
字体目录、缓存位置）共用同一 last_dir，任一处选定即更新。
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog


def _seed(settings) -> str:
    d = settings.get("last_dir")
    return d if d and os.path.isdir(d) else ""


def _remember(settings, d: str) -> None:
    if d and os.path.isdir(d):
        if len(d) == 2 and d[1] == ":":
            d += os.sep        # 盘符根：os.path.dirname 给的是盘相对路径
        settings.set("last_dir", d)


def pick_existing_dir(parent, settings, title: str) -> str:
    d = QFileDialog.getExistingDirectory(parent, title, _seed(settings))
    _remember(settings, d)
    return d


def pick_open_files(parent, settings, title: str, name_filter: str) -> list:
    paths, _ = QFileDialog.getOpenFileNames(parent, title, _seed(settings),
                                            name_filter)
    if paths:
        _remember(settings, os.path.dirname(paths[0]))
    return paths
