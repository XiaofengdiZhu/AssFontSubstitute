from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.gui.jobs import DirStatus


@dataclass
class DirRow:
    path: str
    state: str = "stale"        # ok / stale / indexing / offline
    added: int = 0
    changed: int = 0
    files: int = 0              # 盘上全部支持后缀文件数（含坏文件，probe total）
    fonts: int = 0              # 字体实体数（版本去重后）
    sc: int = 0                 # 支持简体
    tc: int = 0                 # 支持繁体
    fail: int = 0               # 失败/旧版 = 坏文件表行数 + 版本去重跳过数

    @property
    def badge(self) -> str:
        if self.state == "indexing":
            return "索引中"
        if self.state == "offline":
            return "不可用"
        if self.state == "stale":
            return f"需更新：新增 {self.added} · 修改 {self.changed}"
        return "已更新"


class LibraryVM:
    """字体库管理页状态机（零 Qt）。目录增删持久化进 settings.font_dirs。

    路径一律原样保存（不做 str(Path()) 规范化）：Windows 下规范化会把
    "J:/a" 变成 "J:\\a"，与 DirStatus/测试中的正斜杠形式失配；去重、
    移除、标记均经 Path 比较，大小写与分隔符天然无关。"""

    def __init__(self, settings):
        self._settings = settings
        self.dirs = [DirRow(p) for p in settings.get("font_dirs")]
        self.stats: dict = {}
        self.overlaps: list[str] = []
        self.failed_dirs: list[str] = []

    def add_dir(self, path: str) -> bool:
        if any(Path(d.path) == Path(path) for d in self.dirs):
            return False
        self.dirs.append(DirRow(path))
        self.persist_dirs()
        return True

    def remove_dir(self, path: str) -> None:
        self.dirs = [d for d in self.dirs if Path(d.path) != Path(path)]
        self.persist_dirs()

    def refresh(self, statuses: list[DirStatus], snapshot_stats: dict,
                overlaps: list[str], offline_dirs: list[str],
                dir_stats: dict | None = None) -> None:
        by = {s.path: s for s in statuses}
        offline_set = {str(Path(p)) for p in offline_dirs}
        for d in self.dirs:
            if d.state == "indexing":
                continue                        # 更新进行中不被探测结果覆盖
            if str(Path(d.path)) in offline_set:
                d.state, d.added, d.changed = "offline", 0, 0
                d.files, d.fail = 0, 0
                continue
            s = by.get(d.path)
            if s is None or s.state in ("missing", "offline"):
                d.state, d.added, d.changed = "offline", 0, 0
                d.files, d.fail = 0, 0
            else:
                d.state, d.added, d.changed = s.state, s.added, s.changed
                d.files, d.fail = s.total, s.bad
            st = (dir_stats or {}).get(d.path)
            if st:
                d.fonts, d.sc, d.tc = st["fonts"], st["sc"], st["tc"]
                d.fail += st.get("skipped", 0)
        self.stats = snapshot_stats
        self.overlaps = list(overlaps)
        self.failed_dirs = [d.path for d in self.dirs if d.state == "offline"]

    def mark_indexing(self, path: str) -> None:
        d = self._row(path)
        if d:
            d.state = "indexing"

    def mark_done(self, path: str) -> None:
        d = self._row(path)
        if d:
            d.state = "ok"

    def persist_dirs(self) -> None:
        self._settings.set("font_dirs", [d.path for d in self.dirs])

    def _row(self, path: str) -> DirRow | None:
        return next((d for d in self.dirs if Path(d.path) == Path(path)), None)
