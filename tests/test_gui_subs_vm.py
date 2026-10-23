import pytest

from ass_font_substitute.core.coverage.matcher import UsageClass
from ass_font_substitute.core.decision.store import record_decision
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.gui.jobs import FileParse
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.subs_vm import KEEP, OptionItem, SubsVM


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
