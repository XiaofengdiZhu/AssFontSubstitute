
from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.gui.preview_vm import CATEGORY_ORDER
from ass_font_substitute.gui.preview_vm import TILE_CACHE_MAX
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.preview_vm import weight_tag


def _ent(fp, name, category="黑", script="简", weight=400, italic=False):
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=weight, width=5,
                      italic=italic, script=script, category=category,
                      cmap=CmapRanges.from_chars([0x6c38]), dir_path="d",
                      rel_path="r.ttf", face_index=0)


def test_weight_tag_shows_exact_value():
    # 真实值不取整：Semilight 350 取整曾谎报 W300，与 Light 300 同桶
    # 撞车，合并行里只剩一个可切（真库微软雅黑实测）
    assert weight_tag(350) == "W350"
    assert weight_tag(437) == "W437"
    assert weight_tag(700) == "W700"


def test_filter_by_name_casefold_and_alias():
    vm = PreviewVM()
    vm.set_entities([_ent("1", "测试黑体"), _ent("2", "TestHei")])
    vm.name_query = "testhei"
    assert [r.display_name for r in vm.filtered()] == ["TestHei"]


def test_filter_combinations_and_buckets():
    vm = PreviewVM()
    vm.set_entities([_ent("1", "A", "黑", "简", 400), _ent("2", "B", "宋", "繁", 700),
                     _ent("3", "C", "黑", "简繁", 437)])
    assert vm.weight_buckets() == [400, 700]
    assert vm.categories() == [c for c in CATEGORY_ORDER if c in ("黑", "宋")]
    vm.category, vm.script, vm.weight = "黑", "简", 400
    assert [r.display_name for r in vm.filtered()] == ["A"]


def test_filtered_merges_same_display_name():
    # 严格同展示名归一行：行内 face 按字重序，selected = 最近 W400 直立
    vm = PreviewVM()
    vm.set_entities([_ent("1", "YaHei", weight=700),
                     _ent("2", "YaHei", weight=400),
                     _ent("3", "YaHei", weight=400, italic=True),
                     _ent("4", "YaHei UI")])
    rows = vm.filtered()
    assert [(r.display_name, len(r.faces)) for r in rows] == [
        ("YaHei", 3), ("YaHei UI", 1)]
    yahei = rows[0]
    assert [f.weight for f in yahei.faces] == [400, 400, 700]
    assert yahei.selected.fingerprint == "2"    # 400 平局直立优先


def test_no_merge_across_different_names():
    # 用户拍板：严格按名称合并——"XXX" vs "XXX Bold"、华康宋体W5 vs W7
    # 名称不同即不同行（标准命名 Bold face 的别名含家族名才会归并）
    vm = PreviewVM()
    vm.set_entities([_ent("1", "XXX"), _ent("2", "XXX Bold"),
                     _ent("3", "华康宋体W5"), _ent("4", "华康宋体W7")])
    assert [r.display_name for r in vm.filtered()] == [
        "XXX", "XXX Bold", "华康宋体W5", "华康宋体W7"]


def test_bare_style_names_never_merge():
    # 烂字体 name 全填样式词（五个无关家族都叫 "Bold"）：裸样式名零家族
    # 信息，永不合并；"XXX Bold" 带前缀含家族信息，照常合并
    vm = PreviewVM()
    vm.set_entities([_ent(f"b{i}", "Bold", weight=700) for i in range(5)]
                    + [_ent("xb1", "XXX Bold", weight=400),
                       _ent("xb2", "XXX Bold", weight=700)])
    rows = vm.filtered()
    bold_rows = [r for r in rows if r.display_name == "Bold"]
    assert len(bold_rows) == 5 and all(len(r.faces) == 1 for r in bold_rows)
    xb = [r for r in rows if r.display_name == "XXX Bold"]
    assert len(xb) == 1 and len(xb[0].faces) == 2
    assert xb[0].selected.weight == 400


def test_semilight_300_350_share_row_distinct_chips():
    # 微软雅黑实库回归：Light 300 与 Semilight 350 同展示名不同精确字重，
    # 同行不撞（chip 各自 W300/W350）；同 (精确字重, 斜态) 撞车才拆行
    vm = PreviewVM()
    vm.set_entities([_ent("light", "微软雅黑", weight=300),
                     _ent("semilight", "微软雅黑", weight=350),
                     _ent("reg", "微软雅黑", weight=400),
                     _ent("bold", "微软雅黑", weight=700)])
    rows = vm.filtered()
    assert len(rows) == 1
    assert [f.weight for f in rows[0].faces] == [300, 350, 400, 700]


def test_same_exact_weight_and_italic_splits_rows():
    # 同展示名 + 同精确字重 + 同斜态的 face 不是同族字重关系（真库方正
    # 同名字体双格式实测），拆独立行——藏进行里 chip 只剩一个可切
    vm = PreviewVM()
    vm.set_entities([_ent("a", "微软雅黑", weight=300),
                     _ent("b", "微软雅黑", weight=300),
                     _ent("c", "微软雅黑", weight=350),
                     _ent("d", "微软雅黑", weight=400, italic=True),
                     _ent("e", "微软雅黑", weight=400, italic=True)])
    rows = vm.filtered()
    # 300 直立 ×2、400 斜体 ×2 各自撞车拆行；350 直立唯一 → 单 face 主行
    assert len(rows) == 5 and all(r.display_name == "微软雅黑" for r in rows)
    assert all(len(r.faces) == 1 for r in rows)
    singles = sorted(r.faces[0].fingerprint for r in rows)
    assert singles == ["a", "b", "c", "d", "e"]


def test_filter_pins_exact_weight_and_keeps_all_chips():
    # 筛 W300：selected 精确 300（350 距筛选锚点远，不得反超——真库微软
    # 雅黑回归）；faces 全量保留，筛选不隐藏行内其他字重
    vm = PreviewVM()
    vm.set_entities([_ent("light", "微软雅黑", weight=300),
                     _ent("semilight", "微软雅黑", weight=350),
                     _ent("reg", "微软雅黑", weight=400),
                     _ent("bold", "微软雅黑", weight=700)])
    vm.weight = 300
    (row,) = vm.filtered()
    assert [f.weight for f in row.faces] == [300, 350, 400, 700]
    assert [f.weight for f in row.matching] == [300, 350]
    assert row.selected.fingerprint == "light"
    vm.weight = None
    assert vm.filtered()[0].selected.fingerprint == "reg"


def test_filter_drives_selected_face():
    # 筛选直接决定预览 face：行内 selected = 通过筛选的 face 里默认最优
    vm = PreviewVM()
    vm.set_entities([_ent("1", "A", weight=400), _ent("2", "A", weight=700)])
    vm.weight = 700
    rows = vm.filtered()
    assert len(rows) == 1 and rows[0].selected.fingerprint == "2"
    vm.weight = None
    assert vm.filtered()[0].selected.fingerprint == "1"


def test_selected_prefers_nearest_400_even_italic():
    # 无 400 直立 face 时斜体 400 胜 700 直立（字重距离优先于斜态）
    vm = PreviewVM()
    vm.set_entities([_ent("1", "A", weight=400, italic=True),
                     _ent("2", "A", weight=700)])
    assert vm.filtered()[0].selected.fingerprint == "1"


def test_as_rows_keeps_faces_separate():
    vm = PreviewVM()
    rows = vm.as_rows([_ent("1", "A"), _ent("2", "A")])
    assert len(rows) == 2 and all(len(r.faces) == 1 for r in rows)


def test_tile_lru_evicts_oldest():
    vm = PreviewVM()
    for i in range(TILE_CACHE_MAX):
        vm.put_tiles((str(i), "t"), i)
    # 不可先 get_tiles(("0", "t"))：get 会刷新 LRU 新旧度（见
    # test_get_tiles_touches_recency），"0" 被置顶后淘汰的将是 "1" 而非 "0"
    vm.put_tiles(("new", "t"), "x")                    # 触发淘汰最旧
    assert vm.get_tiles(("0", "t")) is None
    assert vm.get_tiles(("new", "t")) == "x"


def test_get_tiles_touches_recency():
    vm = PreviewVM()
    for i in range(TILE_CACHE_MAX):
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
