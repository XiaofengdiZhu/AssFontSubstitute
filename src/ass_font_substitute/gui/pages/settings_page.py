from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, Dialog, InfoBar, LineEdit,
                            PushButton, ScrollArea, StrongBodyLabel, TextEdit)
from qfluentwidgets import FluentIcon as FIF

from ass_font_substitute.gui.settings_store import (COV_BOUNDS, SIM_BOUNDS,
                                                    SIZE_BOUNDS)


def _stepper(lo, hi, step, val, suffix=""):
    from qfluentwidgets import SpinBox
    s = SpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    s.setSuffix(suffix)
    return s


class SettingsPage(ScrollArea):
    def __init__(self, settings, worker, parent=None):
        super().__init__(parent)
        self.settings, self.worker = settings, worker
        self.setObjectName("settingsPage")

        self.view = QWidget(self)
        v = QVBoxLayout(self.view)
        self.setWidget(self.view)
        self.setWidgetResizable(True)

        v.addWidget(StrongBodyLabel("默认参数（处理字幕页初始值）"))
        self.spin_cov = _stepper(int(COV_BOUNDS[0] * 100), int(COV_BOUNDS[1] * 100),
                                 1, int(settings.get("cov_threshold") * 100), "%")
        self.spin_sim = _stepper(int(SIM_BOUNDS[0] * 100), int(SIM_BOUNDS[1] * 100),
                                 5, int(settings.get("min_sim") * 100), "%")
        self.spin_smin = _stepper(int(SIZE_BOUNDS[0] * 100), int(SIZE_BOUNDS[1] * 100),
                                  1, int(settings.get("size_min") * 100), "%")
        self.spin_smax = _stepper(int(SIZE_BOUNDS[0] * 100), int(SIZE_BOUNDS[1] * 100),
                                  1, int(settings.get("size_max") * 100), "%")
        self.spin_pause = _stepper(1, 50, 1, int(settings.get("n_pause")))
        self.spin_threads = _stepper(1, max(1, os.cpu_count() or 4), 1,
                                     int(settings.get("max_threads")))
        self.spin_threads.setToolTip("建索引并行解析上限（并行解析尚未启用，当前仅存储）")
        rows = [("覆盖率阈值", self.spin_cov), ("最低相似率", self.spin_sim),
                ("相对大小下限", self.spin_smin), ("相对大小上限", self.spin_smax),
                ("找到几个相似字体后暂停", self.spin_pause),
                ("最大使用线程数", self.spin_threads)]
        for label, w in rows:
            h = QHBoxLayout()
            h.addWidget(BodyLabel(label))
            h.addStretch(1)
            h.addWidget(w)
            v.addLayout(h)
        self.spin_cov.valueChanged.connect(
            lambda x: self.settings.set("cov_threshold", x / 100))
        self.spin_sim.valueChanged.connect(
            lambda x: self.settings.set("min_sim", x / 100))
        self.spin_smin.valueChanged.connect(
            lambda x: (self.settings.set("size_min", x / 100),
                       self.spin_smax.setMinimum(x)))
        self.spin_smax.valueChanged.connect(
            lambda x: (self.settings.set("size_max", x / 100),
                       self.spin_smin.setMaximum(x)))
        self.spin_pause.valueChanged.connect(
            lambda x: self.settings.set("n_pause", x))
        self.spin_threads.valueChanged.connect(
            lambda x: self.settings.set("max_threads", x))

        v.addWidget(StrongBodyLabel("输出"))
        h = QHBoxLayout()
        h.addWidget(BodyLabel("另存后缀"))
        h.addStretch(1)
        # qfluentwidgets 1.11.3：LineEdit 只接受 parent，文字经 setText
        self.suffix_edit = LineEdit()
        self.suffix_edit.setText(settings.get("save_suffix"))
        h.addWidget(self.suffix_edit)
        v.addLayout(h)
        self.suffix_edit.editingFinished.connect(self._save_suffix)

        v.addWidget(StrongBodyLabel("相似度对比文字"))
        self.sample_preview = CaptionLabel(settings.get("sample_text"))
        self.sample_preview.setWordWrap(True)
        v.addWidget(self.sample_preview)
        btn_edit = PushButton(FIF.EDIT, "编辑…")
        btn_edit.clicked.connect(self._edit_sample)
        v.addWidget(btn_edit, 0, Qt.AlignLeft)

        v.addWidget(StrongBodyLabel("相似度缓存"))
        self.cache_label = CaptionLabel("…")
        v.addWidget(self.cache_label)
        h = QHBoxLayout()
        btn_clear = PushButton("清除缓存")
        btn_move = PushButton("更改位置…")
        for b in (btn_clear, btn_move):
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        btn_clear.clicked.connect(self._clear_cache)
        btn_move.clicked.connect(self._move_cache)
        v.addStretch(1)

        self.worker.result.connect(self._on_result)
        self._refresh_cache_stats()

    def _save_suffix(self):
        try:
            self.settings.set("save_suffix", self.suffix_edit.text().strip())
        except ValueError:
            InfoBar.error("后缀无效", "必须以点开头且非空，如 .substituted.ass",
                          parent=self)
            self.suffix_edit.setText(self.settings.get("save_suffix"))

    def _edit_sample(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑相似度对比文字")
        dlg.resize(480, 320)
        v = QVBoxLayout(dlg)
        warn = BodyLabel("保存后已有的相似度结果将全部失效，下次使用时自动按新文字重新计算。")
        warn.setWordWrap(True)
        v.addWidget(warn)
        edit = TextEdit()
        edit.setPlainText(self.settings.get("sample_text"))
        v.addWidget(edit)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        if dlg.exec():
            try:
                self.settings.set("sample_text", edit.toPlainText())
                self.sample_preview.setText(edit.toPlainText())
            except ValueError:
                InfoBar.error("对比文字不能为空", "", parent=self)

    def _refresh_cache_stats(self):
        self.worker.submit("cachestat", _stat_job)

    def _on_result(self, tag, payload):
        if tag == "cachestat":
            mb = payload["render_bytes"] / 1024 / 1024
            self.cache_label.setText(
                f"位置 {payload['path']} · 渲染条 {mb:.1f} MB · 相似度记录 {payload['pair_count']} 条")
        elif tag == "cacheclear":
            InfoBar.success("缓存已清除", "", parent=self)
            self._refresh_cache_stats()
        elif tag == "cachemove":
            InfoBar.success("缓存位置已更改", payload, parent=self)
            self._refresh_cache_stats()

    def _clear_cache(self):
        w = Dialog("清除相似度缓存", "将删除全部渲染条与相似度记录，下次搜索重新计算。确定？",
                   self)
        if w.exec():
            self.worker.submit("cacheclear", _clear_job)

    def _move_cache(self):
        d = QFileDialog.getExistingDirectory(self, "选择新缓存位置")
        if not d:
            return
        new = Path(d) / "cache.db"
        migrate = True
        if new.exists():
            w = Dialog("目标已有缓存文件",
                       "迁移将覆盖它；选「否」则丢弃现有缓存从空开始。", self)
            migrate = bool(w.exec())
        self.worker.submit("cachemove", _move_job, str(new), migrate)


def _stat_job(ctx):
    from ass_font_substitute.gui.jobs import cache_stats
    return cache_stats(ctx)


def _clear_job(ctx):
    from ass_font_substitute.gui.jobs import cache_clear
    cache_clear(ctx)


def _move_job(ctx, new_path, migrate):
    from ass_font_substitute.gui.jobs import cache_move
    return cache_move(ctx, new_path, migrate)
