from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from qfluentwidgets import CaptionLabel

from ass_font_substitute.core.similarity.render import RenderBundle


def tiles_to_pixmap(tiles: list[np.ndarray]) -> QPixmap:
    """48×48 灰度 tile 横排成条；空列表返回空 QPixmap。"""
    if not tiles:
        return QPixmap()
    row = np.concatenate([np.pad(t, ((0, 0), (0, 1))) for t in tiles], axis=1)
    h, w = row.shape
    img = QImage(row.data, w, h, w, QImage.Format_Grayscale8)
    return QPixmap.fromImage(img)


def _bundle_tiles(payload):
    """render_preview 返回 RenderBundle（tiles 为 {字符: 48×48 数组}，按预览
    文字顺序）；统一成 tile 列表——缓存与 tiles_to_pixmap 只认列表。"""
    if isinstance(payload, RenderBundle):
        return list(payload.tiles.values())
    return payload


class PreviewBar(CaptionLabel):
    """单行预览条：占位 → 异步渲染 → QPixmap。"""

    def __init__(self, worker, pvm, entity, text, tag, height=28, parent=None):
        # qfluentwidgets FluentLabelBase.__init__ 是 singledispatchmethod，
        # (text, parent) 分支会经 self.__init__ 重派发——子类下会递归回本构造，
        # 故只传 parent、占位文字改用 setText（1.11.3 API 适配，语义不变）
        super().__init__(parent)
        self.setText("…")
        self.setFixedHeight(height)
        self._worker, self._pvm = worker, pvm
        self._entity, self._text, self._tag = entity, text, tag
        self._inflight = False
        self._render()

    def _render(self):
        if not self._text.strip() or self._entity is None:
            self.setText(self._entity.display_name if self._entity else "")
            return
        key = (self._entity.fingerprint, self._text)
        cached = self._pvm.get_tiles(key)
        if cached is not None:
            self._apply(cached)
            return
        if self._inflight:               # 在途不重复派发（可视区重复触发廉价）
            return
        self._inflight = True
        self._worker.result.connect(self._on_result)
        self._worker.submit(self._tag, _render_job, self._entity, self._text)

    def _on_result(self, tag, payload):
        if tag != self._tag:
            return
        text, bundle = payload
        if text != self._text:
            # 同 tag（fp + 列表实例）的在途旧任务——行已随 set_rows 重建：
            # 结果的文字与本行不符，直接丢弃，不清缓存不置位
            return
        self._worker.result.disconnect(self._on_result)
        self._inflight = False
        tiles = _bundle_tiles(bundle)
        self._pvm.put_tiles((self._entity.fingerprint, self._text), tiles)
        self._apply(tiles)

    def _apply(self, tiles):
        pm = tiles_to_pixmap(tiles)
        if pm.isNull():
            self.setText("(无可渲染字形)")
            return
        self.setPixmap(pm.scaledToHeight(self.height(),
                                         Qt.SmoothTransformation))


def _render_job(ctx, entity, text):
    from ass_font_substitute.gui.jobs import render_preview
    # 回传 text：结果按 (tag, text) 双重匹配，防换文字后旧在途结果错配缓存键
    return text, render_preview(entity, text)
