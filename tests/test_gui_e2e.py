"""GUI 层端到端（vm + jobs 串全流程，零 Qt）：字体库 → 解析 → 分类 →
搜索 → 映射 → 写回 → 决定记忆。"""
import pytest

from ass_font_substitute.core.fontindex.library import LibraryIndex
from ass_font_substitute.core.fontindex.store import open_db, update_index
from ass_font_substitute.core.similarity.cache import Cache
from ass_font_substitute.gui.jobs import (classify_one, make_searcher,
                                          parse_subtitle, pull_chunk)
from ass_font_substitute.gui.settings_store import Settings
from ass_font_substitute.gui.subs_vm import SubsVM
from tests.fonts import build_font

# 真实样式 Format（简写两列会让解析器把剩余字段并入 Fontname，
# 如「测试黑体,54」——手测清单里的 unknown/invalid quirk 与此无关）
_STYLE_FORMAT = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                 "OutlineColour, BackColour, Bold, Italic, Underline, "
                 "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
                 "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
                 "Encoding")
_EVENT_FORMAT = ("Layer, Start, End, Style, Name, MarginL, MarginR, "
                 "MarginV, Effect, Text")
_SUB = (
    "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
    f"Format: {_STYLE_FORMAT}\n"
    "Style: Def,测试黑体,54,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
    "[Events]\n"
    f"Format: {_EVENT_FORMAT}\n"
    "Dialogue: 0,0:00:00.00,0:00:00.10,Def,,0,0,0,,永中木的水好\n")


@pytest.fixture()
def world(tmp_path):
    d = tmp_path / "fonts"
    d.mkdir()
    # 不设 psname：display_name = aliases[0]（排序后家族名在前），
    # 断言用中文名可直接命中
    build_font(d / "hei.ttf", chars="永中木的水好", family="测试黑体",
               shape="circle")
    # 替身候选须覆盖字幕用字（真实场景：不覆盖就过不了覆盖率门槛，
    # 搜索器直接跳过——见 FontSearcher._evaluate）
    build_font(d / "song.ttf", chars="永中木的水好", family="测试宋体",
               shape="box")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.close()
    sub = tmp_path / "demo.ass"
    sub.write_text(_SUB, "utf-8")
    s = Settings(tmp_path / "s.json")
    s.set("font_dirs", [str(d)])
    from types import SimpleNamespace
    # ctx 与生产 CoreWorker 同构（SimpleNamespace）；lib 必须挂上——
    # make_searcher 经 ctx.lib 建搜索器
    ctx = SimpleNamespace(settings=s, cache=Cache(tmp_path / "cache.db"),
                          snapshot=None, lib=LibraryIndex.open([d]),
                          lib_key=(str(d), None))
    vm = SubsVM(s, tmp_path / "decisions.db")
    lib = ctx.lib
    vm.set_entities(lib.entities())
    return ctx, vm, lib, sub


def test_full_flow(world):
    ctx, vm, lib, sub = world
    fp = parse_subtitle(str(sub))
    assert fp.error is None
    vm.add_files([fp])
    usages = vm.merged_usages()
    assert len(usages) == 1
    cls = classify_one(lib, usages[0], 0.95, "t2s")
    assert cls.kind == "keep"                       # 测试黑体全覆盖 → 保留
    card = vm.make_card(usages[0], cls)
    vm.apply_default_check(card)
    vm.cards = [card]
    assert not card.replaced                        # 默认勾原字体 → 不改写
    # 改选测试宋体 → 相似度搜索可跑通（有 target、有交集）
    target = vm._search_target(card)
    assert target is not None and target.display_name == "测试黑体"
    s = make_searcher(ctx, target=target, chars=set("永中木的水好"),
                      threshold=0.95, n_pause=5, min_sim=0.10,
                      size_range=(0.5, 2.0), direction="t2s")
    cands, state = pull_chunk(s)
    vm.add_candidates(card, cands)
    vm.finish_search(card, s)
    assert state in ("paused", "exhausted")
    assert any(o.display_name == "测试宋体" for o in card.options)
    card.checked_key = next(o.key for o in card.options
                            if o.display_name == "测试宋体")
    assert vm.replacement_counts() == (1, 1)
    rs = vm.apply_files()
    assert rs[0].error is None
    out = sub.with_name("demo.substituted.ass")
    assert "测试宋体" in out.read_text("utf-8")
    assert "测试黑体" not in out.read_text("utf-8")
    vm.record_all()
    from ass_font_substitute.core.decision.store import latest_decision
    assert latest_decision(vm.decisions_db, "测试黑体", "t2s")[0] == \
        card.checked_key
