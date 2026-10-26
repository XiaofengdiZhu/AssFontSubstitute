"""工具提示统一入口：原生 tooltip 样式丑（系统灰底直角），qfw 官方教
程方案 = ToolTipFilter 事件过滤器拦截 ToolTip 事件，代以 Fluent 气泡
（深色半透圆角，随主题）。showDelay=300 官方默认；TOP 位置由 qfw 位
置管理器钳制在屏幕内，顶栏按钮也不会戳出屏。过滤器装一次，之后
setToolTip 换文本照常生效（含动态更新），空文本不弹。"""
from __future__ import annotations

from qfluentwidgets import ToolTipFilter
from qfluentwidgets import ToolTipPosition

SHOW_DELAY = 300    # 悬停多久出泡（ms），官方示例同款


def install_tip(w, text: str | None = None,
                position: ToolTipPosition = ToolTipPosition.TOP) -> ToolTipFilter:
    """给控件装 Fluent 工具提示过滤器；text 非空则顺手设置。返回过滤
    器实例（装在控件上随控件销毁，调用方无需持有）。"""
    if text is not None:
        w.setToolTip(text)
    f = ToolTipFilter(w, showDelay=SHOW_DELAY, position=position)
    w.installEventFilter(f)
    return f
