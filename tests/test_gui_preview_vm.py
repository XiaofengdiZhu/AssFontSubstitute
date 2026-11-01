
import pytest

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.models import CmapRanges
from ass_font_substitute.gui.preview_vm import CATEGORY_ORDER
from ass_font_substitute.gui.preview_vm import TILE_CACHE_MAX
from ass_font_substitute.gui.preview_vm import PreviewVM
from ass_font_substitute.gui.preview_vm import weight_tag


def _ent(fp, name, category="黑", script="简", weight=400, italic=False):
    # eff_weight 与 weight 同值：直构实体无名表语言记录（无补偿语境）；
    # 库装载路径的补偿口径由 test_library.test_eff_weight_compensated_at_load 锁
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=weight, width=5,
                      italic=italic, script=script, category=category,
                      cmap=CmapRanges.from_chars([0x6c38]), dir_path="d",
                      rel_path="r.ttf", face_index=0, eff_weight=weight)


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


def test_same_display_name_groups_normally():
    # display 全等即归组（v5 起 display 不可能再是纯样式词：parser 按
    # ID1/16 官方语义选名入库，真库抽样 400 face ID1/16 纯样式词 0 个，
    # 历史「十几个 Bold」全来自已剔除的 ID17）——异字重 5 个 "Bold"
    # display 合并一行 5 face，chip 各自可切
    vm = PreviewVM()
    vm.set_entities([_ent(f"b{i}", "Bold", weight=w)
                     for i, w in enumerate((400, 500, 700, 800, 900))]
                    + [_ent("xb1", "XXX Bold", weight=400),
                       _ent("xb2", "XXX Bold", weight=700)])
    rows = vm.filtered()
    bold_rows = [r for r in rows if r.display_name == "Bold"]
    assert len(bold_rows) == 1 and len(bold_rows[0].faces) == 5
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


def test_selected_prefers_upright_over_closer_weight():
    # 拍板 2026-10-30（无筛选档非斜体优先，字重其次——预览统一
    # Regular 口径，正体比近字重斜面更贴 Regular 观感）：
    # 斜体 400 输 700 直立
    vm = PreviewVM()
    vm.set_entities([_ent("1", "A", weight=400, italic=True),
                     _ent("2", "A", weight=700)])
    assert vm.filtered()[0].selected.fingerprint == "2"


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


def test_row_label_uses_lang_chain_but_grouping_stays_legacy():
    # 行展示名按界面语言链现挑；行归组键仍用入库 display_name——按链挑
    # 名会因各 face 语言记录集不同裂行（同族 face 记录语言数可能不同）
    from dataclasses import replace
    from types import SimpleNamespace

    a = replace(_ent("1", "YaHei", weight=400),
                name_langs={1: [(0x409, "YaHei"), (0x804, "雅黑"),
                                (0x404, "雅黑繁")]})
    b = replace(_ent("2", "YaHei", weight=700),
                name_langs={1: [(0x409, "YaHei")]})   # 无中文记录
    vm = PreviewVM(SimpleNamespace(get=lambda _k: "zh-Hans"))
    vm.set_entities([a, b])
    rows = vm.filtered()
    assert len(rows) == 1                    # 归组未裂（同 display_name）
    assert rows[0].display_name == "雅黑"     # 行名 = 链挑 main[0]
    vm2 = PreviewVM(SimpleNamespace(get=lambda _k: "zh-Hant"))
    vm2.set_entities([a, b])
    assert vm2.filtered()[0].display_name == "雅黑繁"   # 繁中链命中 0x404
    # 单 face 行（as_rows）同走链挑选
    rec = vm.as_rows([b])[0]
    assert rec.display_name == "YaHei"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_script_filter_no_script_option_strikethrough_and_pick(qapp):
    # 未标注（探针简繁双不中）不在行 meta 上屏，但筛选要能圈出这批：
    # 「简/繁」选项（GUI 删除线渲染 = 两个脚本都划掉）映射 script=未标注
    from PySide6.QtGui import QAction

    from ass_font_substitute.gui import script_filter

    vm = PreviewVM()
    vm.set_entities([_ent("1", "A"), _ent("2", "B", script="未标注")])
    assert script_filter.options(vm) == ["简", "简/繁"]

    # 弹层末项删除线：菜单构建期挂钩生效（镜像 _showComboMenu 的建菜单
    # 循环——exec 模态内联，整段 _showComboMenu 测不了）
    cb = script_filter.StrikeTailComboBox()
    cb.addItems(["全部", *script_filter.options(vm)])
    menu = cb._createComboMenu()
    for it in cb.items:
        menu.addAction(QAction(it.icon, it.text))
    assert menu.view.item(cb.count() - 1).font().strikeOut()
    assert not menu.view.item(0).font().strikeOut()

    cb.setCurrentIndex(2)
    assert script_filter.pick(cb) == "未标注"
    vm.script = script_filter.pick(cb)
    assert [r.display_name for r in vm.filtered()] == ["B"]
    # 按钮上的选定文本同步删除线（弹层外 QPushButton.setText 不吃 FontRole）
    assert cb.font().strikeOut()
    cb.setCurrentIndex(0)
    assert not cb.font().strikeOut()           # 切走即复原
    assert script_filter.pick(cb) is None      # 全部 = 不筛

    vm2 = PreviewVM()
    vm2.set_entities([_ent("1", "A")])
    assert script_filter.options(vm2) == ["简"]   # 无未标注不出选项


def test_require_text_filters_by_cmap_coverage():
    # 覆盖筛选：face cmap 须含预览文字全部字符，缺任一即排除；
    # 空文字无字符可筛=不筛（开关与空输入叠加的兜底）
    def ent(fp, name, cps):
        return FontEntity(fingerprint=fp, display_name=name,
                          aliases=(name,), psname=fp, version=(1,),
                          weight=400, width=5, italic=False, script="简",
                          category="黑", cmap=CmapRanges.from_chars(cps),
                          dir_path="d", rel_path="r.ttf", face_index=0)

    vm = PreviewVM()
    vm.set_entities([ent("1", "仅永", [0x6c38]),       # 永
                     ent("2", "仅水", [0x6c34]),       # 水
                     ent("3", "永水", [0x6c38, 0x6c34])])
    vm.require_text = "永"
    assert [r.display_name for r in vm.filtered()] == ["仅永", "永水"]
    vm.require_text = "永水"
    assert [r.display_name for r in vm.filtered()] == ["永水"]
    vm.require_text = ""                      # 关开关后残留空串同理不筛
    assert len(vm.filtered()) == 3
    vm.require_text = "   "                   # 纯空白同空：页侧门控失效时 VM 兜底
    assert len(vm.filtered()) == 3
    vm.require_text = None
    assert len(vm.filtered()) == 3


def test_require_text_row_keeps_faces_selects_matching():
    # 行全量保留（chips 仍可切看其他字重），selected/匹配子集从通过
    # 筛选的 face 里挑
    e1 = FontEntity(fingerprint="1", display_name="YaHei", aliases=("YaHei",),
                    psname="1", version=(1,), weight=400, width=5,
                    italic=False, script="简", category="黑",
                    cmap=CmapRanges.from_chars([0x6c38]),
                    dir_path="d", rel_path="r.ttf", face_index=0)
    e2 = FontEntity(fingerprint="2", display_name="YaHei", aliases=("YaHei",),
                    psname="2", version=(1,), weight=700, width=5,
                    italic=False, script="简", category="黑",
                    cmap=CmapRanges.from_chars([0x6c38, 0x6c34]),
                    dir_path="d", rel_path="r2.ttf", face_index=0)
    vm = PreviewVM()
    vm.set_entities([e1, e2])
    vm.require_text = "永水"
    rows = vm.filtered()
    assert len(rows) == 1
    row = rows[0]
    assert [f.fingerprint for f in row.faces] == ["1", "2"]
    assert [f.fingerprint for f in row.matching] == ["2"]
    assert row.selected.fingerprint == "2"


def test_require_text_composes_with_name_query():
    # 覆盖筛选与名称搜索是 AND：两条独立 ok() 项同时生效
    vm = PreviewVM()
    vm.set_entities([_ent("1", "YongA"), _ent("2", "YongB"), _ent("3", "WaterB")])
    vm.name_query = "yong"
    vm.require_text = "永"
    assert [r.display_name for r in vm.filtered()] == ["YongA", "YongB"]
