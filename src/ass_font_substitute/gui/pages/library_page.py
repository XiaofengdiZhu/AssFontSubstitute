from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6.QtCore import QCoreApplication
from PySide6.QtCore import QRectF
from PySide6.QtCore import Qt
from PySide6.QtCore import QTimer
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtGui import QIcon
from PySide6.QtGui import QPainter
from PySide6.QtGui import QPainterPath
from PySide6.QtGui import QPen
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel
from qfluentwidgets import CaptionLabel
from qfluentwidgets import CardWidget
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import GroupHeaderCardWidget
from qfluentwidgets import IndeterminateProgressBar
from qfluentwidgets import InfoBar
from qfluentwidgets import MessageBox
from qfluentwidgets import MessageBoxBase
from qfluentwidgets import PrimaryPushButton
from qfluentwidgets import ProgressBar
from qfluentwidgets import PushButton
from qfluentwidgets import PushSettingCard
from qfluentwidgets import ScrollArea
from qfluentwidgets import SubtitleLabel
from qfluentwidgets import TextEdit
from qfluentwidgets import TextWrap
from qfluentwidgets import TransparentToolButton
from qfluentwidgets import VerticalSeparator
from qfluentwidgets import isDarkTheme
from qfluentwidgets import setCustomStyleSheet
from qfluentwidgets import setFont

from ass_font_substitute.gui.drop import accepts
from ass_font_substitute.gui.drop import local_paths
from ass_font_substitute.gui.drop import split_dropped
from ass_font_substitute.gui.jobs import ensure_library
from ass_font_substitute.gui.jobs import probe_dir_status
from ass_font_substitute.gui.jobs import read_failures
from ass_font_substitute.gui.jobs import retry_bad_batch
from ass_font_substitute.gui.lastdir import pick_existing_dir
from ass_font_substitute.gui.library_vm import LibraryVM
from ass_font_substitute.gui.pages.subs_page import _REMOVE_RED
from ass_font_substitute.gui.pages.subs_page import _ElidedLabel
from ass_font_substitute.gui.tip import install_tip
from ass_font_substitute.gui.workers import IndexWorker

_STAT_KEYS = [("dirs", QT_TRANSLATE_NOOP("LibraryPage", "字体目录")),
              ("files", QT_TRANSLATE_NOOP("LibraryPage", "文件总数")),
              ("fonts", QT_TRANSLATE_NOOP("LibraryPage", "字体总数")),
              ("support_sc", QT_TRANSLATE_NOOP("LibraryPage", "支持简体")),
              ("support_tc", QT_TRANSLATE_NOOP("LibraryPage", "支持繁体"))]

# 目录行状态写进行内按钮：进行时「更新中/检查中」，静态词「已最新/需更新/
# 不可用」；stale 另配琥珀底提示可点更新
_STATUS_TEXT = {
    "ok": QT_TRANSLATE_NOOP("LibraryPage", "已最新"),
    "stale": QT_TRANSLATE_NOOP("LibraryPage", "需更新"),
    "indexing": QT_TRANSLATE_NOOP("LibraryPage", "更新中"),
    "pending": QT_TRANSLATE_NOOP("LibraryPage", "检查中"),
    "offline": QT_TRANSLATE_NOOP("LibraryPage", "不可用")}


def _progress_text(done: int, total: int) -> str:
    """行内进度文案；total=0 表示还没收到首笔回执（扫描中）。"""
    return (f"{round(done * 100 / total)}% · {done}/{total}" if total
            else QCoreApplication.translate("LibraryPage", "准备中…"))


# 统计行定宽模板（文件/字体 5 位、简繁/失败 4 位封顶）：各行左块等宽，
# 行中进度文案的横向位置才逐行对齐。超限（单目录 10 万+文件量级）实际
# 文案会宽于定宽被静默截尾，当前量级安全
_STATS_TMPL = ("文件 00000 · 字体 00000 · 支持简体 0000"
               " · 支持繁体 0000 · 失败/重复 0000")


def _bottom_clip_path(bar) -> QPainterPath:
    """条贴卡片底边：底部两角按卡片圆角（qfw CardWidget 默认 r5）的
    裁剪路径——弧心与背景圆角重合，填充端头不出圆角。"""
    w, h, r = float(bar.width()), float(bar.height()), 5.0
    path = QPainterPath()
    path.moveTo(0, 0)
    path.lineTo(w, 0)
    path.lineTo(w, h - r)
    path.arcTo(QRectF(w - 2 * r, h - 2 * r, 2 * r, 2 * r), 0, -90)
    path.lineTo(r, h)
    path.arcTo(QRectF(0, h - 2 * r, 2 * r, 2 * r), 270, -90)
    path.closeSubpath()
    return path


class _RowBar(ProgressBar):
    """行底定态条：不画轨道线（qfw 半透 1px 线贴卡底成脏线），填充画在
    卡片圆角裁剪内。裁剪须与绘制同一 painter——super() 自建 painter 会
    丢掉外部设置的 clip，故自绘复刻 qfw 的填充数学。"""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing)
        p.setClipPath(_bottom_clip_path(self))
        if self.minimum() >= self.maximum():
            return
        p.setPen(Qt.NoPen)
        p.setBrush(self.barColor())
        w = int(self.val / (self.maximum() - self.minimum()) * self.width())
        p.drawRoundedRect(0, 0, w, self.height(),
                          self.height() / 2, self.height() / 2)


class _RowIndeterminateBar(IndeterminateProgressBar):
    """行底不定态条：游走段同样画进卡片圆角裁剪。"""

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing)
        p.setClipPath(_bottom_clip_path(self))
        p.setPen(Qt.NoPen)
        p.setBrush(self.barColor())
        r = self.height() / 2
        x = int((self.shortPos - 0.4) * self.width())
        p.drawRoundedRect(x, 0, int(0.4 * self.width()), self.height(), r, r)
        x = int((self.longPos - 0.6) * self.width())
        p.drawRoundedRect(x, 0, int(0.6 * self.width()), self.height(), r, r)


# stale 行按钮琥珀底（qfw 无警示色按钮；双主题 QSS，含 disabled 态——
# 其他目录更新在途时本行也不可点）
_AMBER_QSS_LIGHT = """
PushButton{
    background-color: #ffb900;
    border: none;
    color: #1f1f1f;
}
PushButton:hover{
    background-color: #efa90a;
}
PushButton:pressed{
    background-color: #e09d00;
}
PushButton:disabled{
    background-color: #f3d78a;
    color: #8a7a33;
}
"""
_AMBER_QSS_DARK = """
PushButton{
    background-color: #9d5d00;
    border: none;
    color: #ffffff;
}
PushButton:hover{
    background-color: #b26e00;
}
PushButton:pressed{
    background-color: #8a5100;
}
PushButton:disabled{
    background-color: #5e3a00;
    color: rgba(255,255,255,0.5);
}
"""


def _box_content(text: str) -> str:
    """确认框文案按 32 汉字宽预折行：qfw MessageBox 按父窗口宽折行
    （1200px 窗 → 140 字/行），长路径单行不折、确认框被撑满整窗宽。
    预折好后 _adjustText 只折超宽行，对话框宽封顶在 ~32 字。"""
    return TextWrap.wrap(text, 64, once=False)[0]


class LibraryPage(ScrollArea):
    libraryChanged = Signal()
    """目录增删或索引更新完成：订阅页重拉库数据（browse 清单/ subs 重分类）"""

    loadCompleted = Signal()
    """首个 lib 任务收尾（成功/失败/无目录）一次性发射：启动画面收幕"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("libraryPage")
        self.setAcceptDrops(True)   # 拖入字体目录：确认框后加库（_apply_dropped）
        self.vm = LibraryVM(settings)
        self._index_worker: IndexWorker | None = None
        self._retry_outstanding = 0        # 在途重试笔数：收尾去抖用
        self._index_progress: dict[str, tuple[int, int]] = {}
        """path → 最近一次 (done, total)：重排行后进度条原地恢复值"""
        self._progress_widgets: dict[str, tuple] = {}
        """path → (不定态条, 定态条, 文本)：随每次重排行重建，逐文件
        回执只原地 set 值/文案，绝不整页重排"""
        self._pending_failures = None       # "failures" 任务结果到达后的弹窗回调
        self._lib_reported = False          # loadCompleted 只发一次
        self._auto_started = False          # 启动 stale 自动开更只做一次

        self.view = QWidget(self)
        v = QVBoxLayout(self.view)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()   # 默认画调色板底，深色主题下亮块
        # 间距节奏对齐设置页：页边距 (36,24,36,36)，区块间 16（区块间
        # 无组标题，32 空得没道理），标题→卡片 12
        v.setContentsMargins(36, 24, 36, 36)
        v.setSpacing(0)

        # BodyLabel 而非裸 QLabel：LABEL qss 黑/白随主题（裸 QLabel 黑字死调色板）
        self.page_title = BodyLabel(self.tr("字体库管理"))
        setFont(self.page_title, 20)   # qfw setFont：20px Normal 字重不变
        v.addWidget(self.page_title)
        v.addSpacing(12)

        # 统计条置顶、操作条其下：打开页先读库概况再看到操作
        self.stat_cards: dict[str, BodyLabel] = {}
        stat_card = CardWidget()
        stat_row = QHBoxLayout(stat_card)
        stat_row.setContentsMargins(24, 14, 24, 14)
        stat_row.setSpacing(0)
        for i, (key, label) in enumerate(_STAT_KEYS):
            if i:
                sep = VerticalSeparator()      # qfw 自取主题色，换肤免刷
                sep.setFixedHeight(30)
                stat_row.addWidget(sep, 0, Qt.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(2)
            title = CaptionLabel(self.tr(label))
            title.setAlignment(Qt.AlignCenter)
            val = BodyLabel("—")
            val.setAlignment(Qt.AlignCenter)
            setFont(val, 18, QFont.Weight.DemiBold)   # 数值层级高于标题
            self.stat_cards[key] = val
            col.addWidget(title)
            col.addWidget(val)
            stat_row.addLayout(col, 1)
        v.addWidget(stat_card)
        v.addSpacing(16)

        # 按钮初始无布局，_rebuild_dir_rows 时装进分组卡标题栏/失败卡。
        # 添加目录=主色（引导首次使用的正事），更新全部=普通（日常少点）
        self.btn_add = PrimaryPushButton(FIF.FOLDER_ADD, self.tr("添加目录"))
        install_tip(self.btn_add, self.tr("选择一个字体目录加入列表"))
        self.btn_refresh = TransparentToolButton(FIF.SYNC)
        install_tip(self.btn_refresh, self.tr("刷新状态"))
        self.btn_update = PushButton(FIF.UPDATE, self.tr("更新全部"))
        install_tip(self.btn_update, self.tr("更新全部目录的索引"))

        # 更新态：按钮原地变「[旋转环]取消更新」，宽度锁定防文字变短塌缩
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(50)
        self._busy_timer.timeout.connect(self._tick_busy)
        self._busy_i = 0
        self._busy_icons: list[QIcon] | None = None
        self._busy_dark: bool | None = None   # 弧色按主题缓存，换主题重画

        self.dirs_box = QVBoxLayout()
        self.dirs_box.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.dirs_box)
        v.addSpacing(16)

        self.failures_card = PushSettingCard(
            self.tr("打开"), FIF.INFO, self.tr("查看失败清单"),
            self.tr("暂无失败文件"))
        v.addWidget(self.failures_card)
        v.addStretch(1)

        self.btn_add.clicked.connect(self._add_dir)
        self.btn_refresh.clicked.connect(self.refresh_status)
        self.btn_update.clicked.connect(self._on_update_clicked)
        self.failures_card.clicked.connect(self._show_failures)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.refresh_status()

    # ---- 目录列表 ----

    def _rebuild_dir_rows(self):
        self._progress_widgets = {}
        while self.dirs_box.count():
            item = self.dirs_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 一张 GroupHeaderCardWidget 框住全部目录行（行间分隔线由组件管）；
        # 标题栏当工具条：左=标题+刷新图标，右=更新全部+添加目录
        card = GroupHeaderCardWidget(self)
        card.setTitle(self.tr("字体目录"))
        card.headerLayout.addWidget(self.btn_refresh)
        card.headerLayout.addStretch(1)
        card.headerLayout.addWidget(self.btn_update)
        card.headerLayout.addWidget(self.btn_add)
        for d in self.vm.dirs:
            right = QWidget()
            rh = QHBoxLayout(right)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(8)
            hint = CaptionLabel(
                self.tr("新增 {0} · 修改 {1}").format(d.added, d.changed))
            rh.addWidget(hint, 0, Qt.AlignVCenter)
            # 先入父再 setVisible：无父 setVisible(True) 即成顶层窗口上屏
            # （默认尺寸+标题栏），addWidget 重挂父再触发原生重建——取消
            # 更新后行回 stale 重排时闪现小窗
            hint.setVisible(d.state == "stale")
            # 状态即按钮：非 indexing 挂更新图标（与「更新全部索引」同款），
            # indexing 换暂停=可单停；stale 可点启动更新，其余纯展示
            btn = PushButton(FIF.UPDATE,
                             self.tr(_STATUS_TEXT.get(d.state, d.state)))
            if d.state == "stale":
                btn.setEnabled(self._index_worker is None)
                install_tip(btn, self.tr("点击更新该目录索引"))
                setCustomStyleSheet(btn, _AMBER_QSS_LIGHT, _AMBER_QSS_DARK)
                btn.clicked.connect(
                    lambda _=False, p=d.path: self._start_update_dir(p))
            elif d.state == "indexing":
                btn.setIcon(FIF.PAUSE)
                install_tip(btn, self.tr("点击停止该目录的索引更新"))
                btn.clicked.connect(
                    lambda _=False, p=d.path: self._stop_dir(p))
            elif d.state == "ok":
                # 已最新也保留更新入口：增量门只看 size+mtime，索引内容
                # 坏了它判定不了——点击弹确认后清库强制重建
                btn.setEnabled(self._index_worker is None)
                install_tip(btn, self.tr("点击删除旧索引并强制重建"))
                btn.clicked.connect(
                    lambda _=False, p=d.path: self._force_update_dir(p))
            else:
                btn.setEnabled(False)
            rh.addWidget(btn, 0, Qt.AlignVCenter)
            rm = PushButton(FIF.REMOVE_FROM.icon(color=_REMOVE_RED),
                            self.tr("移除"))
            # 更新在途禁删：索引线程正写该目录 db，删后状态失配
            rm.setEnabled(self._index_worker is None)
            rm.clicked.connect(lambda _=False, p=d.path: self._remove_dir(p))
            rh.addWidget(rm, 0, Qt.AlignVCenter)

            group = card.addGroup(
                FIF.FOLDER, "",
                self.tr("文件 {0} · 字体 {1} · 支持简体 {2} · 支持繁体 {3} · 失败/重复 {4}").format(
                    d.files, d.fonts, d.sc, d.tc, d.fail), right)
            # 标题位放路径（组件 titleLabel 不省略，换 _ElidedLabel 防窄窗顶飞）
            path_lbl = _ElidedLabel(d.path)
            install_tip(path_lbl, d.path)
            group.textLayout.insertWidget(0, path_lbl)
            group.titleLabel.hide()
            # 左块两行锁同一定宽（统计模板宽）：行中进度文案位置逐行一致
            stats_w = group.contentLabel.fontMetrics().horizontalAdvance(
                _STATS_TMPL)
            group.contentLabel.setFixedWidth(stats_w)
            path_lbl.setFixedWidth(stats_w)
            if d.state == "indexing":
                self._build_progress_strip(group, d.path)
        if not self.vm.dirs:
            ph = CaptionLabel(self.tr("（请添加字体目录）"))
            ph.setAlignment(Qt.AlignCenter)
            wrap = QWidget()
            wh = QHBoxLayout(wrap)
            wh.setContentsMargins(24, 10, 24, 14)
            wh.addWidget(ph)
            card.viewLayout.addWidget(wrap)
        self.dirs_box.addWidget(card)
        self._update_failures_card()

    def _build_progress_strip(self, group, path: str):
        """indexing 行的进度区：文本居中于左块与按钮之间的空档，细条贴
        行底（首笔回执前 total 未知=不定态条，之后切定态条原地推进）。"""
        done, total = self._index_progress.get(path, (0, 0))
        lbl = CaptionLabel(_progress_text(done, total))
        lbl.setAlignment(Qt.AlignCenter)
        # qfw 预置 stretch 之后才是按钮区：插到其前并清其 stretch，文本
        # 独占空档后 AlignCenter 才是真居中（默认各分一半=偏左四分之一）
        group.hBoxLayout.insertWidget(2, lbl, 1, Qt.AlignVCenter)
        group.hBoxLayout.setStretch(3, 0)
        strip = QWidget()
        sv = QVBoxLayout(strip)
        sv.setContentsMargins(0, 2, 0, 1)   # 条贴行底边、左右到卡缘（qfw 圆头填充）
        sv.setSpacing(3)
        ind = _RowIndeterminateBar(strip)
        det = _RowBar(strip)
        det.hide()
        sv.addWidget(ind)
        sv.addWidget(det)
        group.vBoxLayout.addWidget(strip)
        self._progress_widgets[path] = (ind, det, lbl)
        if total:
            # 中途重排行的恢复：停掉不定态动画、关填充动画（否则 setValue
            # 从 0 重播一次「回卷」）
            ind.stop()
            ind.hide()
            det.show()
            det.setUseAni(False)
            det.setRange(0, total)
            det.setValue(done)

    def _update_failures_card(self):
        bad = sum(d.bad for d in self.vm.dirs)
        skipped = sum(d.skipped for d in self.vm.dirs)
        self.failures_card.setContent(
            self.tr("坏文件 {0} · 重复跳过 {1}").format(bad, skipped)
            if bad or skipped else self.tr("暂无失败文件"))

    def _add_dir(self):
        d = pick_existing_dir(self, self.settings, self.tr("选择字体目录"),
                              "library")
        if not d:
            return
        if not self.add_dirs([d]):
            InfoBar.warning(self.tr("提示"), self.tr("该目录已在列表中"),
                            parent=self)

    def add_dirs(self, paths: list[str]) -> list[str]:
        """逐个加入目录列表并持久化，返回实际新增路径（调用方据此提示）。
        供本页与拖拽（预览页 dirsAdded 信号转发）共用；全部处理完只重建
        一次行卡 + 刷新一次状态。"""
        added: list[str] = []
        for p in paths:
            if self.vm.add_dir(p):
                added.append(p)
        if added:
            self._rebuild_dir_rows()
            self.refresh_status()
            # 新目录立即自动建索引；更新在途不并跑（索引线程独占写库），
            # 行上琥珀钮兜底手动点
            if self._index_worker is None:
                self._launch_update(added)
        return added

    # ---- 拖拽入：只认文件夹，逐个确认框后加库 ----

    def dragEnterEvent(self, e):
        if accepts(local_paths(e.mimeData()), dirs_only=True):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        self._apply_dropped(local_paths(e.mimeData()))

    def _apply_dropped(self, paths: list[str]) -> None:
        dirs, _ = split_dropped(paths)
        for d in dirs:
            if any(Path(x.path) == Path(d) for x in self.vm.dirs):
                InfoBar.warning(self.tr("提示"), self.tr("该目录已在列表中"),
                                parent=self)
                continue
            box = MessageBox(self.tr("加入字体库"),
                             self.tr("确认将 {0} 加入字体库？").format(d),
                             self.window())
            box.yesButton.setText(self.tr("加入"))
            box.cancelButton.setText(self.tr("取消"))
            if box.exec():
                self.add_dirs([d])

    def _remove_dir(self, path):
        """二次确认后移除：手滑即移太危险（目录行移除按钮紧邻更新按钮）。"""
        box = MessageBox(
            self.tr("移除字体目录"),
            _box_content(
                self.tr("确定从列表移除以下目录吗？\n\n{0}\n\n仅移出列表：字体文件与已建索引不会被删除。").format(path)),
            self.window())
        box.yesButton.setText(self.tr("移除"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            self._remove_dir_confirmed(path)

    def _remove_dir_confirmed(self, path):
        self.vm.remove_dir(path)
        self._rebuild_dir_rows()
        self.refresh_status()
        # 实体集已变：订阅页（browse 清单 / subs 重分类）随之刷新
        self.libraryChanged.emit()

    # ---- 状态刷新（lib 快照任务收幕即返；目录行明细由 libdetail 任务
    #      收幕后补。两任务同在 worker 串行队列，UI 线程只消费纯数据）----
    def refresh_status(self):
        dirs = [d.path for d in self.vm.dirs]
        self._rebuild_dir_rows()
        if not dirs:
            self._apply_stats({})
            self._report_loaded()           # 无目录：同步就算就绪
            return
        self.worker.submit("lib", _lib_job, dirs)

    def _report_loaded(self):
        # singleShot(0)：空目录路径在 __init__ 里同步走到这里，直接 emit
        # 落在 main() connect 之前；挂到事件循环下一拍，装配永远先于发射
        if not self._lib_reported:
            self._lib_reported = True
            QTimer.singleShot(0, self.loadCompleted.emit)

    def _auto_update_stale(self):
        """启动首轮探测后：需更新目录立即自动开更（一次性，后续源盘变化
        仍走行上琥珀钮/更新全部手动）。被门拒（如启动间隙用户抢先拖入
        占住索引线程）不耗标志——后续 libdetail 到达自然补发。"""
        if self._auto_started:
            return
        stale = [d.path for d in self.vm.dirs if d.state == "stale"]
        if not stale or self._launch_update(stale):
            self._auto_started = True

    def _on_result(self, tag, payload):
        if tag == "lib":
            snap = payload
            self.vm.apply_snapshot(snap.lib.stats(),
                                   snap.lib.dir_overlaps(), snap.offline_dirs)
            self._apply_stats(snap.lib.stats())
            self._rebuild_dir_rows()
            self._warn_overlaps()
            self._report_loaded()
            # 收幕后补目录行明细（探测含全盘 walk，大库 1s+，不挡启动画面）
            self.worker.submit("libdetail", _libdetail_job,
                               [d.path for d in self.vm.dirs])
        elif tag == "libdetail":
            statuses, dir_stats = payload
            self.vm.apply_detail(statuses, dir_stats)
            self._rebuild_dir_rows()        # 行状态/字体数落定，不重发告警
            self._auto_update_stale()
        elif tag == "failures":
            if self._pending_failures is not None:
                show, self._pending_failures = self._pending_failures, None
                show(payload)
        elif isinstance(tag, str) and tag.startswith("retry:"):
            self._retry_settled()

    def _submit_retry(self, dr: str, rel: str):
        """失败清单的重试回调：索引更新在途时拒绝（retry_bad rw 开库
        DELETE+重解析，与 IndexWorker 并发写同一 fontindex.db → 锁冲突
        /重复重建）。"""
        self._submit_retry_batch({dr: [(rel, "")]})

    def _submit_retry_batch(self, bad: dict[str, list[tuple[str, str]]]):
        """重试回调（重试全部按目录聚合）：每目录一单——一次 DELETE 清
        该目录全部坏行 + 单次 update_dir，逐文件曾是 N 坏文件 = N 次全
        目录扫描（审查三轮 P2）。门禁与单文件路径同款。"""
        if self._index_worker is not None:
            InfoBar.warning(self.tr("索引更新中"),
                            self.tr("请等索引更新完成后再重试失败文件"),
                            parent=self)
            return
        for dr, rows in bad.items():
            self._retry_outstanding += 1
            self.worker.submit(f"retry:{dr}", _retry_batch_job, dr,
                               [rel for rel, _reason in rows])

    def _retry_settled(self):
        """重试收尾去抖：逐笔回执各刷一轮全目录探测曾是 N 坏文件=N 轮
        lib+libdetail 灌满串行队列（搜索/预览全饿死）——尾触发一次。"""
        self._retry_outstanding = max(0, self._retry_outstanding - 1)
        if self._retry_outstanding == 0:
            self.refresh_status()

    def _on_failed(self, tag, err):
        if tag == "lib":
            InfoBar.error(self.tr("字体库加载失败"), err, parent=self)
            self._report_loaded()           # 失败也要收启动画面，不能卡死在盖板后
        elif tag == "failures":
            self._pending_failures = None      # 允许再次点开失败清单
            InfoBar.error(self.tr("失败清单读取失败"), err, parent=self)
        elif tag == "libdetail":
            InfoBar.warning(self.tr("目录状态探测失败"), err, parent=self)
        elif isinstance(tag, str) and tag.startswith("retry:"):
            InfoBar.error(self.tr("重试失败"), err, parent=self)
            self._retry_settled()

    def _apply_stats(self, stats):
        for key, _ in _STAT_KEYS:
            self.stat_cards[key].setText(str(stats.get(key, "—")))

    def _warn_overlaps(self):
        if self.vm.overlaps:
            InfoBar.warning(self.tr("目录重叠"), "；".join(self.vm.overlaps),
                            parent=self)
        if self.vm.failed_dirs:
            InfoBar.warning(self.tr("目录不可用"),
                            "；".join(self.vm.failed_dirs), parent=self)

    # ---- 索引更新 ----
    def _on_update_clicked(self):
        """更新中点击=取消（按钮原地变身，不再有独立取消按钮）。"""
        if self._index_worker is not None:
            self._cancel_update()
        else:
            self._start_update()

    def _start_update(self):
        dirs = [d.path for d in self.vm.dirs]
        if dirs:
            self._launch_update(dirs)

    def _start_update_dir(self, path):
        """单目录更新：与「更新全部索引」同一条 IndexWorker 通路，
        任一更新在途即不可再启（索引线程独占写库）。"""
        if path in [d.path for d in self.vm.dirs]:
            self._launch_update([path])

    def _force_update_dir(self, path):
        """「已最新」行的更新入口：增量无活可干，确认后清库强制重建。
        重建耗时与首建相当，必须让用户显式知情。"""
        box = MessageBox(
            self.tr("强制更新索引"),
            _box_content(
                self.tr('是否删除 "{0}" 的已有索引数据库，然后强制更新索引？这将花费一些时间。').format(path)),
            self.window())
        box.yesButton.setText(self.tr("确定"))
        box.cancelButton.setText(self.tr("取消"))
        if box.exec():
            # 旧索引即清：行内派生统计（字体/简繁/失败重复）与总统计卡
            # 同步减去旧值，不带旧数字跑完全程
            self.vm.reset_stats(path)
            self._apply_stats(self.vm.stats)
            self._launch_update([path], force=True)

    def _launch_update(self, dirs: list[str], force: bool = False) -> bool:
        """返回是否真正启动；被门拒（更新/重试在途）返回 False，调用方
        （如启动自动开更）据此不消费触发时机。"""
        if not dirs or self._index_worker is not None:
            return False
        if self._retry_outstanding:
            # 重试门反向：retry_bad rw 开库与 IndexWorker 并发写同库曾锁
            # 冲突——重试在途时不启动索引更新
            InfoBar.warning(self.tr("重试进行中"),
                            self.tr("请等失败文件重试完成后再更新索引"),
                            parent=self)
            return False
        for d in dirs:
            self.vm.mark_indexing(d)
        # 先持 worker 再重排行：行内 upd/rm 按钮的在途禁用读该字段
        self._index_worker = IndexWorker(
            dirs, max_threads=self.settings.get("max_threads"),
            force=set(dirs) if force else None)
        self._rebuild_dir_rows()
        self._set_updating(True)
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.finished_dir.connect(self._on_index_done)
        self._index_worker.failed.connect(self._on_index_failed)
        self._index_worker.finished.connect(self._on_index_all_done)
        self._index_worker.start()
        return True

    def _on_index_progress(self, d, done, total, res):
        self._index_progress[d] = (done, total)
        # 宽度锁定期间计数不进文字（会撑宽/塌缩），进度走 tooltip
        self.btn_update.setToolTip(
            self.tr("正在更新索引（{0}/{1}），点击取消").format(
                done, total))  # 过滤器建时已装
        w = self._progress_widgets.get(d)
        if w is None or not total:
            return                        # 首笔回执前不定态条自转，无需动
        ind, det, lbl = w
        if det.maximum() != total:        # 首笔定态回执先立量程（默认 0 会钳值）
            det.setRange(0, total)
        det.setValue(done)                # 行存活期内原地推进，不重排行
        lbl.setText(_progress_text(done, total))
        if not ind.isHidden():            # isHidden 不依赖窗口显示状态
            ind.stop()                    # loop 动画随隐藏停掉，防空转
            ind.hide()
            det.show()

    def _stop_dir(self, path: str):
        """行内「更新中」按钮：单停该目录（排队即跳、在途中断剩余文件），
        其余目录照常跑完。"""
        if self._index_worker is not None:
            self._index_worker.cancel_dir(path)

    def _on_index_done(self, d, res):
        self._index_progress.pop(d, None)
        if not getattr(res, "cancelled", False):
            self.vm.mark_done(d)           # 取消的目录不标「已最新」
        else:
            self.vm.mark_pending(d)        # 行即刻脱「更新中」，终扫再定状态
        self._rebuild_dir_rows()

    def _on_index_failed(self, d, err):
        self._index_progress.pop(d, None)
        self.vm.mark_pending(d)
        self._rebuild_dir_rows()
        InfoBar.error(self.tr("索引更新失败"), f"{d}: {err}", parent=self)

    def _on_index_all_done(self):
        self._index_worker = None
        # 取消路径没有逐目录回执：残留 indexing 行统一复位，探测结果
        # 才能盖定真实状态（修复「取消后徽章停在索引中直到重启」）
        self._index_progress.clear()
        self.vm.clear_indexing()
        self._set_updating(False)
        self.refresh_status()
        # 索引内容变了（新索引字体/去重变化），与删目录同病同治
        self.libraryChanged.emit()

    def _set_updating(self, on: bool):
        """更新态切换：图标位换自绘旋转弧、文字换「取消更新」，宽度
        锁定为进入前值（「取消更新」比原文短，不锁会塌缩跳位）。"""
        if on:
            w = max(self.btn_update.width(),
                    self.btn_update.sizeHint().width())
            self.btn_update.setFixedWidth(w)
            self.btn_update.setText(self.tr("取消更新"))
            self._busy_i = 0
            self.btn_update.setIcon(self._busy_frames()[0])
            self._busy_timer.start()
            self.btn_update.setToolTip(self.tr("正在更新索引，点击取消"))  # 同上
        else:
            self._busy_timer.stop()
            self.btn_update.setText(self.tr("更新全部"))
            self.btn_update.setIcon(FIF.UPDATE)
            # 16777215 = QWIDGETSIZE_MAX（PySide6 未导出该常量）
            self.btn_update.setMinimumWidth(0)
            self.btn_update.setMaximumWidth(16777215)
            self.btn_update.setToolTip("")

    def _busy_frames(self) -> list[QIcon]:
        """旋转弧逐帧 QIcon（qfw 无内置忙碌图标）。更新按钮已是普通灰底
        （主色让给「添加目录」），弧色随主题取对比色；按 DPR 出图防高分
        屏发虚——PreviewBar 贴图同款教训。"""
        dark = isDarkTheme()
        if self._busy_icons is None or self._busy_dark != dark:
            self._busy_dark = dark
            dpr = self.btn_update.devicePixelRatioF()
            frames = []
            for i in range(12):
                pm = QPixmap(int(16 * dpr), int(16 * dpr))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(Qt.white if dark else Qt.black, 2,
                              Qt.SolidLine, Qt.RoundCap))
                p.drawArc(1, 1, 14, 14, -(i * 30) * 16, -100 * 16)
                p.end()
                frames.append(QIcon(pm))
            self._busy_icons = frames
        return self._busy_icons

    def _tick_busy(self):
        frames = self._busy_frames()
        self._busy_i = (self._busy_i + 1) % len(frames)
        self.btn_update.setIcon(frames[self._busy_i])

    def _cancel_update(self):
        if self._index_worker is not None:
            self._index_worker.cancel()
            # 用户显式取消 = 亲自裁定本次不开更：消费掉启动自动开更时机，
            # 否则 stale 目录仍在，libdetail 迟到到达时 _auto_update_stale
            # 会补发开更，覆盖用户的取消（审查三轮 P2）
            self._auto_started = True

    def shutdown(self) -> bool:
        """退出汇合：取消在途索引更新并等待线程收尾（窗口关闭时先于
        worker.shutdown 调用，给合作退出一个机会）。返回是否已收尾；
        False 时 app.closeEvent 走 os._exit 硬杀（SQLite WAL+事务保证
        不损坏，2026-11-02 拍板）——本方法只管 2s 宽限内能撤则撤。cancel
        只在文件间生效，单文件大字库解析可超 2s。"""
        w = self._index_worker
        if w is not None and w.isRunning():
            w.cancel()
            return bool(w.wait(2000))
        return True

    # ---- 失败清单 ----
    def _show_failures(self):
        if self._pending_failures is not None:   # 已有清单任务在途，防重复弹窗
            return
        dirs = [d.path for d in self.vm.dirs]

        def show(payload):
            dlg = _FailuresDialog(payload,
                                  retry_all=self._submit_retry_batch,
                                  parent=self)
            dlg.exec()

        self._pending_failures = show
        self.worker.submit("failures", _failures_job, dirs)


class _FailuresDialog(MessageBoxBase):
    """失败清单：只读全文 + 重试全部（fluent 风格弹窗）。"""

    def __init__(self, data, retry_all, parent=None):
        super().__init__(parent)
        self._retry_all = retry_all
        self._bad = data.get("bad", {}) or {}

        self.titleLabel = SubtitleLabel(self.tr("失败清单"))
        hint = CaptionLabel(
            self.tr("重试全部会重新解析下列文件；对目录重新索引同样有效。"))
        hint.setWordWrap(True)
        text = TextEdit()
        text.setReadOnly(True)
        text.setMinimumHeight(140)
        lines = []
        if not self._bad:
            lines.append(self.tr("各目录暂无解析失败文件。"))
        for d, rows in self._bad.items():
            lines.append(f"【{d}】")
            for rel, reason in rows:
                lines.append(f"  {rel} —— {reason}")
        skipped = data.get("skipped", [])
        if skipped:
            lines.append(self.tr("【跳过的重复版本】"))
            # 结构化条目 (dir, rel, face, inst)，文案在 GUI 侧拼 tr——
            # core 拼简体串曾残留繁体界面（审查三轮 P2）
            lines.extend(f"  {d}/{rel}#{fi}i{ix} {self.tr('重复版本')}"
                         for d, rel, fi, ix in skipped)
        text.setPlainText("\n".join(lines))
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(hint)
        self.viewLayout.addWidget(text, 1)
        # 窄窗下固定最小宽会横向溢出，与 FontPickerDialog 同款钳制
        self.widget.setFixedWidth(min(720, max(self.width() - 48, 320)))
        # 按钮区钉在 widget 底部：高度封在窗口内，正文超出交给 TextEdit 滚动
        win_h = self.window().height() if self.window() is not None else 0
        if win_h > 350:
            self.widget.setMaximumHeight(win_h - 32)
        if self._bad:
            self.yesButton.setText(self.tr("重试全部"))
            self.cancelButton.setText(self.tr("关闭"))
        else:
            self.hideYesButton()
            self.cancelButton.setText(self.tr("关闭"))

    def validate(self) -> bool:
        if self._bad:
            self._retry_all(self._bad)
        return True


def _dir_library_stats(snap, dirs: list[str]) -> dict:
    """每目录库内统计（worker 线程纯数据）：字体/支持简繁/重复跳过数。

    实体 dir_path 是 str(Path(dir)) 规范化形（反斜杠），目录表是原样
    字符串（可能正斜杠），按 Path 归一映射回原键；skipped 条目是结构化
    (dir, rel, face, inst)，dir 同样按归一键对上。"""
    out = {d: {"fonts": 0, "sc": 0, "tc": 0, "skipped": 0} for d in dirs}
    by_norm = {str(Path(d)): d for d in dirs}
    for e in snap.lib.entities():
        key = by_norm.get(e.dir_path)
        if key is None:
            continue
        s = out[key]
        s["fonts"] += 1
        if e.script in ("简", "简繁"):
            s["sc"] += 1
        if e.script in ("繁", "简繁"):
            s["tc"] += 1
    for d_dir, _rel, _fi, _ix in snap.lib.skipped():
        key = by_norm.get(str(Path(d_dir)))
        if key is not None:
            out[key]["skipped"] += 1
    return out


def _lib_job(ctx, dirs):
    """worker 线程：只做聚合库快照（启动画面收幕只等它）。逐目录探测
    拆到 _libdetail_job 收幕后补——probe 全盘 walk+逐文件 stat，大库
    1s+，只喂目录行状态标记，不值得挡住收幕。"""
    return ensure_library(ctx, dirs)


def _libdetail_job(ctx, dirs):
    """worker 线程：逐目录只读探测（probe 含逐文件 stat）+ 每目录库内
    统计。ensure_library 命中 ctx 缓存（_lib_job 刚建好），零重复开销。"""
    snap = ensure_library(ctx, dirs)
    return [probe_dir_status(d) for d in dirs], _dir_library_stats(snap, dirs)


def _failures_job(ctx, dirs):
    ensure_library(ctx, dirs)
    return read_failures(ctx)


def _retry_batch_job(ctx, d, rels):
    return retry_bad_batch(ctx, d, rels)
