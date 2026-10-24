import pytest

from ass_font_substitute.core.coverage.matcher import UsageClass
from ass_font_substitute.core.decision.store import latest_decision, record_decision
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.gui.jobs import FileParse, classify_one, make_searcher, pull_chunk
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.subs_vm import KEEP, SubsVM
from tests.fonts import build_font


def _parse(path, name, chars="永宇"):
    u = _usage(name, chars)
    return FileParse(path, None, [u], [], [])


def _usage(name, chars):
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.core.fontindex.library import normalize_name
    return FontUsage(raw_name=name, norm_name=normalize_name(name), style_count=1,
                     inline_count=2, chars=set(chars), variants={name})


@pytest.fixture()
def vm(tmp_path):
    return SubsVM(Settings(tmp_path / "s.json"), tmp_path / "decisions.db")


def test_add_files_replaces_same_path(vm):
    vm.add_files([_parse("a.ass", "字体甲"), _parse("b.ass", "字体乙")])
    assert len(vm.files) == 2
    vm.add_files([_parse("a.ass", "字体甲2")])
    assert len(vm.files) == 2
    assert vm.files[0].usages[0].raw_name == "字体甲2"


def test_set_checked_and_merge(vm):
    vm.add_files([_parse("a.ass", "甲", "永"), _parse("b.ass", "乙", "宇"),
                  _parse("c.ass", "丙", "好")])
    vm.set_checked("b.ass", False)
    assert [f.path for f in vm.checked_parses()] == ["a.ass", "c.ass"]
    us = vm.merged_usages()
    assert {u.norm_name for u in us} == {"甲", "丙"}
    assert us[0].chars == {"永"}


def test_error_file_excluded_from_merge(vm):
    vm.add_files([FileParse("bad.ass", "解析失败", [], [], []),
                  _parse("a.ass", "甲", "永")])
    assert [u.norm_name for u in vm.merged_usages()] == ["甲"]


# ---- 分类层（Task 12）----

def _ent(fp, name, chars, category="黑", script="简", weight=400):
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=weight, width=5,
                      italic=False, script=script, category=category,
                      cmap=CmapRanges.from_chars([ord(c) for c in chars]),
                      dir_path="d", rel_path=f"{fp}.ttf", face_index=0)


FULL = "永宇的好"


def _vm_full(tmp_path):
    vm = SubsVM(Settings(tmp_path / "s.json"), tmp_path / "d.db")
    ents = [_ent("fp-hei", "黑体", FULL), _ent("fp-song", "宋体", "永宇", "宋"),
            _ent("fp-twin", "简体黑", FULL)]
    vm.set_entities(ents)
    return vm, ents


@pytest.fixture()
def vm_full(tmp_path):
    # 测试体一律 _vm_full(vm_full) 取环境：此夹具即 tmp_path 别名
    return tmp_path


def test_make_card_keep_with_original_badge(vm_full):
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", FULL)
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(usage, cls)
    assert card.kind == "keep"
    assert card.header == "1 样式 · 2 行内 · 4 字"
    assert [o.key for o in card.options] == ["fp-hei", "fp-song"]
    assert "原字体" in card.options[0].badges and "不改动" in card.options[0].badges
    # _ent("fp-song", "宋体", "永宇", "宋")："宋" 落在 category 位，
    # script 保持默认 "简"、weight 默认 400
    assert card.options[1].meta == ["简", "W400", "宋"]
    assert card.options[1].source == "recommend"      # 在库命中但非原字体
    vm.apply_default_check(card)
    assert card.checked_key == "fp-hei" and card.replaced is False


def test_make_card_twin_lists_originals(vm_full):
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", "永宇好字之外的字")            # 在库但覆盖不足
    cls = UsageClass("twin", [ents[2]], 1.0, [], twin_hit=ents[2])
    card = vm.make_card(usage, cls)
    assert [o.key for o in card.options] == ["fp-twin", "fp-hei"]
    assert "孪生对应" in card.options[0].badges
    assert "原字体" in card.options[1].badges            # 用户拍板：补列原字体
    vm.apply_default_check(card)
    assert card.checked_key == "fp-twin"


def test_make_card_replace_missing_preview(vm_full):
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", "永宇好字之外的字")
    cls = UsageClass("replace", [ents[0]], 0.4, list("之外的字"))
    card = vm.make_card(usage, cls)
    assert card.kind == "replace"
    assert card.missing_preview == list("之外的字")[:12]
    vm.apply_default_check(card)
    assert card.checked_key == "fp-hei"


def test_make_card_missing_and_zero(vm_full):
    vm, ents = _vm_full(vm_full)
    c1 = vm.make_card(_usage("不存在体", FULL), UsageClass("missing"))
    assert c1.kind == "missing" and c1.options[0].key == KEEP
    vm.apply_default_check(c1)
    assert c1.checked_key == KEEP and c1.replaced is False
    c2 = vm.make_card(_usage("空用字体", ""), UsageClass("zero"))
    assert c2.kind == "zero" and c2.status_text == "无对白用字"


def test_default_check_last_decision_wins(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", FULL)
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(usage, cls)
    record_decision(vm.decisions_db, "黑体", "t2s", "fp-song")
    vm.apply_default_check(card)
    assert card.checked_key == "fp-song"
    assert card.option("fp-song").source == "last"
    assert "上次使用" in card.option("fp-song").badges


def test_default_check_replay_keeps_original_source(vm_full, tmp_path):
    # 回归：记忆指向原字体行时只加「上次使用」徽标，不改写 source——
    # 勾原字体行 = 不替换（Card.replaced 语义）不得被回放破坏
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", FULL)
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(usage, cls)
    record_decision(vm.decisions_db, "黑体", "t2s", "fp-hei")
    vm.apply_default_check(card)
    o = card.option("fp-hei")
    assert card.checked_key == "fp-hei"
    assert o.source == "original"
    assert "上次使用" in o.badges
    assert card.replaced is False
    vm.apply_default_check(card)                       # 回放去重：徽标不叠加
    assert o.badges.count("上次使用") == 1


def test_default_check_keep_decision_targets_keep_option(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    card = vm.make_card(_usage("不存在体", FULL), UsageClass("missing"))
    record_decision(vm.decisions_db, "不存在体", "t2s", None, keep=True)
    vm.apply_default_check(card)
    assert card.checked_key == KEEP


def test_default_check_direction_mismatch_ignores_memory(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    vm.direction = "s2t"
    record_decision(vm.decisions_db, "黑体", "t2s", "fp-song")   # 方向不同不生效
    card = vm.make_card(_usage("黑体", FULL), UsageClass("keep", [ents[0]], 1.0, []))
    vm.apply_default_check(card)
    assert card.checked_key == "fp-hei"


def test_rebuild_clears_and_orders(vm_full):
    vm, ents = _vm_full(vm_full)
    vm.rebuild([_usage("黑体", FULL), _usage("不存在体", "永")], 0.95, "t2s")
    assert [c.kind for c in vm.cards] == ["keep", "missing"]
    vm.rebuild([_usage("不存在体", "永")], 0.95, "t2s")
    assert len(vm.cards) == 1


# ---- 搜索层（Task 13）----

@pytest.fixture()
def lib_env(tmp_path):
    """三字体隔离库：目标 TGT（圆）、近亲 NEAR（圆，形状同）、远体 FAR（box）。"""
    d = tmp_path / "fonts"
    d.mkdir()
    build_font(d / "t.ttf", chars="永中木", family="TGT圆", shape="circle")
    build_font(d / "n.ttf", chars="永中木", family="NEAR圆", shape="circle")
    build_font(d / "f.ttf", chars="永中木", family="FAR方", shape="box")
    from ass_font_substitute.core.fontindex.store import update_index, open_db
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.close()
    from ass_font_substitute.core.fontindex.library import LibraryIndex
    lib = LibraryIndex.open([d])
    from types import SimpleNamespace
    s = Settings(tmp_path / "s.json")
    ctx = SimpleNamespace(settings=s, cache=Cache(tmp_path / "c.db"),
                          snapshot=None, lib=lib, lib_key=(str(d), None))
    vm = SubsVM(s, tmp_path / "d.db")
    vm.set_entities(lib.entities())
    return vm, lib, ctx


def _card_for(vm, lib, name):
    usage = _usage(name, "永中木")
    cls = classify_one(lib, usage, 0.95, "t2s")
    card = vm.make_card(usage, cls)
    vm.apply_default_check(card)
    return card


def test_search_round_appends_and_finishes_paused(lib_env):
    vm, lib, ctx = lib_env
    card = _card_for(vm, lib, "TGT圆")
    s = make_searcher(ctx, target=vm._search_target(card), chars=set("永中木"),
                      threshold=0.95, n_pause=1, min_sim=0.5,
                      size_range=(0.5, 2.0), direction="t2s")
    vm.attach_searcher(card, s)
    cands, state = pull_chunk(s)
    vm.add_candidates(card, cands)
    vm.finish_search(card, s)
    assert card.search_state == "paused"               # n_pause=1 攒 1 命中即暂停
    assert {o.display_name for o in card.options} >= {"NEAR圆"}
    assert any(o.source == "search" for o in card.options)
    # 续搜：累计 hit_count——再来 1 个命中又暂停
    cands2, state2 = pull_chunk(s)
    vm.add_candidates(card, cands2)
    vm.finish_search(card, s)
    assert card.search_state in ("paused", "exhausted")


def test_exhausted_and_relax(lib_env):
    vm, lib, ctx = lib_env
    card = _card_for(vm, lib, "TGT圆")
    s = make_searcher(ctx, target=vm._search_target(card), chars=set("永中木"),
                      threshold=0.95, n_pause=99, min_sim=0.99,
                      size_range=(0.5, 2.0), direction="t2s")
    vm.attach_searcher(card, s)
    cands, state = pull_chunk(s)
    vm.add_candidates(card, cands)
    vm.finish_search(card, s)
    assert card.search_state == "exhausted"
    new_min = vm.relax_min_sim(card)
    assert new_min == pytest.approx(0.89)
    s2 = make_searcher(ctx, target=vm._search_target(card), chars=set("永中木"),
                       threshold=0.95, n_pause=99, min_sim=new_min,
                       size_range=(0.5, 2.0), direction="t2s")
    vm.attach_searcher(card, s2)
    cands2, state2 = pull_chunk(s2)
    vm.add_candidates(card, cands2)
    vm.finish_search(card, s2)
    assert card.search_state == "exhausted"
    assert any(o.display_name == "NEAR圆" for o in card.options)   # 放宽后捞回
    # 去重：重复候选不重复进 options
    n_before = len(card.options)
    vm.add_candidates(card, cands2)
    assert len(card.options) == n_before


def test_relax_floor(lib_env):
    vm, lib, ctx = lib_env
    card = _card_for(vm, lib, "TGT圆")
    card.min_sim = 0.55
    assert vm.relax_min_sim(card) == pytest.approx(0.50)
    card.min_sim = 0.50
    assert vm.relax_min_sim(card) is None


def test_search_target_prefers_original(lib_env):
    vm, lib, ctx = lib_env
    card = _card_for(vm, lib, "TGT圆")
    t = vm._search_target(card)
    assert t is not None and t.display_name == "TGT圆"


def test_attach_pick_dedupes_and_checks(lib_env):
    vm, lib, ctx = lib_env
    card = _card_for(vm, lib, "TGT圆")
    e = next(e for e in lib.entities() if e.display_name == "NEAR圆")
    o = vm.attach_pick(card, e)
    assert card.checked_key == e.fingerprint and o.source == "pick"
    vm.attach_pick(card, e)
    assert len([x for x in card.options if x.key == e.fingerprint]) == 1


# ---- 应用层（Task 14）----

def test_build_mapping_covers_all_variants(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    usage = _usage("黑体", FULL)
    usage.variants = {"黑体", "HEITI", "heiti"}
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(usage, cls)
    card.checked_key = "fp-song"                       # 手动改选宋体
    vm.cards = [card]
    m = vm.build_mapping()
    assert m == {"黑体": "宋体", "HEITI": "宋体", "heiti": "宋体"}


def test_replacement_counts(vm_full):
    vm, ents = _vm_full(vm_full)
    c1 = vm.make_card(_usage("黑体", FULL),
                      UsageClass("keep", [ents[0], ents[1]], 1.0, []))
    c1.checked_key = "fp-song"                          # 换宋体 → 替换
    # 宋体卡勾 fp-song 是原字体行（source=original）不计替换；改用孪生行：
    # 勾「非原字体行」实体 → 替换，映射 宋体→宋体（孪生改写同名的真实形态）
    c2 = vm.make_card(_usage("宋体", FULL),
                      UsageClass("twin", [ents[1]], 1.0, [], twin_hit=ents[1]))
    c2.checked_key = "fp-song"                          # 勾孪生行 → 替换
    c3 = vm.make_card(_usage("不存在体", FULL), UsageClass("missing"))
    c3.checked_key = KEEP                               # 保留 → 不替换
    vm.cards = [c1, c2, c3]
    assert vm.replacement_counts() == (2, 3)
    assert vm.build_mapping() == {"黑体": "宋体", "宋体": "宋体"}


def test_apply_files_and_decisions(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    sub = tmp_path / "a.ass"
    # 对白带足中性高频字：detect_encoding 合理性校验要求 ≥5 个
    sub.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname\nStyle: Def,黑体,54\n\n[Events]\n"
        "Format: Layer, Style, Text\nDialogue: 0,Def,,0,0,0,,永宇的好，我在山中的日子\n",
        "utf-8")
    fp = FileParse(str(sub), None, [_usage("黑体", FULL)], [], [])
    fp.checked = True
    vm.files = [fp]
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(_usage("黑体", FULL), cls)
    card.checked_key = "fp-song"
    vm.cards = [card]
    assert vm.pending_out_conflicts() == []
    rs = vm.apply_files()
    assert rs[0].error is None
    out = tmp_path / "a.substituted.ass"
    assert out.exists() and "宋体" in out.read_text("utf-8")
    assert "黑体" not in out.read_text("utf-8")
    vm.record_all()
    assert latest_decision(vm.decisions_db, "黑体", "t2s") == ("fp-song", False)


def test_apply_conflict_requires_confirm(vm_full, tmp_path):
    vm, ents = _vm_full(vm_full)
    sub = tmp_path / "b.ass"
    sub.write_text("x", "utf-8")
    out = tmp_path / "b.substituted.ass"
    out.write_text("旧文件", "utf-8")                    # 已存在 → 冲突
    fp = FileParse(str(sub), None, [_usage("黑体", FULL)], [], [])
    vm.files = [fp]
    cls = UsageClass("keep", [ents[0], ents[1]], 1.0, [])
    card = vm.make_card(_usage("黑体", FULL), cls)
    card.checked_key = "fp-song"
    vm.cards = [card]
    assert vm.pending_out_conflicts() == [str(out)]
    rs = vm.apply_files()
    assert rs[0].error == "exists" and out.read_text("utf-8") == "旧文件"
    rs = vm.apply_files(overwrite_confirm=True)
    assert rs[0].error is None
