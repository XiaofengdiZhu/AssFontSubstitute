from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ass_font_substitute.core.assparse.extract import merge_usages
from ass_font_substitute.core.coverage.matcher import UsageClass, coverage_detail
from ass_font_substitute.core.decision.store import latest_decision, record_decision
from ass_font_substitute.core.fontindex.library import FontEntity, normalize_name
from ass_font_substitute.core.models import FontUsage
from ass_font_substitute.core.similarity.search import FontSearcher
from ass_font_substitute.gui.jobs import FileParse, classify_one

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
    source: str = "search"                            # original/twin/recommend/search/last/pick
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
        self._by_norm: dict[str, list[FontEntity]] = {}

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

    # ---- 分类层 ----
    def set_entities(self, entities) -> None:
        self.ent_by_fp = {e.fingerprint: e for e in entities}
        # 别名 → 实体倒排：孪生卡补列在库原字体用（classify 的 twin 类只带孪生体）
        self._by_norm = {}
        for e in self.ent_by_fp.values():
            for a in e.aliases:
                self._by_norm.setdefault(normalize_name(a), []).append(e)

    def rebuild(self, usages, threshold: float, direction: str) -> None:
        """清空重灌（方向/勾选变化触发）。worker 侧逐 usage 调 classify_one
        后逐卡产出；此入口供测试与同步小集使用。"""
        self.direction = direction
        self.cards = []
        self.chars_by_norm = {}
        for u in usages:
            cls = classify_one(_LibStub(self.ent_by_fp), u, threshold, direction)
            self.cards.append(self.make_card(u, cls))

    def make_card(self, usage: FontUsage, cls: UsageClass) -> Card:
        card = Card(norm_name=usage.norm_name, raw_name=usage.raw_name,
                    kind=cls.kind, style_count=usage.style_count,
                    inline_count=usage.inline_count, n_chars=len(usage.chars),
                    variants=set(usage.variants))
        self.chars_by_norm[usage.norm_name] = set(usage.chars)
        if cls.kind in ("zero", "missing"):
            card.options = [OptionItem(KEEP, "保留原字体名", badges=["不改动"])]
            return card
        opts = [self._entity_option(e, usage) for e in cls.entities]
        if cls.kind == "twin" and cls.twin_hit is not None:
            # 用户拍板 2026-10-23：孪生卡补列在库原字体（繁版可手选保留）。
            # classify 的 twin 类只携带孪生体，在库原字体按名补查实体集。
            twin = cls.twin_hit
            have = {e.fingerprint for e in cls.entities} | {twin.fingerprint}
            opts += [self._entity_option(e, usage)
                     for e in self._by_norm.get(usage.norm_name, [])
                     if e.fingerprint not in have]
        opts.sort(key=lambda o: -(o.coverage or 0.0))
        if cls.kind == "twin":
            twin = cls.twin_hit
            tcov, _ = coverage_detail(twin.cmap, usage.chars)
            twin_opt = OptionItem(twin.fingerprint, twin.display_name, coverage=tcov,
                                  meta=self._meta(twin), badges=["孪生对应"],
                                  source="twin", entity=twin)
            card.options = [twin_opt] + [o for o in opts
                                         if o.key != twin.fingerprint]
        else:
            card.options = opts
        if cls.kind == "replace":
            card.missing_preview = list(cls.missing_chars)[:12]
        card.min_sim = float(self._settings.get("min_sim"))
        return card

    def _entity_option(self, e: FontEntity, usage: FontUsage) -> OptionItem:
        cov, _ = coverage_detail(e.cmap, usage.chars)
        is_orig = usage.norm_name in {normalize_name(a) for a in e.aliases}
        return OptionItem(e.fingerprint, e.display_name, coverage=cov,
                          meta=self._meta(e),
                          badges=(["原字体", "不改动"] if is_orig else []),
                          source="original" if is_orig else "recommend",
                          entity=e)

    def _meta(self, e: FontEntity) -> list[str]:
        return [e.script, weight_tag(e.weight), e.category]

    def apply_default_check(self, card: Card) -> None:
        """默认勾选链：上次使用（方向一致且指纹仍在选项中）> 孪生对应 >
        原字体 > 保留原字体名。"""
        if card.kind in ("zero", "missing"):
            card.checked_key = KEEP
            return
        d = latest_decision(self.decisions_db, card.norm_name, self.direction)
        if d is not None:
            fp, keep = d
            if keep and card.option(KEEP) is not None:
                card.checked_key = KEEP
                return
            o = card.option(fp) if not keep else None
            if o is not None:
                o.source, o.badges = "last", list(o.badges) + ["上次使用"]
                card.checked_key = fp
                return
        if card.kind == "twin":
            card.checked_key = card.options[0].key
            return
        orig = next((o for o in card.options if "原字体" in o.badges), None)
        if orig is not None:
            card.checked_key = orig.key
        elif card.options:
            card.checked_key = card.options[0].key
        else:
            card.checked_key = KEEP


class _LibStub:
    """仅实现 find_by_name 的库替身（按指纹反查别名）。生产路径用真 LibraryIndex。"""

    def __init__(self, ent_by_fp):
        self._ents = list(ent_by_fp.values())

    def find_by_name(self, norm: str) -> list[FontEntity]:
        n = normalize_name(norm)
        return [e for e in self._ents
                if any(normalize_name(a) == n for a in e.aliases)]
