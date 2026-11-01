from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ass_font_substitute.gui.jobs import DirStatus


@dataclass
class DirRow:
    path: str
    state: str = "pending"      # pending / ok / stale / indexing / offline
    added: int = 0
    changed: int = 0
    files: int = 0              # 盘上全部支持后缀文件数（含坏文件，probe total）
    fonts: int = 0              # 字体实体数（版本去重后）
    sc: int = 0                 # 支持简体
    tc: int = 0                 # 支持繁体
    bad: int = 0                # 坏文件表行数
    skipped: int = 0            # 版本去重跳过数
    fail: int = 0               # 失败/重复 = bad + skipped


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
        """一次性全量灌入（测试用）；GUI 启动走 apply_snapshot→apply_detail
        两段（探测不阻塞启动画面收幕）。"""
        self.apply_snapshot(snapshot_stats, overlaps, offline_dirs)
        self.apply_detail(statuses, dir_stats)

    def apply_snapshot(self, snapshot_stats: dict, overlaps: list[str],
                       offline_dirs: list[str]) -> None:
        """聚合库快照就绪（启动画面收幕点）：总统计/重叠告警先上，打不
        开的目录先定 offline；目录行 ok/stale 明细等 apply_detail 探测。"""
        self.stats = snapshot_stats
        self.overlaps = list(overlaps)
        offline_set = {str(Path(p)) for p in offline_dirs}
        # 本轮快照离线的目录：apply_detail 不被探测翻回 ok——深层损坏库
        # 的只读探测（files/bad 表）可能侥幸成功，但装载层已证不可用
        # （审查二轮 P1）；下轮快照重建本集合自然解禁
        self._snapshot_offline = offline_set
        for d in self.dirs:
            if str(Path(d.path)) in offline_set:
                d.state, d.added, d.changed = "offline", 0, 0
                d.files, d.bad, d.skipped, d.fail = 0, 0, 0, 0
        self.failed_dirs = [d.path for d in self.dirs if d.state == "offline"]

    def apply_detail(self, statuses: list[DirStatus],
                     dir_stats: dict | None = None) -> None:
        """逐目录探测结果 + 每目录库内统计：行状态/文件数/字体数落定。"""
        by = {s.path: s for s in statuses}
        snap_offline = getattr(self, "_snapshot_offline", set())
        for d in self.dirs:
            if d.state == "indexing":
                continue                        # 更新进行中不被探测结果覆盖
            if str(Path(d.path)) in snap_offline:
                continue                        # 快照离线同上，不被探测翻回
            s = by.get(d.path)
            if s is None or s.state in ("missing", "offline"):
                d.state, d.added, d.changed = "offline", 0, 0
                d.files, d.bad, d.skipped, d.fail = 0, 0, 0, 0
            else:
                d.state, d.added, d.changed = s.state, s.added, s.changed
                d.files, d.bad = s.total, s.bad
                d.skipped = 0
            st = (dir_stats or {}).get(d.path)
            if st:
                d.fonts, d.sc, d.tc = st["fonts"], st["sc"], st["tc"]
                d.skipped = st.get("skipped", 0)
            d.fail = d.bad + d.skipped
        self.failed_dirs = [d.path for d in self.dirs if d.state == "offline"]

    def mark_indexing(self, path: str) -> None:
        d = self._row(path)
        if d:
            d.state = "indexing"

    def reset_stats(self, path: str) -> None:
        """强制重建起步：旧索引库已清空，行内派生统计先归零、总统计卡
        同步减去该目录旧值（负数钳 0 防目录间去重后口径差），等收尾探测
        盖回真值；文件数是盘上实况不属索引，保留。"""
        d = self._row(path)
        if not d:
            return
        for key, v in (("fonts", d.fonts), ("support_sc", d.sc),
                       ("support_tc", d.tc)):
            self.stats[key] = max(0, self.stats.get(key, 0) - v)
        d.fonts = d.sc = d.tc = 0
        d.bad = d.skipped = d.fail = 0

    def mark_pending(self, path: str) -> None:
        """行脱「更新中」回暂态：取消/单停/失败后真实状态未知，
        挂「检查中」直到收尾探测盖定。"""
        d = self._row(path)
        if d:
            d.state = "pending"

    def clear_indexing(self) -> None:
        """更新全部收尾（含取消）：worker 已停，indexing 行的保护不再
        需要，全部复位暂态让探测结果照常覆盖。"""
        for d in self.dirs:
            if d.state == "indexing":
                d.state = "pending"

    def mark_done(self, path: str) -> None:
        d = self._row(path)
        if d:
            d.state = "ok"

    def persist_dirs(self) -> None:
        self._settings.set("font_dirs", [d.path for d in self.dirs])

    def _row(self, path: str) -> DirRow | None:
        return next((d for d in self.dirs if Path(d.path) == Path(path)), None)
