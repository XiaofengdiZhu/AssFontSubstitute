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

CARD_RANK = {"twin": 0, "replace": 1, "missing": 2, "keep": 3, "zero": 4}
# 卡片展示序（用户拍板 2026-10-28）：孪生最前 → 需替换 → 库中没有 →
# 保留（覆盖足够）→ 保留（无对白用字）垫底（2026-10-30 拍板：保留两档
# 不交错，有字档在前）；classify 回执按此插入（worker 串行，卡序与
# 提交序一致）


def card_rank(card) -> int:
    """插入档位：0 字 keep 归 zero 档（vm 层两者同为 kind=keep，GUI 按
    _ekind 同口径降格展示；Card.kind="zero" 仅测试直喂出现，亦兼容）。"""
    kind = card.kind
    if kind == "keep" and not card.n_chars:
        kind = "zero"
    return CARD_RANK.get(kind, 9)


def _opt_score(o: OptionItem) -> float:
    """候选行综合分：相似率+覆盖率等权求和（无相似率的在库推荐行按
    覆盖率单项计）。仅用于行序，不影响勾选/替换语义。"""
    return (o.similarity or 0.0) + (o.coverage or 0.0)

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
    badges: list[str] = field(default_factory=list)   # 原字体/孪生对应/上次使用
    source: str = "search"                            # original/twin/recommend/search/last/pick
    n_chars: int | None = None
    entity: FontEntity | None = None                  # KEEP 行为 None
    missing: list[str] = field(default_factory=list)  # 未覆盖字符全列（指标悬停浮层源，展示截 30）


@dataclass
class Card:
    norm_name: str
    raw_name: str
    kind: str                                          # zero/keep/twin/replace/missing
    style_count: int = 0
    inline_count: int = 0
    n_chars: int = 0
    variants: set[str] = field(default_factory=set)
    missing: list[str] = field(default_factory=list)   # 未覆盖字符全列（预览截 30 在 UI 层）
    n_missing: int = 0                                # 未覆盖总字数
    embedded: bool = False                            # 原字体已内嵌进字幕（[Fonts] 段）
    options: list[OptionItem] = field(default_factory=list)
    checked_key: str | None = None
    searching: bool = False
    search_state: str = "idle"                         # idle/paused/exhausted/target_error
    threshold_dirty: bool = False                      # 在跑中门槛变了：停后归 idle 不进 paused
    min_sim: float | None = None
    searcher: FontSearcher | None = None               # 仅 UI 线程读写；worker 只借用

    @property
    def header(self) -> str:
        # 源语言镜像：界面渲染走 vm_text.header（同模板经翻译），此处
        # 零 Qt 供测试直喂；改模板须两处同改
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
                    variants=set(usage.variants),
                    embedded=usage.embedded)
        self.chars_by_norm[usage.norm_name] = set(usage.chars)
        if cls.kind in ("zero", "missing"):
            # missing 卡原字体实际由内嵌字体兜底：标题注「（有内嵌）」
            # 提醒保留即可，不必从库换（zero 卡无事发生，不加噪音）
            keep_title = ("保留原字体名（有内嵌）"
                          if cls.kind == "missing" and usage.embedded
                          else "保留原字体名")
            card.options = [OptionItem(KEEP, keep_title)]
            return card
        opts = [self._entity_option(e, usage) for e in cls.entities]
        if cls.kind == "twin" and cls.twin_hit is not None:
            # 用户拍板 2026-10-23：孪生卡补列在库原字体（繁版可手选保留）。
            # classify 的 twin 类只携带孪生体，在库原字体按名补查实体集。
            have = {e.fingerprint for e in cls.entities}
            have.add(cls.twin_hit.fingerprint)
            opts += [self._entity_option(e, usage)
                     for e in self._by_norm.get(usage.norm_name, [])
                     if e.fingerprint not in have]
        # 行序（用户拍板 2026-10-28）：原字体恒第一，孪生第二，其余按
        # 相似率+覆盖率综合分降序——在库推荐行无相似率，覆盖率单项计分
        origs = [o for o in opts if "原字体" in o.badges]
        rest = [o for o in opts if "原字体" not in o.badges]
        if len(origs) > 1:
            # 同名多实体（库中同族多文件，如 W300/W400 两版本别名同名）：
            # 「原」行只留一个——覆盖率最高者持徽标与 original 语义
            # （勾=不改写名），其余降为普通推荐行（仍可选）。平局取
            # 非斜体 + |weight−400| 最小：工具不改字重斜体（拍板
            # 2026-10-30，有斜体必有正体），预览/缺字条/相似度基准统一
            # 按 Regular 口径取面；再平局取先
            best = max(origs, key=lambda o: self._orig_face_key(
                o.entity, usage.chars, cov=o.coverage))
            for o in origs:
                if o is not best:
                    o.badges.remove("原字体")
                    o.source = "recommend"
                    rest.append(o)
            origs = [best]
        rest.sort(key=lambda o: -_opt_score(o))
        if cls.kind != "keep":
            # keep 卡塌缩原行（无事发生态），上次使用行不进（省一次
            # 决策库查询）；replace/twin 卡插原字体（孪生）之后、推荐行之前
            last = self._last_used_option(card, usage,
                                          {o.key for o in opts})
            if last is not None:
                rest.insert(0, last)
        if cls.kind == "keep":
            # keep 卡只留「原」行（用户拍板 2026-10-30，截图实锤：更纱
            # 黑体 TC 一卡列出同族 W200–W700 正斜体十行）——无事发生态，
            # 同族其余面写回语义相同（均为此名），整墙行是噪音；需替换
            # 卡仍全列供挑面
            card.options = origs or [OptionItem(KEEP, "保留原字体名")]
        elif cls.kind == "twin" and cls.twin_hit is not None:
            twin = cls.twin_hit
            same = next((o for o in origs if o.key == twin.fingerprint), None)
            if same is not None:
                # 同名孪生（如 宋体→宋体）：原字体行与孪生行同指纹，单行
                # 呈现、徽章双挂；source 翻成 twin——勾选=替换语义（映射
                # 同名改写真实形态），保持 original 会丢映射
                same.badges = ["原字体", "孪生对应"]
                same.source = "twin"
                card.options = [*origs, *rest]
            else:
                tcov, tmiss = coverage_detail(twin.cmap, usage.chars)
                twin_opt = OptionItem(twin.fingerprint, self._dname(twin),
                                      coverage=tcov, missing=list(tmiss),
                                      meta=self._meta(twin),
                                      badges=["孪生对应"],
                                      source="twin", entity=twin)
                rest = [o for o in rest if o.key != twin.fingerprint]
                card.options = [*origs, twin_opt, *rest]
        else:
            card.options = origs + rest
        if cls.kind == "replace":
            card.n_missing = len(cls.missing_chars)
            card.missing = list(cls.missing_chars)
        elif cls.kind == "twin":
            # 缺字条=**原字体**未覆盖的字（用户纠偏 2026-10-29：这行历来
            # 是原字体缺字口径，孪生体的未覆盖在指标行另说）。cls.missing_chars
            # 是孪生体的，此处不用；原字体实体按名取，取面口径与「原」行
            # 去重一致（_orig_face_key），原字体不在库（不在库→孪生路径）
            # 则无原字体覆盖可言，不显缺字条
            orig_ents = self._by_norm.get(usage.norm_name) or []
            if orig_ents:
                ent = max(orig_ents,
                          key=lambda e: self._orig_face_key(e, usage.chars))
                _cov, missing = coverage_detail(ent.cmap, usage.chars)
                card.n_missing = len(missing)
                card.missing = list(missing)
        card.min_sim = float(self._settings.get("min_sim"))
        return card

    def _orig_face_key(self, e: FontEntity, chars: set[str], cov=None):
        """同名多实体取面键：覆盖率 > 非斜体 > 字重近 400 > 先入序。
        工具不改字重斜体（拍板 2026-10-30），预览与缺字条统一 Regular
        口径（preview_vm.sel_key 无筛选档同序）。cov 传预计算覆盖率
        （make_card 去重路径 OptionItem 上现成）。"""
        if cov is None:
            cov, _ = coverage_detail(e.cmap, chars)
        return cov, not e.italic, -abs(e.weight - 400)

    def _dname(self, e: FontEntity) -> str:
        """选项展示名：按界面语言链从 name 表语言记录现挑。"""
        return pick_display_name(e, ui_lang(self._settings))

    def _entity_option(self, e: FontEntity, usage: FontUsage) -> OptionItem:
        cov, missing = coverage_detail(e.cmap, usage.chars)
        is_orig = usage.norm_name in {normalize_name(a) for a in e.aliases}
        return OptionItem(e.fingerprint, self._dname(e), coverage=cov,
                          missing=list(missing),
                          meta=self._meta(e),
                          badges=(["原字体"] if is_orig else []),
                          source="original" if is_orig else "recommend",
                          entity=e)

    def _last_used_option(self, card: Card, usage: FontUsage,
                          known_keys: set[str]) -> OptionItem | None:
        """上次使用自动加行（用户需求 2026-10-31，同孪生自动加行同理）：
        决策库有该名+方向记录、记的是字体（keep=False）、指纹在库且不在
        本卡已有键集 → 造「上次使用」行；apply_default_check 既有回放链
        会挂徽标并自动勾上。指纹已在选项中不重复加行（回放只挂徽标），
        指纹已不在库（重扫剔除）不加行。keep/missing 卡不进此路径
        （调用点有 kind 守卫；无事发生态/不在库档各有拍板形态）。
        known_keys 传建卡中键集（card.options 此刻尚未赋值）。"""
        d = latest_decision(self.decisions_db, card.norm_name, self.direction)
        if d is None:
            return None
        fp, keep = d
        if keep or fp in known_keys:
            return None
        e = self.ent_by_fp.get(fp)
        if e is None:
            return None
        o = self._entity_option(e, usage)
        o.source = "last"
        o.badges = [*o.badges, "上次使用"]
        return o

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
                # 回放不改写原字体行 source（勾原字体=不替换的 Card.replaced
                # 语义），也不挂「上次使用」徽标（用户拍板 2026-10-30：原行
                # 「原字体」徽章已够，无事发生态不再标注）；其余行翻 last
                # +挂徽标
                if o.source != "original":
                    o.source = "last"
                    if "上次使用" not in o.badges:
                        o.badges = [*o.badges, "上次使用"]
                card.checked_key = fp
                return
        if card.kind == "twin":
            # 行序里孪生行可能在原字体之后，按 source 取（同名孪生合并行
            # 也是 source=twin）；无实体孪生卡兜底 KEEP
            twin = next((o for o in card.options if o.source == "twin"), None)
            if twin is not None:
                card.checked_key = twin.key
            elif card.options:
                card.checked_key = card.options[0].key
            else:
                card.checked_key = KEEP
            return
        orig = next((o for o in card.options if "原字体" in o.badges), None)
        if orig is not None:
            card.checked_key = orig.key
        elif card.options:
            card.checked_key = card.options[0].key
        else:
            card.checked_key = KEEP

    # ---- 搜索层 ----
    def reset_ended_searches(self) -> int:
        """相似候选前三设置（覆盖率/相似率门槛+相对大小区间）变更后
        重置已停的寻找（拍板 2026-10-30）：暂停/耗尽/原字体报错卡归
        idle——钮回「开始寻找」，再点按新设置建新搜索器；已找到候选
        保留在列（回填按指纹去重不会重复）。在跑的不暂停（拍板
        2026-10-30 二次确认），但置 threshold_dirty：跑停后归 idle 不进
        「继续寻找」（finish_search）。返回重置卡数（不含在跑打标）。"""
        n = 0
        for c in self.cards:
            if c.searching:
                c.threshold_dirty = True
                continue
            if c.search_state in ("paused", "exhausted", "target_error"):
                c.search_state = "idle"
                c.searcher = None
                c.threshold_dirty = False
                n += 1
        return n

    def regate_candidates(self) -> int:
        """门槛变化后对已列出的搜索候选按新门槛重筛（拍板 2026-10-31）：
        「候选保留」只保留仍达标的行——调高门槛后旧搜索低分行继续挂卡，
        会被读作「门槛没生效」（用户报告实锤）。无分项（原字体/元数据
        行）不动；在跑卡不筛（停时 threshold_dirty 归 idle，finish_search
        里补筛）。返回移除行数。"""
        ms = float(self._settings.get("min_sim"))
        lo = float(self._settings.get("size_min"))
        hi = float(self._settings.get("size_max"))
        cv = float(self._settings.get("cov_threshold"))
        removed = 0
        for c in self.cards:
            if c.searching:
                continue
            removed += self._regate_card(c, ms, lo, hi, cv)
        return removed

    def _regate_card(self, card: Card, ms: float, lo: float, hi: float,
                     cv: float) -> int:
        """单卡按门槛四参筛掉不达标的搜索行，口径与 FontSearcher._gate
        对齐（sim < ms / size ∉ [lo, hi] / cov < cv 均出局，边界含等号
        同 _gate）；被勾选行豁免（拍板 2026-10-31：用户已明确选择的候选
        不因调门槛被移除——门槛管准入，不管撤销既有选择），勾选键因此
        正常路径不悬挂；文末 apply_default_check 回落块仅为悬挂键防御
        残留。similarity=None 的搜索行两路：KEEP
        行（make_card 默认 source="search"）与缺字卡无 target 搜索行——
        后者本就只受覆盖率管辖（_evaluate 无 target 路径），coverage 照
        筛；None 兜底按 1.0 放行（_gate 处 coverage/size_ratio 恒为实算
        值，None 仅 KEEP 行与防御路径出现，放行不误伤）。"""
        keep = []
        for o in card.options:
            if o.source != "search":
                keep.append(o)          # 原字体/孪生/上次使用行不属搜索门槛管辖
                continue
            if o.key == card.checked_key:
                keep.append(o)          # 勾选行豁免重筛
                continue
            size = o.size_ratio if o.size_ratio is not None else 1.0
            cov = o.coverage if o.coverage is not None else 1.0
            sim_out = o.similarity is not None and o.similarity < ms
            if sim_out or not (lo <= size <= hi) or cov < cv:
                continue
            keep.append(o)
        n = len(card.options) - len(keep)
        if n:
            card.options = keep
            if card.checked_key is not None \
                    and card.option(card.checked_key) is None:
                self.apply_default_check(card)
        return n

    def attach_searcher(self, card: Card, searcher: FontSearcher) -> None:
        card.searcher = searcher
        card.searching = True
        card.search_state = "idle"
        # 卡片门槛记录本轮实搜值（新搜索器已改从设置页现读，此字段仅存档）
        card.min_sim = float(searcher.min_sim)

    def add_candidates(self, card: Card, candidates) -> list[OptionItem]:
        """搜索批次回填：按指纹去重，实体查 ent_by_fp；返回本次新增项。
        候选行并入综合分排序（原字体/孪生位置固定，见 make_card 行序）。"""
        added = []
        for c in candidates:
            if card.option(c.fingerprint) is not None:
                continue
            ent = self.ent_by_fp.get(c.fingerprint)
            missing: list[str] = []
            if ent is not None:
                # 候选自报覆盖率不携带缺字列，按卡字集现补（浮层悬停源）
                _cov, miss = coverage_detail(
                    ent.cmap, self.chars_by_norm.get(card.norm_name, set()))
                missing = list(miss)
            o = OptionItem(c.fingerprint,
                           self._dname(ent) if ent is not None
                           else c.display_name,
                           coverage=c.coverage,
                           similarity=c.similarity, size_ratio=c.size_ratio,
                           missing=missing,
                           meta=self._meta(ent)
                           if ent is not None else [],
                           source="search", n_chars=c.n_chars,
                           entity=ent)
            card.options.append(o)
            added.append(o)
        if added:
            head = [o for o in card.options if o.source == "original"]
            head += [o for o in card.options if o.source == "twin"]
            rest = [o for o in card.options
                    if o.source not in ("original", "twin")]
            rest.sort(key=lambda o: -_opt_score(o))
            card.options = head + rest
        return added

    def finish_search(self, card: Card, searcher: FontSearcher,
                      cancelled: bool = False) -> None:
        """终态：cancelled=搜索中手动暂停（searcher 未自然暂停，归 paused
        态——续搜复用同一 searcher，累计 hit_count/compared 语义不丢）>
        target_error（原字体无法渲染）> paused（可继续）> exhausted（无
        更多相似字体）。门槛 dirty 卡例外（拍板 2026-10-30：在跑中改了
        门槛，停后直接恢复初始态，不进「继续寻找」——旧口径的候选已
        全列，续搜/无更多皆失义；target_error 态与门槛无关保留，但已列
        候选仍按新门槛补筛（同失义，2026-10-31）。"""
        card.searching = False
        if card.threshold_dirty and not searcher.target_error:
            card.search_state = "idle"
            card.searcher = None
            card.threshold_dirty = False
            # 在跑中改过门槛：本轮候选按旧口径收集，停时按新门槛补筛
            self._regate_card(card, float(self._settings.get("min_sim")),
                              float(self._settings.get("size_min")),
                              float(self._settings.get("size_max")),
                              float(self._settings.get("cov_threshold")))
            return
        if cancelled or searcher.paused:
            card.search_state = "paused"
        elif searcher.target_error:
            card.search_state = "target_error"
            if card.threshold_dirty:
                # 在跑中改过门槛：态保留（原字体报错与门槛无关，拍板
                # 2026-10-30），但已列候选同按新门槛补筛——否则旧低分行
                # 挂卡，与 dirty 归 idle 路径同一「门槛没生效」观感
                card.threshold_dirty = False
                self._regate_card(card, float(self._settings.get("min_sim")),
                                  float(self._settings.get("size_min")),
                                  float(self._settings.get("size_max")),
                                  float(self._settings.get("cov_threshold")))
        else:
            card.search_state = "exhausted"

    def _search_target(self, card: Card) -> FontEntity | None:
        """相似度基准 = 字幕原字体对应的在库实体（优先原名命中，否则覆盖
        最高者）；孪生体不作 target（已在选项里，指纹会经池排除逻辑互斥）；
        上次使用行也不作 target（审查实锤 2026-10-31：纯孪生卡旧语义
        target=None 搜穿整池，自动加行曾使其漂移为 target=上次使用实体
        ——白得门槛/暂停语义，渲染失败还会整卡 target_error）。"""
        chars = self.chars_by_norm.get(card.norm_name, set())
        ents = [o.entity for o in card.options if o.entity is not None
                and "孪生对应" not in o.badges and o.source != "last"]
        if not ents:
            return None
        orig = next((e for e in ents
                     if card.norm_name in {normalize_name(a) for a in e.aliases}),
                    None)
        if orig is not None:
            return orig
        return max(ents, key=lambda e: e.cmap.hit_ratio(chars)) if chars else ents[0]

    def attach_pick(self, card: Card, entity: FontEntity,
                    similarity: float | None = None,
                    size_ratio: float | None = None) -> OptionItem:
        """选择对话框回填：去重后勾选该实体（并入综合分行序，同
        add_candidates）。相似率/相对大小由 worker 评分任务回填。"""
        chars = self.chars_by_norm.get(card.norm_name, set())
        cov, missing = coverage_detail(entity.cmap, chars)
        o = card.option(entity.fingerprint)
        if o is None:
            o = OptionItem(entity.fingerprint, self._dname(entity),
                           coverage=cov, similarity=similarity,
                           size_ratio=size_ratio, missing=list(missing),
                           meta=self._meta(entity), source="pick", entity=entity)
            card.options.append(o)
            head = [x for x in card.options if x.source == "original"]
            head += [x for x in card.options if x.source == "twin"]
            rest = [x for x in card.options
                    if x.source not in ("original", "twin")]
            rest.sort(key=lambda x: -_opt_score(x))
            card.options = head + rest
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
        """另存目标已存在的勾选文件（覆盖原文件模式无冲突）。目标与源
        重合（后缀曾被设 .ass 的存量档）也算冲突，走确认流程兜底。"""
        return [str(op) for f in self.checked_parses()
                if not self._settings.get("overwrite_default")
                and (op := self._out_path(f.path))
                and (op.exists() or op == Path(f.path))]

    def apply_files(self, overwrite_confirm: bool = False) -> list:
        """逐勾选文件写回（jobs.apply_to_file）。另存目标已存在且未确认 →
        该文件 error="exists" 跳过；覆盖原文件模式无冲突。"""
        mapping = self.build_mapping()
        overwrite = bool(self._settings.get("overwrite_default"))
        out = []
        for f in self.checked_parses():
            op = self._out_path(f.path)
            if not overwrite and not overwrite_confirm \
                    and (op.exists() or op == Path(f.path)):
                out.append(ApplyFileResult(f.path, error="exists"))
                continue
            out.append(apply_to_file(f.path, mapping,
                                     self._settings.get("save_suffix"), overwrite))
        return out

    def record_all(self) -> None:
        """决定记忆：勾选实体 → fingerprint；KEEP → keep=True。
        键 = norm_name + 当前方向。零用字卡（n_chars==0）跳过——0 字
        必然 keep，记录是无信息 no-op，只徒增 decisions.db 行数。"""
        for c in self.cards:
            if not c.n_chars:
                continue
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
