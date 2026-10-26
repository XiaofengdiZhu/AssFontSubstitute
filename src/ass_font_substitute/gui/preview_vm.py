from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.gui.name_lang import pick_display_name
from ass_font_substitute.gui.name_lang import ui_lang

CATEGORY_ORDER = ["黑", "宋", "圆", "楷", "仿", "未分类"]
SCRIPT_ORDER = ["简", "繁", "简繁"]
TILE_CACHE_MAX = 500     # 条带缓存条数；典型预览（字体名）≈30MB，
                         # 随预览文字长度线性增长（预览输入已限 60 字）


def weight_tag(w: int) -> str:
    # 显示真实值不取整：Semilight 350 曾被折进 W300 桶，与 Light 300
    # 同桶同斜态，合并行里 chip 只剩一个可切（真库微软雅黑实测）
    return f"W{w}"


@dataclass
class FamilyRow:
    """预览列表行：严格同展示名的 face 归组（斜体 toggle/字重 chip 行内
    切换预览）。selected 是当前预览与 picked 的目标：filtered() 按筛选
    重算，chip 手选由行控件就地改字段（下次筛选变化重建时重置）。
    faces = 行全量（chips 与切换范围，筛选不隐藏其他字重）；
    matching = 通过当前筛选的子集（行保留与 selected 的来源）。"""
    display_name: str
    faces: list[FontEntity]
    selected: FontEntity
    matching: list[FontEntity] | None = None

    def __post_init__(self):
        if self.matching is None:
            self.matching = self.faces


class PreviewVM:
    """字体库预览页与「从字体库选择」对话框共享的筛选 + 预览 tile LRU（零 Qt）。"""

    def __init__(self, settings=None):
        self._settings = settings
        self.entities: list[FontEntity] = []
        self.name_query = ""
        self.category: str | None = None
        self.script: str | None = None
        self.weight: int | None = None
        self._tiles: OrderedDict = OrderedDict()

    def set_entities(self, entities) -> None:
        self.entities = list(entities)

    def weight_buckets(self) -> list[int]:
        return sorted({e.weight // 100 * 100 for e in self.entities})

    def categories(self) -> list[str]:
        present = {e.category for e in self.entities}
        return [c for c in CATEGORY_ORDER if c in present]

    def scripts(self) -> list[str]:
        present = {e.script for e in self.entities}
        return [s for s in SCRIPT_ORDER if s in present]

    def _name(self, e: FontEntity) -> str:
        """行展示名：按界面语言链从 name 表语言记录现挑（归组键仍用入
        库 display_name——按链挑名会因各 face 语言记录集不同裂行）。"""
        return pick_display_name(e, ui_lang(self._settings))

    def filtered(self) -> list[FamilyRow]:
        """行保留 = 行内任一 face 通过全部筛选；faces 全量保留（chips 不
        随筛选隐藏其他字重，点chip 可切看），selected 从通过筛选的子集
        里选——锚点 = 筛选字重桶（筛 W300 时精确 300 胜同桶 350），无
        筛选时 400。行序按展示名 casefold。行名 = 语言链挑名、归组/拆
        行 = legacy display_name：二者口径不同，同 legacy 组各行行名可
        偶发不同名、不同 legacy 组可偶发撞出同名行（纯外观边角）。"""
        q = self.name_query.strip().casefold()

        def ok(e: FontEntity) -> bool:
            if q and not any(q in a.casefold() for a in e.aliases):
                return False
            if self.category and e.category != self.category:
                return False
            if self.script and e.script != self.script:
                return False
            return (self.weight is None
                    or e.weight // 100 * 100 == self.weight)

        groups: dict[str, list[FontEntity]] = {}
        for e in self.entities:
            key = e.display_name.strip().casefold()
            groups.setdefault(key, []).append(e)
        target = 400 if self.weight is None else self.weight

        def sel_key(e: FontEntity):
            return abs(e.weight - target), e.italic, e.width

        rows: list[FamilyRow] = []
        for faces in groups.values():
            # 索引序是目录/文件序（TTC 各 face 交错）：行内按 字重 → 斜体
            # → 宽度 归位，chip 点击按同键取 face。结构按全量拆（不随筛
            # 选变，chips/切换范围稳定）
            faces.sort(key=lambda e: (e.weight, e.italic, e.width))
            # 同 (精确字重, 斜态) 撞车的 face 不是同族字重关系（同 psname
            # 早已版本去重，剩下的是别名撞名/同值异字体），拆独立行——藏进
            # 行里 chip 只剩一个可切（真库方正同名字体双格式实测）
            buckets: dict[tuple[int, bool], list[FontEntity]] = {}
            for f in faces:
                buckets.setdefault((f.weight, f.italic), []).append(f)
            main = sorted((fl[0] for fl in buckets.values() if len(fl) == 1),
                          key=lambda e: (e.weight, e.italic, e.width))
            matching = [f for f in main if ok(f)]
            if matching:
                rows.append(FamilyRow(self._name(main[0]), main,
                                      min(matching, key=sel_key), matching))
            for fl in buckets.values():
                if len(fl) > 1:
                    for f in fl:
                        if ok(f):
                            rows.append(FamilyRow(self._name(f), [f], f))
        rows.sort(key=lambda r: r.display_name.casefold())
        return rows

    def as_rows(self, entities) -> list[FamilyRow]:
        """face 列表 → 单 face 行（推荐区不合并，逐 face 显示）。"""
        return [FamilyRow(self._name(e), [e], e) for e in entities]

    # ---- tile LRU：键 (fingerprint, 预览文字) ----
    def get_tiles(self, key):
        t = self._tiles.pop(key, None)
        if t is not None:
            self._tiles[key] = t
        return t

    def put_tiles(self, key, tiles) -> None:
        self._tiles[key] = tiles
        self._tiles.move_to_end(key)
        while len(self._tiles) > TILE_CACHE_MAX:
            self._tiles.popitem(last=False)

    def invalidate(self) -> None:
        self._tiles.clear()
