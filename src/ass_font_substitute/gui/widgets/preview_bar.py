from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtGui import QImage
from PySide6.QtGui import QPixmap
from qfluentwidgets import CaptionLabel
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig

from ass_font_substitute.core.similarity.render import RenderBundle

# 贴图条宽度上限：长字体名（PostScript 名十几字符）逐字 tile 横排会远超
# 卡片宽，被右缘硬裁出半个字母；超宽缩到上限，绝不截半字
_MAX_BAR_W = 360


def tiles_to_pixmap(tiles: list[np.ndarray]) -> QPixmap:
    """48×48 灰度 tile 横排成条；空列表返回空 QPixmap。

    掩码值作 alpha、字形色随主题（暗白亮黑）、底全透明——贴在哪种卡面
    （预览页/选择器/替换卡）都露底色，换肤时 PreviewBar 重贴即可换色。"""
    if not tiles:
        return QPixmap()
    row = np.concatenate([np.pad(t, ((0, 0), (0, 1))) for t in tiles], axis=1)
    h, w = row.shape
    c = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = c.red(), c.green(), c.blue()
    rgba[..., 3] = row
    img = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
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
        self._tiles = None                   # 最近一次贴上的 tiles，换肤重贴用
        self._stale_theme = False            # 不可见期换肤未贴上，showEvent 补
        qconfig.themeChanged.connect(self._on_theme_changed)
        self._render()

    def _on_theme_changed(self, _theme):
        # 贴图字形色按主题取：换肤用已缓存的 tiles 原地重贴（零渲染开销）。
        # 高度为 0（布局未跑/被压塌）时缩图得 null，跳过并记脏留给 showEvent
        if self._tiles is None:
            return
        if self.height() > 0:
            self._apply(self._tiles)
        else:
            self._stale_theme = True

    def showEvent(self, e):
        super().showEvent(e)
        # 不可见期（高度 0 跳过）换肤的补贴：旧贴图非空，得靠脏标记识别
        if self._stale_theme and self.height() > 0:
            self._stale_theme = False
            self._apply(self._tiles)

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
        # 回传 text：结果按 (tag, text) 双重匹配，防换文字后旧在途结果错配
        # 缓存键；渲染失败也折进本通路（err 字段）——failed 信号不带 text
        # 无法做同样的护栏，同 tag 旧任务的失败会错标新行
        text, bundle, err = payload
        if text != self._text:
            # 同 tag（fp + 列表实例）的在途旧任务——行已随 set_rows 重建：
            # 结果的文字与本行不符，直接丢弃，不清缓存不置位
            return
        self._worker.result.disconnect(self._on_result)
        self._inflight = False
        if err is not None:
            self.setText("（渲染失败）")
            self.setToolTip(err)        # hover 可见具体异常，排查怪字体
            return
        tiles = _bundle_tiles(bundle)
        self._pvm.put_tiles((self._entity.fingerprint, self._text), tiles)
        self._apply(tiles)

    def _apply(self, tiles):
        pm = tiles_to_pixmap(tiles)
        if pm.isNull():
            self.setText("（无可渲染字形）")
            return
        self._tiles = tiles
        pm = pm.scaledToHeight(self.height(), Qt.SmoothTransformation)
        if pm.width() > _MAX_BAR_W:
            pm = pm.scaledToWidth(_MAX_BAR_W, Qt.SmoothTransformation)
        self.setPixmap(pm)


def _render_job(ctx, entity, text):
    from ass_font_substitute.gui.jobs import render_preview
    try:
        bundle = render_preview(entity, text)
    except Exception as e:
        return text, None, f"{type(e).__name__}: {e}"
    return text, bundle, None
