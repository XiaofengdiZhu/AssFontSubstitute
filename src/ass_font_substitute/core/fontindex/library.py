from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ass_font_substitute.core.fontindex.store import (FP_ALGO, fingerprint_of,
                                                      load_font_rows, open_db)
from ass_font_substitute.core.models import CmapRanges


def normalize_name(name: str) -> str:
    """名称规范化：strip 空白 + 剥 @ 前缀 + casefold。"""
    return name.strip().lstrip("@").casefold()


@dataclass(frozen=True)
class FontEntity:
    fingerprint: str
    display_name: str
    aliases: tuple[str, ...]
    psname: Optional[str]
    version: Optional[tuple[int, ...]]
    weight: int
    width: int
    italic: bool
    script: str
    category: str
    cmap: CmapRanges
    dir_path: str
    rel_path: str
    face_index: int


class LibraryIndex:
    """多目录聚合视图：跨库行 → FontEntity，并按身份做版本去重。"""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self._entities: list[FontEntity] = []
        self._skipped: list[str] = []
        self._load()

    @classmethod
    def open(cls, dirs: list[Path]) -> "LibraryIndex":
        return cls(dirs)

    def _load(self):
        raw = []
        for di, d in enumerate(self.dirs):
            conn = open_db(d / "fontindex.db")
            try:
                cached = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
                    "SELECT rel_path, face_index, xxh, fp FROM fingerprints "
                    "WHERE algo=?", (FP_ALGO,))}
                writeback = []
                for (rel, fi, ps, ver, al, w, wd, it, cmb, script, cat,
                     xxh, size, mtime) in load_font_rows(conn):
                    aliases = tuple(json.loads(al))
                    version = tuple(json.loads(ver)) if ver else None
                    cmap = CmapRanges.from_bytes(cmb)
                    hit = cached.get((rel, fi))
                    if hit is not None and hit[0] == xxh:
                        fp = hit[1]
                    else:
                        # 缓存缺失（旧库/新文件/xxh 变化）→ 现算并回写
                        fp = fingerprint_of(list(aliases), cmap, w, wd,
                                            bool(it), xxh)
                        writeback.append((rel, fi, xxh, fp))
                    raw.append((di, rel, fi, ps, version, aliases, w, wd, bool(it),
                                script, cat, cmap, fp))
                if writeback:
                    with conn:
                        conn.executemany(
                            "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?)",
                            [(rel, fi, xxh, FP_ALGO, fp)
                             for rel, fi, xxh, fp in writeback])
            finally:
                conn.close()
        # 身份 = psname，无则别名并集；排序保证平局保留目录顺序靠前、rel_path、face_index
        best: dict[str, tuple] = {}
        for item in sorted(raw, key=lambda t: (t[0], t[1], t[2])):
            ps, ver = item[3], item[4]
            key = ps if ps else "\x00".join(sorted(item[5]))
            cur = best.get(key)
            if cur is None:
                best[key] = item
            elif ver is not None and (cur[4] is None or ver > cur[4]):
                self._skipped.append(f"{self.dirs[cur[0]]}/{cur[1]}#{cur[2]} 旧版本")
                best[key] = item
            else:
                self._skipped.append(f"{self.dirs[item[0]]}/{item[1]}#{item[2]} 旧版本")
        self._entities = [
            FontEntity(fp, aliases[0], aliases, ps, ver, w, wd, it, script, cat,
                       cmap, str(self.dirs[di]), rel, fi)
            for (di, rel, fi, ps, ver, aliases, w, wd, it, script, cat, cmap, fp)
            in best.values()]

    def entities(self) -> list[FontEntity]:
        return list(self._entities)

    def find_by_name(self, norm: str) -> list[FontEntity]:
        n = normalize_name(norm)
        return [e for e in self._entities
                if any(normalize_name(a) == n for a in e.aliases)]

    def stats(self) -> dict:
        sc = sum(1 for e in self._entities if e.script in ("简", "简繁"))
        tc = sum(1 for e in self._entities if e.script in ("繁", "简繁"))
        return {"dirs": len(self.dirs), "fonts": len(self._entities),
                "support_sc": sc, "support_tc": tc, "skipped": len(self._skipped)}

    def skipped(self) -> list[str]:
        return list(self._skipped)

    def dir_overlaps(self) -> list[str]:
        """嵌套/重叠目录告警：返回被其他目录包含的目录路径。"""
        out = []
        norm = [str(d.resolve()) for d in self.dirs]
        for i, d in enumerate(norm):
            for j, other in enumerate(norm):
                if i != j and other != d and d.startswith(other.rstrip("\\/") + "\\"):
                    out.append(f"{self.dirs[i]} 嵌套于 {self.dirs[j]}")
        return out
