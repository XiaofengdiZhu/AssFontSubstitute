from __future__ import annotations

import functools
import sys
from datetime import datetime

from babel import Locale
from PySide6.QtCore import QSize
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QListWidget
from PySide6.QtWidgets import QListWidgetItem
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import Action
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import FluentIcon
from qfluentwidgets import MessageBoxBase
from qfluentwidgets import RoundMenu
from qfluentwidgets import ScrollArea
from qfluentwidgets import SimpleCardWidget
from qfluentwidgets import StrongBodyLabel
from qfluentwidgets import SubtitleLabel
from qfluentwidgets import TransparentDropDownPushButton

from ass_font_substitute.core.fontindex.library import FontEntity
from ass_font_substitute.core.fontindex.library import load_name_records
from ass_font_substitute.core.fontindex.library import normalize_name
from ass_font_substitute.core.fontindex.parser import RECORD_IDS
from ass_font_substitute.gui.name_lang import _UI_LANG_ORDER
from ass_font_substitute.gui.name_lang import pick_display_name
from ass_font_substitute.gui.name_lang import ui_lang
from ass_font_substitute.gui.preview_vm import weight_tag
from ass_font_substitute.gui.widgets.font_list import meta_items
from ass_font_substitute.gui.widgets.pill import PillRow
from ass_font_substitute.gui.widgets.pill import pill
from ass_font_substitute.gui.widgets.preview_bar import PreviewBar

BAR_H = 48                 # 详情预览条高（列表行内条是 32，这里给大号）
LIST_BAR_H = 32            # 列表行内预览条高（与预览页行内条一致）
FACE_ROW_H = 68            # 列表行估高兜底：首行 sizeHint 校准只增
WIDTH_NAMES = {1: "超窄", 2: "特窄", 3: "窄", 4: "半窄", 5: "常规",
               6: "半宽", 7: "宽", 8: "特宽", 9: "超宽"}  # usWidthClass

# Windows LCID → 语言标注静态兜底表：babel 解析失败（非
# Windows/极偏 LCID）时用；未收录回退 0x 编码显示
LCID_LANGS = {
    0x409: "英语 - 美国", 0x804: "中文 - 简体", 0x404: "中文 - 繁体（台湾）",
    0xC04: "中文 - 繁体（香港）", 0x1004: "中文 - 简体（新加坡）",
    0x411: "日语", 0x412: "韩语", 0x407: "德语", 0x40C: "法语",
    0x410: "意大利语", 0x40A: "西班牙语", 0x80A: "西班牙语（墨西哥）",
    0xC0A: "西班牙语（国际排序）",
    0x416: "葡萄牙语 - 巴西", 0x816: "葡萄牙语", 0x419: "俄语",
    0x415: "波兰语", 0x41D: "瑞典语", 0x413: "荷兰语", 0x406: "丹麦语",
    0x40B: "芬兰语", 0x414: "挪威语", 0x405: "捷克语", 0x41B: "斯洛伐克语",
    0x424: "斯洛文尼亚语", 0x40E: "匈牙利语", 0x408: "希腊语",
    0x41F: "土耳其语", 0x401: "阿拉伯语", 0x40D: "希伯来语", 0x42D: "巴斯克语",
    0x425: "爱沙尼亚语", 0x426: "拉脱维亚语", 0x427: "立陶宛语",
    0x42A: "越南语", 0x41E: "泰语", 0x421: "印尼语",
}
# 语言展示序：en-US → 简中 → 繁中（台湾/香港）→ 其余按 LCID 升序，
# 与 Windows 字体查看器一致（英文名在前、本地化名随后）
_LANG_PRIO = {0x409: 0, 0x804: 1, 0x404: 2, 0xC04: 3}


@functools.cache
def _lang_display_name(lcid: int, ui: str) -> str | None:
    """LCID → 以界面语言显示的语言名。babel(CLDR) 跨语言显示名，随
    app 设置语言而非系统显示语言（GetLocaleInfoW 跟系统走，英文系统
    上中文 UI 会出英文名）；LCID→BCP47 用系统 LCIDToLocaleName 免手
    抄映射。非 Windows/未知 LCID 返回 None 走静态表兜底。"""
    if sys.platform != "win32":
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.LCIDToLocaleName(lcid, buf, len(buf), 0):
        return None
    try:
        loc = Locale.parse(buf.value.replace("-", "_").lower())
    except Exception:
        return None
    return loc.get_display_name(ui) or None


def _lang_label(platform: int, lang: int, ui: str = "zh_CN") -> str:
    if platform == 1:                       # Macintosh 平台 langID 自成体系
        return "英语 - Mac" if lang == 0 else f"Mac 语言 {lang}"
    return (_lang_display_name(lang, ui) or LCID_LANGS.get(lang)
            or f"语言 0x{lang:04x}")


def group_names(records: list[tuple[int, int, int, str]]
                ) -> list[tuple[str, list[tuple[str, int, int]]]]:
    """name 原始记录 → [(字段名, [(文本, platformID, langID), ...])]，
    命名卡数据。语言过滤（默认视图 ID2/3 只收 en/zh）在显示层做，语言
    选择器按原始 langID 匹配。

    - 字体系(ID1)/版式字体系(ID16)/字体全名(ID4)：全语言；
    - 同一文本多语言只留优先序首条（Mac 平台记录与 Win en-US 同文时
      自然沉底被去重）；ID16 与 ID1 文本全同则整行不出。"""
    by_id: dict[int, list[tuple[int, int, int, str]]] = {}
    for nid, plat, lang, text in records:
        # 与入库白名单同源（parser.RECORD_IDS）：单边改动会静默产生
        # 「入库记录被丢弃」的空洞
        if nid in RECORD_IDS:
            by_id.setdefault(nid, []).append(
                (_LANG_PRIO.get(lang, 10 + lang), plat, lang, text))
    id1_texts = {t for (_p, _pl, _l, t) in by_id.get(1, [])}
    out: list[tuple[str, list[tuple[str, int, int]]]] = []
    for nid, label in ((1, "字体系"), (16, "版式字体系"), (4, "字体全名"),
                       (2, "样式映射字体系"), (3, "唯一字体标识符")):
        seen: set[str] = set()
        rows: list[tuple[str, int, int]] = []
        for _prio, plat, lang, text in sorted(by_id.get(nid, [])):
            if (nid == 16 and text in id1_texts) or text in seen:
                continue
            seen.add(text)
            rows.append((text, plat, lang))
        if rows:
            out.append((label, rows))
    return out


# 默认显示语言优先链常量在 gui/name_lang.py（各界面展示名共用同一条
# 链）；此处只做命名卡视图过滤与默认语言选择


def visible_name_rows(grouped: list[tuple[str, list[tuple[str, int, int]]]],
                      lang: tuple[int, int]
                      ) -> list[tuple[str, list[tuple[str, str]]]]:
    """选中语言视图：各字段只显该语言记录，无记录的字段整行隐藏，行内
    不重复标注。语言恒有值（打开时按 UI 语言优先链选定）——无「全部」
    视图：30 国样式词翻译噪音全靠单语言过滤挡住。"""
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for label, pairs in grouped:
        sel = [(t, p, lg) for t, p, lg in pairs if (p, lg) == lang]
        if sel:
            out.append((label, [(t, "") for t, _p, _lg in sel]))
    return out


def same_file_faces(entities: list[FontEntity],
                    e: FontEntity) -> list[FontEntity]:
    """同物理文件（TTC 内）的其余 face，按 字重→斜体→宽度 归位；
    版本去重后 fingerprint 全库唯一，作身份比较。"""
    return sorted((x for x in entities
                   if x.fingerprint != e.fingerprint
                   and (x.dir_path, x.rel_path) == (e.dir_path, e.rel_path)),
                  key=_face_order)


def same_family_faces(entities: list[FontEntity],
                      e: FontEntity) -> list[FontEntity]:
    """同字体系的其余 face（可跨目录跨文件）。字体系是 name 表实字段
    （FontCreator「字体系名称」= ID16，缺失视 ID1——FOTPunchello 家族
    各字重都填 'FOTPunchello'，微软雅黑 Regular/Bold 同 '微软雅黑'），
    parser 入库 family_names 全语言集合，此处按集合交集归族——同族各
    face 记录语言数可能不同，全等会误裂。超 4 样式家族（如 Arial
    Black）按 spec ID16 也填 'Arial'，交集归族正是 typographic family
    的本意。family 缺失（异常解析）回退别名交集。这与预览页 display
    全等的行合并是两回事——此处是家族血脉导航。"""
    if e.family_names:
        mine = set(e.family_names)
        return sorted((x for x in entities
                       if x.fingerprint != e.fingerprint
                       and mine.intersection(x.family_names)),
                      key=_face_order)
    mine = {normalize_name(a) for a in e.aliases}
    return sorted((x for x in entities
                   if x.fingerprint != e.fingerprint
                   and mine.intersection(map(normalize_name, x.aliases))),
                  key=_face_order)


def _face_order(e: FontEntity):
    return (e.weight, e.italic, e.width)


class _FaceRow(QWidget):
    """详情列表行 = 名 + 元信息 + 行内预览条。容器不画底：悬停/选中
    反馈交给列表样式透出（QLabel 不消费鼠标，双击自然传播到视口——
    预览页 _RowWidget 同构验证过）。只读导航行，无分段/斜体钮。"""

    def __init__(self, worker, pvm, f: FontEntity, text, tag,
                 show_face: bool, name: str):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 6, 12, 6)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(StrongBodyLabel(name))
        # meta 药丸对齐预览页（script · 字重 · 斜体 · 分类），斜体仅斜体
        # 时显；同文件节附 face 序号区分 TTC 内位置，家族节不再缀文件（太长）
        items = meta_items(f)
        if show_face:
            items.append(f"face {f.face_index}")
        row = PillRow()
        row.set_items(items)
        top.addWidget(row)
        top.addStretch(1)
        v.addLayout(top)
        self.bar = PreviewBar(worker, pvm, f, text, tag, height=LIST_BAR_H)
        v.addWidget(self.bar)


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _card(title: str) -> tuple[QWidget, QVBoxLayout]:
    """节卡片：SimpleCardWidget（主题随动底色圆角）+ 标题 + 竖排容器。"""
    card = SimpleCardWidget()
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(6)
    head = StrongBodyLabel(title)
    v.addWidget(head)
    return card, v


def _form(v: QVBoxLayout, rows: list[tuple[str, QWidget]]):
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.setHorizontalSpacing(12)
    # 先安装后 addRow：未安装的 form 里 addRow 的行 widget 无父，等
    # addLayout 挂进可见宿主时 reparent/show 事件序错位，行会以独立
    # 顶层窗上屏一瞬（命名卡回填闪小窗的根因）；安装后 addRow 即时
    # reparent 入宿主，无父窗口期归零
    v.addLayout(form)
    for name, w in rows:
        form.addRow(CaptionLabel(name), w)


def _multi_lang_widget(rows: list[tuple[str, str]]) -> QWidget:
    """多语言名值控件：每语言一行「文本 (语言标注)」，标注空则只显文本；
    可选中复制（IBeam 光标提示可复制）。"""
    w = QWidget()
    va = QVBoxLayout(w)
    va.setContentsMargins(0, 0, 0, 0)
    va.setSpacing(2)
    for text, lang in rows:
        lab = BodyLabel(f"{text} ({lang})" if lang else text)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lab.setCursor(Qt.CursorShape.IBeamCursor)
        va.addWidget(lab)
    return w


def _simple_val(text: str, wrap: bool = False,
                selectable: bool = False) -> BodyLabel:
    lab = BodyLabel(text)
    lab.setWordWrap(wrap)
    if selectable:
        lab.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lab.setCursor(Qt.CursorShape.IBeamCursor)
    return lab


class FontDetailDialog(MessageBoxBase):
    """字体详情：大预览条 + 命名/属性/文件三卡 + 同文件、同字体系列表。

    命名卡同步装载：打开时从索引库读该 face 的 name 表原始记录
    （fonts.name_records，SCHEMA v8；本地库亚毫秒，首屏即结构化命名
    零闪烁），记录缺失/库不可读回退平铺别名。两个列表双击条目嵌套再
    开一个详情（parent=self，可连续跳看）；单击仅高亮。选择对话框
    （FontPickerDialog）不提供此入口，仅浏览页双击列表行打开。"""

    def __init__(self, e: FontEntity, entities: list[FontEntity],
                 worker, pvm, settings=None, parent=None):
        super().__init__(parent)
        self._entities, self._worker, self._pvm = entities, worker, pvm
        self._settings = settings
        self._e = e
        self._row_seq = 0                   # 列表行 tag 自增号（防撞车）
        # 展示名按界面语言链挑（与列表行同源）；预览文字同走——空预览
        # 设置下条上渲的就是这个名字。口径与命名卡默认语言可偶发不同：
        # 标题是逐字段链（ID1 有记录即止），命名卡是对全字段语言并集套
        # 链（ID1 缺该语言时卡片可默认显 ID4/16 的该语言行）
        self._name = pick_display_name(e, ui_lang(settings))

        self.titleLabel = SubtitleLabel(self._name)
        bar = PreviewBar(worker, pvm, e, self._name,
                         f"detail:{e.fingerprint}:{id(self)}", height=BAR_H)

        # ---- 命名卡：同步装载（记录缺失/读库失败才兜底平铺）；
        #      标题行右侧语言选择（记录只一种语言时隐藏）----
        name_card = SimpleCardWidget()
        self._name_v = QVBoxLayout(name_card)
        self._name_v.setContentsMargins(16, 12, 16, 12)
        self._name_v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StrongBodyLabel("命名"))
        head.addStretch(1)
        self._lang_btn = TransparentDropDownPushButton("语言", name_card)
        # 语言设置同款图标（settings_page 语言卡 FIF.LANGUAGE）
        self._lang_btn.setIcon(FluentIcon.LANGUAGE.icon())
        self._lang_btn.setFixedHeight(28)
        self._lang_btn.hide()               # 解析回填后按语言数决定显隐
        head.addWidget(self._lang_btn)
        self._name_v.addLayout(head)
        self._name_form: QFormLayout | None = None
        self._grouped: list = []
        self._lang: tuple[int, int] | None = None
        self._lang_actions: list = []
        # 同步装载（构造内完成，首屏即终态）：读库失败/无记录走兜底，
        # 不做「先兜底再覆盖」——那种两段渲染正是回填闪帧与闪小窗之源
        records = load_name_records(e.dir_path, e.rel_path, e.face_index)
        if records:
            self._apply_name_records(records)
        else:
            self._fill_names_fallback()

        # ---- 属性卡 ----
        attr_card, av = _card("属性")
        ver = ".".join(map(str, e.version)) if e.version else "—"
        wd = WIDTH_NAMES.get(e.width, str(e.width))
        _form(av, [
            ("版本", _simple_val(ver)),
            ("字重", _simple_val(f"{weight_tag(e.weight)}（{e.weight}）")),
            ("宽度", _simple_val(f"{wd}（{e.width}）")),
            ("斜体", _simple_val("是" if e.italic else "否")),
            # 详情卡=全量事实：「未标注」也上屏（行 meta 才隐藏空值，
            # 见 font_list.meta_items）
            ("简繁", pill(e.script)),
            ("分类", pill(e.category)),
            ("字符覆盖", _simple_val(f"{e.cmap.total_chars():,}")),
        ])

        # ---- 文件卡 ----
        file_card, fv = _card("文件")
        path = BodyLabel(f"{e.dir_path}/{e.rel_path}")
        path.setWordWrap(True)
        # 路径可选中复制：长路径手抄不现实；IBeam 提示可复制
        path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setCursor(Qt.CursorShape.IBeamCursor)
        mtime = (datetime.fromtimestamp(e.mtime_ns / 1e9).strftime(
            "%Y-%m-%d %H:%M") if e.mtime_ns else "—")
        _form(fv, [
            ("文件路径", path),
            ("face 序号", _simple_val(str(e.face_index))),
            ("大小", _simple_val(_human_size(e.size) if e.size else "—")),
            ("修改时间", _simple_val(mtime)),
        ])

        # ---- 两节列表卡 ----
        file_faces = same_file_faces(entities, e)
        file_list_card, fv2 = _card("同文件其他字体")
        file_list = self._fill_list(fv2, file_faces, show_face=True)
        fam_faces = same_family_faces(entities, e)
        fam_card, gv2 = _card("同字体系其他字体")
        fam_list = self._fill_list(gv2, fam_faces, show_face=False)

        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(bar)
        v.addWidget(name_card)
        v.addWidget(attr_card)
        v.addWidget(file_card)
        v.addWidget(file_list_card)
        v.addWidget(fam_card)
        v.addStretch(1)
        # 单 face 文件/独行家族整卡隐藏（空列表无内容可看）。必须在
        # addWidget 入父之后：无父 setVisible(True) 即成顶层孤儿窗立刻
        # 上屏（弹详情前闪两个空白小窗的根因——两卡恰一大一小），随后
        # addWidget 重挂父触发原生窗口重建
        file_list_card.setVisible(bool(file_faces))
        fam_card.setVisible(bool(fam_faces))

        self.viewLayout.addWidget(self.titleLabel)
        # 正文自滚（MessageBoxBase 本体无外层滚动区，矮窗口下底部卡片
        # 会被裁掉且不可达）：包 qfw ScrollArea；两节列表是定高小列表，
        # 外滚不会与内滚打架（区别于 picker 不套外滚的理由）
        scroll = ScrollArea(self)
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.enableTransparentBackground()
        self.viewLayout.addWidget(scroll, 1)
        # 窄窗下固定最小宽会横向溢出，与 FontPickerDialog 同款钳制
        self.widget.setFixedWidth(min(720, max(self.width() - 48, 320)))
        # 高度封在宿主窗口内：正文超出交给上面自滚区
        win_h = self.window().height()
        if win_h > 480:
            self.widget.setMaximumHeight(win_h - 32)
        self.hideYesButton()
        # hideYesButton 后取消钮独占按钮行会被拉满整宽
        self.cancelButton.setText("关闭")
        self.cancelButton.setFixedWidth(140)
        self._sections = (file_list, fam_list)
        # 关闭即 cancel 在途渲染 + 销毁：详情可反复开关，不回收则隐藏
        # 对话框与已排队任务随打开次数累积（淡出动画完成后才 deleteLater
        # 不截断动画；嵌套时外层被模态挡住，不会先于内层销毁）
        self._bar = bar
        self.finished.connect(self._on_finished)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    # ---- 命名卡同步装载 ----

    def _apply_name_records(self, records):
        """原始记录 → 结构化命名（构造期共用回填体）。PostScript 名不
        从记录取（RECORD_IDS 白名单不含 ID6），恒走入库的 psname 列。"""
        self._grouped = group_names(records)
        self._rebuild_lang_menu()
        # _rebuild_lang_menu 末尾 _set_lang 已渲染过（有语言记录时）；
        # 再显式渲染一次=白造一轮行又拆一轮（纯浪费+放大拆行开销）。
        # 仅无任何语言记录（_set_lang 未被调）时兜底渲染
        if self._lang is None:
            self._render_names()

    def _swap_names(self, rows: list[tuple[str, QWidget]]):
        # QFormLayout 的行项是 layout 内部 item（非 widget item），外层
        # takeAt 摸不到——逐行取 Label/Field 两角色控件脱离销毁，再整体
        # 摘掉旧 form；漏一步则旧行残留且 deleteLater 永不生效
        form = self._name_form
        if form is not None:
            for i in range(form.rowCount()):
                for role in (QFormLayout.ItemRole.LabelRole,
                             QFormLayout.ItemRole.FieldRole):
                    it = form.itemAt(i, role)
                    if it is not None and it.widget() is not None:
                        w = it.widget()
                        form.removeWidget(w)
                        # 先摘显隐再摘父：对话框可见后行都在可见树中
                        # （vis=True），直接 setParent(None) 会带着可见
                        # 标志变独立顶层窗上屏一瞬（回填闪小窗的根因）
                        w.setVisible(False)
                        w.setParent(None)
                        w.deleteLater()
            self._name_v.removeItem(form)
            form.deleteLater()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        # 先安装后 addRow（理由见 _form：无父 addRow 行在宿主可见时
        # reparent 期以顶层窗上屏一瞬——命名卡异步回填闪小窗）
        self._name_form = form
        self._name_v.addLayout(form)
        for name, w in rows:
            form.addRow(CaptionLabel(name), w)

    def _fill_names_fallback(self):
        """解析结果未到/失败：平铺别名兜底，命名卡不空窗；语言钮隐藏。"""
        self._grouped = []
        self._lang = None
        self._lang_btn.hide()
        self._swap_names([("全部别名", _simple_val(
            "、".join(self._e.aliases) or "—", wrap=True))])

    def _rebuild_lang_menu(self):
        """语言下拉：记录中出现过的各语言（跨字段并集，按展示序）。默认
        选中 = 界面语言优先链（简中界面：简→繁→英→日；繁中界面：繁→
        简→英→日）首个命中，全不中退语言集首；只一种语言时隐藏按钮。"""
        langs = sorted({(p, lg) for _label, rows in self._grouped
                        for (_t, p, lg) in rows},
                       key=lambda k: (_LANG_PRIO.get(k[1], 10 + k[1]), k[0]))
        menu = RoundMenu(parent=self._lang_btn)
        self._lang_actions = []

        def add(text, key):
            act = Action(text)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, k=key: self._set_lang(k))
            menu.addAction(act)
            self._lang_actions.append((key, act))

        ui = self._ui_lang()
        for p, lg in langs:
            add(_lang_label(p, lg, ui), (p, lg))
        self._lang_btn.setMenu(menu)
        self._lang_btn.setVisible(len(langs) > 1)
        # 默认语言：UI 语言链按 langID 匹配（同 ID 多平台 Win 前置），
        # 全不中退展示序首个
        by_lcid: dict[int, list[tuple[int, int]]] = {}
        for p, lg in langs:
            by_lcid.setdefault(lg, []).append((p, lg))
        for keys in by_lcid.values():
            keys.sort(key=lambda k: k[0] != 3)
        chosen = next((by_lcid[lcid][0]
                       for lcid in _UI_LANG_ORDER[ui_lang(self._settings)]
                       if lcid in by_lcid), langs[0] if langs else None)
        if chosen is not None:
            self._set_lang(chosen)

    def _ui_lang(self) -> str:
        """设置语言 → babel 显示语言代码（语言名随 app 设置而非系统）。"""
        zh = (self._settings.get("language")
              if self._settings is not None else None)
        return "zh_TW" if zh == "zh-Hant" else "zh_CN"

    def _set_lang(self, key: tuple[int, int]):
        self._lang = key
        self._lang_btn.setText(_lang_label(*key, self._ui_lang()))
        for k, act in self._lang_actions:
            act.setChecked(k == key)
        self._render_names()

    def _render_names(self):
        rows = [(label, _multi_lang_widget(pairs))
                for label, pairs in visible_name_rows(self._grouped,
                                                      self._lang)]
        rows.append(("PostScript 名",
                     _simple_val(self._e.psname or "—", selectable=True)))
        self._swap_names(rows)

    def _on_finished(self, _result):
        self._bar.cancel()          # 顶栏在途渲染同出队（原来只存没用）
        # 行内预览在途任务出队：关闭后回传没人接，白占工作者串行时段
        for lst in self._sections:
            for i in range(lst.count()):
                w = lst.itemWidget(lst.item(i))
                if w is not None:
                    w.bar.cancel()

    # ---- 两节列表 ----

    def _fill_list(self, v: QVBoxLayout, faces: list[FontEntity],
                   show_face: bool) -> QListWidget:
        """卡内填 face 列表（双击嵌套开详情）。行 = 名 + 元信息 + 预览
        条，条目全显不内滚——定高封 5 条在实机 DPI 下估高必偏差，差 1px
        就出原生丑滚动条；超出整卡的交给对话框外层 ScrollArea（qfw 样
        式）。样张统一用详情主 face 的展示名：同文字跨 face 才是纯比字
        形（家族导航的价值所在）。行数少（≤30）全物化，不搞预览页的可
        视窗口。空列表由调用方整卡隐藏。"""
        lst = QListWidget()
        lst.setStyleSheet("QListWidget{border:none;background:transparent}")
        lst.setWordWrap(False)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        text = self._name
        for f in faces:
            # tag 带实例 id+自增号：同 face 跨弹窗/同现两节时 tag 不撞车
            self._row_seq += 1
            w = _FaceRow(self._worker, self._pvm, f, text,
                         f"dlist:{id(self)}:{self._row_seq}", show_face,
                         pick_display_name(f, ui_lang(self._settings)))
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, FACE_ROW_H))
            it.setData(Qt.ItemDataRole.UserRole, f)
            lst.addItem(it)
            lst.setItemWidget(it, w)
        if faces:
            # 行高按首行内容实测校准（估高随平台字体度量漂，预览页同款
            # 只增策略）；定高 = 全行实测和，多 2px 缓冲防差 1px 出条
            h = max(FACE_ROW_H,
                    lst.itemWidget(lst.item(0)).sizeHint().height())
            if h != FACE_ROW_H:
                for i in range(lst.count()):
                    lst.item(i).setSizeHint(QSize(10, h))
            lst.setFixedHeight(h * len(faces) + lst.frameWidth() + 2)
            lst.itemDoubleClicked.connect(self._open_nested)
        v.addWidget(lst)
        return lst

    def _open_nested(self, item: QListWidgetItem):
        e: FontEntity = item.data(Qt.ItemDataRole.UserRole)
        # 嵌套详情与当前弹窗同源挂载（parent=弹窗时关闭动画层收尾会让页
        # 面区闪一帧空窗，真机+开关二分实锤）。同 parent 让 MaskDialogBase
        # 算出与弹窗相同的铺满矩形，天然对齐不错位；无父对象退回自身兜底。
        dlg = FontDetailDialog(e, self._entities, self._worker, self._pvm,
                               settings=self._settings,
                               parent=self.parentWidget())
        dlg.setGeometry(self.geometry())
        dlg.exec()
