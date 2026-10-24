from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ass_font_substitute.gui.lastdir import pick_existing_dir
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget,
                            InfoBar, MessageBoxBase, PrimaryPushButton,
                            PushButton, ScrollArea, StrongBodyLabel,
                            SubtitleLabel, TextEdit, ToolButton)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.jobs import (ensure_library, probe_dir_status,
                                          read_failures, retry_bad)
from ass_font_substitute.gui.library_vm import LibraryVM
from ass_font_substitute.gui.workers import IndexWorker

_STAT_KEYS = [("dirs", "字体目录"), ("files", "文件总数"), ("fonts", "字体总数"),
              ("support_sc", "支持简体"), ("support_tc", "支持繁体")]


class LibraryPage(ScrollArea):
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

        head = QHBoxLayout()
        self.btn_add = PushButton(FIF.FOLDER_ADD, "添加字体目录")
        self.btn_refresh = PushButton(FIF.SYNC, "刷新状态")
        self.btn_update = PrimaryPushButton(FIF.UPDATE, "更新全部索引")
        for w in (self.btn_add, self.btn_refresh, self.btn_update):
            head.addWidget(w)
        head.addStretch(1)
        v.addLayout(head)

        # 更新态：按钮原地变「[旋转环]取消更新」，宽度锁定防文字变短塌缩
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(50)
        self._busy_timer.timeout.connect(self._tick_busy)
        self._busy_i = 0
        self._busy_icons: list[QIcon] | None = None

        self.btn_failures = PushButton(FIF.INFO, "查看失败清单")
        v.addWidget(self.btn_failures, 0, Qt.AlignLeft)

        self.stat_cards: dict[str, BodyLabel] = {}
        stat_row = QHBoxLayout()
        for key, label in _STAT_KEYS:
            card = CardWidget()
            cv = QVBoxLayout(card)
            title = CaptionLabel(label)
            val = BodyLabel("—")
            val.setAlignment(Qt.AlignCenter)
            self.stat_cards[key] = val
            cv.addWidget(title)
            cv.addWidget(val)
            stat_row.addWidget(card)
        v.addLayout(stat_row)

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
    def _rebuild_dir_rows(self):
        while self.dirs_box.count():
            item = self.dirs_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for d in self.vm.dirs:
            card = CardWidget()
            h = QHBoxLayout(card)
            h.addWidget(StrongBodyLabel(d.path), 1)
            badge = BodyLabel(d.badge)
            badge.setObjectName("dirBadge")
            h.addWidget(badge)
            upd = ToolButton(FIF.SYNC)
            upd.setToolTip("更新该目录索引")
            upd.setEnabled(self._index_worker is None and d.state != "indexing")
            upd.clicked.connect(lambda _=False, p=d.path: self._start_update_dir(p))
            h.addWidget(upd)
            rm = ToolButton(FIF.DELETE)
            rm.clicked.connect(lambda _=False, p=d.path: self._remove_dir(p))
            h.addWidget(rm)
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
        self.vm.remove_dir(path)
        self._rebuild_dir_rows()
        self.refresh_status()

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
            snap, statuses = payload
            self.vm.refresh(statuses, snap.lib.stats(),
                            snap.lib.dir_overlaps(), snap.offline_dirs)
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
        self._rebuild_dir_rows()
        self._set_updating(True)
        self._index_worker = IndexWorker(dirs)
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


def _lib_job(ctx, dirs):
    """worker 线程：聚合库快照 + 逐目录只读探测（probe 含逐文件 stat，
    移出 UI 线程；与 lib 构建同任务串行，无顺序竞态）。"""
    snap = ensure_library(ctx, dirs)
    return snap, [probe_dir_status(d) for d in dirs]


def _failures_job(ctx, dirs):
    ensure_library(ctx, dirs)
    return read_failures(ctx)


def _retry_job(ctx, d, rel):
    return retry_bad(ctx, d, rel)
