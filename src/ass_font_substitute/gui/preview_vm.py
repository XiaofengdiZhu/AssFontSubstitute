from __future__ import annotations

from collections import OrderedDict

from ass_font_substitute.core.fontindex.library import FontEntity

CATEGORY_ORDER = ["黑", "宋", "圆", "楷", "仿", "未分类"]
SCRIPT_ORDER = ["简", "繁", "简繁"]
TILE_CACHE_MAX = 2000


def weight_tag(w: int) -> str:
    return f"W{w // 100 * 100}"


class PreviewVM:
    """字体库预览页与「从字体库选择」对话框共享的筛选 + 预览 tile LRU（零 Qt）。"""

    def __init__(self):
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

    def filtered(self) -> list[FontEntity]:
        q = self.name_query.strip().casefold()
        out = []
        for e in self.entities:
            if q and not any(q in a.casefold() for a in e.aliases):
                continue
            if self.category and e.category != self.category:
                continue
            if self.script and e.script != self.script:
                continue
            if self.weight is not None and e.weight // 100 * 100 != self.weight:
                continue
            out.append(e)
        return out

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
