"""处理字幕页功能冒烟（offscreen）：假 worker 不起线程，parse/classify/
searcher/pull/apply 载荷经 _on_result 直喂，走真实 vm + UI 装配路径。"""
import gc
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtCore import QPoint
from PySide6.QtCore import Signal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _gc_dead_pages():
    # 死页安全点回收：qfw 样式表迭代非 GC 安全，残留死页会让后续
    # 测试文件（如 test_gui_smoke）随机炸 dictionary changed size
    yield
    gc.collect()


def _ent(fp, name, chars="永宇"):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=400, width=5,
                      italic=False, script="简", category="黑",
                      cmap=CmapRanges.from_chars([ord(c) for c in chars]),
                      dir_path="d", rel_path="r.ttf", face_index=0)


class _FakeWorker(QObject):
    """submit 只记账不执行；result/failed 由测试手动发射。"""

    result = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        handle = SimpleNamespace(cancelled=False)   # 预览条撤单句柄
        self.submits.append((tag, fn, args, kw))
        return handle


def _make_page(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))  # 决策库/设置隔离到 tmp
    from ass_font_substitute.gui.pages.subs_page import SubsPage
    from ass_font_substitute.gui.settings_store import Settings
    s = Settings(tmp_path / "settings.json")        # cov .95 / sim .85 / size .9–1.1
    page = SubsPage(s, _FakeWorker())
    return page


def _feed_replace_card(page, ent_a, ent_b, chars="永宇"):
    """parse + classify 各喂一发，返回 (usage, card, norm)。"""
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=2,
                      inline_count=5, chars=set(chars), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    cls = UsageClass(kind="replace", entities=[ent_a, ent_b], coverage=0.6,
                     missing_chars=["阿", "八"])
    page._on_result(f"classify:{page._gen}:0", (usage, cls))
    return usage, page.vm.cards[0], "甲字体"


def test_parse_classify_cards_and_check_roundtrip(qapp, tmp_path, monkeypatch):
    page = _make_page(qapp, tmp_path, monkeypatch)
    assert not page.btn_apply.isEnabled()           # 空态禁用

    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    assert len(page.vm.files) == 1 and page.files_box.count() == 1
    assert page._cards_ui[norm] is not None and card.kind == "replace"
    assert card.checked_key == "fp1"                # 默认勾选链落在原字体行
    assert "需替换 1" in page.summary.text()
    assert "将替换 0 / 1" in page.replace_label.text()
    assert page.btn_apply.isEnabled()

    fc = page._cards_ui[norm]
    fc.option_checked.emit(norm, "fp2")             # 勾推荐行 → UI 回写 + 计数
    assert card.checked_key == "fp2"
    assert fc._boxes["fp2"].isChecked() and not fc._boxes["fp2"].isEnabled()
    assert fc._boxes["fp1"].isEnabled() and not fc._boxes["fp1"].isChecked()
    assert "将替换 1 / 1" in page.replace_label.text()
    fc.option_checked.emit(norm, "fp1")             # 切回原字体 → 不计替换
    assert card.checked_key == "fp1" and "将替换 0 / 1" in page.replace_label.text()

    page._on_file_checked("p.ass", False)           # 取消勾选 → 卡清空 + 禁用
    assert not page.vm.cards and not page._cards_ui
    assert not page.btn_apply.isEnabled()
    stale_gen = page._gen
    page._on_file_checked("p.ass", True)
    assert page._gen == stale_gen + 1
    page._on_result(f"classify:{stale_gen}:0",
                    (_usage, SimpleNamespace(kind="keep")))   # 旧代结果弃置
    assert not page.vm.cards
    page._on_result(f"classify:{page._gen}:0",
                    (_usage, SimpleNamespace(kind="zero")))   # 零用字卡
    assert page.vm.cards[0].kind == "zero"
    assert page.vm.cards[0].checked_key == "__keep__"
    assert "需替换 0" in page.summary.text()        # 汇总只计保留/孪生/需替换/缺失


def test_search_chain_pause_resume_exhaust_and_relax(qapp, tmp_path, monkeypatch):
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=2, chars={"永"}, variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="replace", entities=[ent_a, ent_b])))
    card, norm = page.vm.cards[0], "甲字体"

    page._continue_search(norm)                     # 建新搜索器（参数来自页面）
    assert card.searching
    sub = [s for s in page.worker.submits if s[0] == f"newsearcher:{norm}"][-1]
    target, chars, cov, n_pause, min_sim, size_range, direction = sub[2]
    assert target is ent_a and chars == {"永"}      # 原名命中作相似度基准
    assert (cov, n_pause, min_sim) == (0.95, 5, 0.85)
    assert size_range == (0.9, 1.1) and direction == "t2s"

    searcher = SimpleNamespace(min_sim=0.85, paused=True, target_error=False,
                               pool_size=10, compared=0, resume=lambda: None)
    page._on_result(f"newsearcher:{norm}", searcher)   # 挂接后立即拉第一批
    assert card.searcher is searcher and card.min_sim == 0.85
    assert page.worker.submits[-1][0] == f"pull:{norm}"

    page._on_result(f"pull:{norm}", ([], "paused"))  # 暂停：可续搜
    assert card.search_state == "paused" and not card.searching
    page._continue_search(norm)                      # 续用同一搜索器
    assert page.worker.submits[-1][0] == f"pull:{norm}"
    assert page.worker.submits[-1][2][0] is searcher

    searcher.paused = False                          # 搜索器走完剩余池 → 耗尽
    cands = [SimpleNamespace(fingerprint=f"c{i}", display_name=f"候选{i}",
                             coverage=0.99, similarity=0.9, size_ratio=1.0,
                             n_chars=3) for i in range(2)]
    n_sub = len(page.worker.submits)
    page._on_result(f"pull:{norm}", (cands, "exhausted"))   # 渐进回填后耗尽
    assert card.search_state == "exhausted" and len(card.options) == 4
    assert len(page.worker.submits) == n_sub                 # 不再续拉

    # 耗尽后再搜：新搜索器按设置现值建门槛（relax 钮已删）
    page.settings.set("min_sim", 0.75)
    page._continue_search(norm)
    sub = [s for s in page.worker.submits if s[0] == f"newsearcher:{norm}"][-1]
    assert sub[2][4] == 0.75                         # 新门槛建新搜索器
    page._on_result(f"newsearcher:{norm}",
                    SimpleNamespace(min_sim=0.75, paused=False,
                                    target_error=False, pool_size=10,
                                    compared=5, resume=lambda: None))
    page._on_result(f"pull:{norm}", ([], "cancelled"))   # 重算弃旧拉取不挂死
    assert not card.searching


def test_search_activity_signal_tracks_card_searching(qapp, tmp_path,
                                                      monkeypatch):
    """searchActiveChanged（设置页「搜索中改门槛待生效」提示依赖）：
    起搜发 True、终态回执发 False。曾因 _apply_card 漏调
    _update_summary 整信号死亡（True 永不发射）。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    events = []
    page.searchActiveChanged.connect(events.append)
    page._continue_search(norm)
    assert events == [True]
    page._on_result(f"newsearcher:{norm}",
                    SimpleNamespace(min_sim=0.85, paused=False,
                                    target_error=False, pool_size=10,
                                    compared=0, resume=lambda: None))
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    assert events == [True, False]
    assert not page._search_active


def test_new_searcher_reads_current_min_sim_setting(qapp, tmp_path, monkeypatch):
    """改相似率门槛后点开始寻找=拍板的「下次寻找」：新搜索器按设置页
    现值建（覆盖率/大小同口径，现读）。曾读 card.min_sim——建卡时
    快照，relax 时代遗留，设置改动永远进不了首轮搜索。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    page.settings.set("min_sim", 0.6)
    page._continue_search(norm)
    sub = [s for s in page.worker.submits if s[0] == f"newsearcher:{norm}"][-1]
    assert sub[2][4] == 0.6


def test_keep_card_collapses_to_original_row(qapp, tmp_path, monkeypatch):
    """keep 卡只留「原」行（用户报告 2026-10-30 截图：更纱黑体 TC 一卡
    列出同族 W200–W700 正斜体十行）：无事发生态，同族其余面写回语义
    相同（均为此名），整墙行是噪音；需替换卡仍全列供挑面。"""
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    page = _make_page(qapp, tmp_path, monkeypatch)
    ents = [_ent("fp0", "甲字体"), _ent("fp1", "甲字体", chars="永宇阿"),
            _ent("fp2", "甲字体")]
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=2,
                      inline_count=5, chars={"永", "宇"}, variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="keep", entities=ents)))
    card = page.vm.cards[0]
    assert card.kind == "keep"
    assert len(card.options) == 1
    o = card.options[0]
    assert o.badges == ["原字体"] and o.source == "original"
    assert card.checked_key == o.key          # 默认勾选链落在原行


def test_keep_cards_before_zero_and_unified_text(qapp, tmp_path, monkeypatch):
    """保留两档排序+统一文案（用户报告 2026-10-30）：有字 keep（保留
    （覆盖足够））恒排 0 字 keep（保留（无对白用字））前，与到达序
    无关——两者 vm 同为 kind=keep，按字数分档插位。"""
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    page = _make_page(qapp, tmp_path, monkeypatch)

    def mk_usage(raw, chars):
        return FontUsage(raw_name=raw, norm_name=raw, style_count=1,
                         inline_count=0, chars=set(chars), variants={raw})

    # 先喂全 parse（每份到齐即批内重算清卡，假 worker 不回 classify），
    # 再按「到达序」喂 classify 回执测插位
    feeds = [("乙字体", [], "p1.ass"),        # 零字 keep 先到
             ("甲字体", ["永"], "p2.ass"),     # 有字 keep 后到：须插到其前
             ("丙字体", [], "p3.ass")]         # 零字再到：排队尾
    for raw, chars, path in feeds:
        u = mk_usage(raw, chars)
        page._on_result(f"parse:{path}", FileParse(path, None, [u], [], []))
    for raw, chars, _path in feeds:
        u = mk_usage(raw, chars)
        page._on_result(f"classify:{page._gen}:0", (u, UsageClass(kind="keep")))
    assert [c.norm_name for c in page.vm.cards] == ["甲字体", "乙字体", "丙字体"]

    # 统一文案：卡头徽章「保留（覆盖足够）」/ 药丸「保留（无对白用字）」
    # 两档同灰自绘药丸（2026-10-30 拍板：绿徽章太抢眼）——卡内无 InfoBadge
    from PySide6.QtWidgets import QLabel
    from qfluentwidgets import InfoBadge

    def texts(norm):
        return [lab.text() for lab in page._cards_ui[norm].findChildren(QLabel)]

    assert "保留（覆盖足够）" in texts("甲字体")
    assert "保留（无对白用字）" in texts("乙字体")
    assert not page._cards_ui["甲字体"].findChildren(InfoBadge)
    assert not page._cards_ui["乙字体"].findChildren(InfoBadge)


def test_threshold_change_resets_ended_searches(qapp, tmp_path, monkeypatch):
    """相似候选前三设置（覆盖率/相似率门槛+相对大小区间）调整后重置
    已停的寻找（拍板 2026-10-30）：暂停/耗尽卡归 idle、旧搜索器弃、
    钮回「开始寻找相似字体」，已找到候选保留在列；在跑的不暂停但置
    dirty——停后归初始态（拍板 2026-10-30 二次确认），n_pause 不在
    重置之列。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    btn = _cont_btn(page._cards_ui[norm])
    old_opts = list(card.options)

    # 耗尽卡：调相似率门槛 → 归「开始寻找」，候选保留
    card.search_state = "exhausted"
    card.searcher = SimpleNamespace(min_sim=0.85)
    page.settings.set("min_sim", 0.7)
    assert card.search_state == "idle" and card.searcher is None
    assert btn.text() == "开始寻找相似字体" and btn.isEnabled()
    assert card.options == old_opts

    # 暂停数量不在重置之列；覆盖率门槛重置暂停卡
    card.search_state = "paused"
    card.searcher = SimpleNamespace(min_sim=0.85)
    page.settings.set("n_pause", 3)
    assert card.search_state == "paused" and card.searcher is not None
    page.settings.set("cov_threshold", 0.90)
    assert card.search_state == "idle" and card.searcher is None

    # 相对大小区间两键各触发一发
    card.search_state = "paused"
    card.searcher = SimpleNamespace(min_sim=0.85)
    page.settings.set("size_min", 0.8)
    assert card.search_state == "idle"
    card.search_state = "target_error"
    page.settings.set("size_max", 1.2)
    assert card.search_state == "idle"

    # 在跑的不暂停（快照跑完为止），但置 dirty：停后归初始态
    card.searching = True
    card.search_state = "idle"
    card.searcher = SimpleNamespace(min_sim=0.85, paused=False,
                                    target_error=False)
    page.settings.set("min_sim", 0.6)
    assert card.searching and card.searcher is not None
    assert card.threshold_dirty

    # 手动暂停回执：dirty 卡直接归初始态，不进「继续寻找」
    page._on_result(f"pull:{norm}", ([], "cancelled"))
    assert card.search_state == "idle" and card.searcher is None
    assert not card.threshold_dirty and not card.searching
    assert btn.text() == "开始寻找相似字体"

    # 非 dirty 卡终态照旧：手动暂停归 paused 可续搜
    page.settings.set("min_sim", 0.5)
    card.searching = True
    card.threshold_dirty = False
    card.searcher = SimpleNamespace(min_sim=0.6, paused=False,
                                    target_error=False)
    page._on_result(f"pull:{norm}", ([], "cancelled"))
    assert card.search_state == "paused" and card.searcher is not None

    # dirty 卡自然耗尽同样归初始态（旧口径「无更多」失义）
    card.searching = True
    card.search_state = "paused"
    card.searcher = SimpleNamespace(min_sim=0.5, paused=False,
                                    target_error=False)
    page.settings.set("min_sim", 0.4)      # 在跑中改门槛 → dirty
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    assert card.search_state == "idle" and card.searcher is None

    # dirty 卡原字体渲染失败保留 target_error（与门槛无关）
    card.searching = True
    card.searcher = SimpleNamespace(min_sim=0.4, paused=False,
                                    target_error=True)
    page.settings.set("min_sim", 0.3)      # 在跑中改门槛 → dirty
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    assert card.search_state == "target_error"


def test_apply_confirm_mapping_and_record(qapp, tmp_path, monkeypatch):
    from ass_font_substitute.core.decision.store import latest_decision
    from ass_font_substitute.gui.jobs import ApplyFileResult
    from ass_font_substitute.gui.jobs import FileParse
    page = _make_page(qapp, tmp_path, monkeypatch)
    src = tmp_path / "a.ass"
    src.write_text("[Script Info]", "utf-8")
    out = tmp_path / "a.substituted.ass"
    out.write_text("old", "utf-8")                  # 另存目标已存在 → 冲突

    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars={"永"}, variants={"甲字体"})
    page._on_result(f"parse:{src}", FileParse(str(src), None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="replace", entities=[ent_a, ent_b])))
    page._cards_ui["甲字体"].option_checked.emit("甲字体", "fp2")

    page._apply()                                   # 首击：只警告不执行
    assert not [s for s in page.worker.submits if s[0].startswith("apply")]
    page._apply()                                   # 再击确认 → 派工
    assert page.worker.submits[-1][0] == f"apply:{page._apply_gen}"
    path, mapping, suffix, overwrite = page.worker.submits[-1][2]
    assert path == str(src) and mapping == {"甲字体": "乙字体"}
    assert suffix == ".substituted.ass" and overwrite is False

    page._on_result(f"apply:{page._apply_gen}",
                    ApplyFileResult(str(src), str(out), 1))
    d = latest_decision(Path(page.vm.decisions_db), "甲字体", "t2s")
    assert d == ("fp2", False)                      # record_all 已落库

    # 解析失败文件：勾了也不计入替换、应用保持可用性由剩余文件决定
    page._on_result(f"parse:{tmp_path / 'bad.ass'}",
                    FileParse(str(tmp_path / "bad.ass"), "ParseError: x", [], [], []))
    assert page.vm.checked_parses() == [page.vm.files[0]]


def test_apply_reentry_guard_disables_during_dispatch(qapp, tmp_path, monkeypatch):
    # 回归：_apply 派工在途时入口守卫忽略重复点击，且派工前禁用按钮；
    # 收尾（_on_apply_done）经 _update_summary 恢复
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import ApplyFileResult
    from ass_font_substitute.gui.jobs import FileParse
    page = _make_page(qapp, tmp_path, monkeypatch)
    src = tmp_path / "a.ass"
    src.write_text("[Script Info]", "utf-8")        # 另存目标不存在 → 无冲突确认
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars={"永"}, variants={"甲字体"})
    page._on_result(f"parse:{src}", FileParse(str(src), None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="replace",
                                       entities=[_ent("fp1", "甲字体"),
                                                 _ent("fp2", "乙字体")])))
    assert page.btn_apply.isEnabled()

    page._apply()                                   # 首击即派工（无冲突路径）
    assert len([s for s in page.worker.submits if s[0].startswith("apply")]) == 1
    assert page._apply_dispatching is True
    assert not page.btn_apply.isEnabled()           # 派工期禁用：防连点重复写
    page._apply()                                   # 在途重复点击：忽略
    assert len([s for s in page.worker.submits if s[0].startswith("apply")]) == 1

    page._on_result(f"apply:{page._apply_gen}",
                    ApplyFileResult(str(src),
                                    str(src) + ".substituted.ass", 1))
    assert page._apply_dispatching is False
    assert page.btn_apply.isEnabled()               # 收尾经 _update_summary 恢复


def test_preview_text_and_save_mode_combo(qapp, tmp_path, monkeypatch):
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, _norm = _feed_replace_card(page, ent_a, ent_b)
    old = page._cards_ui["甲字体"]
    page.preview_edit.setText("永宇")               # 防抖后就地刷新
    page._preview_debounce.timeout.emit()
    assert page._cards_ui["甲字体"] is old          # 零重建：卡控件原地换预览
    fc = page._cards_ui["甲字体"]
    assert fc.card is page.vm.cards[0]
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
    assert all(b._text == "永宇" for b in fc.findChildren(PreviewBar))

    assert page.save_mode.currentIndex() == 0       # 默认另存为新文件
    assert not page.suffix_edit.isHidden()          # 另存模式显示后缀行
    page.save_mode.setCurrentIndex(1)               # 切覆盖 → 落设置 + 收后缀行
    assert page.settings.get("overwrite_default") is True
    assert page.suffix_edit.isHidden()
    page.save_mode.setCurrentIndex(0)               # 切回另存 → 后缀行复出
    assert page.settings.get("overwrite_default") is False
    assert not page.suffix_edit.isHidden()
    page.set_save_mode(True)                        # 设置页同步：只搬 UI 不回写
    assert page.save_mode.currentIndex() == 1
    assert page.settings.get("overwrite_default") is False

    page.suffix_edit.setText(".fixed.ass")          # 编辑完成 → 落设置
    page.suffix_edit.editingFinished.emit()
    assert page.settings.get("save_suffix") == ".fixed.ass"
    page.suffix_edit.setText("bad")                 # 非法后缀 → 报错回退
    page.suffix_edit.editingFinished.emit()
    assert page.settings.get("save_suffix") == ".fixed.ass"
    assert page.suffix_edit.text() == ".fixed.ass"


def test_reload_keeps_unchecked_state(qapp, tmp_path, monkeypatch):
    # 回归：重解析回执并入后不得恢复被用户取消勾选的文件（否则
    # 「替换并保存」会写回明确排除的文件）
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    def parse_payload(path):
        return FileParse(path, None, [FontUsage(
            raw_name="甲字体", norm_name="甲字体", style_count=1,
            inline_count=1, chars=set("永宇"), variants={"甲字体"})], [], [])

    page._on_result("parse:p.ass", parse_payload("p.ass"))
    page.vm.set_checked("p.ass", False)
    page._on_result("parse:p.ass", parse_payload("p.ass"))   # 重解析并入
    assert [f.checked for f in page.vm.files if f.path == "p.ass"] == [False]


def test_remove_all_files_button(qapp, tmp_path, monkeypatch):
    """标题栏「移除全部」（用户拍板 2026-10-30）：图标红同文件行移除钮、
    无文件禁用；单文件直清，一个以上 MessageBox 确认（取消不动/确定
    清空——vm 记账、_removed 弃置迟到回执、行卡重建成空态卡、整页重算
    清卡）。"""

    from qfluentwidgets import FluentIcon as FIF

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.pages import subs_page

    page = _make_page(qapp, tmp_path, monkeypatch)
    assert page.btn_clear.text() == "移除全部"
    # 图标红同文件行移除钮（同 reference 渲染逐像素等价）
    ref = FIF.REMOVE_FROM.icon(color=subs_page._REMOVE_RED).pixmap(32, 32)
    img = page.btn_clear.icon().pixmap(32, 32).toImage()
    assert img == ref.toImage()
    # 空列表禁用；有文件启用
    assert not page.btn_clear.isEnabled()

    def parse_payload(path):
        return FileParse(path, None, [FontUsage(
            raw_name="甲字体", norm_name="甲字体", style_count=1,
            inline_count=1, chars=set("永宇"), variants={"甲字体"})], [], [])

    page._on_result("parse:p1.ass", parse_payload("p1.ass"))
    # 单文件直清：不弹窗
    calls = []

    def _dlg_factory(ret):
        class _Dlg:
            def __init__(self, title, content, parent):
                calls.append((title, content, parent))
                self.yesButton, self.cancelButton = _Btn(), _Btn()

            def exec(self):
                return ret

        class _Btn:
            def setText(self, t):
                self.text = t

        return _Dlg

    monkeypatch.setattr(subs_page, "MessageBox", _dlg_factory(1))
    page._remove_all_files()
    assert page.vm.files == [] and not calls
    assert "p1.ass" in page._removed
    assert not page.btn_clear.isEnabled()

    # 两份文件：弹窗确认。取消不动（重加走 _add_paths：解除 _removed）
    page._add_paths(["p1.ass"])
    page._on_result("parse:p1.ass", parse_payload("p1.ass"))
    page._on_result("parse:p2.ass", parse_payload("p2.ass"))
    for i in range(2):
        u = page.vm.files[i].usages[0]
        page._on_result(f"classify:{page._gen}:{i}",
                        (u, UsageClass(kind="keep")))
    assert len(page.vm.files) == 2 and page._cards_ui
    assert page.btn_clear.isEnabled()

    monkeypatch.setattr(subs_page, "MessageBox", _dlg_factory(0))
    page._remove_all_files()
    assert len(page.vm.files) == 2 and len(calls) == 1
    assert calls[0][0] == "移除全部字幕文件"
    assert "确认移除 2 个字幕文件" in calls[0][1]

    # 确定：清空
    monkeypatch.setattr(subs_page, "MessageBox", _dlg_factory(1))
    page._remove_all_files()
    assert page.vm.files == [] and not page._cards_ui
    assert {"p1.ass", "p2.ass"} <= page._removed
    assert not page.btn_clear.isEnabled()
    # 迟到回执弃置：文件不冒回、不重算
    page._on_result("parse:p1.ass", parse_payload("p1.ass"))
    assert page.vm.files == []


def test_parse_batch_gates_reclassify(qapp, tmp_path, monkeypatch):
    # 批次语义：整批文件解析收齐才重算一次——逐份到达不得触发分类
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    def parse_payload(path):
        return FileParse(path, None, [FontUsage(
            raw_name="甲字体", norm_name="甲字体", style_count=1,
            inline_count=1, chars=set("永宇"), variants={"甲字体"})], [], [])

    page._parsing.update(("a.ass", "b.ass"))
    page._on_result("parse:a.ass", parse_payload("a.ass"))
    assert not any(t == "slib" or str(t).startswith("classify:")
                   for t, *_ in page.worker.submits)   # 批未收齐：不分类
    page._on_result("parse:b.ass", parse_payload("b.ass"))
    tags = [t for t, *_ in page.worker.submits]
    assert tags.count("slib") == 1                     # 收齐：一次重算
    assert sum(1 for t in tags if str(t).startswith("classify:")) == 1

    # 批内失败也按到达计：不卡批次
    page._parsing.update(("c.ass",))
    page._on_failed("parse:c.ass", "boom")
    assert page._parsing == set()
    assert [t for t, *_ in page.worker.submits].count("slib") == 2

    # 批中的勾选变化延迟到批末生效（不半批拆卡）
    page._parsing.update(("d.ass",))
    page.vm.add_files([FileParse("d.ass", None, [], [], [])])
    page._on_file_checked("d.ass", False)
    assert [t for t, *_ in page.worker.submits].count("slib") == 2
    page._on_result("parse:d.ass", FileParse("d.ass", None, [], [], []))
    assert [t for t, *_ in page.worker.submits].count("slib") == 3


def test_apply_late_result_after_failed_reset_is_dropped(qapp, tmp_path, monkeypatch):
    # 派工中断复位后，迟到的 apply 回执不得再触发收尾（重复 InfoBar/record_all）
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.gui.jobs import ApplyFileResult

    page._on_failed("apply:0", "boom")
    before = len(page.vm.files)                    # record_all 不该被迟到回执触发
    page._on_apply_done(ApplyFileResult("p.ass", ".substituted.ass", None))
    assert page._apply_results == []
    assert page._apply_total == 0
    assert len(page.vm.files) == before


def test_apply_late_receipt_of_old_batch_ignored(qapp, tmp_path, monkeypatch):
    # 终审 P1：批 1 失败复位后批 2 派工，批 1 迟到回执不得计入批 2 计数
    # （裸 tag 曾提前凑满 _apply_total、成功 InfoBar 早发）
    from ass_font_substitute.gui.jobs import ApplyFileResult

    page = _make_page(qapp, tmp_path, monkeypatch)
    page._apply_gen = 1                            # 批 1 已派工（直接摆状态）
    page._apply_dispatching = True
    page._apply_total = 2
    page._apply_results = []
    page._on_failed("apply:1", "boom")             # 批 1 中断复位
    assert page._apply_dispatching is False
    page._apply_gen = 2                            # 批 2 派工
    page._apply_dispatching = True
    page._apply_total = 1
    page._apply_results = []
    page._on_result("apply:1", ApplyFileResult("p.ass", "q.ass", 1))  # 批 1 迟到
    assert page._apply_results == []               # 不计入批 2
    assert page._apply_dispatching is True
    page._on_result("apply:2", ApplyFileResult("p.ass", "q.ass", 1))  # 批 2 正主
    assert page._apply_dispatching is False        # 正常收尾


def test_zero_usage_keep_card_presentation_and_no_search(qapp, tmp_path, monkeypatch):
    # 回归：空库下 0 字用法分类出 kind=keep——不得冒充「库中有」徽章，
    # 也不给搜索入口（无可比字型，误触发会空转）
    from PySide6.QtWidgets import QLabel
    from qfluentwidgets import InfoBadge
    from qfluentwidgets import TransparentPushButton

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    page = _make_page(qapp, tmp_path, monkeypatch)
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=0, chars=set(), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0", (usage, UsageClass(kind="keep")))
    fc = page._cards_ui["甲字体"]
    # 零字卡徽章是自绘药丸（QLabel），不是 InfoBadge（白字浅底隐形问题）
    texts = ([b.text() for b in fc.findChildren(InfoBadge)]
             + [lab.text() for lab in fc.findChildren(QLabel)])
    assert "保留（无对白用字）" in texts
    assert not [btn for btn in fc.findChildren(TransparentPushButton)
                if "继续找" in btn.text()]
    page._continue_search("甲字体")            # 兜底：无字卡不派搜索工
    assert not [s for s in page.worker.submits
                if s[0].startswith("newsearcher:")]


def test_keep_row_hugs_left_edge(qapp, tmp_path, monkeypatch):
    # KEEP 行（missing/zero 卡独苗行，无预览条）贴左缘：定宽左列进
    # hbox 时 AlignVCenter 不带水平对齐位，Qt alignedRect 默认水平居中
    # ——独苗行曾整体缩进行中部（真机 missing 卡截图实锤）
    from PySide6.QtWidgets import QLabel

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.subs_vm import KEEP

    page = _make_page(qapp, tmp_path, monkeypatch)
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set("永"), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0", (usage, UsageClass(
        kind="missing", missing_chars=["永"])))
    fc = page._cards_ui["甲字体"]
    assert "保留原字体名" in [b.text() for b in fc.findChildren(QLabel)]
    page.show()
    page.resize(1100, 800)
    qapp.processEvents()
    left = fc._boxes[KEEP].parentWidget()       # 勾选框 reparent 进左列
    assert left.geometry().x() <= 15            # 左列贴行左（旧居中 ~300）
    page.hide()
    page.deleteLater()


def test_on_library_changed_reclassifies_and_defers_in_batch(qapp, tmp_path, monkeypatch):
    # 字体库管理页广播（删目录/索引更新）：空闲态立即换代重分类；
    # 解析批次中不打断——批末 _parse_arrived 才重算
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    assert page.vm.cards and norm in page._cards_ui

    gen0 = page._gen
    page.on_library_changed()
    assert page._gen == gen0 + 1
    assert not page.vm.cards and not page._cards_ui

    page._parsing.add("q.ass")
    gen1 = page._gen
    page.on_library_changed()
    assert page._gen == gen1                    # 批次中不立即重算
    page._parse_arrived("q.ass")
    assert page._gen == gen1 + 1


def test_remove_file_guards_late_parse_receipts(qapp, tmp_path, monkeypatch):
    # 移除=整个文件剔出列表：迟到 parse 成功回执不得复活（_removed 弃置）、
    # 迟到失败回执不触发重算；空态仍有卡壳（添加钮在卡标题栏，曾出回归）
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    def parse_payload(path):
        return FileParse(path, None, [FontUsage(
            raw_name="甲字体", norm_name="甲字体", style_count=1,
            inline_count=1, chars=set("永宇"), variants={"甲字体"})], [], [])

    page._on_result("parse:a.ass", parse_payload("a.ass"))
    page._remove_file("a.ass")
    assert not page.vm.files and page.files_box.count() == 1   # 空态卡壳在
    page._on_result("parse:a.ass", parse_payload("a.ass"))     # 迟到成功
    assert not page.vm.files
    gen1 = page._gen
    page._on_failed("parse:a.ass", "boom")                     # 迟到失败
    assert page._gen == gen1                                   # 不重算
    page._removed.discard("a.ass")                             # 重加解除弃置
    page._on_result("parse:a.ass", parse_payload("a.ass"))
    assert [f.path for f in page.vm.files] == ["a.ass"]
    assert page._gen == gen1 + 1                               # 重加照常重算


def test_cards_inserted_by_kind_rank(qapp, tmp_path, monkeypatch):
    # 卡序（2026-10-28）：孪生→需替换→库中没有→保留；回执乱序到达也按
    # 档插入，vm.cards 与 UI 同步
    page = _make_page(qapp, tmp_path, monkeypatch)
    from types import SimpleNamespace

    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    for n in ("甲", "乙", "丙"):
        page._on_result(f"parse:{n}.ass", FileParse(f"{n}.ass", None, [], [], []))

    def feed(norm, kind):
        usage = FontUsage(raw_name=norm, norm_name=norm, style_count=1,
                          inline_count=1, chars=set("永"), variants={norm})
        cls = SimpleNamespace(kind=kind, entities=[], twin_hit=None,
                              coverage=1.0, missing_chars=[])
        page._on_result(f"classify:{page._gen}:x", (usage, cls))

    for norm, kind in (("乙", "replace"), ("丙", "keep"), ("甲", "twin")):
        feed(norm, kind)
    assert [c.kind for c in page.vm.cards] == ["twin", "replace", "keep"]
    idxs = [page.cards_box.indexOf(page._cards_ui[n])
            for n in ("甲", "乙", "丙")]
    assert idxs == sorted(idxs) and -1 not in idxs


def test_pick_score_receipt_and_metrics_text(qapp, tmp_path, monkeypatch):
    # 手选流程：picker 回执走 pickscore 分支 attach（相似/大小/覆盖齐）；
    # 覆盖不足的手选行第二行补「未覆盖 N 字」（原只显覆盖的 bug）
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体", chars="永宇好")
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set("永宇好我在"), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="replace", entities=[ent_a])))
    assert page.vm.cards

    page._on_result("pickscore:甲字体", (ent_b, 0.88, 1.04))
    card = page.vm.cards[0]
    o = card.option("fp2")
    assert o is not None and o.source == "pick" and card.checked_key == "fp2"
    assert o.similarity == 0.88 and o.size_ratio == 1.04
    assert o.coverage == 0.6                       # 区段覆盖率口径（3 字子集）
    fc = page._cards_ui["甲字体"]
    row = fc._rows["fp2"]                     # 勾选框 reparent 进左列后，
    fulls = [lab.fullText()                   # parentWidget 不再是 optRow
             for lab in row.findChildren(_MetricsLabel)]
    # 次序（用户拍板 2026-10-29）：相似 → 覆盖 → 未覆盖 → 相对大小。
    # fullText 断言：文本列定宽后超宽指标折省略号，text() 不再保全文
    assert any(t == "相似 88.0% · 覆盖 60.0% · 未覆盖 2 字 · 相对大小 104.0%"
               for t in fulls), fulls

    # 卡被重算后迟到的评分回执：弃置不炸
    page.on_library_changed()
    page._on_result("pickscore:甲字体", (ent_b, 0.5, 1.0))


    # 评分失败降级：无分回填保住用户选择（不弹「任务失败」改 warning 语义）
    page2 = _make_page(qapp, tmp_path, monkeypatch)
    from qfluentwidgets import InfoBar
    page2._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page2._on_result(f"classify:{page2._gen}:0",
                     (usage, UsageClass(kind="replace", entities=[ent_a])))
    page2._pending_picks["甲字体"] = ent_b
    fired = []
    monkeypatch.setattr(InfoBar, "warning",
                        classmethod(lambda cls, *a, **k: fired.append(a[0])))
    page2._on_failed("pickscore:甲字体", "boom")
    monkeypatch.setattr(InfoBar, "warning", InfoBar.warning)   # 还原防误伤后段
    assert fired == ["相似度评分失败"]    # 降级走 warning，不是静默也不是 error
    card2 = page2.vm.cards[0]
    o2 = card2.option("fp2")
    assert o2 is not None and o2.source == "pick" and card2.checked_key == "fp2"
    assert o2.similarity is None and o2.size_ratio is None
    assert "甲字体" not in page2._pending_picks

    # 无 pending（迟到失败）/卡已重算孤儿：无从回填，照白名单报错不炸
    fired.clear()
    page2._on_failed("pickscore:甲字体", "boom")
    assert fired == []                 # 白名单走 InfoBar.error，未拦
    page2.on_library_changed()
    page2._pending_picks["甲字体"] = ent_b
    page2._on_failed("pickscore:甲字体", "boom")


def test_twin_metrics_lead_phrase(qapp, tmp_path, monkeypatch):
    # 孪生行次序（用户拍板 2026-10-29）：行首注明来由，后接覆盖/未覆盖；
    # 孪生行无相似率/相对大小
    page = _make_page(qapp, tmp_path, monkeypatch)
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from qfluentwidgets import TransparentToolButton

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel
    ent_a = _ent("fp1", "甲字体")
    ent_twin = _ent("fp-twin", "甲字体简", chars="永宇好")
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set("永宇好我在"), variants={"甲字体"})
    page.vm.set_entities([ent_a])     # 缺字条=原字体口径，原字体按名取自 vm 库
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="twin", entities=[ent_a],
                                       coverage=0.6,
                                       missing_chars=["我", "在"],
                                       twin_hit=ent_twin)))
    card = page.vm.cards[0]
    assert card.checked_key == "fp-twin"          # 默认勾选链：孪生优先
    fc = page._cards_ui["甲字体"]
    row = fc._rows["fp-twin"]                 # 勾选框 reparent 后取行走 _rows
    fulls = [lab.fullText() for lab in row.findChildren(_MetricsLabel)]
    assert any(t == "原字体名称对应的简体名称 · 覆盖 60.0% · 未覆盖 2 字"
               for t in fulls), fulls
    # 孪生卡也带缺字条（用户纠偏 2026-10-29：这行=**原字体**未覆盖，
    # 非孪生体——孪生体的未覆盖在指标行另说）。原字体甲字体 cmap 永宇，
    # 用字 永宇好我在 → 缺 好我在 3 字。前缀+字符标签已拆（划选用）
    strips = [b for b in fc.findChildren(QLabel)
              if b.text().startswith("未覆盖 ")]
    assert len(strips) == 1
    assert strips[0].text() == "未覆盖 3 字："
    # missing 集合无序：只钉字集不钉序；字符标签定位收进 tofu 框
    # （字体名标签现在也可划选）
    from PySide6.QtWidgets import QFrame
    tofu = next(f for f in fc.findChildren(QFrame, "tofu"))
    chars_labs = [b for b in tofu.findChildren(QLabel)
                  if b.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse]
    assert len(chars_labs) == 1
    assert set(chars_labs[0].text()) == set("好我在")
    copy_btns = [b for b in fc.findChildren(TransparentToolButton)
                 if b.toolTip() == "复制未覆盖字符"]
    assert len(copy_btns) == 1


def test_elide_labels_survive_layout(qapp, tmp_path, monkeypatch):
    """定宽省略标签：布局后名称/指标可见非空（曾病：文字走 QLabel 构造
    传参而 mixin _full 未立账，首次 resize elide 空串把整行文字清空——
    真机截图名称/指标全白实锤）。"""
    from PySide6.QtWidgets import QApplication

    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel
    from ass_font_substitute.gui.widgets.font_card import _NameLabel
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    page.show()
    page.resize(1100, 800)
    for _ in range(10):
        QApplication.processEvents()
    fc = page._cards_ui[norm]
    names = fc.findChildren(_NameLabel)
    metrics = fc.findChildren(_MetricsLabel)
    assert names and metrics
    assert all(lab.text() for lab in names)             # 名称/指标非空可见
    assert all(lab.text() and lab.fullText() for lab in metrics)
    for lab in (*names, *metrics):        # fullText 前缀关系（未超宽=全文）
        assert lab.text() == "" or lab.fullText().startswith(
            lab.text().rstrip("…"))
    page.hide()


def test_continue_search_keeps_scroll_focus_and_identity(qapp, tmp_path, monkeypatch):
    # 真机回归三连：点「继续寻找更多」只翻按钮态不重建——卡控件身份
    # 不变、焦点停在原按钮（旧整卡重建路线焦点随卡销毁乱跳致滚动瞬移）、
    # 滚动位置一动不动。
    page = _make_page(qapp, tmp_path, monkeypatch)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    for i in range(8):
        name = f"字体{i}"
        ents = [_ent(f"fp{i}-{j}", f"{name}候选{j}", chars="永宇好")
                for j in range(4)]
        usage = FontUsage(raw_name=name, norm_name=name, style_count=1,
                          inline_count=1, chars=set("永宇好我在"),
                          variants={name})
        page._on_result(f"parse:p{i}.ass",
                        FileParse(f"p{i}.ass", None, [usage], [], []))
    for i in range(8):
        name = f"字体{i}"
        ents = [_ent(f"fp{i}-{j}", f"{name}候选{j}", chars="永宇好")
                for j in range(4)]
        usage = FontUsage(raw_name=name, norm_name=name, style_count=1,
                          inline_count=1, chars=set("永宇好我在"),
                          variants={name})
        page._on_result(
            f"classify:{page._gen}:{i}",
            (usage, UsageClass(kind="replace", entities=ents, coverage=0.6,
                               missing_chars=["阿", "八"])))
    page.show()
    page.resize(1100, 800)
    for _ in range(10):
        QApplication.processEvents()
    sb = page.verticalScrollBar()
    sb.setValue(sb.maximum())
    for _ in range(5):
        QApplication.processEvents()
    before = sb.value()
    assert before > 0                     # 内容须超视口：滚动才有意义

    norm = "字体5"
    fc = page._cards_ui[norm]
    btn = fc.findChild(QWidget, "contBtn")
    btn.setFocus()
    for _ in range(5):
        QApplication.processEvents()
    page._continue_search(norm)
    for _ in range(10):
        QApplication.processEvents()
    assert sb.value() == before           # 瞬移即回归
    assert page._cards_ui[norm] is fc     # 整卡不重建
    assert QApplication.focusWidget() is btn   # 焦点原地不动
    assert btn.text() == "寻找中…"          # 钮态已翻：寻找中


def test_bottom_bar_pinned_to_viewport(qapp, tmp_path, monkeypatch):
    # 底栏（保存方式/替换并保存）钉死视口下缘：在滚动区外、滚动前后
    # 位置不动（用户拍板 2026-10-29：常驻可见，不随内容滚走）。
    page = _make_page(qapp, tmp_path, monkeypatch)
    from PySide6.QtWidgets import QApplication

    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.show()
    page.resize(1100, 500)                # 压小视口：内容必溢出
    for _ in range(10):
        QApplication.processEvents()
    foot = page.btn_apply.parentWidget()
    assert foot.parent() is page          # 挂页面本体，不在滚动区里
    sb = page.verticalScrollBar()
    sb.setValue(sb.maximum())
    for _ in range(5):
        QApplication.processEvents()
    assert page.btn_apply.isVisible()
    y = foot.mapTo(page, QPoint(0, 0)).y()
    assert y + foot.height() == page.height()   # 贴视口底缘
    sb.setValue(0)
    for _ in range(5):
        QApplication.processEvents()
    assert foot.mapTo(page, QPoint(0, 0)).y() == y      # 滚动不挪底栏


def test_option_row_retheme_tracks_accent_and_theme(qapp, tmp_path, monkeypatch):
    # 选中行描边/底色烤着 themeColor() 派生值，须跟两路刷新：换强调色
    # （themeColorChanged，且信号发出时 themeColor() 尚是旧值——实测慢
    # 一拍，靠 singleShot(0) 重取）与亮暗主题切换（qfw 只发
    # themeChanged/themeChangedFinished）
    from PySide6.QtWidgets import QFrame
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme
    from qfluentwidgets import setThemeColor

    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    fc = page._cards_ui[norm]
    rows = fc.findChildren(QFrame, "optRow")
    assert len(rows) == 2

    def seg_color(w):
        # 行 sheet 里 sel 段的描边色
        for line in w.styleSheet().splitlines():
            if '[sel="true"]' in line and "solid" in line:
                return line.split("solid ")[1].split(";")[0]
        return None

    for col in ("#f5c1cb", "#009faa", "#e09042"):
        setThemeColor(col, save=False)
        qapp.processEvents()                    # singleShot(0) 落地
        assert all(seg_color(r) == col for r in rows), seg_color(rows[0])

    setTheme(Theme.DARK, save=False)
    qapp.processEvents()
    dark_c = seg_color(rows[0])
    assert dark_c is not None
    setTheme(Theme.LIGHT, save=False)
    qapp.processEvents()
    assert seg_color(rows[0]) != dark_c         # 亮暗派生色不同且已互刷
    setThemeColor("#009faa", save=False)
    qapp.processEvents()


def test_tofu_frame_dashes_survive_corners(qapp):
    # 缺字框虚线沿圆角路径均匀（QSS dashed+radius 圆角段实线的缺陷已改
    # 自绘 CustomDashLine）：白底宿主 grab 后沿顶边采样，虚段（透出白底）
    # 必须存在
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.widgets.font_card import _TofuFrame

    host = QWidget()
    host.setAttribute(Qt.WA_StyledBackground, True)
    host.setStyleSheet("background: white;")
    box = _TofuFrame(host)
    box.setGeometry(0, 0, 160, 40)
    host.resize(160, 40)
    host.show()
    qapp.processEvents()
    img = host.grab().toImage()
    # 顶边扫描（笔画中心在 y≈0.5，AA 摊在 0/1 两行）：dash 段红调、
    # gap 段白底，两类都要有
    reds = gaps = 0
    for x in range(12, 148):
        c0 = img.pixelColor(x, 0)
        c1 = img.pixelColor(x, 1)
        if (c0.red() > c0.green() + 30 and c0.red() > c0.blue() + 30) or \
           (c1.red() > c1.green() + 30 and c1.red() > c1.blue() + 30):
            reds += 1
        elif c0.red() > 200 and c0.green() > 200 and c0.blue() > 200 and \
                c1.red() > 200 and c1.green() > 200 and c1.blue() > 200:
            gaps += 1
    assert reds > 8 and gaps > 8, (reds, gaps)
    host.deleteLater()


def test_file_row_checkbox_fixed_width(qapp, tmp_path, monkeypatch):
    # qfw CheckBox QSS min-width:28px（空文本拖 ~36px 空白）——选项行经
    # setCustomStyleSheet 覆写后定宽 24 生效且 polish 稳定
    from qfluentwidgets import CheckBox

    from ass_font_substitute.gui.subs_vm import Card
    from ass_font_substitute.gui.subs_vm import OptionItem
    from ass_font_substitute.gui.widgets.font_card import FontCard

    c = Card(norm_name="a", raw_name="a", kind="replace",
             options=[OptionItem(key="keep", display_name="a"),
                      OptionItem(key="orig", display_name="o")],
             checked_key="orig")
    fc = FontCard(c, None, None, lambda: "")
    fc.show()
    qapp.processEvents()
    boxes = fc.findChildren(CheckBox)
    assert boxes and all(b.width() == 24 for b in boxes)


def _cont_btn(fc):
    """foot 搜索钮（按态文案前缀识别，避开手选钮）。"""
    from qfluentwidgets import TransparentPushButton
    hits = [b for b in fc.findChildren(TransparentPushButton) if b.text()[
        :4] in ("开始寻找", "继续寻找", "寻找中…", "无更多符", "原字体无")]
    assert len(hits) == 1
    return hits[0]



def _pick_btn(fc):
    """foot 手选钮（文案全等识别）。"""
    from qfluentwidgets import TransparentPushButton
    hits = [b for b in fc.findChildren(TransparentPushButton)
            if b.text() == "从字体库手动选择"]
    assert len(hits) == 1
    return hits[0]


def _icon_ink_luma(btn):
    """钮图标区（x≈12 起竖居中）最暗不透明像素 luma：图标墨色采样，
    亮主题黑墨趋低、暗主题白墨趋高。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage

    img = QImage(btn.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    btn.render(img)
    best = 1000
    cy = btn.height() // 2
    for y in range(max(0, cy - 10), min(btn.height(), cy + 10)):
        for x in range(10, 32):
            c = img.pixelColor(QPoint(x, y))
            if c.alpha() == 0:
                continue
            luma = (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
            best = min(best, luma)
    return best


def test_cont_btn_icon_follows_theme(qapp):
    """搜索钮图标随亮暗主题换色（用户报告 2026-10-30）：qfw 按钮收
    FluentIconBase 才逐 paint 以 Theme.AUTO 重渲；收 .icon() 得到的
    QIcon 定死建时主题色，切主题不跟随。"""
    from qfluentwidgets import Theme
    from qfluentwidgets import setTheme

    from ass_font_substitute.gui.subs_vm import Card
    from ass_font_substitute.gui.subs_vm import OptionItem
    from ass_font_substitute.gui.widgets.font_card import FontCard

    c = Card(norm_name="a", raw_name="a", kind="replace", n_chars=1,
             options=[OptionItem(key="keep", display_name="a"),
                      OptionItem(key="orig", display_name="o")],
             checked_key="orig")
    try:
        setTheme(Theme.LIGHT)
        fc = FontCard(c, None, None, lambda: "")
        fc.show()
        qapp.processEvents()
        btn = _cont_btn(fc)
        light_ink = _icon_ink_luma(btn)
        setTheme(Theme.DARK)
        qapp.processEvents()
        dark_ink = _icon_ink_luma(btn)
        assert light_ink < 120, light_ink
        assert dark_ink > 160, dark_ink
    finally:
        setTheme(Theme.AUTO)
        fc.deleteLater()


def test_cont_button_states_and_manual_pause(qapp, tmp_path, monkeypatch):
    """搜索钮一钮三态（用户拍板 2026-10-28）：idle=继续寻找/Pause=寻找中
    计数/耗尽=禁用 Info 文案；搜索中再点=置取消标志，cancelled 回执归
    paused 态（续搜复用同一 searcher）。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    btn = _cont_btn(page._cards_ui[norm])
    # 没寻找过=开始寻找；寻过暂停=继续寻找更多相似字体（用户拍板 2026-10-29）
    assert btn.text() == "开始寻找相似字体" and btn.isEnabled()

    page._continue_search(norm)                 # 搜索器未挂：无计数的寻找中
    btn = _cont_btn(page._cards_ui[norm])
    assert btn.text() == "寻找中…" and btn.isEnabled()

    s = SimpleNamespace(min_sim=0.85, paused=False, target_error=False,
                        pool_size=10, compared=3)
    page._on_result(f"newsearcher:{norm}", s)   # 挂接后计数上车
    fc = page._cards_ui[norm]
    assert _cont_btn(fc).text() == "寻找中… 3/10"
    assert not _pick_btn(fc).isEnabled()        # 搜索中禁用手选

    n = len(page.worker.submits)
    _cont_btn(page._cards_ui[norm]).click()     # 搜索中再点 = 暂停
    assert page._cancels[norm]["cancelled"]
    assert len(page.worker.submits) == n        # 暂停不派新任务

    page._on_result(f"pull:{norm}", ([], "cancelled"))
    assert card.search_state == "paused" and not card.searching
    fc = page._cards_ui[norm]
    btn = _cont_btn(fc)
    assert btn.text() == "继续寻找更多相似字体" and btn.isEnabled()
    assert _pick_btn(fc).isEnabled()            # 暂停即恢复手选

    s.paused = False                            # 搜索器走完剩余池 → 耗尽
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    btn = _cont_btn(page._cards_ui[norm])
    assert not btn.isEnabled()
    assert btn.text() == "无更多符合要求的相似字体"


def test_running_receipts_coalesce_refresh(qapp, tmp_path, monkeypatch):
    """渐进回执节流：running 回执只记账 vm，刷新由 300ms 单发定时器
    合并成一次 FontCard.apply() 就地刷（按钮计数原地改，零重建零闪烁）
    ——回执洪逐回执整卡拆除重建的旧路线已废（布局挤崩+整卡频闪）。
    终态回执仍即时刷。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    s = SimpleNamespace(min_sim=0.85, paused=False, target_error=False,
                        pool_size=100, compared=0)
    page._on_result(f"newsearcher:{norm}", s)
    fc0 = page._cards_ui[norm]
    for i in range(10):                         # 回执洪：记账不刷
        s.compared = 24 * (i + 1)
        page._on_result(f"pull:{norm}", ([], "running"))
        assert card.searcher.compared == s.compared
    assert page._cards_ui[norm] is fc0          # 卡控件身份不变
    assert norm in page._refresh_pending
    page._flush_refresh()
    assert page._cards_ui[norm] is fc0          # 就地刷：身份仍不变
    assert not page._refresh_pending
    assert _cont_btn(fc0).text() == "寻找中… 240/100"
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    assert card.search_state == "exhausted"     # 终态不受节流影响
    assert not _cont_btn(fc0).isEnabled()



class _StubSearcher:
    """最小搜索器桩：页面续搜路径会调 resume()。"""

    def __init__(self, paused=False, target_error=False):
        self.min_sim = 0.85
        self.paused = paused
        self.target_error = target_error
        self.pool_size = 10
        self.compared = 0
        self.hit_count = 99

    def resume(self):
        self.paused = False
        self.hit_count = 0


def test_natural_pause_resume_and_inflight_pause(qapp, tmp_path, monkeypatch):
    """自然暂停后续搜必须经 resume() 清暂停+重置批计数（P0：否则续搜
    空片回执原地打转）；newsearcher 在途点暂停，取消标志沿用到 pull
    提交，回执归 paused。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    s = _StubSearcher(paused=True)
    page._on_result(f"newsearcher:{norm}", s)
    page._on_result(f"pull:{norm}", ([], "paused"))    # 自然暂停
    assert card.search_state == "paused"
    page._continue_search(norm)                         # 续搜同一搜索器
    assert s.paused is False and s.hit_count == 0      # resume 生效
    page._on_result(f"pull:{norm}", ([], "cancelled"))  # 手动暂停收尾
    assert card.search_state == "paused" and not card.searching

    s.paused = False
    page._on_result(f"pull:{norm}", ([], "exhausted"))  # 走到耗尽
    page._continue_search(norm)                         # 新搜索器在途
    d = page._cancels[norm]
    _cont_btn(page._cards_ui[norm]).click()             # 在途暂停
    assert d["cancelled"]
    s2 = _StubSearcher()
    page._on_result(f"newsearcher:{norm}", s2)
    sub = page.worker.submits[-1]
    assert sub[0] == f"pull:{norm}" and sub[2][1] is d  # 回执沿用已取消 dict
    page._on_result(f"pull:{norm}", ([], "cancelled"))
    assert card.search_state == "paused" and not card.searching


def test_target_error_button_disabled(qapp, tmp_path, monkeypatch):
    """target_error 归禁用说明钮（原独立标签行），手选钮保持可用。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    s = _StubSearcher(target_error=True)
    page._on_result(f"newsearcher:{norm}", s)
    page._on_result(f"pull:{norm}", ([], "target_error"))
    assert card.search_state == "target_error"
    fc = page._cards_ui[norm]
    btn = _cont_btn(fc)
    assert not btn.isEnabled()
    assert btn.text() == "原字体无法渲染，无法比较相似度"
    assert _pick_btn(fc).isEnabled()


def test_exhausted_handoff_focus_to_pick(qapp, tmp_path, monkeypatch):
    """搜索钮带焦被禁（exhausted/target_error）时焦点交同 foot 手选钮：
    Qt 缺省把焦点塞给任意下一焦点控件，QScrollArea 见 FocusIn 就滚过去
    ——真机「无更多符合要求的相似字体」出现时滚动乱跳实锤。"""
    from PySide6.QtWidgets import QApplication
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    s = _StubSearcher()
    page._on_result(f"newsearcher:{norm}", s)
    fc = page._cards_ui[norm]
    btn = _cont_btn(fc)
    page.show()                           # 焦点断言须窗口可见
    qapp.processEvents()
    btn.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is btn
    page._on_result(f"pull:{norm}", ([], "exhausted"))
    assert not btn.isEnabled()
    assert QApplication.focusWidget() is _pick_btn(fc)   # 焦点交棒手选钮
    page._on_result(f"pull:{norm}", ([], "cancelled"))   # 回 paused 仍正常
    assert btn.isEnabled() and _pick_btn(fc).isEnabled()
    page.hide()



def test_apply_keeps_preview_jobs_and_teardown_cancels(qapp, tmp_path, monkeypatch):
    """apply 就地刷新不撤预览渲染单（行都活着，任务仍有效——旧整卡重建
    路线每轮重建都重复派任务塞死队列）；预览文字变化经 set_text 原地撤
    旧单重派；teardown（整页重算）才全撤。"""
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    bars = page._cards_ui[norm].findChildren(PreviewBar)
    assert bars
    old_jobs = [b._job for b in bars]
    assert all(j is not None and not j.cancelled for j in old_jobs)
    page._apply_card(card)
    assert all(not j.cancelled for j in old_jobs)           # 就地刷不撤单

    page.preview_edit.setText("永宇好")                      # 预览文字变化
    page._apply_all_cards()
    assert all(b._text == "永宇好" for b in bars)           # 原地换文字
    assert all(b._job is not None and not b._job.cancelled
               for b in bars)                               # 新单照常在途
    page._reclassify()                                      # 整页重算同撤
    assert all(b._job.cancelled for b in bars)



def test_new_candidates_insert_in_score_order(qapp, tmp_path, monkeypatch):
    """新候选行按综合分位次插入（vm 稳定排序：新项插队、既有行相对次序
    恒稳），apply 后行布局序 = card.options 序，旧行控件不搬不重建。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体", chars="永宇")
    _usage, card, norm = _feed_replace_card(page, ent_a, ent_b)
    fc = page._cards_ui[norm]
    old_rows = dict(fc._rows)
    s = SimpleNamespace(min_sim=0.85, paused=False, target_error=False,
                        pool_size=100, compared=24)
    page._on_result(f"newsearcher:{norm}", s)
    # 分数构造插队：c0 压过乙字体、c1 居中、c2 垫底（乙字体覆盖率单项
    # 计分 0.4，落在 c1 与 c2 之间）
    scores = [("c0", 0.99, 0.9), ("c1", 0.2, 0.3), ("c2", 0.3, 0.05)]
    cands = [SimpleNamespace(fingerprint=k, display_name=k, coverage=cov,
                             similarity=sim, size_ratio=1.0, n_chars=2)
             for k, sim, cov in scores]
    page._on_result(f"pull:{norm}", (cands, "running"))
    page._flush_refresh()
    assert page._cards_ui[norm] is fc               # 仍零重建
    layout_keys = []
    for i in range(fc._opt_layout.count()):
        w = fc._opt_layout.itemAt(i).widget()
        layout_keys.append(next(k for k, r in fc._rows.items() if r is w))
    assert layout_keys == [o.key for o in card.options]   # 行序 = 数据序
    assert all(fc._rows[k] is old_rows.get(k)
               for k in old_rows)                   # 旧行控件原对象保留


def _solid_strip() -> "object":
    import numpy as np
    a = np.zeros((28, 100), dtype=np.uint8)
    a[4:24, 4:96] = 255
    return a


def test_card_preview_no_shrink_loop(qapp, tmp_path, monkeypatch):
    """width_fn 预算条防自反塌缩：真实行结构（定宽左列+预算条）下布局
    迭代宽度不漂、pixmap 不 null。预算只读行几何（bar 宽与贴图无关），
    单控件 hbox 里 QLabel Preferred 策略吞宽度、反馈环不启动——必须走
    整卡装配才钉得住（真机实锤 68→12→1px→贴图 null）。"""
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    for e in (ent_a, ent_b):                # 建卡前预置条带缓存：构造期
        page._pvm.put_tiles((e.fingerprint, e.display_name, False),
                            _solid_strip())
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    page.show()
    page.resize(1100, 800)
    qapp.processEvents()
    bars = page._cards_ui[norm].findChildren(PreviewBar)
    assert bars
    ws = {b._entity.fingerprint: b.width() for b in bars}
    assert all(w > 1 for w in ws.values())
    for _ in range(10):                     # 布局迭代：宽度钉死不漂
        qapp.processEvents()
        for b in bars:
            assert b.width() == ws[b._entity.fingerprint]
            assert not b.pixmap().isNull()
    page.hide()
    page.deleteLater()


def test_mono_switch_broadcast_rerenders_bars(qapp, tmp_path, monkeypatch):
    """强制等宽开关（两页同键 preview_mono，默认关）：翻转落设置；设置
    广播回页面 → 各卡预览条按新口径清贴图重派（派发参数带 mono）。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, _card, norm = _feed_replace_card(page, ent_a, ent_b)
    fc = page._cards_ui[norm]
    assert not page.sw_mono.isChecked()             # 默认关（自然字距）
    bar = next(iter(fc._bar_by_key.values()))
    n = len(page.worker.submits)
    page.sw_mono.setChecked(True)
    assert page.settings.get("preview_mono") is True
    assert bar._mono is True                        # 广播后按新口径重派
    assert len(page.worker.submits) > n
    assert page.worker.submits[-1][2][-1] is True   # 派发参数带 mono
    page.sw_mono.setChecked(False)
    assert page.settings.get("preview_mono") is False
    page.deleteLater()


def test_missing_strip_30_cap_and_copy_button(qapp, tmp_path, monkeypatch):
    """缺字条：前缀+可划选字符标签+省略号分离；复制钮载荷=全部未覆盖；
    划选+Ctrl+C 只拷选中段；点字符标签不触发行切换。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    from qfluentwidgets import TransparentToolButton

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse

    page = _make_page(qapp, tmp_path, monkeypatch)
    chars = "永宇好在" + "".join(chr(0x4E00 + i) for i in range(40))
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set(chars), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    ent = _ent("fp1", "乙字体", chars="永")     # 覆盖极少 → 大量未覆盖
    page._on_result(f"classify:{page._gen}:0", (usage, UsageClass(
        kind="replace", entities=[ent], coverage=0.02,
        missing_chars=list(chars[1:]))))
    fc = page._cards_ui["甲字体"]
    card = page.vm.cards[0]

    def strip_label():
        return next(b for b in fc.findChildren(QLabel)
                    if b.text().startswith("未覆盖 "))

    lab = strip_label()
    assert lab.text() == "未覆盖 43 字："
    sel_flags = (Qt.TextInteractionFlag.TextSelectableByMouse
                 | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    # 字符标签定位收进 tofu 框——字体名标签现在也可划选
    from PySide6.QtWidgets import QFrame
    tofu = next(f for f in fc.findChildren(QFrame, "tofu"))
    chars_labs = [b for b in tofu.findChildren(QLabel)
                  if b.textInteractionFlags() & sel_flags]
    assert len(chars_labs) == 1                  # 可划选的字符标签独一份
    chars_lab = chars_labs[0]
    assert chars_lab.cursor().shape() == Qt.CursorShape.IBeamCursor
    # Keyboard 位的真机 Ctrl+C 靠 focusPolicy=ClickFocus（QTest.keyClick
    # 绕焦点路由，须独立钉住——审查 P2-1）
    assert chars_lab.focusPolicy() == Qt.FocusPolicy.ClickFocus
    shown = chars_lab.text()
    assert len(shown) == 30                      # 预览截 30 字
    assert "…" in [b.text() for b in tofu.findChildren(QLabel)]
    btns = [b for b in fc.findChildren(TransparentToolButton)
            if b.toolTip() == "复制未覆盖字符"]
    assert len(btns) == 1
    assert btns[0].width() == 22                # 小号钮
    # 钮紧跟文本尾（不钉行尾）：钮左缘 − 字符标签右缘 ≤ 间距
    assert btns[0].x() - (chars_lab.x() + chars_lab.width()) <= 16
    qapp.clipboard().clear()
    btns[0].click()
    # 拷贝载荷=**全部**未覆盖 43 字（用户纠偏 2026-10-29：展示截 30 只
    # 管显示）；cls.missing_chars 列表序直通 card.missing
    assert qapp.clipboard().text() == chars[1:]

    # 划选只复制一段（用户需求 2026-10-29）：拖选 + Ctrl+C
    page.show()
    page.resize(1100, 800)
    qapp.processEvents()
    QTest.mousePress(chars_lab, Qt.MouseButton.LeftButton, pos=QPoint(2, 8))
    QTest.mouseMove(chars_lab, QPoint(34, 8))
    QTest.mouseRelease(chars_lab, Qt.MouseButton.LeftButton, pos=QPoint(34, 8))
    qapp.processEvents()
    assert chars_lab.hasSelectedText()
    qapp.clipboard().clear()
    QTest.keyClick(chars_lab, Qt.Key_C, Qt.ControlModifier)
    qapp.processEvents()
    assert qapp.clipboard().text() == chars_lab.selectedText()
    assert 0 < len(qapp.clipboard().text()) < 30   # 部分，非全列

    # 点字符标签划选不触发行点击切换（QLabel 吃掉 press 不落行）
    before = card.checked_key
    QTest.mouseClick(chars_lab, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert card.checked_key == before
    page.deleteLater()


def test_missing_hover_popup_chars_and_copy(qapp, tmp_path, monkeypatch):
    """未覆盖指标悬停 Flyout：qfw Flyout 加复制钮，常驻可点（旧 ToolTip
    窗悬停即收，复制钮够不着，真机实锤）；鼠标同离指标行与浮层即收
    （轮询光标）；复制/行移除也收；原生 tooltip 事件被吃不双弹。"""
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QPoint
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from qfluentwidgets import Flyout
    from qfluentwidgets import TransparentToolButton

    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.tip import SHOW_DELAY
    from ass_font_substitute.gui.widgets.font_card import _MissingFlyoutFilter

    page = _make_page(qapp, tmp_path, monkeypatch)
    chars = "永" + "".join(chr(0x4E00 + i) for i in range(43))
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set(chars), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    ent = _ent("fp1", "乙字体", chars="永")     # 只盖「永」→ 43 字未覆盖
    page._on_result(f"classify:{page._gen}:0", (usage, UsageClass(
        kind="replace", entities=[ent], coverage=0.02,
        missing_chars=list(chars[1:]))))
    fc = page._cards_ui["甲字体"]
    m_lab = fc._metrics["fp1"]
    filt = m_lab.findChildren(_MissingFlyoutFilter)[0]
    assert filt.eventFilter(m_lab, QEvent(QEvent.Type.ToolTip)) is True
    # offscreen 光标恒 (0,0)，钉行内防轮询 tick 误收；移出判定走
    # _close_if_out 直喂
    filt._pos_fn = lambda: m_lab.mapToGlobal(m_lab.rect().center())

    page.show()
    page.resize(1100, 800)
    qapp.processEvents()
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 300)               # 真定时器走完延时出泡
    fly = page.findChildren(Flyout)[0]
    assert fly.isVisible() and filt._tick.isActive()
    sel_flags = (Qt.TextInteractionFlag.TextSelectableByMouse
                 | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    assert fly.view.label.textInteractionFlags() & sel_flags
    assert (fly.view.label.cursor().shape()
            == Qt.CursorShape.IBeamCursor)
    assert len(fly.view.label.text()) == 30      # 展示截 30，省略号另拆
    assert fly.view.em_label is not None and fly.view.em_label.text() == "…"
    # 锚点：x=光标在浮层中轴（复刻 qfw 屏缘钳制公式，含阴影余量 5——
    # qfw 1.11.3 flyout.py FlyoutAnimationManager._adjustPosition，
    # qfw 升级若挂先查此处）、y 贴指标行下缘
    from PySide6.QtGui import QGuiApplication
    scr = QGuiApplication.primaryScreen().availableGeometry()
    cx = filt._pos_fn().x()
    w = fly.sizeHint().width() + 5
    expected_x = max(scr.left(), min(cx - (w - 5) // 2, scr.right() - w))
    assert abs(fly.geometry().left() - expected_x) <= 4
    lab_bottom = m_lab.mapToGlobal(QPoint(0, m_lab.height())).y()
    assert abs(fly.geometry().top() - lab_bottom) <= 12

    # 移出判定：行内/浮层内都不收，两者之外才收
    filt._close_if_out(m_lab.mapToGlobal(m_lab.rect().center()))
    assert fly.isVisible()
    filt._close_if_out(fly.geometry().center())
    assert fly.isVisible()
    filt._close_if_out(QPoint(fly.geometry().left() - 500,
                              fly.geometry().top() - 500))
    assert not fly.isVisible()                  # 同离两者即收
    QTest.qWait(50)
    # 实走 destroyed→_forget 停 tick 路径（deleteOnClose DeferredDelete
    # 在 qWait 内落地），非 _on_tick 早退路径
    assert not filt._tick.isActive()

    # 复制收：载荷=**全部**未覆盖（用户纠偏 2026-10-29：截 30 只管
    # 显示）；o.missing 经 coverage_detail 集合来，无序
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 300)
    fly2 = page.findChildren(Flyout)[0]
    assert fly2.isVisible()
    btn = fly2.view.findChild(TransparentToolButton)
    qapp.clipboard().clear()
    btn.click()
    copied = qapp.clipboard().text()
    assert len(copied) == 43 and set(copied) == set(chars[1:])
    assert not fly2.isVisible()
    QTest.qWait(50)                             # deleteOnClose 析构落地
    assert filt._flyout is None

    # 划选部分拷贝：拖选 + Ctrl+C（Popup 激活态下键盘直达标签）
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 300)
    fly_sel = page.findChildren(Flyout)[0]
    lab3 = fly_sel.view.label
    QTest.mousePress(lab3, Qt.MouseButton.LeftButton, pos=QPoint(2, 8))
    QTest.mouseMove(lab3, QPoint(30, 8))
    QTest.mouseRelease(lab3, Qt.MouseButton.LeftButton, pos=QPoint(30, 8))
    qapp.processEvents()
    assert lab3.hasSelectedText()
    qapp.clipboard().clear()
    QTest.keyClick(lab3, Qt.Key_C, Qt.ControlModifier)
    qapp.processEvents()
    assert qapp.clipboard().text() == lab3.selectedText()
    assert 0 < len(qapp.clipboard().text()) < 30
    fly_sel.close()
    QTest.qWait(50)

    # 行被移除（Hide）→ 浮层立收不留孤窗
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 300)
    fly3 = page.findChildren(Flyout)[0]
    assert fly3.isVisible()
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Hide))
    assert not fly3.isVisible()
    QTest.qWait(50)                             # 析构落地，下组断言从零起

    # 延时窗口内移出（Enter→Leave）不出泡
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Leave))
    QTest.qWait(SHOW_DELAY + 100)
    assert not page.findChildren(Flyout)        # Leave 停了延时定时器

    # 可见期重复 Enter 不重复弹（_show 可见即 return）
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 300)
    assert len(page.findChildren(Flyout)) == 1
    qapp.sendEvent(m_lab, QEvent(QEvent.Type.Enter))
    QTest.qWait(SHOW_DELAY + 100)
    assert len(page.findChildren(Flyout)) == 1
    page.deleteLater()


def test_full_coverage_row_has_no_popup_filter(qapp, tmp_path, monkeypatch):
    # 全覆盖行无浮层过滤器：无未覆盖字符可言，省略号全文 tooltip 照旧
    from ass_font_substitute.gui.widgets.font_card import _MissingFlyoutFilter

    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _feed_replace_card(page, ent_a, ent_b)
    fc = page._cards_ui["甲字体"]
    assert fc._metrics["fp1"].findChildren(_MissingFlyoutFilter) == []
    assert fc._metrics["fp2"].findChildren(_MissingFlyoutFilter) == []
    page.deleteLater()


def test_font_name_labels_selectable(qapp, tmp_path, monkeypatch):
    # 字体名可划选（用户需求 2026-10-29，选择对话框除外）：卡头原字体
    # 名 + 候选行名（_NameLabel）
    from qfluentwidgets import StrongBodyLabel

    from ass_font_substitute.gui.widgets.font_card import _NameLabel
    from ass_font_substitute.gui.widgets.selectable import SELECT_FLAGS

    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _feed_replace_card(page, ent_a, ent_b)
    fc = page._cards_ui["甲字体"]
    from PySide6.QtCore import Qt
    heads = [x for x in fc.findChildren(StrongBodyLabel)
             if x.text() == "甲字体"]
    assert len(heads) == 1
    assert heads[0].textInteractionFlags() & SELECT_FLAGS
    names = fc.findChildren(_NameLabel)
    assert {x.fullText() for x in names} == {"甲字体", "乙字体"}
    assert all(x.textInteractionFlags() & SELECT_FLAGS for x in names)
    # 可选处指针变 IBeam（用户拍板 2026-10-29）
    ibeam = Qt.CursorShape.IBeamCursor
    assert heads[0].cursor().shape() == ibeam
    assert all(x.cursor().shape() == ibeam for x in names)
    page.deleteLater()


def test_add_dir_scans_and_parses(qapp, tmp_path, monkeypatch):
    import ass_font_substitute.gui.pages.subs_page as m

    d = tmp_path / "subs"
    (d / "nested").mkdir(parents=True)
    (d / "a.ass").write_text("", encoding="utf-8")
    (d / "b.SSA").write_text("", encoding="utf-8")
    (d / "nested" / "c.ass").write_text("", encoding="utf-8")
    (d / "skip.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(m, "pick_existing_dir", lambda *a, **k: str(d))
    page = _make_page(qapp, tmp_path, monkeypatch)
    page._add_dir()
    assert len(page.worker.submits) == 1
    tag, fn, args, _ = page.worker.submits[0]
    assert tag == f"scandir:{d}" and fn is m._scan_dir_job
    paths = fn(None, *args)          # 假 worker：手动执行扫描任务
    # 递归+剔除非字幕；.ssa 不收（light_ass 不认 SSA v4 样式段，静默半残）
    assert set(paths) == {str(d / "a.ass"), str(d / "nested" / "c.ass")}
    page._on_result(tag, paths)
    parse_tags = [t.split(":", 1)[1] for t, *_ in page.worker.submits[1:]
                  if t.startswith("parse:")]
    assert set(parse_tags) == set(paths)
    assert page._parsing == set(paths)
    page.deleteLater()


def test_add_dir_empty_warning(qapp, tmp_path, monkeypatch):
    import ass_font_substitute.gui.pages.subs_page as m

    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.setattr(m, "pick_existing_dir", lambda *a, **k: str(d))
    warned = []
    monkeypatch.setattr(m.InfoBar, "warning",
                        lambda title, content, **k: warned.append(content))
    page = _make_page(qapp, tmp_path, monkeypatch)
    page._add_dir()
    tag, fn, args, _ = page.worker.submits[0]
    page._on_result(tag, fn(None, *args))
    assert warned == [str(d)]
    page.deleteLater()


def _cand(fp, name, sim, size=1.0, cov=1.0):
    from ass_font_substitute.core.models import Candidate
    c = Candidate(fingerprint=fp, display_name=name, coverage=cov,
                  script="简", category="黑", weight=400, source="recommend")
    c.similarity, c.size_ratio, c.n_chars = sim, size, 2
    return c


def test_threshold_change_regates_listed_candidates(qapp, tmp_path, monkeypatch):
    """调高门槛后已列出的低分候选必须重筛（用户报告 2026-10-31：调门槛
    点开始寻找，列出的字体不符新门槛——「候选保留」把旧搜索低分行留在
    卡上，被读作门槛没生效）。四关逐次钉死：相似率（含 ==0.99 边界
    放行，同 _gate 的 < 出局）→ 相对大小区间 → 覆盖率。"""
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.settings.set("size_max", 2.0)             # 先放宽尺寸档：重筛四关
    card.search_state = "exhausted"                # 同发，各步只踩中一关
    page.vm.add_candidates(card, [_cand("fpB", "乙体", 0.995),
                                  _cand("fpE", "戊字体", 0.99),
                                  _cand("fpX", "丙字体", 0.82),
                                  _cand("fpS", "丁字体", 0.995, size=1.3),
                                  _cand("fpC", "己字体", 0.995, cov=0.96)])
    assert {o.key for o in card.options if o.source == "search"} == \
        {"fpB", "fpE", "fpX", "fpS", "fpC"}        # 前置：五行都在
    page.settings.set("min_sim", 0.99)             # 门槛变化 → 重筛
    assert {o.key for o in card.options if o.source == "search"} == \
        {"fpB", "fpE", "fpS", "fpC"}               # fpX 0.82 出局；fpE 压线留
    page.settings.set("size_max", 1.0)
    assert {o.key for o in card.options if o.source == "search"} == \
        {"fpB", "fpE", "fpC"}                      # fpS 尺寸 1.3 出局
    page.settings.set("cov_threshold", 0.99)
    assert {o.key for o in card.options if o.source == "search"} == \
        {"fpB", "fpE"}                             # fpC 覆盖率 0.96 出局


def test_threshold_change_regates_no_target_rows_by_coverage(
        qapp, tmp_path, monkeypatch):
    # 缺字卡无 target 搜索行（similarity=None）只受覆盖率管辖（同
    # FontSearcher._evaluate 无 target 路径口径）；调高覆盖率门槛照筛。
    # coverage=None 的搜索行（KEEP 行默认 source="search"）兜底按 1.0
    # 放行，不误伤
    from ass_font_substitute.gui.subs_vm import OptionItem
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.vm.add_candidates(card, [_cand("fpN", "无靶体", None, cov=0.5)])
    keep_row = OptionItem("fpK", "保留原字体名", source="search")
    card.options.append(keep_row)                  # 直造 KEEP 形态：无 coverage
    page.settings.set("cov_threshold", 0.99)
    assert card.option("fpN") is None              # 覆盖率 0.5 出局
    assert card.option("fpK") is keep_row          # None 兜底放行


def test_threshold_change_regate_exempts_checked_row(qapp, tmp_path,
                                                     monkeypatch):
    # 被勾选行豁免重筛（用户拍板 2026-10-31：选中的候选不移除——用户
    # 已明确选择，门槛无权撤销）；未勾选不达标行照筛
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.vm.add_candidates(card, [_cand("fpB", "乙体", 0.7),
                                  _cand("fpX", "丙字体", 0.82)])
    card.checked_key = "fpB"
    page.settings.set("min_sim", 0.99)
    assert card.option("fpB") is not None        # 勾选行豁免
    assert card.checked_key == "fpB"
    assert card.option("fpX") is None            # 未勾选低分行出局


def test_threshold_change_regate_exempts_checked_other_gates(
        qapp, tmp_path, monkeypatch):
    # 豁免钉全三关：勾选行撞覆盖率关、撞大小区间关均存活；勾选 KEEP
    # 行（checked_key == "keep"，KEEP 行 source="search" 且 coverage/
    # size_ratio 为 None）也豁免——顺带钉死 None→1.0 兜底不再误杀勾选
    # KEEP 行的旧边界
    from ass_font_substitute.gui.subs_vm import OptionItem
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.settings.set("size_max", 2.0)             # 先放宽尺寸档：各步只踩中一关
    page.vm.add_candidates(card, [_cand("fpC", "己字体", 0.995, cov=0.96),
                                  _cand("fpS", "丁字体", 0.995, size=1.3),
                                  _cand("fpX", "丙字体", 0.82)])
    keep_row = OptionItem("keep", "甲字体", source="search")
    card.options.append(keep_row)
    card.checked_key = "fpC"
    page.settings.set("cov_threshold", 0.99)
    assert card.option("fpC") is not None        # 勾选行撞覆盖率关豁免
    assert card.option("fpX") is None            # 未勾选低分行出局
    card.checked_key = "fpS"
    page.settings.set("size_max", 1.0)
    assert card.option("fpS") is not None        # 勾选行撞尺寸关豁免
    assert card.checked_key == "fpS"             # 勾选键不回落
    card.checked_key = "keep"
    page.settings.set("min_sim", 0.99)           # 第三关触发，KEEP 行在场
    assert card.option("keep") is keep_row       # 勾选 KEEP 行豁免


def test_make_card_auto_adds_last_used_row(qapp, tmp_path, monkeypatch):
    """上次选择自动加行（用户需求 2026-10-31，同孪生自动加行同理）：
    决策库有该名+方向记录、记的是字体（keep=False）、指纹在库且不在
    选项中 → 造「上次使用」行插原字体行之后；apply_default_check 既有
    回放链会自动勾上它。已在选项中的指纹不重复加行（回放只挂徽标）；
    keep=True（上次选了保留原名）不加行；指纹已不在库（重扫剔除）不
    加行。"""
    from ass_font_substitute.core.decision.store import record_decision
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    ent_l = _ent("fpL", "上次体")
    page.vm.set_entities([ent_a, ent_b, ent_l])

    record_decision(Path(page.vm.decisions_db), "甲字体", "t2s",
                    "fpL", False)
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    o = card.option("fpL")
    assert o is not None and o.source == "last"
    assert "上次使用" in o.badges
    assert card.checked_key == "fpL"           # 回放链自动勾上次使用
    keys = [x.key for x in card.options]
    assert keys.index("fp1") < keys.index("fpL") < keys.index("fp2")

    record_decision(Path(page.vm.decisions_db), "甲字体", "t2s",
                    "fp2", False)
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    assert sum(1 for x in card.options if x.key == "fp2") == 1   # 不重复加行
    assert "上次使用" in card.option("fp2").badges                # 回放挂徽标
    assert card.checked_key == "fp2"

    record_decision(Path(page.vm.decisions_db), "甲字体", "t2s",
                    "fpL", True)
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    assert card.option("fpL") is None          # keep=True 不加行

    record_decision(Path(page.vm.decisions_db), "甲字体", "t2s",
                    "fpGone", False)
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    assert card.option("fpGone") is None       # 指纹不在库不加行
    assert card.checked_key == "fp1"           # 回放不中，落原字体默认链


def test_last_used_row_not_search_target_on_pure_twin_card(
        qapp, tmp_path, monkeypatch):
    # 纯孪生卡（原字体不在库）旧语义 target=None 搜穿整池；上次使用
    # 自动加行不得使 target 漂移为上次使用实体（审查 P1 实锤：白得
    # 门槛/暂停语义，渲染失败还会整卡 target_error）
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.decision.store import record_decision
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_t, ent_l = _ent("fpT", "乙字体"), _ent("fpL", "上次体")
    page.vm.set_entities([ent_t, ent_l])
    record_decision(Path(page.vm.decisions_db), "甲字体", "t2s",
                    "fpL", False)
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=2,
                      inline_count=5, chars={"永"}, variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    cls = UsageClass(kind="twin", entities=[ent_t], coverage=1.0,
                     missing_chars=[], twin_hit=ent_t)
    page._on_result(f"classify:{page._gen}:0", (usage, cls))
    card = page.vm.cards[0]
    assert card.option("fpL") is not None       # 上次使用行已自动加
    assert page.vm._search_target(card) is None  # 但不作搜索基准


def test_finish_search_dirty_card_regates_candidates(qapp, tmp_path,
                                                     monkeypatch):
    # 在跑中改门槛（threshold_dirty）：停时归 idle 且候选按新门槛重筛
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.vm.add_candidates(card, [_cand("fpB", "乙体", 0.995),
                                  _cand("fpX", "丙字体", 0.82)])
    card.searching = True
    card.threshold_dirty = True
    searcher = SimpleNamespace(paused=False, target_error=False)
    page.vm.finish_search(card, searcher)
    assert card.search_state == "idle" and card.searcher is None
    assert {o.key for o in card.options if o.source == "search"} == {"fpB"}


def test_finish_search_target_error_dirty_still_regates(qapp, tmp_path,
                                                        monkeypatch):
    # 在跑中改门槛 + 原字体渲染失败停搜：态保留 target_error（钮语义
    # 与门槛无关，拍板 2026-10-30），但已列候选仍按新门槛补筛——否则
    # 旧低分行挂卡，与归 idle 路径同一「门槛没生效」观感
    page = _make_page(qapp, tmp_path, monkeypatch)
    ent_a, ent_b = _ent("fp1", "甲字体"), _ent("fp2", "乙字体")
    _usage, card, _norm = _feed_replace_card(page, ent_a, ent_b)
    page.vm.add_candidates(card, [_cand("fpB", "乙体", 0.995),
                                  _cand("fpX", "丙字体", 0.82)])
    card.searching = True
    card.threshold_dirty = True
    searcher = SimpleNamespace(paused=False, target_error=True)
    page.vm.finish_search(card, searcher)
    assert card.search_state == "target_error"
    assert {o.key for o in card.options if o.source == "search"} == {"fpB"}


def test_sync_rows_removes_dropped_options(qapp):
    """行集 reconcile 必须支持删行：门槛重筛（efd6ad9）从 vm 卡移除不
    达标候选后，apply() 曾只增改不删——UI 行永不消失，用户读作重筛没
    生效。删行需撤该行预览条在途渲染（teardown 同款口径）。"""
    from ass_font_substitute.gui.subs_vm import Card
    from ass_font_substitute.gui.subs_vm import OptionItem
    from ass_font_substitute.gui.widgets.font_card import FontCard

    c = Card(norm_name="a", raw_name="a", kind="replace", n_chars=1,
             options=[OptionItem(key="o1", display_name="甲"),
                      OptionItem(key="o2", display_name="乙"),
                      OptionItem(key="o3", display_name="丙")],
             checked_key="o1")
    fc = FontCard(c, None, None, lambda: "")
    fc.show()
    qapp.processEvents()
    assert set(fc._rows) == {"o1", "o2", "o3"}

    c.options = [o for o in c.options if o.key != "o2"]
    fc.apply()
    qapp.processEvents()
    assert set(fc._rows) == {"o1", "o3"}
    assert set(fc._metrics) == {"o1", "o3"}
    assert set(fc._boxes) == {"o1", "o3"}
    # 布局里行真的没了（不是藏起来）
    laid = [fc._opt_layout.itemAt(i).widget() for i in range(fc._opt_layout.count())]
    assert all(w is None or w not in [fc._rows.get(k) for k in ("o2",)] for w in laid)
    assert fc._opt_layout.count() == 2
    # 重复 apply（幂等）与删除后再加回
    fc.apply()
    fc.apply()
    assert set(fc._rows) == {"o1", "o3"}
    c.options.append(OptionItem(key="o2", display_name="乙"))
    fc.apply()
    assert set(fc._rows) == {"o1", "o2", "o3"}
    fc.deleteLater()


def test_sync_rows_removal_cancels_preview_bar(qapp):
    # 被删行的预览条撤在途渲染单（teardown 同款），并从簿记中清出
    from ass_font_substitute.gui.subs_vm import Card
    from ass_font_substitute.gui.subs_vm import OptionItem
    from ass_font_substitute.gui.widgets.font_card import FontCard
    from tests.test_gui_subs_page import _ent

    ent = _ent("fp1", "甲字体")
    c = Card(norm_name="a", raw_name="a", kind="replace", n_chars=1,
             options=[OptionItem(key="o1", display_name="甲", entity=ent),
                      OptionItem(key="o2", display_name="乙", entity=ent)],
             checked_key="o1")
    class _W(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def submit(self, *a, **k):
            return SimpleNamespace(cancelled=False)

    pvm = SimpleNamespace(mono_cells=lambda: False, get_tiles=lambda k: None)
    fc = FontCard(c, _W(), pvm, lambda: "")
    fc.show()
    qapp.processEvents()
    assert set(fc._bar_by_key) == {"o1", "o2"}
    c.options = [o for o in c.options if o.key != "o2"]
    fc.apply()
    qapp.processEvents()
    assert set(fc._bar_by_key) == {"o1"}
    assert all(b.key != "o2" if hasattr(b, "key") else True for b in fc._bars)
    fc.deleteLater()


def test_psname_subline_on_same_name_collision(qapp):
    """同名碰撞行显 PS 名副行（用户 2026-10-31：台版/港版同中文名不同
    PS 名——去重是错案已 reset，真不同字体须 UI 区分）；无碰撞行零噪音；
    碰撞消解（同名候选被删）副行随 apply 隐藏。"""
    from ass_font_substitute.gui.subs_vm import Card
    from ass_font_substitute.gui.subs_vm import OptionItem
    from ass_font_substitute.gui.widgets.font_card import FontCard
    from tests.test_gui_subs_page import _ent

    e1 = _ent("fp1", "華康中特圓體")
    e1 = e1.__class__(**{**e1.__dict__, "psname": "DFPNYuanMXBold-B5"})
    e2 = _ent("fp2", "華康中特圓體")
    e2 = e2.__class__(**{**e2.__dict__, "psname": "DFPNYuanMXBold-B5.HK"})
    e3 = _ent("fp3", "別の字体")
    c = Card(norm_name="a", raw_name="a", kind="replace", n_chars=1,
             options=[OptionItem(key="o1", display_name="華康中特圓體",
                                 entity=e1),
                      OptionItem(key="o2", display_name="華康中特圓體",
                                 entity=e2),
                      OptionItem(key="o3", display_name="別の字体",
                                 entity=e3)],
             checked_key="o1")

    class _W(QObject):
        result = Signal(object, object)
        failed = Signal(object, str)

        def submit(self, *a, **k):
            return SimpleNamespace(cancelled=False)

    pvm = SimpleNamespace(mono_cells=lambda: False, get_tiles=lambda k: None)
    # 回归守卫：无父状态 setVisible(True) 会把控件抬成顶层窗口（Windows
    # 空标题回退显 exe 名「python」，加载碰撞行时闪空白窗）。注意
    # addWidget 进未安装布局不挂父——挂父发生在布局装到已 realizes 的
    # 祖先时，ps_lab 曾栽在这（2026-11-01）
    from PySide6.QtWidgets import QWidget
    stray: list[QWidget] = []
    _orig_vis = QWidget.setVisible

    def _spy(self, v):
        if v and self.parent() is None and self.isWindow():
            stray.append(self)
        return _orig_vis(self, v)

    QWidget.setVisible = _spy
    try:
        fc = FontCard(c, _W(), pvm, lambda: "")
    finally:
        QWidget.setVisible = _orig_vis
    assert not stray
    fc.show()
    qapp.processEvents()

    from PySide6.QtWidgets import QLabel

    def ps_labels(key):
        return [x for x in fc._rows[key].findChildren(QLabel)
                if x.objectName() == "psname"]

    ps1, ps2, ps3 = (ps_labels(k) for k in ("o1", "o2", "o3"))
    assert len(ps1) == len(ps2) == len(ps3) == 1
    # 断言走 fullText()（elide 前）：offscreen 字体下 text() 已是省略号截断
    assert ps1[0].isVisible()
    assert ps1[0].fullText() == "PostScript 名：DFPNYuanMXBold-B5"
    assert ps2[0].isVisible()
    assert ps2[0].fullText() == "PostScript 名：DFPNYuanMXBold-B5.HK"
    assert not ps3[0].isVisible()                        # 无碰撞零噪音
    # 碰撞消解：同名候选删除 → 剩行副行隐藏（apply 就地更新，不重建）
    c.options = [o for o in c.options if o.key != "o2"]
    fc.apply()
    qapp.processEvents()
    assert not ps1[0].isVisible()
    # 碰撞重现：同名行回卡 → 副行重显
    c.options.append(OptionItem(key="o2", display_name="華康中特圓體",
                                entity=e2))
    fc.apply()
    qapp.processEvents()
    assert ps1[0].isVisible()
    fc.deleteLater()


def test_embedded_metrics_and_keep_title(qapp, tmp_path, monkeypatch):
    # 内嵌标注上 UI：keep 卡原字体行第二行尾「· 有内嵌」；missing 卡
    # 保留行标题「保留原字体名（有内嵌）」
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel

    ent = _ent("fp1", "甲字体")
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set("永宇"), variants={"甲字体"},
                      embedded=True)
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="keep", entities=[ent], coverage=1.0)))
    card = page.vm.cards[0]
    assert card.embedded is True
    fc = page._cards_ui["甲字体"]
    row = fc._rows["fp1"]
    fulls = [lab.fullText() for lab in row.findChildren(_MetricsLabel)]
    assert any(t.endswith("· 有内嵌") for t in fulls), fulls

    # missing 卡：保留行标题带（有内嵌）
    m_usage = FontUsage(raw_name="乙字体", norm_name="乙字体", style_count=1,
                        inline_count=0, chars=set("永宇"), variants={"乙字体"},
                        embedded=True)
    page._on_result("parse:q.ass", FileParse("q.ass", None, [m_usage], [], []))
    page._on_result(f"classify:{page._gen}:1",
                    (m_usage, UsageClass(kind="missing", entities=[], coverage=0.0,
                                         missing_chars=["永"])))
    mfc = page._cards_ui["乙字体"]
    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel
    from ass_font_substitute.gui.widgets.font_card import _NameLabel
    names = [lab.fullText() for lab in mfc.findChildren(_NameLabel)]
    assert any("保留原字体名（有内嵌）" in t for t in names), names
    # 第二行不与标题矛盾（审查 P1）：内嵌字体播放器直接可用，无回退
    metrics = [lab.fullText() for lab in mfc.findChildren(_MetricsLabel)]
    assert any(t == "已内嵌，播放器直接可用" for t in metrics), metrics
    page.deleteLater()


def test_direction_default_follows_language(qapp, tmp_path, monkeypatch):
    # 拍板 2026-11-01：孪生检测方向默认随界面语言——简体界面繁转简、
    # 繁体界面简转繁（文案「简 → 繁」）
    from ass_font_substitute.gui.settings_store import Settings

    monkeypatch.setenv("AFS_DATA_DIR", str(tmp_path))
    from ass_font_substitute.gui.pages.subs_page import SubsPage

    s_hans = Settings(tmp_path / "a.json")
    page = SubsPage(s_hans, _FakeWorker())
    assert page._direction == "t2s"
    assert page.seg_dir.currentRouteKey() == "t2s"
    assert "简 → 繁" in [page.seg_dir.items[k].text()
                         for k in page.seg_dir.items]
    page.deleteLater()

    s_hant = Settings(tmp_path / "b.json")
    s_hant.set("language", "zh-Hant")
    page2 = SubsPage(s_hant, _FakeWorker())
    assert page2._direction == "s2t"
    assert page2.seg_dir.currentRouteKey() == "s2t"
    page2.deleteLater()
