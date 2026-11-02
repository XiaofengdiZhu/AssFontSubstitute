from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from ass_font_substitute.core.fontindex.probes import effective_weight
from ass_font_substitute.core.fontindex.store import DB_NAME
from ass_font_substitute.core.fontindex.store import FP_ALGO
from ass_font_substitute.core.fontindex.store import fingerprint_of
from ass_font_substitute.core.fontindex.store import load_font_rows
from ass_font_substitute.core.fontindex.store import open_db
from ass_font_substitute.core.models import CmapRanges


def normalize_name(name: str) -> str:
    """名称规范化：strip 空白 + 剥 @ 前缀 + casefold。"""
    return name.strip().lstrip("@").casefold()


def ro_uri(path: Path) -> str:
    """SQLite 只读 URI。UNC 路径 as_uri 产出带 authority 的
    file://host/share/...，SQLite 一律拒绝（invalid uri authority，
    审查三轮 P1）——改拼 4 斜杠 file:////host/share/...（实测可读）。
    保留字符/中文由 as_uri 统一 percent-encode，不必手工 quote。"""
    s = path.resolve().as_uri()
    if s.startswith("file://") and not s.startswith("file:///"):
        s = "file:////" + s[len("file://"):]
    return s


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
        # ro_uri 与 gui/jobs.py 只读打开同口径（UNC 4 斜杠，审查三轮 P1）
        conn = sqlite3.connect(ro_uri(p) + "?mode=ro",
                               uri=True, timeout=5)
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
    inst_index: int = 0   # fvar 命名实例序号（静态行 0，fvar 声明序留空洞）
    var_axes: tuple[float, ...] = ()   # 实例坐标（静态行 ()，与轴 tag 序对齐）
    # 错标补偿有效字重（probes.effective_weight 装载时算一次）：仅比较/
    # 排序消费（rank_pool 粗排、同名取面键）；weight 恒保真 OS/2，展示
    # 用 weight（W 徽标拍板显示原始值）。直构陷阱：默认 400 仅对
    # weight==400 的直构实体正确——weight≠400 且无 name_langs 的直构
    # 实体须显式传 eff_weight=weight，否则比较口径静默错位
    eff_weight: int = 400


class LibraryIndex:
    """多目录聚合视图：跨库行 → FontEntity，并按身份做版本去重。"""

    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self._entities: list[FontEntity] = []
        # 重复版本跳过条目 (dir, rel, face_index, inst_index) 结构化数据：
        # 展示文案由 GUI 层拼 tr（core 拼简体串曾残留繁体界面，审查三轮
        # P2；展示串也不再被 GUI 当数据键解析）
        self._skipped: list[tuple[str, str, int, int]] = []
        # 装载期降级离线的目录（深层损坏等）：open_db 头级探测挡不住的
        # （坏块/截断在装载 SELECT / zlib 解压层炸出）按目录剔除，不拖垮
        # 整个聚合装载（审查二轮 P1）
        self.offline_dirs: list[Path] = []
        self._by_alias: dict[str, list[FontEntity]] = {}   # _load 末尾灌满
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
            buf: list = []
            try:
                self._load_dir(di, d, memo, buf)
            except Exception:
                self.offline_dirs.append(d)
            else:
                # 成功才并入：中途坏页前已解析的行不泄漏进聚合结果——
                # 目录既判 offline，行必须整体剔除（口径一致）
                raw.extend(buf)
        # 身份 = (psname, inst_index)，psname 缺则别名并集替代；同 VF 各实例
        # 行共享 psname，键必须带 inst 维度，否则去重把实例折叠成默认行。
        # 排序保证平局保留目录顺序靠前、rel_path、face_index、inst_index
        best: dict[tuple, tuple] = {}
        for item in sorted(raw, key=lambda t: (t[0], t[1], t[2], t[18])):
            ps, ver, ix = item[3], item[4], item[18]
            key = (ps if ps else "\x00".join(sorted(item[5])), ix)
            cur = best.get(key)
            if cur is None:
                best[key] = item
            elif ver is not None and (cur[4] is None or ver > cur[4]):
                self._skipped.append(
                    (str(self.dirs[cur[0]]), cur[1], cur[2], cur[18]))
                best[key] = item
            else:
                self._skipped.append(
                    (str(self.dirs[item[0]]), item[1], item[2], item[18]))
        self._files_count = len({(item[0], item[1]) for item in raw})
        self._entities = [
            FontEntity(fp, disp, aliases, ps, ver, w, wd, it,
                       script, cat, cmap, str(self.dirs[di]), rel, fi,
                       family_names=family, name_langs=nl,
                       size=size, mtime_ns=mtime,
                       inst_index=ix, var_axes=vax,
                       eff_weight=effective_weight(nl, w, ix))
            for (di, rel, fi, ps, ver, aliases, disp, family, nl, w, wd, it,
                 script, cat, cmap, fp, size, mtime, ix, vax)
            in best.values()]
        # 别名查找索引：find_by_name 每调用全实体×全别名重跑 normalize 曾是
        # 18k 实体库批量替换的秒级悬崖（matcher 每用量再触发多次全扫）
        self._by_alias: dict[str, list[FontEntity]] = {}
        for e in self._entities:
            for a in e.aliases:
                self._by_alias.setdefault(normalize_name(a), []).append(e)

    def _load_dir(self, di: int, d: Path, memo: dict, raw: list) -> None:
        """单目录装载；任何异常（sqlite3.Error / zlib.error / json 等）
        由 _load 捕获降级 offline——sqlite3.Error 之外的类型同样可能
        （坏块穿透到解压/JSON 层），故这里捕 Exception。"""
        conn = open_db(d / DB_NAME)
        try:
            # v2：缓存键含 inst_index（同文件各实例行 face_index 相同，只按
            # (rel, fi) 键会互相串指纹）
            cached = {(r[0], r[1], r[2]): (r[3], r[4]) for r in conn.execute(
                "SELECT rel_path, face_index, inst_index, mtime_ns, fp "
                "FROM fingerprints WHERE algo=?", (FP_ALGO,))}
            writeback = []
            for (rel, fi, ps, ver, al, disp, fam, nl, w, wd, it, cmb,
                 script, cat, size, mtime, ix, vax) in load_font_rows(conn):
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
                hit = cached.get((rel, fi, ix))
                if hit is not None and hit[0] == mtime:
                    fp = hit[1]
                else:
                    # 缓存缺失（旧库/新文件/mtime 变化）→ 现算并回写
                    fp = fingerprint_of(list(aliases), cmap, w, wd, bool(it))
                    writeback.append((rel, fi, ix, mtime, fp))
                raw.append((di, rel, fi, ps, version, aliases, disp, family,
                            name_langs, w, wd, bool(it), script, cat, cmap,
                            fp, size, mtime, ix, vax))
            if writeback:
                with conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO fingerprints"
                        "(rel_path,face_index,inst_index,mtime_ns,algo,fp) "
                        "VALUES (?,?,?,?,?,?)",
                        [(rel, fi, ix, mtime, FP_ALGO, fp)
                         for rel, fi, ix, mtime, fp in writeback])
        finally:
            conn.close()

    def entities(self) -> list[FontEntity]:
        return list(self._entities)

    def find_by_name(self, norm: str) -> list[FontEntity]:
        return list(self._by_alias.get(normalize_name(norm), ()))

    def stats(self) -> dict:
        sc = sum(1 for e in self._entities if e.script in ("简", "简繁"))
        tc = sum(1 for e in self._entities if e.script in ("繁", "简繁"))
        return {"dirs": len(self.dirs), "files": self._files_count,
                "fonts": len(self._entities),
                "support_sc": sc, "support_tc": tc, "skipped": len(self._skipped)}

    def skipped(self) -> list[tuple[str, str, int, int]]:
        """重复版本跳过条目 (dir, rel, face_index, inst_index)；展示文案
        由 GUI 层拼。"""
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
