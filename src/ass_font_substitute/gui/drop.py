"""拖拽入页的路径归集：纯函数拆分 + QUrl 胶水，三页共用。"""
from __future__ import annotations

from pathlib import Path


def split_dropped(paths: list[str]) -> tuple[list[str], list[str]]:
    """拖入路径拆 (目录, 文件)。不存在/不可访问项忽略；保序去重。"""
    dirs: list[str] = []
    files: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            if Path(p).is_dir():
                dirs.append(p)
            elif Path(p).is_file():
                files.append(p)
        except OSError:
            continue
    return dirs, files


def accepts(paths: list[str], dirs_only: bool) -> bool:
    """dragEnter 准入门：库页只认目录；字幕页目录或 .ass 文件皆可。"""
    dirs, files = split_dropped(paths)
    if dirs:
        return True
    if dirs_only:
        return False
    return any(Path(f).suffix.lower() == ".ass" for f in files)


def local_paths(mime) -> list[str]:
    """mimeData 的本地 URL → 路径串列表（QUrl 胶水）。"""
    out = []
    for url in mime.urls():
        if url.isLocalFile():
            out.append(url.toLocalFile())
    return out
