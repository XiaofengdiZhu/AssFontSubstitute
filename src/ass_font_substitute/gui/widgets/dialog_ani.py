"""MaskDialogBase 弹窗开/关动画重写——顶层窗口零特效，动画由自绘层完成。

根因：qfw 的 MaskDialogBase 在 showEvent/done 里把 QGraphicsOpacityEffect
装到顶层窗口上做淡入淡出，而 Qt 不支持顶层特效：窗口映射后再装特效会
触发原生窗口重建，DWM 短暂露出默认样式的带标题栏小窗（主窗左上角闪现
"python" 小窗的来源）。本模块移植 Ghost-Downloader-3（GPL-3.0，
app/view/components/dialog_animation.py）的方案：monkey-patch 掉
showEvent/done，开/关动画改由覆盖整个弹窗的 AnimationLayer 逐帧合成——
预渲染底图（父窗口内容）与面板快照（含阴影），动画结束才露出真实控件。
经 patch_mask_dialog_animation() 全局生效，所有 MessageBoxBase 系弹窗
自动受益，子类零改动。
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QDialog, QWidget
from qfluentwidgets.common.animation import FluentAnimation
from qfluentwidgets.components.dialog_box.mask_dialog_base import MaskDialogBase

_FADE_IN_MS = 200
_FADE_OUT_MS = 100
_SLIDE_MS = 300
_FADE_IN_CURVE = QEasingCurve(QEasingCurve.Type.InSine)
_SLIDE_IN_CURVE = FluentAnimation.createBezierCurve(0, 0, 0, 1)
_SLIDE_OUT_CURVE = FluentAnimation.createBezierCurve(0.8, 0, 0.78, 1)


def patch_mask_dialog_animation() -> None:
    """全局替换 MaskDialogBase 的开/关动画。在 QApplication 创建后调用一次。"""
    MaskDialogBase.slideEdge = None   # 子类设成 Qt.Edge 时，面板从这条边滑入滑出
    # qfw 1.11.3 全部子类都不设 slideEdge，实际恒走淡入淡出；滑移路径按
    # GPL 原实现保留（预留给未来想给某个弹窗加方向感的场景）
    MaskDialogBase.showEvent = _show_event
    MaskDialogBase.done = _done


def _show_event(dialog: MaskDialogBase, e) -> None:
    QDialog.showEvent(dialog, e)
    _AnimationLayer(dialog, is_closing=False)


def _done(dialog: MaskDialogBase, code: int) -> None:
    layers = dialog.findChildren(
        _AnimationLayer, options=Qt.FindChildOption.FindDirectChildrenOnly)
    if any(layer.is_closing for layer in layers):
        return
    layer = _AnimationLayer(dialog, is_closing=True)
    layer.finished.connect(lambda: QDialog.done(dialog, code))


def _slide_offset(dialog: MaskDialogBase) -> QPoint:
    panel = dialog.widget.geometry()
    return {
        Qt.Edge.LeftEdge: QPoint(-panel.right() - 1, 0),
        Qt.Edge.RightEdge: QPoint(dialog.width() - panel.left(), 0),
        Qt.Edge.TopEdge: QPoint(0, -panel.bottom() - 1),
        Qt.Edge.BottomEdge: QPoint(0, dialog.height() - panel.top()),
    }.get(dialog.slideEdge, QPoint())


class _AnimationLayer(QWidget):
    """覆盖整个弹窗的自绘动画层：底图 + 遮罩 + 面板快照逐帧合成。

    真实控件全程存在但被本层盖住；动画结束 hide + deleteLater 露出。
    面板快照只截一次（阴影连着截进去），动画期间每帧只画三张图，
    不重算阴影模糊。"""

    def __init__(self, dialog: MaskDialogBase, is_closing: bool):
        super().__init__(dialog)
        self.is_closing = is_closing
        self._dialog = dialog
        if dialog.slideEdge is not None:
            duration = _SLIDE_MS
        else:
            duration = _FADE_OUT_MS if is_closing else _FADE_IN_MS
        self._animation = QVariantAnimation(self, startValue=0.0,
                                            endValue=float(duration),
                                            duration=duration)
        self._panel = QPixmap()
        self._is_rendering_panel = False
        self.finished = self._animation.finished
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(dialog.rect())
        # 先停掉上一段动画，免得它被截进新图
        for layer in dialog.findChildren(
                _AnimationLayer,
                options=Qt.FindChildOption.FindDirectChildrenOnly):
            if layer is not self:
                layer._animation.stop()
        self._backdrop = self._build_backdrop()
        self._animation.valueChanged.connect(self.update)
        self._animation.stateChanged.connect(self._on_state_changed)
        # 对话框声明不透明，Qt 就不再重画底下的页面
        dialog.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        if is_closing:
            # 关闭时当场截图，窗口最小化时也能关掉
            self._run()
        else:
            # 打开不能等首次 paintEvent 再截图：本层创建于 showEvent，
            # 首帧绘制时 dialog 仍在 InPaintEvent 里，_build_panel 的
            # render 会触发 Qt 递归重绘中止并废掉本帧画笔（终端刷
            # Recursive repaint/painter not active）。showEvent 阶段
            # 不在绘制过程内，render 合法；先强制激活布局再截。
            self._dialog.layout().activate()
            self._run()
        self.show()
        # polish 会清掉这个属性，所以在 show 之后设置
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.raise_()

    def _run(self) -> None:
        self._mask_color = self._build_mask_color()
        self._panel_origin, self._panel = self._build_panel()
        # 关掉阴影效果，面板就不会每帧重新模糊
        self._set_panel_effect_enabled(False)
        self._animation.start()

    def _to_frame(self, ms: float) -> tuple[float, float, QPoint]:
        # 返回 (遮罩透明度, 面板透明度, 面板偏移)
        offset = _slide_offset(self._dialog)
        if not self.is_closing:
            opacity = _FADE_IN_CURVE.valueForProgress(
                min(ms / _FADE_IN_MS, 1))
            return opacity, opacity, offset * (1 - _SLIDE_IN_CURVE.valueForProgress(
                ms / _SLIDE_MS))
        if self._dialog.slideEdge is None:
            opacity = 1 - ms / _FADE_OUT_MS
            return opacity, opacity, offset
        progress = ms / _SLIDE_MS
        return 1 - progress, 1.0, offset * _SLIDE_OUT_CURVE.valueForProgress(progress)

    def _on_state_changed(self, state: QVariantAnimation.State) -> None:
        if state == QVariantAnimation.State.Stopped:
            self._dialog.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,
                                      False)
            self._set_panel_effect_enabled(True)
            self.hide()
            self.deleteLater()

    def _set_panel_effect_enabled(self, enabled: bool) -> None:
        effect = self._dialog.widget.graphicsEffect()
        if effect is not None:
            effect.setEnabled(enabled)

    def _build_backdrop(self) -> QPixmap:
        window = self._dialog.window()
        store = window.backingStore()
        # 窗口还没画过时后备缓冲还没分配图像，读它会崩溃
        image = None if store.size().isEmpty() else store.paintDevice()
        # 打开时对话框还没画过，后备缓冲里就是底下的画面，直接复制最快
        if not self.is_closing and isinstance(image, QImage):
            ratio = window.devicePixelRatioF()
            pixmap = QPixmap.fromImage(image.copy(
                QRect(self._dialog.mapTo(window, QPoint(0, 0)) * ratio,
                      self.size() * ratio)))
            pixmap.setDevicePixelRatio(ratio)
            return pixmap
        parent = self._dialog.parentWidget()
        pixmap = self._build_pixmap(self.size())
        origin = -self._dialog.pos()
        parent.render(pixmap, origin, QRegion(), QWidget.RenderFlag(0))
        siblings = [child for child in parent.children()
                    if isinstance(child, QWidget)]
        # 不画盖在对话框上面的兄弟控件，如 InfoBar
        for child in siblings[:siblings.index(self._dialog)]:
            if child.isVisible() and not child.isWindow():
                child.render(pixmap, origin + child.pos(), QRegion(),
                             QWidget.RenderFlag.DrawChildren)
        return pixmap

    def _build_mask_color(self) -> QColor:
        pixmap = QPixmap(1, 1)
        pixmap.fill(Qt.GlobalColor.transparent)
        # 不画窗口背景，否则半透明底色会叠两层
        self._dialog.windowMask.render(pixmap, QPoint(), QRegion(0, 0, 1, 1),
                                       QWidget.RenderFlag(0))
        return pixmap.toImage().pixelColor(0, 0)

    def _build_panel(self) -> tuple[QPoint, QPixmap]:
        panel = self._dialog.widget
        mask = self._dialog.windowMask
        effect = panel.graphicsEffect()
        bounds = (effect.boundingRectFor(panel.rect()).toAlignedRect()
                  if effect else panel.rect())
        pixmap = self._build_pixmap(bounds.size())
        # 从对话框渲染才画得出阴影，渲染期间撤掉遮罩底色和这一层的不透明声明
        self._is_rendering_panel = True
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        mask.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._dialog.render(pixmap, QPoint(),
                            QRegion(bounds.translated(panel.pos())),
                            QWidget.RenderFlag.DrawChildren)
        mask.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._is_rendering_panel = False
        return bounds.topLeft(), pixmap

    def _build_pixmap(self, size) -> QPixmap:
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(size * ratio)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap

    def paintEvent(self, e) -> None:
        # 截面板时也会渲染到这一层
        if self._is_rendering_panel:
            return
        # 仅剩异常保护：两路 _run() 均已移入 __init__（7feb4c3 起打开/关闭
        # 都在构造时截图）。正常流程此分支不可达——绘制中 render 会触发
        # Qt 递归重绘中止，正是当年要根治的问题，勿把截图移回这里
        if self._panel.isNull():
            self._run()
        mask_opacity, panel_opacity, panel_offset = self._to_frame(
            self._animation.currentValue())
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawPixmap(0, 0, self._backdrop)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        # 遮罩是纯色，直接填色最快
        mask_color = QColor(self._mask_color)
        mask_color.setAlphaF(mask_color.alphaF() * mask_opacity)
        painter.fillRect(self._dialog.windowMask.geometry(), mask_color)
        painter.setOpacity(panel_opacity)
        painter.drawPixmap(self._dialog.widget.pos() + panel_offset
                           + self._panel_origin, self._panel)
