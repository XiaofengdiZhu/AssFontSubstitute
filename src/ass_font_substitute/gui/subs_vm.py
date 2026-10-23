from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ass_font_substitute.core.assparse.extract import merge_usages
from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.decision.store import latest_decision, record_decision
from ass_font_substitute.core.fontindex.library import FontEntity, normalize_name
from ass_font_substitute.core.models import FontUsage
from ass_font_substitute.core.similarity.search import FontSearcher
from ass_font_substitute.gui.jobs import FileParse

KEEP = "__keep__"           # 「保留原字体名」选项键（missing/zero 卡）
SIM_FLOOR = 0.50            # 放宽重算下限（= SIM_BOUNDS[0]）
RELAX_STEP = 0.10

_STATUS = {"zero": "无对白用字", "keep": "保留", "twin": "孪生对应",
           "replace": "需替换", "missing": "库中没有"}


def weight_tag(w: int) -> str:
    return f"W{w // 100 * 100}"


@dataclass
class OptionItem:
    key: str
    display_name: str
    coverage: float | None = None
    similarity: float | None = None
    size_ratio: float | None = None
    meta: list[str] = field(default_factory=list)     # 简/繁/简繁 · W400 · 黑
    badges: list[str] = field(default_factory=list)   # 孪生对应/上次使用/原字体/不改动
    source: str = "search"                            # original/twin/search/last/pick
    n_chars: int | None = None
    entity: FontEntity | None = None                  # KEEP 行为 None


@dataclass
class Card:
    norm_name: str
    raw_name: str
    kind: str                                          # zero/keep/twin/replace/missing
    style_count: int = 0
    inline_count: int = 0
    n_chars: int = 0
    variants: set[str] = field(default_factory=set)
    missing_preview: list[str] = field(default_factory=list)   # 需替换卡 tofu 预览
    options: list[OptionItem] = field(default_factory=list)
    checked_key: str | None = None
    searching: bool = False
    search_state: str = "idle"                         # idle/paused/exhausted/target_error
    min_sim: float | None = None
    searcher: FontSearcher | None = None               # 仅 UI 线程读写；worker 只借用

    @property
    def header(self) -> str:
        return f"{self.style_count} 样式 · {self.inline_count} 行内 · {self.n_chars} 字"

    @property
    def status_text(self) -> str:
        return _STATUS[self.kind]

    @property
    def replaced(self) -> bool:
        """勾选了将改写名称的实体选项。KEEP / 未勾 / 勾原字体行 = False。"""
        if self.checked_key is None or self.checked_key == KEEP:
            return False
        o = self.option(self.checked_key)
        return o is not None and o.source != "original"

    def option(self, key: str) -> OptionItem | None:
        return next((o for o in self.options if o.key == key), None)


class SubsVM:
    """处理字幕页状态机（零 Qt）。跨线程约定：worker 只读 vm 公开字段并在
    回调里交还数据，一切变更走 UI 线程的 vm 方法。"""

    def __init__(self, settings, decisions_db: Path):
        self._settings = settings
        self.decisions_db = Path(decisions_db)
        self.direction = "t2s"
        self.files: list[FileParse] = []
        self.cards: list[Card] = []
        self.chars_by_norm: dict[str, set[str]] = {}
        self.ent_by_fp: dict[str, FontEntity] = {}

    # ---- 文件层 ----
    def add_files(self, parses: list[FileParse]) -> None:
        by = {f.path: f for f in self.files}
        for p in parses:
            by[p.path] = p
        self.files = list(by.values())

    def set_checked(self, path: str, checked: bool) -> None:
        for f in self.files:
            if f.path == path:
                f.checked = checked
                return
        raise KeyError(path)

    def checked_parses(self) -> list[FileParse]:
        return [f for f in self.files if f.checked and f.error is None]

    def merged_usages(self) -> list[FontUsage]:
        return merge_usages([f.usages for f in self.checked_parses()])
