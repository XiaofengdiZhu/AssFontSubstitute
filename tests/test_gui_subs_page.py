"""处理字幕页功能冒烟（offscreen）：假 worker 不起线程，parse/classify/
searcher/pull/apply 载荷经 _on_result 直喂，走真实 vm + UI 装配路径。"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
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
        self.submits.append((tag, fn, args, kw))


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

    searcher = SimpleNamespace(min_sim=0.85, paused=True, target_error=False)
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
    page._on_result(f"pull:{norm}", (cands, "exhausted"))   # 渐进回填后耗尽
    assert card.search_state == "exhausted" and len(card.options) == 4
    assert page.worker.submits[-1][0] != f"pull:{norm}"      # 不再续拉

    # 耗尽后调低门槛再搜：新门槛建新搜索器（relax 钮已删，直设等价）
    card.min_sim = 0.75
    page._continue_search(norm)
    sub = [s for s in page.worker.submits if s[0] == f"newsearcher:{norm}"][-1]
    assert sub[2][4] == 0.75                         # 新门槛建新搜索器
    page._on_result(f"searcher:{norm}",
                    SimpleNamespace(min_sim=0.75, paused=False,
                                    target_error=False))
    page._on_result(f"pull:{norm}", ([], "cancelled"))   # 重算弃旧拉取不挂死
    assert not card.searching


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
    page.preview_edit.setText("永宇")               # 防抖后整卡重建
    page._preview_debounce.timeout.emit()
    assert page._cards_ui["甲字体"] is not old       # 旧卡弃置、新卡持同一 vm 卡
    assert page._cards_ui["甲字体"].card is page.vm.cards[0]

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
