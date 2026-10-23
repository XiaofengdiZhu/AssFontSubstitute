import pytest

from ass_font_substitute.gui.jobs import FileParse
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.subs_vm import SubsVM


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
