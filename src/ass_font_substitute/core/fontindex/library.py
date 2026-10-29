from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from urllib.parse import quote

from ass_font_substitute.core.fontindex.store import DB_NAME
from ass_font_substitute.core.fontindex.store import FP_ALGO
from ass_font_substitute.core.fontindex.store import fingerprint_of
from ass_font_substitute.core.fontindex.store import load_font_rows
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.models import CmapRanges


def normalize_name(name: str) -> str:
    """名称规范化：strip 空白 + 剥 @ 前缀 + casefold。"""
    return name.strip().lstrip("@").casefold()


def load_name_records(dir_path: str, rel_path: str, face_index: int
                      ) -> list[tuple[int, int, int, str]] | None:
    """按需读单 face 命名卡原始记录 (nameID, platformID, langID, 文本)
    （fonts.name_records，SCHEMA v8）。详情打开时 UI 线程同步调：本地库
    亚毫秒（zlib 解压 ~1KB），NAS 库几十 ms，双击场景可感阈值内。URI
    mode=ro 只读打开——普通 connect 在库文件缺失时会静默创建空库并取
    写锁（NAS 上正是慢点与故障点）；行缺失/库损坏/任何异常一律 None
    （调用方走别名平铺兜底）。"""
    p = Path(dir_path) / DB_NAME
    try:
        conn = sqlite3.connect(f"file:{quote(str(p))}?mode=ro", uri=True,
                               timeout=5)
        try:
            row = conn.execute(
                "SELECT name_records FROM fonts WHERE rel_path=? AND "
                "face_index=?", (rel_path, face_index)).fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return [tuple(rec) for rec in json.loads(zlib.decompress(row[0]))]
    except Exception:
        return None


@dataclass(frozen=True)
class FontEntity:
    fingerprint: str
    display_name: str
    aliases: tuple[str, ...]
    psname: str | None
    version: tuple[int, ...] | None
    weight: int
    width: int
    italic: bool
    script: str
    category: str
    cmap: CmapRanges
    dir_path: str
    rel_path: str
    face_index: int
    family_names: tuple[str, ...] = ()   # 字体系全语言名（ID16 缺视 ID1）
    # 展示名语言候选 {nameID: [(langID, 文本)]}（ID1/4/16 平台3 记录，
    # GUI 按界面语言链挑名）
    name_langs: dict[int, list[tuple[int, str]]] = field(default_factory=dict)
    size: int = 0
    mtime_ns: int = 0


class LibraryIndex:
    """多目录聚合视图：跨库行 → FontEntity，并按身份做版本去重。"""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self._entities: list[FontEntity] = []
        self._skipped: list[str] = []
        self._load()

    @classmethod
    def open(cls, dirs: list[Path]) -> LibraryIndex:
        return cls(dirs)

    def _load(self):
        raw = []
        # v4 去重存储：同 blob 解压一次并共享同一 CmapRanges 实例——
        # 同族各字重共享 cmap，18k face 仅 ~1.5k 份 distinct（内存同享）
        memo: dict[bytes, CmapRanges] = {}
        for di, d in enumerate(self.dirs):
            conn = open_db(d / "fontindex.db")
            try:
                cached = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
                    "SELECT rel_path, face_index, mtime_ns, fp FROM fingerprints "
                    "WHERE algo=?", (FP_ALGO,))}
                writeback = []
                for (rel, fi, ps, ver, al, disp, fam, nl, w, wd, it, cmb,
                     script, cat, size, mtime) in load_font_rows(conn):
                    aliases = tuple(json.loads(al))
                    version = tuple(json.loads(ver)) if ver else None
                    family = tuple(json.loads(fam)) if fam else ()
                    # JSON 键恒为串、对为列表：还原 int 键/元组对，GUI
                    # 侧链挑选按 int nameID/langID 匹配
                    name_langs = ({int(k): [(int(lg), t) for lg, t in v]
                                   for k, v in json.loads(nl).items()}
                                  if nl else {})
                    cmap = memo.get(cmb)
                    if cmap is None:
                        cmap = CmapRanges.from_bytes(zlib.decompress(cmb))
                        memo[cmb] = cmap
                    hit = cached.get((rel, fi))
                    if hit is not None and hit[0] == mtime:
                        fp = hit[1]
                    else:
                        # 缓存缺失（旧库/新文件/mtime 变化）→ 现算并回写
                        fp = fingerprint_of(list(aliases), cmap, w, wd, bool(it))
                        writeback.append((rel, fi, mtime, fp))
                    raw.append((di, rel, fi, ps, version, aliases, disp, family,
                                name_langs, w, wd, bool(it), script, cat, cmap,
                                fp, size, mtime))
                if writeback:
                    with conn:
                        conn.executemany(
                            "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?,?,?)",
                            [(rel, fi, mtime, FP_ALGO, fp)
                             for rel, fi, mtime, fp in writeback])
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
                self._skipped.append(f"{self.dirs[cur[0]]}/{cur[1]}#{cur[2]} 重复版本")
                best[key] = item
            else:
                self._skipped.append(f"{self.dirs[item[0]]}/{item[1]}#{item[2]} 重复版本")
        self._files_count = len({(item[0], item[1]) for item in raw})
        self._entities = [
            FontEntity(fp, disp, aliases, ps, ver, w, wd, it,
                       script, cat, cmap, str(self.dirs[di]), rel, fi,
                       family_names=family, name_langs=nl,
                       size=size, mtime_ns=mtime)
            for (di, rel, fi, ps, ver, aliases, disp, family, nl, w, wd, it,
                 script, cat, cmap, fp, size, mtime)
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
        return {"dirs": len(self.dirs), "files": self._files_count,
                "fonts": len(self._entities),
                "support_sc": sc, "support_tc": tc, "skipped": len(self._skipped)}

    def skipped(self) -> list[str]:
        return list(self._skipped)

    def dir_overlaps(self) -> list[str]:
        """嵌套/重叠目录告警：返回被其他目录包含的目录路径。"""
        out = []
        norm = [str(d.resolve()) for d in self.dirs]
        # casefold：传入不存在/异大小写路径时 resolve() 归一不了盘上大小写
        low = [d.casefold() for d in norm]
        for i, d in enumerate(low):
            for j, other in enumerate(low):
                if i != j and other != d \
                        and d.startswith(other.rstrip("\\/") + "\\"):
                    out.append(f"{self.dirs[i]} 嵌套于 {self.dirs[j]}")
        return out
