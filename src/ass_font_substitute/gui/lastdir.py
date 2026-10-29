"""文件/目录对话框统一入口：按 scope 各自记忆上次目录（settings.last_dirs）。

scope 是调用点自定义字符串：字幕文件 "subs"、字体目录 "library"——
互不串扰，选字幕不该把字体目录对话框带到字幕文件夹去。（缓存位置
"cache" 随绿色软件化删除）
"""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog


def _seed(settings, scope: str) -> str:
    d = (settings.get("last_dirs") or {}).get(scope, "")
    return d if d and os.path.isdir(d) else ""


def _remember(settings, scope: str, d: str) -> None:
    if d and os.path.isdir(d):
        if len(d) == 2 and d[1] == ":":
            d += os.sep        # 盘符根：os.path.dirname 给的是盘相对路径
        dirs = dict(settings.get("last_dirs"))
        dirs[scope] = d
        settings.set("last_dirs", dirs)


def pick_existing_dir(parent, settings, title: str, scope: str) -> str:
    d = QFileDialog.getExistingDirectory(parent, title, _seed(settings, scope))
    _remember(settings, scope, d)
    return d


def pick_open_files(parent, settings, title: str, name_filter: str,
                    scope: str) -> list:
    paths, _ = QFileDialog.getOpenFileNames(parent, title,
                                            _seed(settings, scope),
                                            name_filter)
    if paths:
        _remember(settings, scope, os.path.dirname(paths[0]))
    return paths
