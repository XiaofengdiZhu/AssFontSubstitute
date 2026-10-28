from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtGui import QImage
from PySide6.QtGui import QPixmap
from qfluentwidgets import CaptionLabel
from qfluentwidgets import isDarkTheme
from qfluentwidgets import qconfig

from ass_font_substitute.gui.tip import install_tip

# 贴图条宽度静态上限（布局未跑时的兜底）；布局定宽后取实际可用宽再扣
# 呼吸边——顶满标签宽会末字贴死右缘、细笔画被裁半个
_MAX_BAR_W = 360
_WIDTH_MARGIN = 16


def strip_to_pixmap(strip: np.ndarray, dpr: float = 1.0) -> QPixmap:
    """灰度条带（render_text_strip 产物，H×W 掩码）→ QPixmap。

    掩码值作 alpha、字形色随主题（暗白亮黑）、底全透明——贴在哪种卡面
    （预览页/选择器/替换卡）都露底色，换肤时 PreviewBar 重贴即可换色。
    dpr>1 时标记 devicePixelRatio，物理分辨率直接交给 Qt 显示，不缩放。"""
    if strip is None or strip.size == 0:
        return QPixmap()
    h, w = strip.shape
    c = QColor(255, 255, 255) if isDarkTheme() else QColor(0, 0, 0)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = c.red(), c.green(), c.blue()
    rgba[..., 3] = strip
    img = QImage(rgba.data, w, h, w * 4, QImage.Format_RGBA8888)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


class PreviewBar(CaptionLabel):
    """单行预览条：占位 → 异步渲染（高分辨率真实排版条带）→ QPixmap。

    条带按屏幕 DPR 渲染物理分辨率（STRIP_PX 超采样），显示前缩到
    本行物理高度再标 DPR——逻辑尺寸 28、像素级清晰。"""

    def __init__(self, worker, pvm, entity, text, tag, height=28,
                 parent=None, fit_width=True):
        # qfluentwidgets FluentLabelBase.__init__ 是 singledispatchmethod，
        # (text, parent) 分支会经 self.__init__ 重派发——子类下会递归回本构造，
        # 故只传 parent、占位文字改用 setText（1.11.3 API 适配，语义不变）
        super().__init__(parent)
        self.setText("…")
        self.setFixedHeight(height)
        self._worker, self._pvm = worker, pvm
        self._entity, self._text, self._tag = entity, text, tag
        # fit_width=True：条占满行宽（浏览页/选择器，vbox 拉伸行宽外部钉
        # 死，width()-16 呼吸边稳定）。False：条按自身贴图 hint 定宽
        # （字幕卡选项行，hbox 里没人管宽）——width() 就是贴图宽，呼吸边
        # 自反：每轮 resizeEvent 缩 16px 直到 1px 隐形（真机实锤），改用
        # 静态 360 上限、禁宽度反馈重贴
        self._fit_width = fit_width
        self._inflight = False
        self._connected = False              # result 信号是否仍挂着本槽
        self._job = None                     # 在途任务的取消句柄
        self._strip = None                   # 最近一次贴上的条带，换肤重贴用
        self._stale_theme = False            # 不可见期换肤未贴上，showEvent 补
        self._applied_w = -1                 # 最近一次贴图用的宽度上限
        qconfig.themeChanged.connect(self._on_theme_changed)
        # 错误详情走 Fluent 气泡（过滤器装一次，set_row 换 tooltip 文本照常生效）
        install_tip(self)
        self._render()

    def _on_theme_changed(self, _theme):
        # 贴图字形色按主题取：换肤用已缓存的条带原地重贴（零渲染开销）。
        # 高度为 0（布局未跑/被压塌）时缩图得 null，跳过并记脏留给 showEvent
        if self._strip is None:
            return
        if self.height() > 0:
            self._apply(self._strip)
        else:
            self._stale_theme = True

    def showEvent(self, e):
        super().showEvent(e)
        # 不可见期（高度 0 跳过）换肤的补贴：旧贴图非空，得靠脏标记识别
        if self._stale_theme and self.height() > 0:
            self._stale_theme = False
            self._apply(self._strip)
        # 布局时序保险：首贴宽度上限过时（首贴早于定宽）在此补齐
        if (self._strip is not None and self.width() > 1
                and self._applied_w != self._max_w()):
            self._apply(self._strip)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 首贴发生在布局前（宽未知，退 360 上限）；布局定宽后按实际
        # 可用宽重贴——QLabel 只裁不缩，超宽贴图会右缘截半字。
        # hint 定宽模式（fit_width=False）无外部宽度可言，禁反馈重贴
        if (self._fit_width and self._strip is not None and self.width() > 1
                and self._applied_w != self._max_w()):
            self._apply(self._strip)

    def _max_w(self) -> int:
        if not self._fit_width:
            return _MAX_BAR_W
        if self.width() <= 1:
            return _MAX_BAR_W
        return max(0, min(_MAX_BAR_W, self.width() - _WIDTH_MARGIN))

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
        self._connected = True
        self._job = self._worker.submit(self._tag, _render_job,
                                        self._entity, self._text)

    def set_entity(self, entity, tag):
        """行内切换 face（字重/斜体分段）：撤在途任务、断旧结果槽、清贴
        图重渲。tag 随 fingerprint 换新——已开跑不可撤的旧任务回传时
        tag 失配，_on_result 直接丢弃。"""
        if entity is self._entity:
            return
        self.cancel()
        if self._connected:              # 无连接时 disconnect 会刷 warning
            self._worker.result.disconnect(self._on_result)
            self._connected = False
        self._entity, self._tag = entity, tag
        self._strip = None
        self._stale_theme = False
        self._applied_w = -1
        self.setToolTip("")               # 上一 face 失败遗留的错误详情
        self.setText("…")
        self.setPixmap(QPixmap())
        self._render()

    def cancel(self):
        """行滚出物化窗口/列表重建时调用：未开始的渲染任务从队列除名，
        不占工作者串行时段（否则新视口的预览排在几百个过期任务后）。
        已开始的不可中断，结果回传时控件多半已销毁、连接自动断开。"""
        if self._job is not None:
            self._job.cancelled = True
        self._inflight = False

    def _on_result(self, tag, payload):
        if tag != self._tag:
            return
        # 回传 text：结果按 (tag, text) 双重匹配，防换文字后旧在途结果错配
        # 缓存键；渲染失败也折进本通路（err 字段）——failed 信号不带 text
        # 无法做同样的护栏，同 tag 旧任务的失败会错标新行
        text, strip, err = payload
        if text != self._text:
            # 同 tag（fp + 列表实例）的在途旧任务——行已随 set_rows 重建：
            # 结果的文字与本行不符，直接丢弃，不清缓存不置位
            return
        # 同 tag 双任务（滚出重物化后孤儿任务被新行认领）会回传两次：
        # 第二次已无连接，disconnect 会刷 RuntimeWarning
        if self._connected:
            self._worker.result.disconnect(self._on_result)
            self._connected = False
        self._inflight = False
        if err is not None:
            self.setText("（渲染失败）")
            self.setToolTip(err)        # hover 可见具体异常，排查怪字体
            return
        if strip is None:
            # 全缺字也是事实，缓存住（空条带哨兵，_apply 显「无可渲染字形」）：
            # 存 None 等于无缓存，滚回可视区会每次重开字体文件重渲
            strip = np.zeros((0, 0), dtype=np.uint8)
        self._pvm.put_tiles((self._entity.fingerprint, self._text), strip)
        self._apply(strip)

    def _apply(self, strip):
        dpr = self.devicePixelRatioF() or 1.0
        pm = strip_to_pixmap(strip, dpr)
        if pm.isNull():
            self.setText("（无可渲染字形）")
            return
        self._strip = strip
        # 条带是超采样位图：缩到本行物理高度（逻辑 28 × DPR），标 DPR 后
        # Qt 按逻辑尺寸显示，像素级 1:1 不再拉伸——模糊根源（48px tile 放大）即除
        pm = pm.scaledToHeight(max(1, round(self.height() * dpr)),
                               Qt.SmoothTransformation)
        mw = self._max_w()
        if round(pm.width() / dpr) > mw:
            pm = pm.scaledToWidth(max(1, round(mw * dpr)),
                                  Qt.SmoothTransformation)
            # Qt 6.12 的 scaled 家族已保留 DPR 标签，此行是防版本行为漂移
            # 的防御——几何数学依赖「缩放后 DPR 仍为 dpr」，勿删
            pm.setDevicePixelRatio(dpr)
        self._applied_w = mw
        self.setPixmap(pm)


def _render_job(ctx, entity, text):
    from ass_font_substitute.gui.jobs import render_preview
    try:
        strip = render_preview(entity, text)
    except Exception as e:
        return text, None, f"{type(e).__name__}: {e}"
    return text, strip, None
