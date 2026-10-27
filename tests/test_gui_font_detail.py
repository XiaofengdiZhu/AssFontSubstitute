"""字体详情对话框：命名记录分组/语言标注、同文件/同字体系、嵌套双击、
v8 同步命名装载（零闪烁）与失败回退。"""
import os

import pytest
from PySide6.QtCore import QObject
from PySide6.QtCore import Qt
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
    failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.submits = []

    def submit(self, tag, fn, *args, **kw):
        from types import SimpleNamespace
        handle = SimpleNamespace(cancelled=False)
        self.submits.append((tag, fn, args, handle))
        return handle


def _ent(fp, name, *, display=None, aliases=None, family_names=(),
         weight=400, italic=False,
         dir_path="d", rel_path="r.ttf", face_index=0, chars="永",
         size=0, mtime_ns=0):
    from ass_font_substitute.core.fontindex.library import FontEntity
    from ass_font_substitute.core.models import CmapRanges
    return FontEntity(fingerprint=fp, display_name=display or name,
                      aliases=aliases or (name,), psname=name, version=(1, 0),
                      family_names=family_names,
                      weight=weight, width=5, italic=italic, script="简",
                      category="黑",
                      cmap=CmapRanges.from_chars([ord(c) for c in chars]),
                      dir_path=dir_path, rel_path=rel_path,
                      face_index=face_index, size=size, mtime_ns=mtime_ns)


def _name_texts(dlg):
    """命名卡域内文本（排除标题/预览/其他卡的假阳性）。"""
    from PySide6.QtWidgets import QLabel
    return {x.text() for x in dlg._name_v.parentWidget().findChildren(QLabel)}


def _pool():
    """同文件 TTC 双 face + 同家族跨文件 + 异家族独户（a1 的主视角）。"""
    return [_ent("a1", "合并体", weight=300, rel_path="fam.ttc", face_index=0,
                 chars="爱备木"),
            _ent("a2", "合并体", weight=700, rel_path="fam.ttc", face_index=1),
            _ent("a3", "合并体", weight=900, rel_path="other/fam.otf"),
            _ent("b1", "独户", rel_path="solo.ttf")]


# ---- group_names / visible_name_rows 纯函数 ----

def test_group_names_dedup_and_order():
    from ass_font_substitute.gui.widgets.font_detail import group_names

    recs = [(1, 3, 0x0804, "微软雅黑"), (1, 3, 0x0409, "Microsoft YaHei"),
            (1, 1, 0x0000, "Microsoft YaHei"),          # Mac 同文 → 去重
            (4, 3, 0x0409, "Microsoft YaHei"),
            (4, 3, 0x0804, "微软雅黑"),
            (16, 3, 0x0409, "Microsoft YaHei UI"),      # 与 ID1 不同 → 出
            (6, 3, 0x0409, "MicrosoftYaHei")]
    grouped = dict(group_names(recs))
    # 语言序 en-US 在前、zh 随后；同文只留一条；原始 (platform, lang)
    # 留给语言筛选层
    assert grouped["字体系"] == [
        ("Microsoft YaHei", 3, 0x409), ("微软雅黑", 3, 0x804)]
    assert grouped["字体全名"] == [
        ("Microsoft YaHei", 3, 0x409), ("微软雅黑", 3, 0x804)]
    assert grouped["版式字体系"] == [("Microsoft YaHei UI", 3, 0x409)]


def test_group_names_id16_same_as_id1_dropped():
    from ass_font_substitute.gui.widgets.font_detail import group_names

    recs = [(1, 3, 0x0409, "Same"), (16, 3, 0x0409, "Same")]
    labels = [label for label, _rows in group_names(recs)]
    assert "版式字体系" not in labels


def test_visible_rows_single_lang():
    # 单语言视图（无「全部」）：各字段只显该语言、行内不重复标注、
    # 该语言无记录的字段整行隐藏
    from ass_font_substitute.gui.widgets.font_detail import visible_name_rows

    grouped = [("字体系", [("Light", 3, 0x409), ("细体", 3, 0x804),
                           ("Fein", 3, 0x407)]),
               ("样式映射字体系", [("Light", 3, 0x409), ("细体", 3, 0x804),
                                   ("Fein", 3, 0x407), ("Mager", 3, 0x41D)]),
               ("唯一字体标识符", [("X; 1.0", 3, 0x409),
                                   ("X it", 3, 0x410)])]
    de = dict(visible_name_rows(grouped, (3, 0x407)))
    assert de == {"字体系": [("Fein", "")], "样式映射字体系": [("Fein", "")]}
    # 该语言仅 ID2 有记录：其余字段行隐藏，ID2 行照常出
    assert visible_name_rows(grouped, (3, 0x41D)) == [
        ("样式映射字体系", [("Mager", "")])]


# ---- 对话框 ----

def test_dialog_cards_and_lists(qapp):
    from PySide6.QtWidgets import QLabel
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog
    from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

    pool = _pool()
    host = QWidget()            # 留引用：临时父对象被回收会连带销毁子控件
    w = _FakeWorker()
    dlg = FontDetailDialog(pool[0], pool, w, PreviewVM(), parent=host)
    fl, gl = dlg._sections
    assert fl.count() == 1 and gl.count() == 2
    assert not fl.parentWidget().isHidden()

    def row_texts(lst, i):
        return {x.text() for x in lst.itemWidget(lst.item(i)).findChildren(
            QLabel)}

    it = fl.item(0)
    assert it.data(Qt.ItemDataRole.UserRole).fingerprint == "a2"
    texts0 = row_texts(fl, 0)
    assert any("W700" in t for t in texts0)
    assert any("face 1" in t for t in texts0)   # 同文件节按 face 序号区分
    texts1 = row_texts(gl, 1)
    assert not any("fam.otf" in t for t in texts1)   # 家族节不再缀文件名
    # 覆盖单行（总 3），探针简/繁行已去；路径在文件卡
    all_texts = [x.text() for x in dlg.findChildren(QLabel)]
    assert "3" in all_texts and "字符覆盖（总）" not in all_texts
    assert any("/" in t and "fam.ttc" in t for t in all_texts)   # 完整路径
    assert dlg.cancelButton.text() == "关闭"

    # 行内预览条：每行一个、渲染任务按行派发（tag 含行 face 指纹）、
    # 样张统一用详情主 face 展示名（同文字跨 face 纯比字形）
    bars = [lst.itemWidget(lst.item(i)).bar
            for lst in (fl, gl) for i in range(lst.count())]
    assert all(isinstance(b, PreviewBar) for b in bars)
    dlist = [t for t in w.submits if t[0].startswith("dlist:")]
    assert len(dlist) == 3
    assert all(a[1] == "合并体" for _tag, _f, a, _h in dlist)
    assert len({t[0] for t in dlist}) == 3      # 各行 tag 互不相同
    # 关闭：行内在途渲染任务全部出队
    dlg.finished.emit(0)
    assert all(h.cancelled for _tag, _fn, _a, h in dlist)


def test_dialog_hides_empty_sections(qapp):
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    pool = _pool()
    host = QWidget()
    dlg = FontDetailDialog(pool[3], pool, _FakeWorker(), PreviewVM(),
                           parent=host)
    fl, gl = dlg._sections
    assert fl.parentWidget().isHidden()     # 空节整卡隐藏
    assert gl.parentWidget().isHidden()


def test_dialog_nested_double_click(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    pool = _pool()
    host = QWidget()
    dlg = FontDetailDialog(pool[0], pool, _FakeWorker(), PreviewVM(),
                           parent=host)
    opened = []
    monkeypatch.setattr(FontDetailDialog, "exec",
                        lambda self: opened.append(self) or 0)
    dlg._open_nested(dlg._sections[1].item(0))      # 家族节首条 = a2
    assert len(opened) == 1
    assert opened[0].titleLabel.text() == "合并体"
    # 嵌套与弹窗同源挂载（parentWidget=host）：parent=弹窗时关闭动画层
    # 收尾页面区闪一帧空窗（真机+开关二分实锤）；同 parent 铺满矩形
    # 天然对齐。setWindowFlags 清 Dialog 类型后 window() 不含弹窗自身
    assert opened[0].parent() is host
    assert opened[0].geometry() == dlg.geometry()   # 几何对齐弹窗防错位


def test_dialog_sync_names_fill_and_fallback(qapp, monkeypatch):
    # v8 同步装载：构造期从索引库读 name 记录，首屏即结构化命名（零闪
    # 烁，无「先兜底再覆盖」二段渲染）；读不到/失败回退别名平铺
    from PySide6.QtWidgets import QLabel
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets import font_detail as fd
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    pool = _pool()
    a1 = pool[0]
    key = (a1.dir_path, a1.rel_path, a1.face_index)
    # name_records 生产形态：RECORD_IDS 白名单无 ID6（PostScript 名走
    # fonts.psname 列，测试夹具同口径，防死路径假阳性）
    recs = [(1, 3, 0x0409, "Merge"), (1, 3, 0x0804, "合并体"),
            (2, 3, 0x0409, "Light")]
    table = {key: recs}

    def fake_load(d, r, fi):
        return table.get((d, r, fi))

    monkeypatch.setattr(fd, "load_name_records", fake_load)

    host = QWidget()
    # 有记录：结构化命名直接上屏（默认语言链简中→中文名；PostScript 名
    # 恒显 = 实体 psname 列「合并体」，不从记录取）
    dlg = FontDetailDialog(a1, pool, _FakeWorker(), PreviewVM(),
                           parent=host)
    assert "全部别名" not in {x.text() for x in dlg.findChildren(QLabel)}
    texts = _name_texts(dlg)
    assert "合并体" in texts          # 中文名 + PS 行同文（a1.psname）
    assert not dlg._lang_btn.isHidden()
    dlg.reject()

    # 无记录（库缺行/读库失败）→ 别名平铺兜底、语言钮隐藏
    table.clear()
    dlg2 = FontDetailDialog(a1, pool, _FakeWorker(), PreviewVM(),
                            parent=host)
    texts2 = {x.text() for x in dlg2.findChildren(QLabel)}
    assert "全部别名" in texts2
    assert dlg2._lang_btn.isHidden()
    dlg2.reject()


def test_human_size():
    from ass_font_substitute.gui.widgets.font_detail import _human_size

    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2.0 KB"
    assert _human_size(3 * 1024 * 1024) == "3.0 MB"


def test_lang_selector_default_chain_and_filter(qapp, monkeypatch):
    # 语言下拉无「全部」：默认按界面语言链选（简中：简→繁→英→日），
    # 记录缺该语言时顺延；单语言隐藏按钮但仍按其语言过滤
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets import font_detail as fd
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog
    from ass_font_substitute.gui.widgets.font_detail import _lang_label

    pool = _pool()
    a1 = pool[0]
    w = _FakeWorker()
    host = QWidget()
    key = (a1.dir_path, a1.rel_path, a1.face_index)
    recs = [(1, 3, 0x0409, "Merge"), (1, 3, 0x0804, "合并体"),
            (1, 3, 0x0404, "合併體"), (2, 3, 0x0407, "Fein")]
    table = {}
    monkeypatch.setattr(fd, "load_name_records",
                        lambda d, r, fi: table.get((d, r, fi)))

    dlg = FontDetailDialog(a1, pool, w, PreviewVM(), parent=host)
    assert dlg._lang_btn.isHidden()         # 兜底态无语言钮

    # 简中界面（settings 缺省按简中）：默认简中
    table[key] = recs
    dlg._apply_name_records(recs)
    assert not dlg._lang_btn.isHidden()
    assert dlg._lang_btn.text() == _lang_label(3, 0x804)
    assert dlg._lang == (3, 0x804)
    # 文案随系统 GetLocaleInfoW 输出（中文系统本地化名），钉数量与
    # 「全部语言」缺席，不钉系统文案
    menu_texts = {a.text() for _k, a in dlg._lang_actions}
    assert len(menu_texts) == 4 and "全部语言" not in menu_texts
    texts = _name_texts(dlg)
    assert "合并体" in texts and "Merge" not in texts

    # 切英语：结构化行只显该语言（PS 行恒显实体 psname「合并体」，不在断言排除列）
    dlg._set_lang((3, 0x409))
    assert dlg._lang_btn.text() == _lang_label(3, 0x409)
    texts = _name_texts(dlg)
    assert "Merge" in texts and "合併體" not in texts and "Fein" not in texts

    # 繁中界面：默认繁体
    w3 = _FakeWorker()
    dlg3 = FontDetailDialog(
        a1, pool, w3, PreviewVM(),
        settings=SimpleNamespace(get=lambda _k: "zh-Hant"), parent=host)
    dlg3._apply_name_records(recs)
    assert dlg3._lang == (3, 0x404)
    assert "合併體" in _name_texts(dlg3)

    # 链全不中（只有德语记录）：退语言集首
    w4 = _FakeWorker()
    dlg4 = FontDetailDialog(a1, pool, w4, PreviewVM(), parent=host)
    dlg4._apply_name_records([(2, 3, 0x0407, "Fein")])
    assert dlg4._lang == (3, 0x407)
    assert "Fein" in _name_texts(dlg4)

    # 单语言记录：按钮隐藏、仍按其语言过滤
    w2 = _FakeWorker()
    dlg2 = FontDetailDialog(a1, pool, w2, PreviewVM(), parent=host)
    dlg2._apply_name_records([(1, 3, 0x0409, "Only")])
    assert dlg2._lang_btn.isHidden()
    assert "Only" in _name_texts(dlg2)


def test_lang_icon_and_lcid_0c0a():
    # 语言钮带 FIF.LANGUAGE 同款图标；0x0C0A 经 babel 可解析（此前回退
    # 编码显示），静态表兜底值仍在（.黑体-日本語/微软雅黑 name 表实证
    # 出现该 LCID）
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import LCID_LANGS
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog
    from ass_font_substitute.gui.widgets.font_detail import _lang_label

    assert not _lang_label(3, 0x0C0A).startswith("语言 0x")
    assert LCID_LANGS[0x0C0A] == "西班牙语（国际排序）"
    host = QWidget()
    dlg = FontDetailDialog(_pool()[0], _pool(), _FakeWorker(), PreviewVM(),
                           parent=host)
    assert not dlg._lang_btn.icon().isNull()


def test_no_top_level_flash_on_construct(qapp):
    # 回归：同文件/同字体系两卡曾先 setVisible(True) 后 addWidget 入父
    # ——无父 setVisible 即顶层孤儿窗上屏（双击行弹详情前闪两个空白小
    # 窗，两卡内容不同恰一大一小），修复后必须先入父再 setVisible
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    pool = _pool()          # a1:同文件 1 条+同家族 2 条,两卡全非空
    host = QWidget()

    class Spy(QObject):
        def __init__(self):
            super().__init__()
            self.flashed = []

        def eventFilter(self, obj, ev):
            # 顶层 Show 且非宿主 = 有控件以独立窗口上屏
            if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                    and obj.isWindow() and obj is not host):
                self.flashed.append(obj)
            return False

    spy = Spy()
    qapp.installEventFilter(spy)
    try:
        dlg = FontDetailDialog(pool[0], pool, _FakeWorker(), PreviewVM(),
                               parent=host)
    finally:
        qapp.removeEventFilter(spy)
    assert spy.flashed == []
    # 卡显隐语义仍在：两节非空卡可见
    assert not dlg._sections[0].parentWidget().isHidden()
    assert not dlg._sections[1].parentWidget().isHidden()


def test_no_top_level_flash_on_names_fill(qapp, monkeypatch):
    # 回归：对话框可见后的命名回填/重渲染曾闪小窗，三处病根——
    # ① _swap_names 拆可见树中的旧行直接 setParent(None)，带可见标志
    #   变独立顶层窗上屏一瞬（真窗口下闪，offscreen 测不到，由实机
    #   探针背书；此处钉拆除代码必须先 setVisible(False)）
    # ② _on_names 双重渲染（_set_lang 已渲染又显式渲染一次）放大拆除
    # ③ _form/_swap_names 先 addRow 后 addLayout，无父行 reparent 期
    #   事件序错位同样上屏
    from PySide6.QtCore import QEvent
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets import font_detail as fd
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    # 钉拆除顺序：hide 必须先于 setParent(None)（真窗口下保序=不闪）。
    # 类级 patch 仅影响 Python 层调用（C++ 内部调用不走此路径），
    # monkeypatch 测试后恢复
    order = []
    orig_sp = fd.QWidget.setParent
    orig_sv = fd.QWidget.setVisible

    def sv(self, v, *a):
        if not v:
            order.append("H")
        return orig_sv(self, v, *a)

    def sp(self, *a):
        if (a and a[0] is None
                and (not order or order[-1] != "H")):
            order.append("X")              # 摘父前未见 hide
        else:
            order.append("ok")
        return orig_sp(self, *a)

    monkeypatch.setattr(fd.QWidget, "setVisible", sv)
    monkeypatch.setattr(fd.QWidget, "setParent", sp)

    pool = _pool()
    a1 = pool[0]
    w = _FakeWorker()
    host = QWidget()

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
    swaps = []
    orig_swap = fd.FontDetailDialog._swap_names
    monkeypatch.setattr(fd.FontDetailDialog, "_swap_names",
                        lambda self, rows: (swaps.append(1),
                                            orig_swap(self, rows)))
    # 注入结构化记录：钉住的是 _apply_name_records 结构化路径单渲染
    # （旧 _on_names 双渲染病根的回归钉；不注入则走兜底路径测不到）
    monkeypatch.setattr(
        fd, "load_name_records",
        lambda d, r, fi: [(1, 3, 0x0409, "Merge"), (1, 3, 0x0804, "合并体")])
    try:
        # v8 同步装载：构造内一次渲染（不再「先兜底再回填」二段）；若
        # _apply_name_records 内部双渲染（_set_lang 已渲染又显式渲染），
        # 此处即 2
        dlg = FontDetailDialog(a1, pool, w, PreviewVM(), parent=host)
        assert len(swaps) == 1
        dlg.show()                          # 卡进入可见树=拆除/重挂危险态
        dlg._set_lang((3, 0x409))              # 切语言：拆可见行再装（危险态主路径）
        assert len(swaps) == 2
        dlg.reject()
    finally:
        qapp.removeEventFilter(spy)
        monkeypatch.undo()
    # ① 摘父前必 hide，零孤儿
    assert "X" not in order
    # ② 只有对话框自身与主窗级顶层 Show，零孤儿控件
    assert [x for x in spy.flashed if x is not dlg] == []


def test_dialog_title_uses_lang_chain(qapp):
    # 详情标题/预览文字/列表行名按界面语言链现挑（v7 语言记录入库）
    from dataclasses import replace
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    from ass_font_substitute.gui.preview_vm import PreviewVM
    from ass_font_substitute.gui.widgets.font_detail import FontDetailDialog

    e = replace(_ent("a1", "合并体"),
                name_langs={1: [(0x409, "Merged"), (0x804, "合并简"),
                                (0x404, "合併繁")]})
    host = QWidget()            # 留引用：临时父对象被回收会连带销毁子控件
    w = _FakeWorker()
    dlg = FontDetailDialog(e, [e], w, PreviewVM(), parent=host)
    assert dlg.titleLabel.text() == "合并简"
    dlg2 = FontDetailDialog(
        e, [e], w, PreviewVM(),
        settings=SimpleNamespace(get=lambda _k: "zh-Hant"), parent=host)
    assert dlg2.titleLabel.text() == "合併繁"
    dlg.reject()
    dlg2.reject()
