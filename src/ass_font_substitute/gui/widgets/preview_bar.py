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

# 布局未跑（宽未知/预算≤0）时的兜底上限；真上限=实际可用宽（fit_width
# 行取自身宽扣呼吸边，width_fn 行取宿主预算），贴图超宽才缩
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
                 parent=None, fit_width=True, width_fn=None):
        # qfluentwidgets FluentLabelBase.__init__ 是 singledispatchmethod，
        # (text, parent) 分支会经 self.__init__ 重派发——子类下会递归回本构造，
        # 故只传 parent、占位文字改用 setText（1.11.3 API 适配，语义不变）
        super().__init__(parent)
        self.setText("…")
        self.setFixedHeight(height)
        self._worker, self._pvm = worker, pvm
        self._entity, self._text, self._tag = entity, text, tag
        # fit_width=True：条占满行宽（浏览页/选择器，vbox 拉伸行宽外部钉
        # 死，width()-16 呼吸边稳定）。False：条宽由 width_fn 给出（字幕卡
        # 选项行传行几何预算，左侧文本列定宽后各行预览同宽）——width() 就
        # 是贴图宽，呼吸边自反：每轮 resizeEvent 缩 16px 直到 1px 隐形
        # （真机实锤），禁用宽度反馈
        self._fit_width = fit_width
        self._width_fn = width_fn
        self._inflight = False
        self._connected = False              # result 信号是否仍挂着本槽
        self._job = None                     # 在途任务的取消句柄
        self._strip = None                   # 最近一次贴上的条带，换肤重贴用
        self._stale_theme = False            # 不可见期换肤未贴上，showEvent 补
        self._applied_w = -1                 # 最近一次贴图用的宽度上限
        self._applied_h = -1                 # 同上高度维（0 高首贴曾固化 1px 贴图）
        self._mono = False                   # 在途任务的 mono 口径（回传校验）
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
        # 布局时序保险：首贴宽度/高度上限过时（首贴早于定宽定高，0 高时
        # 曾缩成 1 物理像素且宽度恰未变而永不重贴）在此补齐
        if (self._strip is not None and self.width() > 1
                and (self._applied_w != self._max_w()
                     or self._applied_h != round(self.height()
                                                 * (self.devicePixelRatioF()
                                                    or 1.0)))):
            self._apply(self._strip)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 首贴发生在布局前（宽未知，退 360 上限）；布局定宽后按实际
        # 可用宽重贴——QLabel 只裁不缩，超宽贴图会右缘截半字。
        # hint 定宽模式（fit_width=False）无外部宽度可言，宽度免反馈重贴；
        # 高度维两种模式都要跟（0 高首贴固化的 1px 贴图靠此解封）
        if (self._strip is not None and self.width() > 1
                and (self._applied_h != round(self.height()
                                              * (self.devicePixelRatioF()
                                                 or 1.0))
                     or (self._fit_width
                         and self._applied_w != self._max_w()))):
            self._apply(self._strip)

    def _max_w(self) -> int:
        if self._width_fn is not None:
            b = self._width_fn()
            # 布局未跑（预算≤0）：退兜底上限，行定宽后 budget_changed 补贴
            return b if b > 1 else _MAX_BAR_W
        if self.width() <= 1:
            return _MAX_BAR_W
        return max(0, self.width() - _WIDTH_MARGIN)

    def budget_changed(self):
        """宿主行 resize 后由行回调：条带现成且真上限变了才重贴（_apply
        记 _applied_w 防重复贴）。"""
        if (self._width_fn is not None and self._strip is not None
                and self.width() > 1 and self._applied_w != self._max_w()):
            self._apply(self._strip)

    def _render(self):
        if not self._text.strip() or self._entity is None:
            self.setText(self._entity.display_name if self._entity else "")
            return
        # 缓存键含 mono 口径：开关翻转后旧条带自然失配（LRU 慢慢挤掉），
        # 无需显式失效
        self._mono = self._pvm.mono_cells()
        key = (self._entity.fingerprint, self._text, self._mono)
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
                                        self._entity, self._text, self._mono)

    def rerender(self):
        """强制等宽开关翻转后由页面调用：文字不变，清贴图按新口径重渲
        （缓存键含 mono，命中旧口径不发生）。撤在途/断槽/清态同 set_text。"""
        self.cancel()
        if self._connected:              # 无连接时 disconnect 会刷 warning
            self._worker.result.disconnect(self._on_result)
            self._connected = False
        self._strip = None
        self._stale_theme = False
        self._applied_w = -1
        self.setToolTip("")               # 上一文字失败遗留的错误详情
        # 先清贴图再置占位：setPixmap（含 null）会清 QLabel text，反序
        # 则占位「…」被清成空白（真机表现为无占位）
        self.setPixmap(QPixmap())
        self.setText("…")
        self._render()

    def set_text(self, text):
        """换预览文字（预览框输入变化）：撤在途任务、断旧结果槽、清贴图
        重渲。tag 不变（tag 标识行非文字），旧在途结果回传时按 (tag, text)
        双重匹配失配丢弃。文字没变直接返回（apply 高频调用防抖）。"""
        if text == self._text:
            return
        self._text = text
        self.rerender()

    def set_entity(self, entity, tag):
        """行内切换 face（字重/斜体分段）：撤在途任务、断旧结果槽、清贴
        图重渲。tag 随 fingerprint 换新——已开跑不可撤的旧任务回传时
        tag 失配，_on_result 直接丢弃。清态与重渲全部复用 rerender。"""
        if entity is self._entity:
            return
        self._entity, self._tag = entity, tag
        self.rerender()

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
        # 回传 (text, mono)：结果按 tag+文字+口径 三重匹配，防换文字/翻转
        # 等宽开关后旧在途结果错配缓存键；渲染失败也折进本通路（err 字段）
        # ——failed 信号不带 text 无法做同样的护栏，同 tag 旧任务的失败会
        # 错标新行
        text, mono, strip, err = payload
        if text != self._text or mono != self._pvm.mono_cells():
            # 同 tag 在途旧任务（行已重建/开关已翻转）：口径不符直接丢弃，
            # 不动连接与 _inflight——那是重派新一代（rerender）的账
            return
        # 同 tag 双任务（滚出重物化后孤儿任务被新行认领）会回传两次：
        # 第二次已无连接，disconnect 会刷 RuntimeWarning
        if self._connected:
            self._worker.result.disconnect(self._on_result)
            self._connected = False
        self._inflight = False
        if err is not None:
            self.setText(self.tr("（渲染失败）"))
            self.setToolTip(err)        # hover 可见具体异常，排查怪字体
            return
        if strip is None:
            # 全缺字也是事实，缓存住（空条带哨兵，_apply 显「无可渲染字形」）：
            # 存 None 等于无缓存，滚回可视区会每次重开字体文件重渲
            strip = np.zeros((0, 0), dtype=np.uint8)
        self._pvm.put_tiles(
            (self._entity.fingerprint, self._text, self._mono), strip)
        self._apply(strip)

    def _apply(self, strip):
        dpr = self.devicePixelRatioF() or 1.0
        pm = strip_to_pixmap(strip, dpr)
        if pm.isNull():
            self.setText(self.tr("（无可渲染字形）"))
            return
        self._strip = strip
        # 条带是超采样位图：缩到本行物理高度（逻辑 28 × DPR），标 DPR 后
        # Qt 按逻辑尺寸显示，像素级 1:1 不再拉伸——模糊根源（48px tile 放大）即除
        pm = pm.scaledToHeight(max(1, round(self.height() * dpr)),
                               Qt.SmoothTransformation)
        mw = self._max_w()
        if round(pm.width() / dpr) > mw:
            # 过长直接截断不缩放（用户拍板 2026-10-29：字号恒定，不再因
            # 长文缩小）——按预算物理像素裁右缘（copy 设备像素坐标，DPR
            # 标签随 copy 保留，防御性重标同上）
            pm = pm.copy(0, 0, max(1, round(mw * dpr)), pm.height())
            pm.setDevicePixelRatio(dpr)
        self._applied_w = mw
        self._applied_h = round(self.height() * dpr)
        self.setPixmap(pm)


def _render_job(ctx, entity, text, mono_cells):
    from ass_font_substitute.gui.jobs import render_preview
    try:
        strip = render_preview(entity, text, mono_cells)
    except Exception as e:
        return text, mono_cells, None, f"{type(e).__name__}: {e}"
    return text, mono_cells, strip, None
