from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ass_font_substitute.gui.lastdir import pick_existing_dir
from ass_font_substitute.gui.pages.subs_page import _ElidedLabel
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, IconWidget,
                            InfoBadge, InfoBar, MessageBox, MessageBoxBase,
                            PrimaryPushButton, PushButton, ScrollArea,
                            StrongBodyLabel, SubtitleLabel, TextEdit,
                            ToolButton, isDarkTheme, setFont)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.jobs import (ensure_library, probe_dir_status,
                                          read_failures, retry_bad)
from ass_font_substitute.gui.library_vm import LibraryVM
from ass_font_substitute.gui.workers import IndexWorker

_STAT_KEYS = [("dirs", "字体目录"), ("files", "文件总数"), ("fonts", "字体总数"),
              ("support_sc", "支持简体"), ("support_tc", "支持繁体")]


class LibraryPage(ScrollArea):
    libraryChanged = Signal()
    """目录增删或索引更新完成：订阅页重拉库数据（browse 清单/ subs 重分类）"""

    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("libraryPage")
        self.vm = LibraryVM(settings)
        self._index_worker: IndexWorker | None = None
        self._pending_failures = None       # "failures" 任务结果到达后的弹窗回调

        self.view = QWidget(self)
        v = QVBoxLayout(self.view)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        # 统计条置顶、操作条其下：打开页先读库概况再看到操作
        self.stat_cards: dict[str, BodyLabel] = {}
        stat_card = CardWidget()
        stat_row = QHBoxLayout(stat_card)
        stat_row.setContentsMargins(24, 14, 24, 14)
        stat_row.setSpacing(0)
        for i, (key, label) in enumerate(_STAT_KEYS):
            if i:
                dark = isDarkTheme()
                sep = QFrame()
                sep.setFixedSize(1, 30)
                sep.setStyleSheet(
                    f"background-color: rgba({'255,255,255' if dark else '0,0,0'},"
                    f"{36 if dark else 18});")
                stat_row.addWidget(sep, 0, Qt.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(2)
            title = CaptionLabel(label)
            title.setAlignment(Qt.AlignCenter)
            val = BodyLabel("—")
            val.setAlignment(Qt.AlignCenter)
            setFont(val, 18, QFont.Weight.DemiBold)   # 数值层级高于标题
            self.stat_cards[key] = val
            col.addWidget(title)
            col.addWidget(val)
            stat_row.addLayout(col, 1)
        v.addWidget(stat_card)

        head = QHBoxLayout()
        self.btn_add = PushButton(FIF.FOLDER_ADD, "添加字体目录")
        self.btn_refresh = PushButton(FIF.SYNC, "刷新状态")
        self.btn_failures = PushButton(FIF.INFO, "查看失败清单")
        self.btn_update = PrimaryPushButton(FIF.UPDATE, "更新全部索引")
        for b in (self.btn_add, self.btn_refresh, self.btn_failures):
            head.addWidget(b)
        head.addStretch(1)
        head.addWidget(self.btn_update)     # 主操作靠右
        v.addLayout(head)

        # 更新态：按钮原地变「[旋转环]取消更新」，宽度锁定防文字变短塌缩
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(50)
        self._busy_timer.timeout.connect(self._tick_busy)
        self._busy_i = 0
        self._busy_icons: list[QIcon] | None = None

        self.dirs_box = QVBoxLayout()
        v.addLayout(self.dirs_box)
        v.addStretch(1)

        self.btn_add.clicked.connect(self._add_dir)
        self.btn_refresh.clicked.connect(self.refresh_status)
        self.btn_update.clicked.connect(self._on_update_clicked)
        self.btn_failures.clicked.connect(self._show_failures)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.refresh_status()

    # ---- 目录列表 ----
    _BADGE_MAKER = {"ok": InfoBadge.success, "stale": InfoBadge.warning,
                    "indexing": InfoBadge.info, "offline": InfoBadge.error}

    def _rebuild_dir_rows(self):
        while self.dirs_box.count():
            item = self.dirs_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for d in self.vm.dirs:
            card = CardWidget()
            h = QHBoxLayout(card)
            h.setContentsMargins(16, 10, 12, 10)
            h.setSpacing(12)
            icon = IconWidget(FIF.FOLDER, card)
            icon.setFixedSize(22, 22)
            h.addWidget(icon, 0, Qt.AlignVCenter)
            mid = QVBoxLayout()
            mid.setSpacing(1)
            path_lbl = _ElidedLabel(d.path)     # 窄窗中部省略，tooltip 全路径
            path_lbl.setToolTip(d.path)
            mid.addWidget(path_lbl)
            sub = CaptionLabel(f"文件 {d.files} · 字体 {d.fonts}"
                               f" · 支持简体 {d.sc} · 支持繁体 {d.tc}")
            mid.addWidget(sub)
            h.addLayout(mid, 1)
            badge = self._BADGE_MAKER.get(d.state, InfoBadge.info)(d.badge)
            h.addWidget(badge, 0, Qt.AlignVCenter)
            upd = ToolButton(FIF.SYNC)
            upd.setToolTip("更新该目录索引")
            upd.setEnabled(self._index_worker is None and d.state != "indexing")
            upd.clicked.connect(lambda _=False, p=d.path: self._start_update_dir(p))
            h.addWidget(upd, 0, Qt.AlignVCenter)
            rm = ToolButton(FIF.DELETE)
            # 更新在途禁删：索引线程正写该目录 db，删后状态失配
            rm.setEnabled(self._index_worker is None)
            rm.clicked.connect(lambda _=False, p=d.path: self._remove_dir(p))
            h.addWidget(rm, 0, Qt.AlignVCenter)
            self.dirs_box.addWidget(card)

    def _add_dir(self):
        d = pick_existing_dir(self, self.settings, "选择字体目录", "library")
        if not d:
            return
        if not self.vm.add_dir(d):
            InfoBar.warning("提示", "该目录已在列表中", parent=self)
            return
        self._rebuild_dir_rows()
        self.refresh_status()

    def _remove_dir(self, path):
        """二次确认后移除：手滑即删太危险（目录行删除按钮紧邻更新按钮）。"""
        box = MessageBox(
            "删除字体目录",
            f"确定从列表移除以下目录吗？\n\n{path}\n\n"
            "仅移出列表：字体文件与已建索引不会被删除。",
            self.window())
        box.yesButton.setText("删除")
        box.cancelButton.setText("取消")
        if box.exec():
            self._remove_dir_confirmed(path)

    def _remove_dir_confirmed(self, path):
        self.vm.remove_dir(path)
        self._rebuild_dir_rows()
        self.refresh_status()
        # 实体集已变：订阅页（browse 清单 / subs 重分类）随之刷新
        self.libraryChanged.emit()

    # ---- 状态刷新（lib 构建与逐目录探测在 worker 线程同一任务内串行完成，
    #      UI 线程只消费纯数据，无先 probe 后 lib 的顺序竞态）----
    def refresh_status(self):
        dirs = [d.path for d in self.vm.dirs]
        self._rebuild_dir_rows()
        if not dirs:
            self._apply_stats({})
            return
        self.worker.submit("lib", _lib_job, dirs)

    def _on_result(self, tag, payload):
        if tag == "lib":
            snap, statuses, dir_stats = payload
            self.vm.refresh(statuses, snap.lib.stats(),
                            snap.lib.dir_overlaps(), snap.offline_dirs,
                            dir_stats)
            self._apply_stats(snap.lib.stats())
            self._rebuild_dir_rows()
            self._warn_overlaps()
        elif tag == "failures":
            if self._pending_failures is not None:
                show, self._pending_failures = self._pending_failures, None
                show(payload)
        elif isinstance(tag, str) and tag.startswith("retry:"):
            self.refresh_status()

    def _on_failed(self, tag, err):
        if tag == "lib":
            InfoBar.error("字体库加载失败", err, parent=self)
        elif tag == "failures":
            self._pending_failures = None      # 允许再次点开失败清单
            InfoBar.error("失败清单读取失败", err, parent=self)
        elif isinstance(tag, str) and tag.startswith("retry:"):
            InfoBar.error("重试失败", err, parent=self)

    def _apply_stats(self, stats):
        for key, _ in _STAT_KEYS:
            self.stat_cards[key].setText(str(stats.get(key, "—")))

    def _warn_overlaps(self):
        if self.vm.overlaps:
            InfoBar.warning("目录重叠", "；".join(self.vm.overlaps), parent=self)
        if self.vm.failed_dirs:
            InfoBar.warning("目录不可用", "；".join(self.vm.failed_dirs), parent=self)

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

    def _launch_update(self, dirs: list[str]):
        if not dirs or self._index_worker is not None:
            return
        for d in dirs:
            self.vm.mark_indexing(d)
        # 先持 worker 再重排行：行内 upd/rm 按钮的在途禁用读该字段
        self._index_worker = IndexWorker(dirs)
        self._rebuild_dir_rows()
        self._set_updating(True)
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.finished_dir.connect(self._on_index_done)
        self._index_worker.failed.connect(self._on_index_failed)
        self._index_worker.finished.connect(self._on_index_all_done)
        self._index_worker.start()

    def _on_index_progress(self, d, done, total, res):
        # 宽度锁定期间计数不进文字（会撑宽/塌缩），进度走 tooltip
        self.btn_update.setToolTip(f"正在更新索引（{done}/{total}），点击取消")

    def _on_index_done(self, d, res):
        if not getattr(res, "cancelled", False):
            self.vm.mark_done(d)           # 取消的目录不标「已更新」
        self._rebuild_dir_rows()

    def _on_index_failed(self, d, err):
        InfoBar.error("索引更新失败", f"{d}: {err}", parent=self)

    def _on_index_all_done(self):
        self._index_worker = None
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
            self.btn_update.setText("取消更新")
            self._busy_i = 0
            self.btn_update.setIcon(self._busy_frames()[0])
            self._busy_timer.start()
            self.btn_update.setToolTip("正在更新索引，点击取消")
        else:
            self._busy_timer.stop()
            self.btn_update.setText("更新全部索引")
            self.btn_update.setIcon(FIF.UPDATE)
            # 16777215 = QWIDGETSIZE_MAX（PySide6 未导出该常量）
            self.btn_update.setMinimumWidth(0)
            self.btn_update.setMaximumWidth(16777215)
            self.btn_update.setToolTip("")

    def _busy_frames(self) -> list[QIcon]:
        """旋转弧逐帧 QIcon（qfw 无内置忙碌图标；主色按钮底上画白弧，
        按 DPR 出图防高分屏发虚——PreviewBar 贴图同款教训）。"""
        if self._busy_icons is None:
            dpr = self.btn_update.devicePixelRatioF()
            frames = []
            for i in range(12):
                pm = QPixmap(int(16 * dpr), int(16 * dpr))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(Qt.white, 2, Qt.SolidLine, Qt.RoundCap))
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

    def shutdown(self):
        """退出汇合：取消在途索引更新并等待线程收尾（窗口关闭时先于
        worker.shutdown 调用，避免索引线程写库时进程直接退出）。"""
        w = self._index_worker
        if w is not None and w.isRunning():
            w.cancel()
            w.wait(5000)

    # ---- 失败清单 ----
    def _show_failures(self):
        if self._pending_failures is not None:   # 已有清单任务在途，防重复弹窗
            return
        dirs = [d.path for d in self.vm.dirs]

        def show(payload):
            dlg = _FailuresDialog(payload,
                                  retry=lambda dr, rel: self.worker.submit(
                                      f"retry:{dr}:{rel}", _retry_job, dr, rel),
                                  parent=self)
            dlg.exec()

        self._pending_failures = show
        self.worker.submit("failures", _failures_job, dirs)


class _FailuresDialog(MessageBoxBase):
    """失败清单：只读全文 + 重试全部（fluent 风格弹窗）。"""

    def __init__(self, data, retry, parent=None):
        super().__init__(parent)
        self._retry = retry
        self._bad = data.get("bad", {}) or {}

        self.titleLabel = SubtitleLabel("失败清单")
        hint = CaptionLabel("重试全部会重新解析下列文件；对目录重新索引同样有效。")
        hint.setWordWrap(True)
        text = TextEdit()
        text.setReadOnly(True)
        text.setMinimumHeight(140)
        lines = []
        if not self._bad:
            lines.append("各目录暂无解析失败文件。")
        for d, rows in self._bad.items():
            lines.append(f"【{d}】")
            for rel, reason in rows:
                lines.append(f"  {rel} —— {reason}")
        skipped = data.get("skipped", [])
        if skipped:
            lines.append("【跳过的旧版本】")
            lines.extend(f"  {s}" for s in skipped)
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
            self.yesButton.setText("重试全部")
            self.cancelButton.setText("关闭")
        else:
            self.hideYesButton()
            self.cancelButton.setText("关闭")

    def validate(self) -> bool:
        if self._bad:
            for d, rows in self._bad.items():
                for rel, _reason in rows:
                    self._retry(d, rel)
        return True


def _dir_library_stats(snap, dirs: list[str]) -> dict:
    """每目录库内统计（worker 线程纯数据）：文件/字体/支持简繁。

    实体 dir_path 是 str(Path(dir)) 规范化形（反斜杠），目录表是原样
    字符串（可能正斜杠），按 Path 归一映射回原键。"""
    out = {d: {"files": set(), "fonts": 0, "sc": 0, "tc": 0} for d in dirs}
    by_norm = {str(Path(d)): d for d in dirs}
    for e in snap.lib.entities():
        key = by_norm.get(e.dir_path)
        if key is None:
            continue
        s = out[key]
        s["fonts"] += 1
        s["files"].add(e.rel_path)
        if e.script in ("简", "简繁"):
            s["sc"] += 1
        if e.script in ("繁", "简繁"):
            s["tc"] += 1
    return {d: {**v, "files": len(v["files"])} for d, v in out.items()}


def _lib_job(ctx, dirs):
    """worker 线程：聚合库快照 + 逐目录只读探测（probe 含逐文件 stat，
    移出 UI 线程；与 lib 构建同任务串行，无顺序竞态）。"""
    snap = ensure_library(ctx, dirs)
    return snap, [probe_dir_status(d) for d in dirs], _dir_library_stats(snap, dirs)


def _failures_job(ctx, dirs):
    ensure_library(ctx, dirs)
    return read_failures(ctx)


def _retry_job(ctx, d, rel):
    return retry_bad(ctx, d, rel)
