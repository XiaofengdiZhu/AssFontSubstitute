"""字体选择弹窗：pickreco 取消通路（弹窗关闭即置标志，任务提前退出）。"""
import os

import pytest
from PySide6.QtCore import QObject
from PySide6.QtCore import Signal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("qfluentwidgets")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeWorker(QObject):
    result = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        self.submits.append((tag, fn, args, kw))


def _ent(fp, name, category="黑", chars="永宇", weight=400, italic=False):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=name, aliases=(name,),
                      psname=name, version=(1,), weight=weight, width=5,
                      italic=italic, script="简", category=category,
                      cmap=CmapRanges.from_chars([ord(c) for c in chars]),
                      dir_path="d", rel_path="r.ttf", face_index=0)


def test_picker_close_cancels_reco_and_none_payload_hides_rec(qapp, tmp_path):
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    ents = [_ent("fp1", "甲字体"), _ent("fp2", "乙字体")]
    host = QWidget()
    host.resize(1100, 750)
    dlg = FontPickerDialog(s, w, ents, chars=set("永宇"), parent=host)

    # 提交带了取消标志字典（列表里混有预览渲染的 plist 任务，按 tag 找）
    reco = next(t for t in w.submits if t[0] == "pickreco")
    cancel = reco[2][3]
    assert cancel == {"cancelled": False}

    # 关闭（done→finished）置标志
    dlg.finished.emit(0)
    assert cancel["cancelled"] is True

    # 取消/空载荷的回执：解订阅、不显示推荐区
    dlg._on_reco("pickreco", None)
    assert dlg._reco is None
    assert not dlg.rec_title.isVisible()


def test_double_click_accepts_and_closes(qapp, tmp_path, monkeypatch):
    # 双击=快捷接受：置 selected 并调 accept（qfw done 走淡出动画，
    # result 非同步可断，钉 accept 调用本身；真关闭链路由实机手测覆盖）
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    ents = [_ent("fp1", "甲字体"), _ent("fp2", "乙字体")]
    host = QWidget()
    dlg = FontPickerDialog(s, w, ents, parent=host)
    calls = []
    monkeypatch.setattr(dlg, "accept", lambda: calls.append(1))
    dlg.all_list.detail_requested.emit(ents[1])
    assert dlg.selected.fingerprint == "fp2"
    assert calls == [1]


def test_picker_row_names_not_selectable(qapp, tmp_path):
    # 守卫（审查 P0-1）：选择对话框明确除外划选——共享 _RowWidget 曾把
    # 可选化击穿进 picker，名是行宽最大的点击目标，可选化吃掉单击选中/
    # 双击接受。行名标签必须无 Mouse 可选位、无 IBeam
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    ents = [_ent("fp1", "甲字体"), _ent("fp2", "乙字体")]
    host = QWidget()
    dlg = FontPickerDialog(s, w, ents, parent=host)
    assert dlg.all_list._rows                     # 构造即填行
    dlg.all_list.resize(300, 200)
    qapp.processEvents()
    # 物化首行探针
    item = dlg.all_list.item(0)
    assert item is not None
    wid = dlg.all_list.itemWidget(item)
    assert wid is not None, "首行未物化（可视区外）"
    sel = Qt.TextInteractionFlag.TextSelectableByMouse
    name_labs = [x for x in wid.findChildren(QLabel)
                 if x.text() in {"甲字体", "乙字体"}]
    assert name_labs, "行名标签未找到"
    for lab in name_labs:
        assert not lab.textInteractionFlags() & sel
        assert lab.cursor().shape() != Qt.CursorShape.IBeamCursor


def test_picker_preview_tools_and_filter_bar(qapp, tmp_path):
    # 照预览页重做（2026-10-29）：预览工具组与筛选行是 font_filter 共享
    # 控件，行为口径与字体库预览页同源——预览文字进渲染通路（空退字体
    # 名）、覆盖筛选写 require_text、等宽开关落 settings 双向同步、搜索
    # 防抖重载、下拉即时筛选、计数随 all_list
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    # 乙字体 cmap 不含「永」：覆盖筛选钉行数变化用
    ents = [_ent("fp1", "甲字体"),
            _ent("fp2", "乙字体", category="宋", chars="宇")]
    host = QWidget()
    host.resize(780, 560)
    dlg = FontPickerDialog(s, w, ents, parent=host)
    assert dlg.empty_label.isHidden()       # 非空态：空库提示保持隐藏

    # 预览文字：空=行退字体名（FontListWidget 兜底），非空进渲染通路
    assert dlg._preview() == ""
    dlg.tools.preview_edit.setText("永宇")
    assert dlg._preview() == "永宇"

    # 覆盖筛选：开=写 require_text 且行随之收窄（乙字体缺「永」被筛掉），
    # 关=清且行回全量
    dlg.tools.btn_cov.setChecked(True)
    assert dlg.pvm.require_text == "永宇"
    assert len(dlg.all_list._rows) == 1
    dlg.tools.btn_cov.setChecked(False)
    assert dlg.pvm.require_text is None
    assert len(dlg.all_list._rows) == 2

    # 等宽开关：本页翻转落设置；他处改设置回开关联动
    dlg.tools.sw_mono.setChecked(True)
    assert s.get("preview_mono") is True
    s.set("preview_mono", False)
    assert not dlg.tools.sw_mono.isChecked()

    # 搜索防抖重载 + 计数随 all_list
    dlg.bar.name_edit.setText("甲")
    QTest.qWait(400)
    assert dlg.pvm.name_query == "甲"
    assert len(dlg.all_list._rows) == 1
    assert dlg.bar.count_label.text() == "1 款字体"

    # 下拉筛选：写 pvm + 即时重载（不经防抖）
    dlg.bar.name_edit.setText("")
    QTest.qWait(400)
    dlg.bar.cb_cat.setCurrentIndex(2)       # 宋（0=全部、1=黑）
    assert dlg.pvm.category == "宋"
    assert len(dlg.all_list._rows) == 1
    assert dlg.bar.count_label.text() == "1 款字体"
    dlg.bar.cb_cat.setCurrentIndex(0)
    assert len(dlg.all_list._rows) == 2
    dlg.deleteLater()


def test_picker_empty_library_hides_tools_and_bar(qapp, tmp_path):
    # 空库态：工具组/筛选行与列表一起全隐，只剩标题+提示；构造期零顶层
    # Show（旧码挂父前 empty_label.show() 曾成顶层窗口，审查二轮 P1）
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    host = QWidget()            # 持引用：临时对象被回收会连坐销毁弹窗内容

    class Spy(QObject):
        def __init__(self):
            super().__init__()
            self.flashed = []

        def eventFilter(self, obj, ev):
            if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and obj.isWindow() and obj is not host):
                self.flashed.append(obj)
            return False

    spy = Spy()
    qapp.installEventFilter(spy)
    try:
        dlg = FontPickerDialog(s, _FakeWorker(), [], parent=host)
    finally:
        qapp.removeEventFilter(spy)
    assert dlg.tools.isHidden()
    assert dlg.bar.isHidden()
    assert dlg.all_list.isHidden()
    assert dlg.hint.isHidden()
    assert not dlg.empty_label.isHidden()
    assert spy.flashed == []        # 零顶层 Show：无孤儿控件闪窗

    # 呈现路径实测：宿主上屏后提示真正可见（isHidden 只钉显隐标志不证
    # 可见；qfw MessageBoxBase 被 FramelessWindowHint 抹掉窗口类型位，
    # 本体是宿主内嵌 overlay 而非独立窗，须挂的宿主可见才 isVisible）
    host.show()
    qapp.processEvents()
    assert dlg.isVisible()
    assert dlg.empty_label.isVisible()
    assert not dlg.empty_label.grab().isNull()
    dlg.deleteLater()
    host.deleteLater()


def test_picker_hides_weight_filter_and_face_controls(qapp, tmp_path):
    # 拍板 2026-10-30：工具不改字重斜体——筛选行不建字重下拉，行内不建
    # 字重分段/斜体钮；多 face 行只看 sel_key 选出的面（非斜体+400 最近）
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import FontPickerDialog

    s = Settings(tmp_path / "s.json")
    w = _FakeWorker()
    ents = [_ent("fp300", "甲字体", weight=300),
            _ent("fp400", "甲字体"),
            _ent("fp400i", "甲字体", italic=True),
            _ent("fp2", "乙字体")]
    host = QWidget()
    host.resize(780, 560)
    dlg = FontPickerDialog(s, w, ents, parent=host)
    assert dlg.bar.cb_weight.parent() is None   # 字重下拉未建 _fld
    assert dlg.all_list._face_controls is False
    assert dlg.rec_list._face_controls is False
    # 甲字体行：3 face 合一行，selected = 非斜体 400
    row = next(r for r in dlg.all_list._rows if r.display_name == "甲字体")
    assert row.selected.fingerprint == "fp400"
    assert len(row.faces) == 3
    # 钉序：无筛选档斜体优先于字重（正体 300 胜斜体 400，与 subs_vm
    # _orig_face_key 同序）——pvm 层直接验证，不起第二个对话框
    # （同进程双对话框会弄脏后续测试的 SVG 绘制态，offscreen 必崩）
    dlg.pvm.set_entities([_ent("up300", "丙字体", weight=300),
                          _ent("it400", "丙字体", italic=True)])
    rows = dlg.pvm.filtered()
    assert len(rows) == 1 and rows[0].selected.fingerprint == "up300"
    dlg.deleteLater()


def test_reco_job_executes_pull_loop(qapp, tmp_path):
    # 审查二轮 P0-1 回归：_reco_job 必须真执行——曾传已删的 chunk 参数
    # （60cd763 重构漏改 picker），worker 内必 TypeError 且 pickreco 不在
    # failed 白名单，推荐区永久静默失效；旧测试只测回执 plumbing 故没红。
    # target=None 走纯元数据串行路径，无需进程池
    from types import SimpleNamespace

    from ass_font_substitute.core.fontindex.library import LibraryIndex
    from ass_font_substitute.core.fontindex.store import open_db
    from ass_font_substitute.core.fontindex.store import update_index
    from ass_font_substitute.core.similarity.cache import Cache
    from ass_font_substitute.gui.settings_store import Settings
    from ass_font_substitute.gui.widgets.font_picker import _reco_job
    from tests.fonts import build_font

    d = tmp_path / "lib"
    d.mkdir()
    build_font(d / "cand.ttf", chars="永中木水火土石田8Aa0?",
               family="候選圓", psname="CAND")
    build_font(d / "far.ttf", chars="8Aa0?", family="黑體遠", psname="FAR")
    conn = open_db(d / "fontindex.db")
    update_index(d, conn)
    conn.close()
    lib = LibraryIndex.open([d])
    s = Settings(tmp_path / "s.json")
    ctx = SimpleNamespace(lib=lib, cache=Cache(tmp_path / "cache.db"),
                          settings=s)
    out = _reco_job(ctx, set("永中"), 0.8, "t2s")
    assert [e.psname for e in out] == ["CAND"]   # 低覆盖 far 不达标

    # 弹窗已关：取消标志即刻生效，返回 None 不白算
    out2 = _reco_job(ctx, set("永中"), 0.8, "t2s",
                     cancel={"cancelled": True})
    assert out2 is None
