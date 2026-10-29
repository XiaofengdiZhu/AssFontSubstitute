"""处理字幕页功能冒烟（offscreen）：假 worker 不起线程，parse/classify/
searcher/pull/apply 载荷经 _on_result 直喂，走真实 vm + UI 装配路径。"""
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
    monkeypatch.setenv("APPDATA", str(tmp_path))    # 决策库/设置隔离到 tmp
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

    # 耗尽后调低门槛再搜：新门槛建新搜索器（relax 钮已删，直设等价）
    card.min_sim = 0.75
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
    assert not [s for s in page.worker.submits if s[0] == "apply"]
    page._apply()                                   # 再击确认 → 派工
    assert page.worker.submits[-1][0] == "apply"
    path, mapping, suffix, overwrite = page.worker.submits[-1][2]
    assert path == str(src) and mapping == {"甲字体": "乙字体"}
    assert suffix == ".substituted.ass" and overwrite is False

    page._on_result("apply", ApplyFileResult(str(src), str(out), 1))
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
    assert len([s for s in page.worker.submits if s[0] == "apply"]) == 1
    assert page._apply_dispatching is True
    assert not page.btn_apply.isEnabled()           # 派工期禁用：防连点重复写
    page._apply()                                   # 在途重复点击：忽略
    assert len([s for s in page.worker.submits if s[0] == "apply"]) == 1

    page._on_result("apply", ApplyFileResult(str(src),
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
    # 回归：重新载入重解析后不得恢复被用户取消勾选的文件（否则
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
    page._on_result("parse:p.ass", parse_payload("p.ass"))   # 重载重解析
    assert [f.checked for f in page.vm.files if f.path == "p.ass"] == [False]


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

    page._on_failed("apply", "boom")
    before = len(page.vm.files)                    # record_all 不该被迟到回执触发
    page._on_apply_done(ApplyFileResult("p.ass", ".substituted.ass", None))
    assert page._apply_results == []
    assert page._apply_total == 0
    assert len(page.vm.files) == before


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


def test_twin_metrics_lead_phrase(qapp, tmp_path, monkeypatch):
    # 孪生行次序（用户拍板 2026-10-29）：行首注明来由，后接覆盖/未覆盖；
    # 孪生行无相似率/相对大小
    page = _make_page(qapp, tmp_path, monkeypatch)
    from ass_font_substitute.core.coverage.matcher import UsageClass
    from ass_font_substitute.core.models import FontUsage
    from ass_font_substitute.gui.jobs import FileParse
    from ass_font_substitute.gui.widgets.font_card import _MetricsLabel
    ent_a = _ent("fp1", "甲字体")
    ent_twin = _ent("fp-twin", "甲字体简", chars="永宇好")
    usage = FontUsage(raw_name="甲字体", norm_name="甲字体", style_count=1,
                      inline_count=1, chars=set("永宇好我在"), variants={"甲字体"})
    page._on_result("parse:p.ass", FileParse("p.ass", None, [usage], [], []))
    page._on_result(f"classify:{page._gen}:0",
                    (usage, UsageClass(kind="twin", entities=[ent_a],
                                       twin_hit=ent_twin)))
    card = page.vm.cards[0]
    assert card.checked_key == "fp-twin"          # 默认勾选链：孪生优先
    fc = page._cards_ui["甲字体"]
    row = fc._rows["fp-twin"]                 # 勾选框 reparent 后取行走 _rows
    fulls = [lab.fullText() for lab in row.findChildren(_MetricsLabel)]
    assert any(t == "原字体名称对应的简体名称 · 覆盖 60.0% · 未覆盖 2 字"
               for t in fulls), fulls


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
