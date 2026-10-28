from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from ass_font_substitute.core.assparse.extract import merge_usages
from ass_font_substitute.core.coverage.matcher import UsageClass
from ass_font_substitute.core.coverage.matcher import coverage_detail
from ass_font_substitute.core.decision.store import latest_decision
from ass_font_substitute.core.decision.store import record_decision
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.models import FontUsage
from ass_font_substitute.core.similarity.search import FontSearcher
from ass_font_substitute.gui.jobs import ApplyFileResult
from ass_font_substitute.gui.jobs import FileParse
from ass_font_substitute.gui.jobs import apply_to_file
from ass_font_substitute.gui.jobs import classify_one
from ass_font_substitute.gui.name_lang import pick_display_name
from ass_font_substitute.gui.name_lang import ui_lang

KEEP = "__keep__"           # 「保留原字体名」选项键（missing/zero 卡）
SIM_FLOOR = 0.50            # 放宽重算下限（= SIM_BOUNDS[0]）
RELAX_STEP = 0.10

_STATUS = {"zero": "无对白用字", "keep": "保留", "twin": "孪生对应",
           "replace": "需替换", "missing": "库中没有"}


def weight_tag(w: int) -> str:
    # 显示真实值不取整，与 preview_vm.weight_tag 同步（350 谎报 W300 曾
    # 掩盖 Light/Semilight 两个字重）
    return f"W{w}"


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
    n_missing: int = 0                                # 未覆盖总字数（预览截 12 字）
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
            old = by.get(p.path)
            if old is not None:
                # 重载/重解析保留用户勾选：默认 checked=True 会静默恢复
                # 被排除的文件，「替换并保存」会写回用户明确排除的文件
                p.checked = old.checked
            by[p.path] = p
        self.files = list(by.values())

    def set_checked(self, path: str, checked: bool) -> None:
        for f in self.files:
            if f.path == path:
                f.checked = checked
                return
        raise KeyError(path)

    def remove_file(self, path: str) -> None:
        """从列表剔除文件（UI 移除钮）；文件从未在列则不动。"""
        self.files = [f for f in self.files if f.path != path]

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
            twin_opt = OptionItem(twin.fingerprint, self._dname(twin),
                                  coverage=tcov,
                                  meta=self._meta(twin), badges=["孪生对应"],
                                  source="twin", entity=twin)
            card.options = [twin_opt] + [o for o in opts
                                         if o.key != twin.fingerprint]
        else:
            card.options = opts
        if cls.kind == "replace":
            card.n_missing = len(cls.missing_chars)
            card.missing_preview = list(cls.missing_chars)[:12]
        card.min_sim = float(self._settings.get("min_sim"))
        return card

    def _dname(self, e: FontEntity) -> str:
        """选项展示名：按界面语言链从 name 表语言记录现挑。"""
        return pick_display_name(e, ui_lang(self._settings))

    def _entity_option(self, e: FontEntity, usage: FontUsage) -> OptionItem:
        cov, _ = coverage_detail(e.cmap, usage.chars)
        is_orig = usage.norm_name in {normalize_name(a) for a in e.aliases}
        return OptionItem(e.fingerprint, self._dname(e), coverage=cov,
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
                # 回放只加「上次使用」徽标，不改写 source：记忆落在原字体行
                # 时保持 source=original，Card.replaced 语义（勾原字体=不替换）
                if o.source != "original":
                    o.source = "last"
                if "上次使用" not in o.badges:
                    o.badges = [*o.badges, "上次使用"]
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

    # ---- 搜索层 ----
    def attach_searcher(self, card: Card, searcher: FontSearcher) -> None:
        card.searcher = searcher
        card.searching = True
        card.search_state = "idle"
        # 卡片门槛跟随本轮实搜值：放宽重算以此为基准（降 0.10 至下限）
        card.min_sim = float(searcher.min_sim)

    def add_candidates(self, card: Card, candidates) -> list[OptionItem]:
        """搜索批次回填：按指纹去重，实体查 ent_by_fp；返回本次新增项。"""
        added = []
        for c in candidates:
            if card.option(c.fingerprint) is not None:
                continue
            ent = self.ent_by_fp.get(c.fingerprint)
            o = OptionItem(c.fingerprint,
                           self._dname(ent) if ent is not None
                           else c.display_name,
                           coverage=c.coverage,
                           similarity=c.similarity, size_ratio=c.size_ratio,
                           meta=self._meta(ent)
                           if ent is not None else [],
                           source="search", n_chars=c.n_chars,
                           entity=ent)
            card.options.append(o)
            added.append(o)
        return added

    def finish_search(self, card: Card, searcher: FontSearcher) -> None:
        """终态：target_error（原字体无法渲染）> paused（可继续）>
        exhausted（无更多相似字体）。"""
        card.searching = False
        if searcher.target_error:
            card.search_state = "target_error"
        elif searcher.paused:
            card.search_state = "paused"
        else:
            card.search_state = "exhausted"

    def relax_min_sim(self, card: Card) -> float | None:
        """放宽重算门槛：降 10 个百分点，下限 50%。新门槛入 card.min_sim，
        由页面用新值建**新**搜索器（hit 计数清零）；旧结果保留——被拒 pair
        已入缓存，重算零渲染。已达下限返回 None。"""
        cur = card.min_sim if card.min_sim is not None \
            else float(self._settings.get("min_sim"))
        nxt = round(max(SIM_FLOOR, cur - RELAX_STEP), 2)
        if nxt >= cur:
            return None
        card.min_sim = nxt
        return nxt

    def _search_target(self, card: Card) -> FontEntity | None:
        """相似度基准 = 字幕原字体对应的在库实体（优先原名命中，否则覆盖
        最高者）；孪生体不作 target（已在选项里，指纹会经池排除逻辑互斥）。"""
        chars = self.chars_by_norm.get(card.norm_name, set())
        ents = [o.entity for o in card.options if o.entity is not None
                and "孪生对应" not in o.badges]
        if not ents:
            return None
        orig = next((e for e in ents
                     if card.norm_name in {normalize_name(a) for a in e.aliases}),
                    None)
        if orig is not None:
            return orig
        return max(ents, key=lambda e: e.cmap.hit_ratio(chars)) if chars else ents[0]

    def attach_pick(self, card: Card, entity: FontEntity) -> OptionItem:
        """选择对话框回填：去重后勾选该实体。"""
        chars = self.chars_by_norm.get(card.norm_name, set())
        cov, _ = coverage_detail(entity.cmap, chars)
        o = card.option(entity.fingerprint)
        if o is None:
            o = OptionItem(entity.fingerprint, self._dname(entity),
                           coverage=cov,
                           meta=self._meta(entity), source="pick", entity=entity)
            card.options.append(o)
        card.checked_key = o.key
        return o

    # ---- 应用层 ----
    def build_mapping(self) -> dict[str, str]:
        """raw 拼写全集 → 选中实体名。勾选原字体行（source=original）不改写。
        写回用入库 display_name（功能名，渲染器匹配用），不随界面语言链
        挑名走——展示名换语言不该改变落盘字幕里的字体名。"""
        m: dict[str, str] = {}
        for c in self.cards:
            if not c.replaced:
                continue
            o = c.option(c.checked_key)
            for v in c.variants:
                m[v] = (o.entity.display_name if o.entity is not None
                        else o.display_name)
        return m

    def replacement_counts(self) -> tuple[int, int]:
        return sum(1 for c in self.cards if c.replaced), len(self.cards)

    def _out_path(self, path: str) -> Path:
        return Path(path).with_name(Path(path).stem + self._settings.get("save_suffix"))

    def pending_out_conflicts(self) -> list[str]:
        """另存目标已存在的勾选文件（覆盖原文件模式无冲突）。"""
        return [str(self._out_path(f.path)) for f in self.checked_parses()
                if not self._settings.get("overwrite_default")
                and self._out_path(f.path).exists()]

    def apply_files(self, overwrite_confirm: bool = False) -> list:
        """逐勾选文件写回（jobs.apply_to_file）。另存目标已存在且未确认 →
        该文件 error="exists" 跳过；覆盖原文件模式无冲突。"""
        mapping = self.build_mapping()
        overwrite = bool(self._settings.get("overwrite_default"))
        out = []
        for f in self.checked_parses():
            if not overwrite and not overwrite_confirm \
                    and self._out_path(f.path).exists():
                out.append(ApplyFileResult(f.path, error="exists"))
                continue
            out.append(apply_to_file(f.path, mapping,
                                     self._settings.get("save_suffix"), overwrite))
        return out

    def record_all(self) -> None:
        """决定记忆：勾选实体 → fingerprint；KEEP → keep=True。
        键 = norm_name + 当前方向。"""
        for c in self.cards:
            if c.checked_key == KEEP:
                record_decision(self.decisions_db, c.norm_name, self.direction,
                                None, keep=True)
            else:
                record_decision(self.decisions_db, c.norm_name, self.direction,
                                c.checked_key)


class _LibStub:
    """仅实现 find_by_name 的库替身（按指纹反查别名）。生产路径用真 LibraryIndex。"""

    def __init__(self, ent_by_fp):
        self._ents = list(ent_by_fp.values())

    def find_by_name(self, norm: str) -> list[FontEntity]:
        n = normalize_name(norm)
        return [e for e in self._ents
                if any(normalize_name(a) == n for a in e.aliases)]
