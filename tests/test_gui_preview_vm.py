
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.gui.preview_vm import CATEGORY_ORDER
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.preview_vm import weight_tag


def _ent(fp, name, category="黑", script="简", weight=400):
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=weight, width=5,
                      italic=False, script=script, category=category,
                      cmap=CmapRanges.from_chars([0x6c38]), dir_path="d",
                      rel_path="r.ttf", face_index=0)


def test_weight_tag():
    assert weight_tag(437) == "W400"
    assert weight_tag(700) == "W700"


def test_filter_by_name_casefold_and_alias():
    vm = PreviewVM()
    vm.set_entities([_ent("1", "测试黑体"), _ent("2", "TestHei")])
    vm.name_query = "testhei"
    assert [e.display_name for e in vm.filtered()] == ["TestHei"]


def test_filter_combinations_and_buckets():
    vm = PreviewVM()
    vm.set_entities([_ent("1", "A", "黑", "简", 400), _ent("2", "B", "宋", "繁", 700),
                     _ent("3", "C", "黑", "简繁", 437)])
    assert vm.weight_buckets() == [400, 700]
    assert vm.categories() == [c for c in CATEGORY_ORDER if c in ("黑", "宋")]
    vm.category, vm.script, vm.weight = "黑", "简", 400
    assert [e.display_name for e in vm.filtered()] == ["A"]


def test_tile_lru_evicts_oldest():
    vm = PreviewVM()
    for i in range(2000):
        vm.put_tiles((str(i), "t"), i)
    # 不可先 get_tiles(("0", "t"))：get 会刷新 LRU 新旧度（见
    # test_get_tiles_touches_recency），"0" 被置顶后淘汰的将是 "1" 而非 "0"
    vm.put_tiles(("new", "t"), "x")                    # 触发淘汰最旧
    assert vm.get_tiles(("0", "t")) is None
    assert vm.get_tiles(("new", "t")) == "x"


def test_get_tiles_touches_recency():
    vm = PreviewVM()
    for i in range(2000):
        vm.put_tiles((str(i), "t"), i)
    vm.get_tiles(("0", "t"))                           # 置顶
    vm.put_tiles(("new", "t"), "x")
    assert vm.get_tiles(("0", "t")) is not None        # 未被淘汰
    assert vm.get_tiles(("1", "t")) is None


def test_invalidate():
    vm = PreviewVM()
    vm.put_tiles(("a", "t"), 1)
    vm.invalidate()
    assert vm.get_tiles(("a", "t")) is None
