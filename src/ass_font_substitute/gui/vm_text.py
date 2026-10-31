"""vm 层展示串翻译出口（context 固定 "vm"）。

subs_vm 是零 Qt 状态机：徽章/KEEP 行标题等中文串身兼显示与逻辑键
（成员判断、去重），vm 数据一律保持源语言（简体），渲染处经本模块
按 "vm" context 取译文。font_card / subs_page 渲染 vm 数据时调用。

动态值（core 产的 script/category、vm 徽章表）不以字面量进 translate()，
lupdate 提取不到——_VOCAB 以 QT_TRANSLATE_NOOP 逐条登记（运行时原样
返回源串，仅供 lupdate 扫描收录）。
"""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6.QtCore import QCoreApplication

from ass_font_substitute.gui.subs_vm import KEEP


def text(s: str) -> str:
    """单值翻译：徽章/药丸/KEEP 行标题等源语言串。无译文（简体运行/
    未登记的值）原样返回。"""
    return QCoreApplication.translate("vm", s)


def header(style_count: int, inline_count: int, n_chars: int) -> str:
    """卡头用量行（subs_vm.Card.header 的渲染层版本：vm 侧 property
    保持零 Qt 供测试直喂）。"""
    return QCoreApplication.translate(
        "vm", "{0} 样式 · {1} 行内 · {2} 字").format(
        style_count, inline_count, n_chars)


def opt_name(key: str, display_name: str) -> str:
    """选项行展示名：KEEP 行标题是 vm 串须翻译；实体行是字体展示名
    （数据，非文案），原样返回。"""
    return text(display_name) if key == KEEP else display_name


# lupdate 提取登记（须逐条字面量——lupdate 不认变量参数）
_VOCAB = (
    # 徽章 / 状态（subs_vm badges、_STATUS）
    QT_TRANSLATE_NOOP("vm", "原字体"),
    QT_TRANSLATE_NOOP("vm", "孪生对应"),
    QT_TRANSLATE_NOOP("vm", "上次使用"),
    QT_TRANSLATE_NOOP("vm", "无对白用字"),
    QT_TRANSLATE_NOOP("vm", "保留"),
    QT_TRANSLATE_NOOP("vm", "需替换"),
    QT_TRANSLATE_NOOP("vm", "库中没有"),
    # KEEP 行标题（subs_vm.make_card）
    QT_TRANSLATE_NOOP("vm", "保留原字体名"),
    QT_TRANSLATE_NOOP("vm", "保留原字体名（有内嵌）"),
    # core 领域值药丸（probes.classify_script / classify_category）
    QT_TRANSLATE_NOOP("vm", "简"),
    QT_TRANSLATE_NOOP("vm", "繁"),
    QT_TRANSLATE_NOOP("vm", "简繁"),
    QT_TRANSLATE_NOOP("vm", "未标注"),
    QT_TRANSLATE_NOOP("vm", "简/繁"),       # script_filter.NO_SCRIPT
    QT_TRANSLATE_NOOP("vm", "仿"),
    QT_TRANSLATE_NOOP("vm", "黑"),
    QT_TRANSLATE_NOOP("vm", "楷"),
    QT_TRANSLATE_NOOP("vm", "宋"),
    QT_TRANSLATE_NOOP("vm", "圆"),
    QT_TRANSLATE_NOOP("vm", "未分类"),
)
